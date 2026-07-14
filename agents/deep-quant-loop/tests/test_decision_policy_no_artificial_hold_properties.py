"""Decision-policy property tests + byte-identical Trade_Validator golden test.

Feature: deep-quant-decision-reliability (Bug 6 — agent defaults to HOLD too easily)

This module locks in the Bug 6 rebalance in ``agents/deep-quant-loop/graph.py``.
The fix removes ARTIFICIAL HOLD drivers (unavailable OPTIONAL inputs, a
polluted/low-sample track record, starved candles, and a content-free
reasoning-cap HOLD) via prompt guidance + Best_Current_Read surfacing WITHOUT
touching the Trade_Validator. These tests therefore assert two complementary
things:

  * Property 11 (Bug Condition — no artificial HOLD): a reasoning-cap HOLD is an
    ACTIONABLE stand-aside carrying a directional Best_Current_Read (bias/levels/
    why) folded into ``setup_validation`` / ``execution_plan`` and the emitted
    JSON; and ``graph._best_current_read`` surfaces a directional read from the
    SECONDARY prediction when the PRIMARY forecast is unavailable, while staying
    neutral (no fabricated direction) when BOTH are unavailable.
    Validates: Requirements 2.13, 2.14, 2.15, 2.16, 2.17, 2.18

  * Property 12 (Preservation — genuine HOLD + intact validator): genuine
    risk-gate failures still produce the expected validator outcomes, and — the
    CRITICAL guarantee — the Trade_Validator check surface
    (``graph._verify_mode_validator_checks`` and the shapes handled by
    ``_declare_was_rejected`` / ``_decision_from_declare``) is byte-identical:
    a golden/snapshot over a fixed set of representative ``(action, levels, atr)``
    inputs proves no hard rule (stop >= 1.5xATR, R:R >= 1:2, levels present) was
    weakened by the Bug 6 changes.
    Validates: Requirements 3.12, 3.13, 3.14, 3.15

LLM / network are never involved — every function under test is a pure,
in-process read. The sys.path / import bootstrap mirrors the sibling
``tests/test_force_hold_terminal_read.py`` and
``tests/test_best_current_read_omission_properties.py`` modules.
"""

import json
import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import graph`` resolves exactly as every sibling test module expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import (  # noqa: E402
    _best_current_read,
    _declare_was_rejected,
    _decision_from_declare,
    _prediction_entry,
    _verify_mode_validator_checks,
    force_hold,
)

# The directional execution levels a committed BUY/SELL carries; a stand-aside
# HOLD must never surface a finite value for any of these.
_DIRECTIONAL_LEVEL_KEYS = ("entry", "stop_loss", "take_profit")


