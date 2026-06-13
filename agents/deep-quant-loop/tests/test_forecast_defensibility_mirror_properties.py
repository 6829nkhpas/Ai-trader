"""Property-based test for the defensibility forecast entry (graph.py, task 9.2).

Feature: volatility-aware-forecaster

This module implements design **Property 23: The defensibility forecast entry
mirrors the tool result without fabrication**:

    For any most-recent ``get_forecast`` Forecast_Label present in message
    history, the forecast entry built by ``build_defensibility_record`` (via
    ``_forecast_entry`` over ``_latest_tool_results``) copies
    ``projected_direction``, ``up_probability``, ``expected_move_atr``,
    ``forecast_confidence``, ``forecast_alignment``, and the named
    Forecast_Measures (drift / volatility / standardized_drift / atr) VERBATIM
    from that result — it never infers, defaults, or substitutes a value not
    present in the tool output (including a null ``expected_move_atr``).

Validates: Requirements 9.1, 9.2.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` —
    assembles the record whose ``"forecast"`` key holds the entry.
  - ``_forecast_entry(results)`` — the pure read of the latest result.
  - ``_latest_tool_results(messages)`` — picks the most-recent non-error result
    per tool name (later results win), so the MOST RECENT label is mirrored.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads. Tool results are
serialized both as JSON (``{"...": ...}``) and as Python dict-repr
(``{'...': ...}``) strings, since both quoting styles flow through the stack.

The sys.path / import pattern mirrors
``tests/test_rs_defensibility_mirror_properties.py``: the service directory
(one level up) is prepended to ``sys.path`` so ``graph`` is importable when
pytest is run from anywhere.
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
    _forecast_entry,
    _latest_tool_results,
    _FORECAST_MEASURE_FIELDS,
)

FORECAST_TOOL = "get_forecast"


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
_projected_direction = st.sampled_from(["up", "down", "flat"])
_forecast_alignment = st.sampled_from(["aligned", "misaligned", "neutral"])
_serialization_style = st.sampled_from(["json", "repr"])

# A measure value is a finite number or null (None), per the forecast contract.
_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)
# A probability/confidence is a finite number in [0.0, 1.0].
_unit_value = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
# expected_move_atr is a finite signed number OR null.
_expected_move_atr = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _forecast_label(draw):
    """Draw a conforming Forecast_Label (the fields the entry mirrors)."""
    return {
        "projected_direction": draw(_projected_direction),
        "up_probability": draw(_unit_value),
        "expected_move_atr": draw(_expected_move_atr),
        "forecast_confidence": draw(_unit_value),
        "forecast_alignment": draw(_forecast_alignment),
        "measures": {
            "drift": draw(_measure_value),
            "volatility": draw(st.one_of(st.none(), st.floats(
                min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))),
            "standardized_drift": draw(_measure_value),
            "atr": draw(st.one_of(st.none(), st.floats(
                min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))),
        },
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "candles_used": draw(st.integers(min_value=1, max_value=500)),
    }


def _assert_mirrors(entry, label):
    """The forecast entry mirrors the source label verbatim, with no fabrication."""
    assert entry.get("available") is True
    # Categorical / scalar fields copied verbatim (R9.1, R9.2).
    assert entry["projected_direction"] == label["projected_direction"]
    assert entry["up_probability"] == label["up_probability"]
    # expected_move_atr copied verbatim, INCLUDING a null value (no substitution).
    assert entry["expected_move_atr"] == label["expected_move_atr"]
    assert entry["forecast_confidence"] == label["forecast_confidence"]
    assert entry["forecast_alignment"] == label["forecast_alignment"]
    # Each named Forecast_Measure copied verbatim — present, no inference.
    measures = entry["measures"]
    assert set(measures.keys()) == set(_FORECAST_MEASURE_FIELDS)
    for field in _FORECAST_MEASURE_FIELDS:
        assert measures[field] == label["measures"][field]


# ─────────────────────────────────────────────────────────────────────────────
# Property 23: the defensibility forecast entry mirrors the tool result
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 23: The defensibility forecast entry mirrors the tool result without fabrication
@settings(max_examples=100, deadline=None)
@given(
    target=_forecast_label(),
    earlier=st.lists(_forecast_label(), min_size=0, max_size=3),
    style=_serialization_style,
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
)
def test_property_23_defensibility_forecast_entry_mirrors_tool_result(
    target, earlier, style, action
):
    """Validates: Requirements 9.1, 9.2

    For any most-recent ``get_forecast`` Forecast_Label in message history, the
    forecast entry in the defensibility record copies projected_direction /
    up_probability / expected_move_atr / forecast_confidence /
    forecast_alignment and the named measures verbatim (no inference, no
    substitution, including a null expected_move_atr). Earlier labels are
    present first, so the test also confirms the MOST RECENT label is the one
    mirrored (R9.1).
    """
    # Earlier (stale) forecast labels first, then the target as the LATEST one.
    messages = [
        StubToolMessage(content=_serialize(lbl, style), name=FORECAST_TOOL)
        for lbl in earlier
    ]
    messages.append(StubToolMessage(content=_serialize(target, style), name=FORECAST_TOOL))

    decision = {"action": action, "source": "declare_trade"}

    # ── Via the full record builder: record["forecast"] mirrors target ───────
    record = build_defensibility_record(messages, decision, mode="FIND")
    _assert_mirrors(record["forecast"], target)

    # The mirror carries the LATEST label's context verbatim (most-recent wins).
    assert record["forecast"].get("symbol") == target["symbol"]
    assert record["forecast"].get("timeframe") == target["timeframe"]
    assert record["forecast"].get("candles_used") == target["candles_used"]

    # ── Via _forecast_entry over _latest_tool_results directly ───────────────
    entry = _forecast_entry(_latest_tool_results(messages))
    _assert_mirrors(entry, target)
