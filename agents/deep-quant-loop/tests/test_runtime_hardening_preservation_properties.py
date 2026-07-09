"""Cross-layer preservation property test — non-triggering (¬C) inputs unchanged.

Feature: deep-quant-runtime-hardening (bugfix)

Property 17 (Preservation), Python pure cores on the ¬C set:

    For inputs that trigger NONE of the six bug conditions — a normal directional
    BUY/SELL run with sufficient data, a reachable/configured sentiment
    classifier, resolved core acquisition, and the heartbeat at its intended
    cadence — the affected components behave EXACTLY as they did before the
    fixes. The six fixes only alter behaviour on the defect-triggering set (C);
    the ¬C set is invariant.

    This module asserts the ¬C guarantee on three deterministic pure cores that
    the hardening work touched-around-but-must-not-change:

      * ``graph._decision_from_declare`` — ``declare_trade`` remains the single
        authoritative completion signal, and the committed decision carries the
        declared arguments VERBATIM (no fabrication of a decision). (R7.1, R7.6)

      * ``graph._verify_mode_validator_checks`` — the Trade_Validator hard rules
        are unchanged for every BUY/SELL: stop-loss distance ≥ 1.5× ATR and the
        minimum risk/reward (≥ 2.0), plus direction consistency, are enforced
        exactly as the arithmetic rule dictates. (R7.2)

      * ``tools._has_honest_marker`` — the filter-not-generator doctrine and the
        graceful-degradation Unavailable_Marker conventions are preserved: an
        honest error/unavailable/sentiment-unavailable/status marker is still
        recognised as a non-fatal pass-through, and a clean directional result
        is never mistaken for a marker. (R7.3)

    Validates: Requirements 7.1, 7.2, 7.3, 7.6, 7.8.

    Requirement 7.5 (Glass_Box_Stream / defensibility record / journal /
    telemetry unchanged) is a whole-run behaviour asserted by the full-suite
    checkpoint (task 20), not a single pure core, and the Rust candle
    merge/dedup/slice preservation (Property 4) is covered by task 9.3 — neither
    is duplicated here.

This module reuses the sibling tests' ``sys.path`` setup (``tests/`` sits
directly under the service dir) so ``import graph`` / ``import tools`` resolve
exactly as every sibling test module expects; it performs no network I/O.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import graph`` / ``import tools`` resolve as every sibling test expects.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import _decision_from_declare, _verify_mode_validator_checks  # noqa: E402
from tools import _has_honest_marker  # noqa: E402


# ── Generators ────────────────────────────────────────────────────────────────

# A finite, validated-trade-shaped price.
_finite_price = st.floats(
    min_value=0.01, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
)

# A conviction score threaded verbatim (never defaulted on the Python side).
_conviction = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=100),
    st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
)

# Free-form prose fields carried through the decision verbatim.
_prose = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)), min_size=0, max_size=40
)

# Names of non-completion tool calls that must NEVER produce a committed decision.
_NON_DECLARE_TOOLS = [
    "get_market_regime",
    "get_consensus_report",
    "get_relative_strength",
    "get_session_context",
    "get_order_flow",
    "get_support_resistance",
    "watch_price_condition",
    "get_news_context",
]

_non_declare_call = st.builds(
    lambda name, args: {"name": name, "args": args},
    st.sampled_from(_NON_DECLARE_TOOLS),
    st.dictionaries(st.text(min_size=1, max_size=8), st.integers(), max_size=3),
)


def _directional_declare_args(draw):
    """A ¬C directional declare_trade args dict with finite, ordered levels."""
    action = draw(st.sampled_from(["BUY", "SELL"]))
    entry = draw(_finite_price)
    stop_loss = draw(_finite_price)
    take_profit = draw(_finite_price)
    return {
        "action": action,
        "conviction_score": draw(_conviction),
        "setup_validation": draw(_prose),
        "execution_plan": draw(_prose),
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "atr_14": draw(_finite_price),
    }


# ── Property 17a — declare_trade is the single authoritative completion signal ──

@st.composite
def _call_lists_with_one_declare(draw):
    """A tool-call list that contains exactly one directional declare_trade,
    surrounded by an arbitrary number of non-completion calls."""
    before = draw(st.lists(_non_declare_call, max_size=3))
    after = draw(st.lists(_non_declare_call, max_size=3))
    declare = {"name": "declare_trade", "args": _directional_declare_args(draw)}
    return before + [declare] + after, declare["args"]


@given(_call_lists_with_one_declare())
@settings(max_examples=200, deadline=None)
def test_declare_trade_is_single_completion_signal_carried_verbatim(payload):
    """Feature: deep-quant-runtime-hardening, Property 17: declare_trade is the
    single authoritative completion signal and its structured args are carried
    into the committed decision VERBATIM — nothing is fabricated. (R7.1, R7.6)"""
    ok_calls, declare_args = payload

    decision = _decision_from_declare(ok_calls)

    # A decision is committed BECAUSE a declare_trade call is present — it is the
    # single completion signal (R7.1).
    assert decision is not None
    assert decision["source"] == "declare_trade"

    # Every structured field is the declared value VERBATIM — no synthesis,
    # no defaulting, no fabrication (R7.6). ``action`` only defaults to HOLD when
    # the declared action is falsy, which never happens for a directional run.
    assert decision["action"] == declare_args["action"]
    assert decision["conviction_score"] == declare_args["conviction_score"]
    assert decision["setup_validation"] == declare_args["setup_validation"]
    assert decision["execution_plan"] == declare_args["execution_plan"]
    assert decision["entry"] == declare_args["entry"]
    assert decision["stop_loss"] == declare_args["stop_loss"]
    assert decision["take_profit"] == declare_args["take_profit"]
    assert decision["atr_14"] == declare_args["atr_14"]


@given(st.lists(_non_declare_call, max_size=6))
@settings(max_examples=200, deadline=None)
def test_no_declare_trade_yields_no_decision(ok_calls):
    """Feature: deep-quant-runtime-hardening, Property 17: no completion is ever
    manufactured from non-``declare_trade`` calls — declare_trade is the SOLE
    completion signal, so a call list without it commits nothing. (R7.1, R7.6)"""
    assert _decision_from_declare(ok_calls) is None


# ── Property 17b — Trade_Validator hard rules preserved for every BUY/SELL ─────

def _outcome(checks, name):
    for c in checks:
        if c.get("check") == name:
            return c.get("outcome")
    return None


@given(
    action=st.sampled_from(["BUY", "SELL"]),
    entry=_finite_price,
    stop_loss=_finite_price,
    take_profit=_finite_price,
    atr_14=st.floats(min_value=0.001, max_value=100_000.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200, deadline=None)
def test_trade_validator_hard_rules_enforced_exactly(action, entry, stop_loss, take_profit, atr_14):
    """Feature: deep-quant-runtime-hardening, Property 17: the Trade_Validator
    hard rules are unchanged for every directional BUY/SELL — stop distance
    ≥ 1.5× ATR, minimum R:R ≥ 2.0, and direction consistency are reported exactly
    as the arithmetic rule dictates (no rule weakened or strengthened). (R7.2)"""
    levels = {"entry": entry, "stop_loss": stop_loss, "take_profit": take_profit}
    checks = _verify_mode_validator_checks(action, levels, atr_14)

    # Levels are present, so the checks are evaluable.
    assert _outcome(checks, "execution-levels-present") == "pass"

    # Direction consistency: BUY needs sl < entry < tp; SELL needs tp < entry < sl.
    if action == "BUY":
        expected_dir = stop_loss < entry < take_profit
    else:
        expected_dir = take_profit < entry < stop_loss
    assert _outcome(checks, "direction-consistency") == ("pass" if expected_dir else "fail")

    # Stop-distance-vs-ATR: the 1.5× ATR floor is enforced exactly.
    risk = abs(entry - stop_loss)
    expected_stop = risk >= 1.5 * atr_14
    assert _outcome(checks, "stop-distance-vs-atr") == ("pass" if expected_stop else "fail")

    # Risk/reward: the 2.0 minimum is enforced exactly (guarded on non-zero risk).
    if risk > 0:
        expected_rr = (abs(take_profit - entry) / risk) >= 2.0
        assert _outcome(checks, "risk-reward") == ("pass" if expected_rr else "fail")


@st.composite
def _valid_directional_trade(draw):
    """A ¬C directional trade that satisfies EVERY hard rule (the normal run).

    Levels are built with comfortable margins above the 1.5× ATR stop floor and
    the 2.0 R:R minimum so the guarantee is exercised on the safe interior of the
    valid region — not on the exact floating-point boundary (where recomputing
    ``abs(entry - stop)`` / ``abs(target - entry)`` in float64 can land a hair
    below the threshold, which is the rule working correctly, not a regression).
    """
    action = draw(st.sampled_from(["BUY", "SELL"]))
    atr_14 = draw(st.floats(min_value=0.5, max_value=500.0, allow_nan=False, allow_infinity=False))
    entry = draw(st.floats(min_value=1_000.0, max_value=100_000.0, allow_nan=False, allow_infinity=False))
    # Stop distance = 1.5x ATR scaled well above the floor (>= 1.575x ATR).
    stop_margin = draw(st.floats(min_value=1.05, max_value=20.0, allow_nan=False, allow_infinity=False))
    risk = 1.5 * atr_14 * stop_margin
    # Reward = at least 2.0R, scaled well above the minimum (>= 2.1R).
    rr_margin = draw(st.floats(min_value=1.05, max_value=3.0, allow_nan=False, allow_infinity=False))
    reward = 2.0 * risk * rr_margin
    if action == "BUY":
        levels = {"entry": entry, "stop_loss": entry - risk, "take_profit": entry + reward}
    else:
        levels = {"entry": entry, "stop_loss": entry + risk, "take_profit": entry - reward}
    return action, levels, atr_14


@given(_valid_directional_trade())
@settings(max_examples=200, deadline=None)
def test_valid_directional_trade_passes_every_hard_rule(trade):
    """Feature: deep-quant-runtime-hardening, Property 17: a ¬C directional trade
    that respects the rules (correct ordering, stop ≥ 1.5× ATR, R:R ≥ 2.0) passes
    every Trade_Validator hard rule — the safe path is unchanged. (R7.2, R7.8)"""
    action, levels, atr_14 = trade
    checks = _verify_mode_validator_checks(action, levels, atr_14)
    for name in ("execution-levels-present", "direction-consistency",
                 "stop-distance-vs-atr", "risk-reward"):
        assert _outcome(checks, name) == "pass", f"{name} should pass for a valid trade"


# ── Property 17c — Unavailable_Marker / filter-not-generator convention preserved ─

# Payloads that ARE honest graceful-degradation markers (non-fatal pass-through).
_honest_marker_payload = st.one_of(
    st.fixed_dictionaries({"error": st.text(max_size=30)}),
    st.builds(lambda extra: {"unavailable": True, **extra},
              st.dictionaries(st.text(min_size=1, max_size=6), st.integers(), max_size=2)),
    st.builds(lambda s: {"sentiment_summary": s},
              st.sampled_from(["Unavailable", "unavailable", "  UNAVAILABLE  "])),
    st.builds(lambda s: {"status": s},
              st.sampled_from(["unavailable", "Unavailable", "watch_registration_failed"])),
    # get_candles' error path returns a single-element list-error marker.
    st.builds(lambda msg: [{"error": msg}], st.text(max_size=30)),
)


@given(_honest_marker_payload)
@settings(max_examples=200, deadline=None)
def test_honest_markers_are_recognised(payload):
    """Feature: deep-quant-runtime-hardening, Property 17: the graceful-degradation
    Unavailable_Marker conventions are preserved — an honest error / unavailable /
    sentiment-unavailable / status marker (dict or list-error) is still recognised
    as a non-fatal pass-through. (R7.3)"""
    assert _has_honest_marker(payload) is True


# A "clean" directional result carries none of the honest-marker keys.
_clean_result = st.fixed_dictionaries({
    "symbol": st.text(min_size=1, max_size=8),
    "trend_state": st.sampled_from(["uptrend", "downtrend", "ranging"]),
    "close": _finite_price,
    "sentiment": st.sampled_from(["Bullish", "Bearish", "Neutral"]),
    "status": st.sampled_from(["ok", "success", "classified"]),
})


@given(_clean_result)
@settings(max_examples=200, deadline=None)
def test_clean_results_are_not_markers(payload):
    """Feature: deep-quant-runtime-hardening, Property 17: filter-not-generator is
    preserved — a clean, real tool result is NEVER mistaken for an Unavailable_
    Marker, so genuine data still flows to contract validation unchanged. (R7.3)"""
    assert _has_honest_marker(payload) is False