def _is_finite_number(x) -> bool:
    """A real, finite, non-bool number (mirrors ``graph._is_finite_num``)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _minimal_hold_state():
    """A minimal AgentState for a reasoning-exhaustion HOLD (mirrors the sibling
    ``tests/test_force_hold_terminal_read.py`` fixture)."""
    return {
        "messages": [HumanMessage(content="Find a trade on NIFTY.")],
        "mode": "FIND",
        "symbol": "NIFTY",
        "timeframe": "INTRADAY",
        "manual_trade": None,
        "reasoning_turns": graph.MAX_REASONING_TURNS,
        "decision": None,
    }


# ═════════════════════════════════════════════════════════════════════════════
# Property 11 (Bug Condition) — reasoning-cap HOLD is an ACTIONABLE stand-aside
# ═════════════════════════════════════════════════════════════════════════════


def test_force_hold_folds_directional_best_current_read_into_surfaced_text(monkeypatch):
    """A reasoning-cap ``force_hold`` whose stand-aside carries a DIRECTIONAL
    Best_Current_Read folds that read (bias + reference levels + why) into
    ``setup_validation`` / ``execution_plan`` and the emitted JSON — so the
    reasoning-cap HOLD is an actionable stand-aside, not a content-free HOLD.

    ``_finalize_decision`` (-> ``_stamp_opportunity_tier``) is the seam that
    normally computes and stamps the read; here it is mocked to stamp a
    directional read deterministically (as the task allows), isolating the
    force_hold surfacing logic.

    Validates: Requirements 2.13, 2.16, 2.17, 2.18
    """
    directional_read = {
        "bias": "bullish",
        "levels": {"support": 24180.0, "resistance": 24420.0},
        "why_standing_aside": "Reclaim of VWAP with buyers defending support.",
    }

    def _fake_finalize(state, decision, thread_id=None):
        # Mirror what _stamp_opportunity_tier does for a stand-aside HOLD.
        decision["opportunity_tier"] = "stand_aside"
        decision.setdefault("best_current_read", directional_read)
        return {"defensibility": "stub"}

    monkeypatch.setattr(graph, "_finalize_decision", _fake_finalize)

    update = force_hold(_minimal_hold_state())
    decision = update["decision"]

    # Sanity: this is the reasoning-exhaustion HOLD.
    assert decision["action"] == "HOLD"
    assert decision["reason"] == "no-decision-reached"

    # The directional read is carried on the decision.
    assert decision["best_current_read"] == directional_read

    # It is FOLDED into the surfaced setup_validation (bias + levels + why).
    sv = decision["setup_validation"]
    assert "Best_Current_Read:" in sv
    assert "bullish" in sv
    assert "support=24180.0" in sv and "resistance=24420.0" in sv
    assert "Reclaim of VWAP with buyers defending support." in sv

    # The execution_plan reflects the directional stand-aside (still no committed
    # entry/stop/target).
    ep = decision["execution_plan"]
    assert "bullish" in ep
    assert "No entry/stop/target committed" in ep

    # The emitted glass-box JSON carries the read too.
    emitted = json.loads(update["messages"][0].content)
    assert emitted["best_current_read"] == directional_read
    assert "Best_Current_Read:" in emitted["setup_validation"]

    # Preservation guard: the actionable stand-aside still fabricates NO committed
    # directional levels on the decision.
    for key in _DIRECTIONAL_LEVEL_KEYS:
        value = decision.get(key)
        assert value is None or not _is_finite_number(value)


# ── Property 11 — secondary-prediction directional fallback in _best_current_read ──

_DIRECTIONS = st.sampled_from(["Up", "Down", "Flat"])
_finite_val = st.floats(
    min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
)
_confidence = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

_EXPECTED_BIAS_FROM_DIR = {"Up": "bullish", "Down": "bearish", "Flat": "neutral"}


# Feature: deep-quant-decision-reliability, Property 11: when the PRIMARY forecast is unavailable, _best_current_read surfaces the SECONDARY prediction's up/down projection as a directional bias (Flat stays neutral); it NEVER fabricates a direction.
@settings(max_examples=200, deadline=None)
@given(direction=_DIRECTIONS, projected=_finite_val, confidence=_confidence)
def test_best_current_read_surfaces_secondary_prediction_when_forecast_unavailable(
    direction, projected, confidence
):
    """With a neutral primary read and NO forecast, a genuine up/down secondary
    prediction sets the interim bias; a 'Flat' projection stays neutral.

    Validates: Requirements 2.17, 2.18
    """
    # _prediction_entry-shaped evidence, built from a get_prediction result.
    entry = _prediction_entry(
        {
            "get_prediction": {
                "projected_direction": direction,
                "projected_value": projected,
                "confidence": confidence,
            }
        }
    )
    assert entry["available"] is True  # sanity: usable projection

    # Empty confluence signals => primary bias is neutral; forecast absent => the
    # secondary prediction fallback governs the interim direction.
    evidence = {"prediction": entry}
    read = _best_current_read(evidence, None)

    assert read["bias"] == _EXPECTED_BIAS_FROM_DIR[direction]

    if direction in ("Up", "Down"):
        # The rationale notes the secondary-source fallback, and no committed
        # directional levels are fabricated on the read.
        assert "secondary prediction" in read["why_standing_aside"].lower()
    assert read["levels"] == {}


def test_best_current_read_stays_neutral_when_forecast_and_prediction_unavailable():
    """When BOTH the primary forecast and the secondary prediction are
    unavailable, the interim read stays neutral — no direction is fabricated.

    Validates: Requirements 2.17, 2.18, 3.14
    """
    # An unavailable prediction entry (nothing usable in message history).
    entry = _prediction_entry({})
    assert entry["available"] is False

    read = _best_current_read({"prediction": entry}, None)
    assert read["bias"] == "neutral"

    # No prediction key at all + no forecast => still neutral.
    assert _best_current_read({}, None)["bias"] == "neutral"


def test_best_current_read_does_not_override_when_forecast_available():
    """An AVAILABLE forecast already fed the primary bias; the secondary
    prediction must NOT override it (the fallback is forecast-unavailable only).

    Validates: Requirements 2.17, 3.14
    """
    down_pred = _prediction_entry(
        {
            "get_prediction": {
                "projected_direction": "Down",
                "projected_value": 100.0,
                "confidence": 0.9,
            }
        }
    )
    # Forecast marked available => the prediction fallback branch is skipped, so
    # the delegated primary read (neutral here, empty confluence) is returned
    # unchanged rather than being flipped bearish by the prediction.
    read = _best_current_read(
        {"forecast": {"available": True}, "prediction": down_pred}, None
    )
    assert read["bias"] == "neutral"


# ═════════════════════════════════════════════════════════════════════════════
# Property 12 (Preservation) — genuine risk-gate failures still fail
# ═════════════════════════════════════════════════════════════════════════════


def _outcome_map(checks):
    """Collapse the per-check list into a {check: outcome} dict for comparison."""
    return {c["check"]: c["outcome"] for c in checks}


def test_validator_checks_genuine_risk_gate_failures_and_valid_buy():
    """Genuine risk-gate failures still produce the expected validator outcomes,
    and a fully valid BUY passes every check (nothing weakened).

    Validates: Requirements 3.12, 3.13
    """
    # R:R < 1:2 -> risk-reward fail (ATR unavailable so the ATR check is skipped).
    rr = _outcome_map(
        _verify_mode_validator_checks(
            "BUY", {"entry": 100.0, "stop_loss": 90.0, "take_profit": 115.0}, None
        )
    )
    assert rr["risk-reward"] == "fail"
    assert rr["execution-levels-present"] == "pass"
    assert rr["direction-consistency"] == "pass"

    # stop < 1.5xATR -> stop-distance fail (RR healthy so the failure is isolated).
    stop = _outcome_map(
        _verify_mode_validator_checks(
            "BUY", {"entry": 100.0, "stop_loss": 90.0, "take_profit": 140.0}, 10.0
        )
    )
    assert stop["stop-distance-vs-atr"] == "fail"
    assert stop["risk-reward"] == "pass"

    # Missing levels -> execution-levels-present fail; the rest not-evaluable.
    missing = _outcome_map(_verify_mode_validator_checks("BUY", None, 10.0))
    assert missing["execution-levels-present"] == "fail"
    assert missing["direction-consistency"] == "not-evaluable — missing levels"
    assert missing["risk-reward"] == "not-evaluable — missing levels"

    # A valid BUY (sl<entry<tp, RR>=2, stop>=1.5xATR) -> ALL pass.
    valid = _outcome_map(
        _verify_mode_validator_checks(
            "BUY", {"entry": 100.0, "stop_loss": 80.0, "take_profit": 160.0}, 10.0
        )
    )
    assert valid == {
        "execution-levels-present": "pass",
        "direction-consistency": "pass",
        "stop-distance-vs-atr": "pass",
        "risk-reward": "pass",
    }


# ═════════════════════════════════════════════════════════════════════════════
# Property 12 — CRITICAL byte-identical Trade_Validator golden test
# ═════════════════════════════════════════════════════════════════════════════

# A fixed, representative set of (action, levels, atr_14) inputs with the EXACT
# per-check outcome each MUST always produce. This snapshot is the proof that the
# Bug 6 changes did not weaken any hard rule: if any rule (stop >= 1.5xATR,
# R:R >= 1:2, levels present, direction consistency) is loosened, one of these
# expected outcomes changes and the test fails. Only the boolean/enum ``outcome``
# is asserted (detail strings are metadata and intentionally ignored).
_GOLDEN_VALIDATOR_CASES = [
    # 1) Fully valid BUY -> every check passes.
    (
        "valid_buy_all_pass",
        "BUY",
        {"entry": 100.0, "stop_loss": 80.0, "take_profit": 160.0},
        10.0,
        {
            "execution-levels-present": "pass",
            "direction-consistency": "pass",
            "stop-distance-vs-atr": "pass",
            "risk-reward": "pass",
        },
    ),
    # 2) Fully valid SELL -> every check passes (tp<entry<sl, RR=3, stop>=1.5xATR).
    (
        "valid_sell_all_pass",
        "SELL",
        {"entry": 100.0, "stop_loss": 110.0, "take_profit": 70.0},
        5.0,
        {
            "execution-levels-present": "pass",
            "direction-consistency": "pass",
            "stop-distance-vs-atr": "pass",
            "risk-reward": "pass",
        },
    ),
    # 3) R:R exactly 1:2 boundary -> risk-reward passes (>= is preserved).
    (
        "rr_boundary_two_passes",
        "BUY",
        {"entry": 100.0, "stop_loss": 90.0, "take_profit": 120.0},
        None,
        {
            "execution-levels-present": "pass",
            "direction-consistency": "pass",
            "stop-distance-vs-atr": "not-evaluable — ATR unavailable",
            "risk-reward": "pass",
        },
    ),
    # 4) R:R below 1:2 -> risk-reward fails.
    (
        "rr_below_two_fails",
        "BUY",
        {"entry": 100.0, "stop_loss": 90.0, "take_profit": 119.0},
        None,
        {
            "execution-levels-present": "pass",
            "direction-consistency": "pass",
            "stop-distance-vs-atr": "not-evaluable — ATR unavailable",
            "risk-reward": "fail",
        },
    ),
    # 5) Stop distance exactly 1.5xATR -> stop-distance passes (>= is preserved).
    (
        "stop_distance_boundary_passes",
        "BUY",
        {"entry": 100.0, "stop_loss": 85.0, "take_profit": 145.0},
        10.0,
        {
            "execution-levels-present": "pass",
            "direction-consistency": "pass",
            "stop-distance-vs-atr": "pass",
            "risk-reward": "pass",
        },
    ),
    # 6) Stop tighter than 1.5xATR -> stop-distance fails (RR healthy, isolated).
    (
        "stop_too_tight_fails",
        "BUY",
        {"entry": 100.0, "stop_loss": 90.0, "take_profit": 140.0},
        10.0,
        {
            "execution-levels-present": "pass",
            "direction-consistency": "pass",
            "stop-distance-vs-atr": "fail",
            "risk-reward": "pass",
        },
    ),
    # 7) BUY with stop above entry -> direction inconsistency fails.
    (
        "buy_direction_inconsistent_fails",
        "BUY",
        {"entry": 100.0, "stop_loss": 110.0, "take_profit": 130.0},
        None,
        {
            "execution-levels-present": "pass",
            "direction-consistency": "fail",
            "stop-distance-vs-atr": "not-evaluable — ATR unavailable",
            "risk-reward": "pass",
        },
    ),
    # 8) SELL with stop below entry -> direction inconsistency fails.
    (
        "sell_direction_inconsistent_fails",
        "SELL",
        {"entry": 100.0, "stop_loss": 90.0, "take_profit": 70.0},
        None,
        {
            "execution-levels-present": "pass",
            "direction-consistency": "fail",
            "stop-distance-vs-atr": "not-evaluable — ATR unavailable",
            "risk-reward": "pass",
        },
    ),
    # 9) Missing levels entirely -> present fails, the rest are not-evaluable.
    (
        "missing_levels_none",
        "BUY",
        None,
        10.0,
        {
            "execution-levels-present": "fail",
            "direction-consistency": "not-evaluable — missing levels",
            "stop-distance-vs-atr": "not-evaluable — missing levels",
            "risk-reward": "not-evaluable — missing levels",
        },
    ),
    # 10) Partial levels (missing take_profit) -> treated as missing levels.
    (
        "missing_levels_partial",
        "SELL",
        {"entry": 100.0, "stop_loss": 110.0},
        5.0,
        {
            "execution-levels-present": "fail",
            "direction-consistency": "not-evaluable — missing levels",
            "stop-distance-vs-atr": "not-evaluable — missing levels",
            "risk-reward": "not-evaluable — missing levels",
        },
    ),
    # 11) HOLD bypasses all level checks (direction n/a).
    (
        "hold_bypasses_checks",
        "HOLD",
        {"entry": 100.0, "stop_loss": 90.0, "take_profit": 130.0},
        10.0,
        {"direction": "n/a — HOLD/abstain bypasses level checks"},
    ),
]


def test_validator_golden_snapshot_is_byte_identical():
    """CRITICAL golden test: over a fixed, representative input set the
    ``_verify_mode_validator_checks`` outcomes match the encoded expectations
    EXACTLY, proving the Bug 6 policy rebalance weakened no hard rule.

    Validates: Requirements 3.12, 3.13, 3.14, 3.15
    """
    for name, action, levels, atr, expected in _GOLDEN_VALIDATOR_CASES:
        actual = _outcome_map(_verify_mode_validator_checks(action, levels, atr))
        assert actual == expected, (
            f"validator golden case {name!r} drifted: expected {expected}, got {actual}"
        )


def test_declare_rejected_and_decision_from_declare_shapes_unchanged():
    """The validator-adjacent surfaces that gate finalization are shape-stable:

      * ``_declare_was_rejected`` reports True only when a ``declare_trade`` tool
        result carries the ``TRADE_REJECTED`` marker, and
      * ``_decision_from_declare`` reconstructs the structured decision (action +
        entry/stop/take_profit/atr_14) from a declare_trade tool call, and None
        when no such call is present.

    This guards the Bug 6 constraint that the validator/finalize gating path is
    byte-identical.

    Validates: Requirements 3.13, 3.15
    """
    rejected_msgs = [
        AIMessage(content="declaring"),
        ToolMessage(
            content="TRADE_REJECTED: risk-reward too low",
            name="declare_trade",
            tool_call_id="tc-1",
        ),
    ]
    accepted_msgs = [
        ToolMessage(
            content="TRADE_COMMITTED: id=42",
            name="declare_trade",
            tool_call_id="tc-2",
        ),
    ]
    assert _declare_was_rejected(rejected_msgs) is True
    assert _declare_was_rejected(accepted_msgs) is False
    assert _declare_was_rejected([HumanMessage(content="hi")]) is False

    ok_calls = [
        {
            "name": "declare_trade",
            "args": {
                "action": "BUY",
                "conviction_score": 7,
                "setup_validation": "confluence ok",
                "execution_plan": "enter on retest",
                "entry": 100.0,
                "stop_loss": 90.0,
                "take_profit": 130.0,
                "atr_14": 6.0,
            },
        }
    ]
    decision = _decision_from_declare(ok_calls)
    assert decision is not None
    assert decision["action"] == "BUY"
    assert decision["entry"] == 100.0
    assert decision["stop_loss"] == 90.0
    assert decision["take_profit"] == 130.0
    assert decision["atr_14"] == 6.0
    assert decision["source"] == "declare_trade"

    assert _decision_from_declare([{"name": "get_candles", "args": {}}]) is None
