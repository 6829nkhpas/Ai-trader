"""R1 verification/preservation property test — DECISION payload level threading.

Feature: deep-quant-runtime-hardening (bugfix)

Property 11 (Preservation), Python ``stream_events.build_decision_event`` seam —
"the decision payload threads action, tier, and gated execution levels":

    For any committed decision dict, ``build_decision_event`` SHALL:
      * always thread the committed ``action`` verbatim,
      * carry ``opportunity_tier`` when the decision supplies a non-empty tier,
      * include an ``execution_levels`` block carrying entry/stop_loss/take_profit
        IF AND ONLY IF the action normalizes to a directional BUY/SELL AND all
        three levels are finite numbers, and
      * carry NO ``execution_levels`` for a HOLD / stand-aside decision (or any
        directional decision missing a finite level) — never synthesized,
        never zero-filled.

    Validates: Requirements 1.1, 1.5.

This is the verification counterpart of
``test_stream_events_decision_levels_bug_properties.py`` (task 1): it runs
against the FIXED ``build_decision_event`` (task 2.1) and is EXPECTED TO PASS.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import stream_events`` resolves exactly as every sibling test module expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from stream_events import build_decision_event, _is_finite_num  # noqa: E402


# ── Generators ────────────────────────────────────────────────────────────────

_DIRECTIONAL_ACTIONS = ["BUY", "SELL", "buy", "sell", " Buy ", "SELL "]
_HOLD_ACTIONS = ["HOLD", "hold", " Hold ", "stand_aside", "WAIT", "", None, 123]

# Finite, validated-trade-shaped prices.
_finite_price = st.floats(
    min_value=0.01, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
)

# A "level slot" that may be a finite number, a non-finite float, a non-number,
# or absent — so the iff-gate is exercised across the whole input space.
_MISSING = object()
_level_slot = st.one_of(
    _finite_price,
    st.sampled_from([float("nan"), float("inf"), float("-inf")]),
    st.sampled_from(["1.5x", None, True, _MISSING]),
)

_tier_slot = st.sampled_from(
    ["a_plus", "a", "b", "stand_aside", "watch", "", None, 0]
)

_conviction_slot = st.one_of(
    st.integers(min_value=0, max_value=100),
    st.sampled_from([None, 74.5]),
)


def _normalizes_directional(action):
    return isinstance(action, str) and action.strip().upper() in ("BUY", "SELL")


@st.composite
def _decisions(draw):
    """Draw an arbitrary committed-decision dict spanning directional / HOLD /
    malformed shapes with finite / non-finite / missing levels and tiers."""
    action = draw(st.sampled_from(_DIRECTIONAL_ACTIONS + _HOLD_ACTIONS))
    decision = {
        "action": action,
        "conviction_score": draw(_conviction_slot),
        "setup_validation": draw(st.sampled_from(
            ["Breakout confirmed.", "Standing aside — chop.", "", None]
        )),
        "execution_plan": "Entry: X. Stop-loss >= 1.5x ATR. Target 1: reassess.",
    }

    tier = draw(_tier_slot)
    if tier is not None:
        decision["opportunity_tier"] = tier

    for key in ("entry", "stop_loss", "take_profit"):
        value = draw(_level_slot)
        if value is not _MISSING:
            decision[key] = value

    return decision


# ── Property 11: action + tier + gated execution levels ─────────────────────────

# Feature: deep-quant-runtime-hardening, Property 11: build_decision_event threads
# the committed action and opportunity_tier, and includes execution_levels iff the
# action is directional (BUY/SELL) and entry/stop_loss/take_profit are all finite;
# a HOLD / stand-aside payload carries no execution_levels.
@settings(max_examples=250)
@given(decision=_decisions())
def test_decision_payload_threads_action_tier_and_gated_levels(decision):
    """Validates: Requirements 1.1, 1.5."""
    payload = build_decision_event(decision)
    assert payload is not None
    assert isinstance(payload, dict)

    # (R1.1) The committed action is always threaded verbatim.
    assert payload.get("action") == decision["action"]

    # opportunity_tier is carried through iff it is a non-empty string.
    tier = decision.get("opportunity_tier")
    if isinstance(tier, str) and tier:
        assert payload.get("opportunity_tier") == tier
    else:
        assert "opportunity_tier" not in payload

    # (R1.5 / Property 11) execution_levels present IFF directional AND all-finite.
    is_directional = _normalizes_directional(decision.get("action"))
    levels_finite = all(
        _is_finite_num(decision.get(k)) for k in ("entry", "stop_loss", "take_profit")
    )
    should_have_levels = is_directional and levels_finite

    if should_have_levels:
        assert "execution_levels" in payload, (
            "directional decision with three finite levels must thread "
            "execution_levels for the UI to gate on"
        )
        levels = payload["execution_levels"]
        assert isinstance(levels, dict)
        assert math.isclose(levels["entry"], decision["entry"])
        assert math.isclose(levels["stop_loss"], decision["stop_loss"])
        assert math.isclose(levels["take_profit"], decision["take_profit"])
        # Never synthesized/zero-filled — every threaded level is a finite number.
        assert all(_is_finite_num(v) for v in levels.values())
    else:
        assert "execution_levels" not in payload, (
            "non-directional or incomplete-level decision must never carry "
            "execution_levels (no synthesis / zero-fill)"
        )


# ── HOLD / stand-aside: never carries execution_levels ──────────────────────────

# Feature: deep-quant-runtime-hardening, Property 11: a committed HOLD / stand-aside
# decision carries no execution_levels even when spurious level fields are present.
@settings(max_examples=150)
@given(
    tier=st.sampled_from(["stand_aside", "watch", "", None]),
    conviction=_conviction_slot,
    entry=_level_slot,
    stop_loss=_level_slot,
    take_profit=_level_slot,
)
def test_hold_decision_never_carries_execution_levels(
    tier, conviction, entry, stop_loss, take_profit
):
    """Validates: Requirements 1.1, 1.5."""
    decision = {
        "action": "HOLD",
        "conviction_score": conviction,
        "setup_validation": "Standing aside — chop. Rule: stop >= 1.5x ATR.",
        "execution_plan": "No trade. Watch for a break. Target 1: reassess.",
    }
    if tier is not None:
        decision["opportunity_tier"] = tier
    for key, value in (("entry", entry), ("stop_loss", stop_loss), ("take_profit", take_profit)):
        if value is not _MISSING:
            decision[key] = value

    payload = build_decision_event(decision)
    assert payload is not None
    assert payload.get("action") == "HOLD"
    assert "execution_levels" not in payload


# ── Directional preservation: validated levels threaded structurally ────────────

# Feature: deep-quant-runtime-hardening, Property 11: a directional BUY/SELL with
# three finite validated prices threads exactly those prices in execution_levels.
@settings(max_examples=150)
@given(
    action=st.sampled_from(["BUY", "SELL"]),
    entry=_finite_price,
    stop_loss=_finite_price,
    take_profit=_finite_price,
    tier=st.sampled_from(["a_plus", "a", "b"]),
)
def test_directional_decision_threads_validated_levels(
    action, entry, stop_loss, take_profit, tier
):
    """Validates: Requirements 1.1, 1.5."""
    decision = {
        "action": action,
        "conviction_score": 82,
        "opportunity_tier": tier,
        "setup_validation": "Momentum breakout confirmed with volume.",
        "execution_plan": f"Entry: {entry}. Stop-loss: {stop_loss}. Target 1: {take_profit}.",
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }

    payload = build_decision_event(decision)
    assert payload is not None
    assert payload.get("action") == action
    assert payload.get("opportunity_tier") == tier
    assert "execution_levels" in payload
    levels = payload["execution_levels"]
    assert math.isclose(levels["entry"], entry)
    assert math.isclose(levels["stop_loss"], stop_loss)
    assert math.isclose(levels["take_profit"], take_profit)
