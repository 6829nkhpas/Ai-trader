"""Tests for the trade defensibility record (graph.py, task 13.2).

Feature: deep-quant-analysis-hardening

These tests exercise the defensibility-record correctness properties built by
``build_defensibility_record(messages, decision, mode, manual_trade)`` in
``graph.py`` (and its helpers). They cover:

  - Property 25 (task 13.4) — committed trades carry a complete defensibility
    record (R7.1, R7.2)
  - Property 26 (task 13.5) — high-confidence patterns are named in the thesis
    (R7.3, R11.3)
  - Property 42 (task 13.6) — a projection conflicting with bias is stated
    (R12.3)
  - Property 45 (task 13.7) — a trade opposing the 1D trend states the macro
    conflict (R13.3)
  - Property 27 (task 13.8) — VERIFY mode reports an outcome for every validator
    check (R7.4)
  - task 13.9 — decision provenance: a fixed scenario's record cites only values
    present in the tool results (R5.4)

The real LLM / tool server are never invoked. Lightweight stub ToolMessage-like
objects (``.name``, ``.content``, ``.type == "tool"``) carry realistic JSON tool
results so ``build_defensibility_record`` reads them exactly as it would the live
message history.
"""

import json
import math
import os
import sys

from hypothesis import given, settings, strategies as st

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from graph import build_defensibility_record  # noqa: E402


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a LangChain ToolMessage tool result.

    ``build_defensibility_record`` only reads ``.type`` (must be ``"tool"``),
    ``.name`` (the originating Analysis_Tool), and ``.content`` (the serialized
    tool result). Content is supplied as a realistic JSON string.
    """

    def __init__(self, name, payload):
        self.name = name
        self.content = payload if isinstance(payload, str) else json.dumps(payload)
        self.type = "tool"


def tool_msg(name, payload):
    return StubToolMessage(name, payload)


# ── Shared building blocks ───────────────────────────────────────────────────
def consensus_payload(atr=None, price=2450.5):
    body = {"symbol": "RELIANCE", "current_price": price, "rsi_14": 38.2}
    if atr is not None:
        body["atr_14"] = atr
    return body


def multi_tf_payload(bias_1d="Neutral", bias_1h="Bullish", bias_4h="Bullish"):
    return {
        "symbol": "RELIANCE",
        "trend_1h": bias_1h,
        "trend_4h": bias_4h,
        "trend_1d": bias_1d,
    }


def sr_payload():
    return {
        "pivot": 2445.0,
        "s1": 2440.0,
        "s2": 2418.0,
        "s3": 2400.0,
        "r1": 2470.0,
        "r2": 2492.0,
        "r3": 2510.0,
    }


def chart_patterns_payload(patterns, timeframe="15m"):
    return {"symbol": "RELIANCE", "timeframe": timeframe, "patterns": patterns}


def plan_text(action, entry, stop_loss, take_profit):
    """Format an execution plan the way declare_trade prose carries levels."""
    return f"{action} entry {entry}, SL {stop_loss}, TP {take_profit}"


# ── Property 25 (task 13.4) ──────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 25: Committed trades carry a
# complete defensibility record
@st.composite
def committed_trade(draw):
    action = draw(st.sampled_from(["BUY", "SELL"]))
    # entry is kept comfortably above the max offsets so every derived level
    # (BUY stop_loss = entry - risk, SELL take_profit = entry - reward) stays
    # strictly positive and parses cleanly out of the plan prose.
    entry = draw(st.integers(min_value=2000, max_value=9000))
    risk = draw(st.integers(min_value=1, max_value=150))
    reward = draw(st.integers(min_value=1, max_value=400))
    atr = draw(st.floats(min_value=0.5, max_value=60.0, allow_nan=False, allow_infinity=False))
    if action == "BUY":
        stop_loss = entry - risk
        take_profit = entry + reward
    else:
        stop_loss = entry + risk
        take_profit = entry - reward
    return action, entry, stop_loss, take_profit, risk, reward, atr


@settings(max_examples=100, deadline=None)
@given(trade=committed_trade())
def test_property_25_complete_defensibility_record(trade):
    """Feature: deep-quant-analysis-hardening, Property 25: Committed trades
    carry a complete defensibility record — the record includes multi_tf_bias,
    support_resistance, volatility_basis, and risk_reward (when levels present).

    Validates: Requirements 7.1, 7.2
    """
    action, entry, stop_loss, take_profit, risk, reward, atr = trade
    multi_tf = multi_tf_payload()
    sr = sr_payload()
    messages = [
        tool_msg("get_multi_tf_trend", multi_tf),
        tool_msg("get_consensus_report", consensus_payload(atr=atr)),
        tool_msg("get_support_resistance", sr),
    ]
    decision = {
        "action": action,
        "conviction_score": 70,
        "setup_validation": "Confluence across timeframes supports the setup.",
        "execution_plan": plan_text(action, entry, stop_loss, take_profit),
    }

    record = build_defensibility_record(messages, decision, mode="FIND")

    # Multi-timeframe trend bias is recorded (R7.1).
    assert record["multi_tf_bias"] == multi_tf
    # Key support/resistance levels used are recorded (R7.1).
    assert record["support_resistance"] == sr
    # Volatility basis for the stop is recorded and cites ATR (R7.1).
    assert isinstance(record["volatility_basis"], str) and record["volatility_basis"]
    assert "ATR" in record["volatility_basis"]
    # The Risk_Reward_Ratio is recorded when levels are present (R7.2).
    expected_rr = round(reward / risk, 4)
    assert record["risk_reward"] == expected_rr
    # The execution levels were recovered from the plan.
    assert record["levels"] == {
        "entry": float(entry),
        "stop_loss": float(stop_loss),
        "take_profit": float(take_profit),
    }


# ── Property 26 (task 13.5) ──────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 26: High-confidence patterns
# are named in the thesis
_PATTERN_POOL = [
    "InverseHeadShoulders",
    "DoubleBottom",
    "CupHandle",
    "AscendingTriangle",
    "BullFlag",
    "RisingWedge",
    "Rectangle",
    "SymmetricalTriangle",
    "DescendingTriangle",
    "TripleTop",
    "FallingWedge",
    "BearPennant",
]


@st.composite
def patterns_set(draw):
    names = draw(
        st.lists(
            st.sampled_from(_PATTERN_POOL),
            min_size=1,
            max_size=len(_PATTERN_POOL),
            unique=True,
        )
    )
    patterns = []
    for nm in names:
        conf = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
        patterns.append(
            {
                "pattern_type": nm,
                "sentiment": draw(st.sampled_from(["Bullish", "Bearish", "Neutral"])),
                "confidence": conf,
                "description": "structural pattern",
            }
        )
    return patterns


@settings(max_examples=100, deadline=None)
@given(patterns=patterns_set())
def test_property_26_high_confidence_patterns_named(patterns):
    """Feature: deep-quant-analysis-hardening, Property 26: High-confidence
    patterns are named in the thesis — every chart pattern with confidence > 0.6
    appears in record["patterns"]/summary; patterns <= 0.6 do not.

    Validates: Requirements 7.3, 11.3
    """
    messages = [tool_msg("get_chart_patterns", chart_patterns_payload(patterns))]
    decision = {
        "action": "BUY",
        "conviction_score": 65,
        "setup_validation": "Pattern confluence considered.",
        "execution_plan": "BUY on confirmation",
    }

    record = build_defensibility_record(messages, decision, mode="FIND")
    named_types = {p["pattern_type"] for p in record["patterns"]}
    summary = record["summary"]

    for p in patterns:
        name = p["pattern_type"]
        if p["confidence"] > 0.6:
            assert name in named_types, f"{name} (conf {p['confidence']}) should be named"
            assert name in summary
        else:
            assert name not in named_types, f"{name} (conf {p['confidence']}) must not be named"
            assert name not in summary


# ── Property 42 (task 13.6) ──────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 42: A projection conflicting
# with bias is stated
@settings(max_examples=100, deadline=None)
@given(
    action=st.sampled_from(["BUY", "SELL"]),
    conflicting=st.booleans(),
    proj_value=st.floats(min_value=1.0, max_value=10000.0, allow_nan=False, allow_infinity=False),
)
def test_property_42_predictive_conflict_stated(action, conflicting, proj_value):
    """Feature: deep-quant-analysis-hardening, Property 42: A projection
    conflicting with bias is stated — when the prediction direction opposes the
    trade bias, the predictive_conflict statement says CONFLICT.

    Validates: Requirements 12.3
    """
    agent_dir = "Up" if action == "BUY" else "Down"
    opposite = "Down" if agent_dir == "Up" else "Up"
    proj_dir = opposite if conflicting else agent_dir

    messages = [
        tool_msg("get_consensus_report", consensus_payload(atr=18.0)),
        tool_msg(
            "get_prediction",
            {"projected_direction": proj_dir, "projected_value": proj_value, "confidence": 0.7},
        ),
    ]
    decision = {
        "action": action,
        "conviction_score": 72,
        "setup_validation": "Directional bias established.",
        "execution_plan": f"{action} at market",
    }

    record = build_defensibility_record(messages, decision, mode="FIND")
    statement = record["predictive_conflict"]

    if conflicting:
        # The opposing projection must be flagged with an explicit CONFLICT marker.
        assert "CONFLICT" in statement
    else:
        # An aligned projection must not raise an uppercase CONFLICT marker.
        assert "CONFLICT" not in statement


# ── Property 45 (task 13.7) ──────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 45: A trade opposing the 1D
# trend states the macro conflict
@settings(max_examples=100, deadline=None)
@given(
    action=st.sampled_from(["BUY", "SELL"]),
    opposing=st.booleans(),
)
def test_property_45_macro_trend_conflict_stated(action, opposing):
    """Feature: deep-quant-analysis-hardening, Property 45: A trade opposing the
    1D trend states the macro conflict — when action opposes the 1D bias,
    macro_trend_conflict states the conflict.

    Validates: Requirements 13.3
    """
    if action == "BUY":
        bias_1d = "Bearish" if opposing else "Bullish"
    else:
        bias_1d = "Bullish" if opposing else "Bearish"

    messages = [
        tool_msg("get_multi_tf_trend", multi_tf_payload(bias_1d=bias_1d)),
        tool_msg("get_consensus_report", consensus_payload(atr=18.0)),
    ]
    decision = {
        "action": action,
        "conviction_score": 68,
        "setup_validation": "Macro alignment reviewed.",
        "execution_plan": f"{action} at market",
    }

    record = build_defensibility_record(messages, decision, mode="FIND")
    statement = record["macro_trend_conflict"]

    if opposing:
        assert "MACRO CONFLICT" in statement
    else:
        assert "MACRO CONFLICT" not in statement


# ── Property 27 (task 13.8) ──────────────────────────────────────────────────
# Feature: deep-quant-analysis-hardening, Property 27: VERIFY mode reports an
# outcome for every validator check
_REQUIRED_VERIFY_CHECKS = {
    "execution-levels-present",
    "direction-consistency",
    "stop-distance-vs-atr",
    "risk-reward",
}


@settings(max_examples=100, deadline=None)
@given(
    side=st.sampled_from(["BUY", "SELL"]),
    entry=st.floats(min_value=100.0, max_value=9000.0, allow_nan=False, allow_infinity=False),
    sl_off=st.floats(min_value=1.0, max_value=300.0, allow_nan=False, allow_infinity=False),
    tp_off=st.floats(min_value=1.0, max_value=600.0, allow_nan=False, allow_infinity=False),
    with_atr=st.booleans(),
)
def test_property_27_verify_mode_reports_every_check(side, entry, sl_off, tp_off, with_atr):
    """Feature: deep-quant-analysis-hardening, Property 27: VERIFY mode reports
    an outcome for every validator check — in VERIFY mode, validator_checks
    reports an outcome for each of the checks (execution-levels-present,
    direction-consistency, stop-distance-vs-atr, risk-reward).

    Validates: Requirements 7.4
    """
    if side == "BUY":
        stop_loss = entry - sl_off
        take_profit = entry + tp_off
    else:
        stop_loss = entry + sl_off
        take_profit = entry - tp_off

    manual_trade = {
        "side": side,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }
    messages = []
    if with_atr:
        messages.append(tool_msg("get_consensus_report", consensus_payload(atr=20.0)))

    record = build_defensibility_record(messages, decision={}, mode="VERIFY", manual_trade=manual_trade)

    assert "validator_checks" in record
    checks = record["validator_checks"]
    reported = {c["check"] for c in checks}
    # Every one of the four checks is reported.
    assert _REQUIRED_VERIFY_CHECKS.issubset(reported)
    # Each reported check carries an explicit outcome.
    for c in checks:
        assert "outcome" in c and isinstance(c["outcome"], str) and c["outcome"]


# ── task 13.9 — decision provenance unit test ────────────────────────────────
# Feature: deep-quant-analysis-hardening, decision provenance (R5.4)
def test_decision_provenance_cites_only_tool_values():
    """A fixed scenario's defensibility record cites only values present in the
    tool results — no fabricated numbers or directions.

    Validates: Requirements 5.4
    """
    multi_tf = multi_tf_payload(bias_1d="Neutral")
    consensus = consensus_payload(atr=18.5, price=2450.5)
    sr = sr_payload()
    pattern = {
        "pattern_type": "InverseHeadShoulders",
        "sentiment": "Bullish",
        "confidence": 0.71,
        "description": "Inverse H&S confirms reversal",
    }
    news = {"symbol": "RELIANCE", "headlines": ["positive update"], "sentiment_summary": "Bullish"}

    messages = [
        tool_msg("get_multi_tf_trend", multi_tf),
        tool_msg("get_consensus_report", consensus),
        tool_msg("get_support_resistance", sr),
        tool_msg("get_chart_patterns", chart_patterns_payload([pattern])),
        tool_msg("get_news_context", news),
        # Note: NO get_prediction result is supplied on purpose.
    ]
    decision = {
        "action": "BUY",
        "conviction_score": 74,
        "setup_validation": "Entry at S1 with stop below S2; inverse H&S confirms.",
        "execution_plan": "BUY entry 2440, SL 2418, TP 2492",
    }

    record = build_defensibility_record(messages, decision, mode="FIND")

    # Volatility basis cites the ATR that was actually returned (18.5) — nothing
    # invented.
    assert record["atr_14"] == 18.5

    # Levels are exactly those parsed from the execution plan.
    assert record["levels"] == {"entry": 2440.0, "stop_loss": 2418.0, "take_profit": 2492.0}

    # Risk_Reward is derived purely from the cited levels.
    expected_rr = round(abs(2492.0 - 2440.0) / abs(2440.0 - 2418.0), 4)
    assert record["risk_reward"] == expected_rr

    # Multi-TF bias and S/R are the exact tool payloads — not re-derived numbers.
    assert record["multi_tf_bias"] == multi_tf
    assert record["trend_1d"] == "Neutral"
    assert record["support_resistance"] == sr

    # News sentiment is the classification the service returned.
    assert record["news_sentiment"] == "Bullish"

    # The high-confidence pattern is cited with its real confidence value.
    assert any(
        p["pattern_type"] == "InverseHeadShoulders" and p["confidence"] == 0.71
        for p in record["patterns"]
    )

    # No predictive projection was supplied, so the record must NOT fabricate a
    # direction — it states the projection is unavailable.
    assert "unavailable" in record["predictive_conflict"].lower()
    assert "CONFLICT" not in record["predictive_conflict"]

    # The volatility basis numbers trace back to the cited ATR (18.5 and 1.5x it).
    assert "18.5" in record["volatility_basis"]
    assert f"{1.5 * 18.5:.4f}" in record["volatility_basis"]
