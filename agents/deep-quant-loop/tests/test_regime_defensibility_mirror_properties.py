"""Property-based test for the defensibility regime entry (graph.py, task 8.2).

Feature: regime-detection-gate

This module implements design **Property 18: The defensibility regime entry
mirrors the tool result without fabrication**:

    For any most-recent ``get_market_regime`` Regime_Label present in message
    history, the regime entry built by ``build_defensibility_record`` (via
    ``_regime_entry`` over ``_latest_tool_results``) copies ``trend_state``,
    ``volatility_state``, ``favorability``, and the named Regime_Measures
    VERBATIM from that result — it never infers, defaults, or substitutes a
    value not present in the tool output.

Validates: Requirements 7.1, 7.2.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` —
    assembles the record whose ``"regime"`` key holds the regime entry.
  - ``_regime_entry(results)`` — the pure read of the latest regime result.
  - ``_latest_tool_results(messages)`` — picks the most-recent non-error result
    per tool name (later results win), so the MOST RECENT regime label is the
    one mirrored.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads. Regime tool results
are serialized both as JSON (``{"...": ...}``) and as Python dict-repr
(``{'...': ...}``) strings, since both quoting styles flow through the stack.

The sys.path / import pattern mirrors ``tests/test_regime_market_data_gate_properties.py``:
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
from graph import (  # noqa: E402
    build_defensibility_record,
    _regime_entry,
    _latest_tool_results,
    _REGIME_MEASURE_FIELDS,
)

REGIME_TOOL = "get_market_regime"


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a tool result. ``_is_tool_message`` matches ``type == 'tool'``."""

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
# Symbol/timeframe restricted to tokens that can never contain the "error" or
# "unavailable" substrings, so the result is classified purely by its structure.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_trend_state = st.sampled_from(["trending", "ranging", "transitional"])
_volatility_state = st.sampled_from(["low", "normal", "high"])
_favorability = st.sampled_from(["favorable", "unfavorable", "neutral"])
_serialization_style = st.sampled_from(["json", "repr"])

# A measure value is a finite number or null (None), per the regime contract.
_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _regime_label(draw):
    """Draw a conforming Regime_Label (the structured fields the entry mirrors)."""
    return {
        "trend_state": draw(_trend_state),
        "volatility_state": draw(_volatility_state),
        "favorability": draw(_favorability),
        "measures": {
            "directional_strength": draw(_measure_value),
            "choppiness": draw(_measure_value),
            "efficiency_ratio": draw(_measure_value),
            "atr_percentile": draw(_measure_value),
            "bb_width": draw(_measure_value),
        },
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "candles_used": draw(st.integers(min_value=1, max_value=500)),
    }


def _assert_mirrors(entry, label):
    """The regime entry mirrors the source label verbatim, with no fabrication."""
    assert entry.get("available") is True
    # Categorical states copied verbatim (R7.1, R7.2).
    assert entry["trend_state"] == label["trend_state"]
    assert entry["volatility_state"] == label["volatility_state"]
    assert entry["favorability"] == label["favorability"]
    # Each named Regime_Measure copied verbatim — present, no inference (R7.1, R7.2).
    measures = entry["measures"]
    assert set(measures.keys()) == set(_REGIME_MEASURE_FIELDS)
    for field in _REGIME_MEASURE_FIELDS:
        assert measures[field] == label["measures"][field]


# ─────────────────────────────────────────────────────────────────────────────
# Property 18: the defensibility regime entry mirrors the tool result
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 18
@settings(max_examples=200, deadline=None)
@given(
    target=_regime_label(),
    earlier=st.lists(_regime_label(), min_size=0, max_size=3),
    style=_serialization_style,
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
)
def test_property_18_defensibility_regime_entry_mirrors_tool_result(
    target, earlier, style, action
):
    """Validates: Requirements 7.1, 7.2

    For any most-recent ``get_market_regime`` Regime_Label in message history,
    the regime entry in the defensibility record copies trend_state /
    volatility_state / favorability and the named measures verbatim (no
    inference, no substitution). Earlier regime labels are present first, so the
    test also confirms the MOST RECENT label is the one mirrored (R7.1).
    """
    # Earlier (stale) regime labels first, then the target as the LATEST one.
    messages = [
        StubToolMessage(content=_serialize(lbl, style), name=REGIME_TOOL)
        for lbl in earlier
    ]
    messages.append(StubToolMessage(content=_serialize(target, style), name=REGIME_TOOL))

    decision = {"action": action, "source": "declare_trade"}

    # ── Via the full record builder: record["regime"] mirrors the target ─────
    record = build_defensibility_record(messages, decision, mode="FIND")
    _assert_mirrors(record["regime"], target)

    # The mirror carries the LATEST label's context verbatim (most-recent wins).
    assert record["regime"].get("symbol") == target["symbol"]
    assert record["regime"].get("timeframe") == target["timeframe"]
    assert record["regime"].get("candles_used") == target["candles_used"]

    # ── Via _regime_entry over _latest_tool_results directly ─────────────────
    entry = _regime_entry(_latest_tool_results(messages))
    _assert_mirrors(entry, target)
