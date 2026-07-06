"""Property-based test for the absent-regime defensibility entry (graph.py, task 8.3).

Feature: regime-detection-gate

This module implements design **Property 19: Absent regime is recorded as
unavailable**:

    When no usable ``get_market_regime`` result is present in message history
    (none present at all, or only error results ``{"error": ...}`` and/or
    Unavailable_Markers ``{"unavailable": true}``), the defensibility regime
    entry is recorded as unavailable with NO fabricated Trend_State,
    Volatility_State, Favorability, or Regime_Measures — and never raises.

Validates: Requirements 7.3.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` — the
    top-level record builder, whose ``record["regime"]`` entry is asserted here.
  - ``_regime_entry(results)`` / ``_latest_tool_results(messages)`` — the
    underlying helpers, asserted directly as a second, lower-level check.

``_latest_tool_results`` SKIPS error results (those carrying an ``error``
marker), so an error-only history yields no ``get_market_regime`` entry at all;
an Unavailable_Marker is an honest non-fatal result that passes through and is
recognised by ``_regime_entry`` via its ``unavailable: true`` flag. In every one
of these cases the regime entry must be ``{"available": False, "reason": ...}``
with the categorical states and measures ABSENT.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads. Regime tool results
are serialized both as JSON (``{"...": ...}``) and as Python dict-repr
(``{'...': ...}``) strings, since both quoting styles flow through the stack.

The sys.path / import pattern mirrors ``tests/test_defensibility_record.py``:
the service directory (one level up) is prepended to ``sys.path`` so ``graph``
is importable when pytest is run from anywhere.
"""

import json
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import build_defensibility_record, _latest_tool_results, _regime_entry  # noqa: E402

REGIME_TOOL = "get_market_regime"

# Categorical states / measures that MUST be absent from an unavailable entry.
_FORBIDDEN_KEYS = ("trend_state", "volatility_state", "favorability", "measures")


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a LangChain ToolMessage. ``_is_tool_message`` matches type 'tool'."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _serialize(payload, style):
    """Serialize a result dict as a JSON string or a Python dict-repr string."""
    if style == "json":
        return json.dumps(payload)
    return repr(payload)  # Python dict-repr: single quotes, True/None tokens


# ── Strategies ───────────────────────────────────────────────────────────────
# Symbol restricted to tokens that can never contain the "error"/"unavailable"
# substrings, so the classification of each regime result is decided purely by
# its structure (the explicit error/unavailable key), not incidental free text.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_style = st.sampled_from(["json", "repr"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# Reason strings deliberately free of the substring "error" so an Unavailable_
# Marker is never misclassified as an error result.
_unavailable_reason = st.sampled_from([
    "insufficient data: 18 valid candles received, 50 required",
    "retrieval timeout",
    "no measure could be computed",
    "insufficient data: 0 valid candles received, 50 required",
])
_error_text = st.sampled_from([
    "Failed to retrieve candles from Rust server: timeout",
    "connection refused",
    "contract_violation",
    "no data",
])


@st.composite
def _error_regime_msg(draw):
    """A get_market_regime error result message (carries an ``error`` key)."""
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "error": draw(_error_text),
    }
    return StubToolMessage(_serialize(payload, draw(_style)), REGIME_TOOL)


@st.composite
def _unavailable_regime_msg(draw):
    """A get_market_regime Unavailable_Marker result message."""
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "unavailable": True,
        "reason": draw(_unavailable_reason),
    }
    return StubToolMessage(_serialize(payload, draw(_style)), REGIME_TOOL)


@st.composite
def _noise_msg(draw):
    """A non-regime tool result message (never a get_market_regime label)."""
    name = draw(st.sampled_from(["get_multi_tf_trend", "get_consensus_report", "get_support_resistance"]))
    if name == "get_multi_tf_trend":
        payload = {"symbol": draw(_symbol), "trend_1h": "Bullish", "trend_4h": "Bullish", "trend_1d": "Neutral"}
    elif name == "get_consensus_report":
        payload = {"symbol": draw(_symbol), "current_price": 2450.5, "rsi_14": 38.2, "atr_14": 18.0}
    else:
        payload = {"pivot": 2445.0, "s1": 2440.0, "r1": 2470.0}
    return StubToolMessage(_serialize(payload, draw(_style)), name)


def _assert_unavailable(entry):
    """The regime entry must be unavailable with NO fabricated states/measures."""
    assert isinstance(entry, dict)
    # Recorded as unavailable (available is exactly False, not truthy/missing).
    assert entry.get("available") is False
    # An honest reason is carried.
    assert isinstance(entry.get("reason"), str) and entry["reason"]
    # NONE of the categorical states or measures may be fabricated.
    for key in _FORBIDDEN_KEYS:
        assert key not in entry, f"unavailable regime entry must not contain {key!r}"
    # And certainly no opposition statement (that only attaches to a usable label).
    assert "trade_opposes_regime" not in entry


# ─────────────────────────────────────────────────────────────────────────────
# Property 19: Absent regime is recorded as unavailable
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 19
@settings(max_examples=200, deadline=None)
@given(
    # 0+ regime results, each an error OR an Unavailable_Marker (never a usable
    # label). An empty list models the "no get_market_regime result at all" case.
    regime_msgs=st.lists(
        st.one_of(_error_regime_msg(), _unavailable_regime_msg()),
        min_size=0,
        max_size=4,
    ),
    noise=st.lists(_noise_msg(), min_size=0, max_size=3),
    noise_first=st.booleans(),
    action=_action,
)
def test_property_19_absent_regime_recorded_unavailable(regime_msgs, noise, noise_first, action):
    """Validates: Requirements 7.3

    For any message history containing NO usable get_market_regime label (none
    present, or only error / Unavailable_Marker results), the defensibility
    regime entry is recorded as unavailable with no fabricated trend/volatility/
    favorability/measures, and the build never raises.
    """
    messages = (noise + regime_msgs) if noise_first else (regime_msgs + noise)

    decision = {
        "action": action,
        "conviction_score": 60,
        "setup_validation": "Setup reviewed.",
        "execution_plan": f"{action} at market",
    }

    # ── Top-level record: never raises, regime entry recorded as unavailable ──
    record = build_defensibility_record(messages, decision, mode="FIND")
    assert "regime" in record
    _assert_unavailable(record["regime"])

    # The summary surfaces the regime as unavailable (no fabricated favorability).
    assert "Regime: unavailable" in record["summary"]

    # ── Lower-level helpers: same outcome via the documented call path ────────
    results = _latest_tool_results(messages)
    # Error results are skipped entirely; only an Unavailable_Marker (if any)
    # may surface here — never a usable label.
    regime_result = results.get(REGIME_TOOL)
    assert regime_result is None or regime_result.get("unavailable") is True

    entry = _regime_entry(results)
    _assert_unavailable(entry)


# Feature: regime-detection-gate, Property 19
def test_property_19_no_regime_message_at_all_is_unavailable():
    """A history with no get_market_regime message of any kind records the regime
    entry as unavailable — an explicit example of the empty-regime case (R7.3)."""
    messages = [
        StubToolMessage(json.dumps({"symbol": "RELIANCE", "trend_1d": "Bullish"}), "get_multi_tf_trend"),
        StubToolMessage(json.dumps({"symbol": "RELIANCE", "atr_14": 18.0}), "get_consensus_report"),
    ]
    record = build_defensibility_record(messages, {"action": "BUY", "execution_plan": "BUY at market"}, mode="FIND")
    _assert_unavailable(record["regime"])
    _assert_unavailable(_regime_entry(_latest_tool_results(messages)))
