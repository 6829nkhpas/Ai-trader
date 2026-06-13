"""Property-based test for the defensibility relative-strength entry (graph.py, task 8.2).

Feature: relative-strength-context

This module implements design **Property 20: The defensibility relative-strength
entry mirrors the tool result without fabrication**:

    For any most-recent ``get_relative_strength`` Relative_Strength_Label present
    in message history, the relative-strength entry built by
    ``build_defensibility_record`` (via ``_relative_strength_entry`` over
    ``_latest_tool_results``) copies ``index_direction``,
    ``relative_strength_state``, ``alignment``, the named Relative_Strength_Measures,
    and the ``benchmark`` VERBATIM from that result — it never infers, defaults,
    or substitutes a value not present in the tool output.

Validates: Requirements 8.1, 8.2.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` —
    assembles the record whose ``"relative_strength"`` key holds the entry.
  - ``_relative_strength_entry(results)`` — the pure read of the latest result.
  - ``_latest_tool_results(messages)`` — picks the most-recent non-error result
    per tool name (later results win), so the MOST RECENT label is mirrored.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads. Tool results are
serialized both as JSON (``{"...": ...}``) and as Python dict-repr
(``{'...': ...}``) strings, since both quoting styles flow through the stack.

The sys.path / import pattern mirrors
``tests/test_regime_defensibility_mirror_properties.py``: the service directory
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
    _relative_strength_entry,
    _latest_tool_results,
    _RS_MEASURE_FIELDS,
)

RS_TOOL = "get_relative_strength"


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
# Symbol/benchmark/timeframe restricted to tokens that can never contain the
# "error" or "unavailable" substrings, so the result is classified purely by its
# structure.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_benchmark = st.sampled_from(["NIFTY 50", "BANKNIFTY", "NIFTY IT", "FINNIFTY"])
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_index_direction = st.sampled_from(["up", "down", "flat"])
_relative_strength_state = st.sampled_from(["leader", "inline", "laggard"])
_alignment = st.sampled_from(["aligned", "misaligned", "neutral"])
_serialization_style = st.sampled_from(["json", "repr"])

# A measure value is a finite number or null (None), per the RS contract.
_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _rs_label(draw):
    """Draw a conforming Relative_Strength_Label (the fields the entry mirrors)."""
    return {
        "index_direction": draw(_index_direction),
        "relative_strength_state": draw(_relative_strength_state),
        "alignment": draw(_alignment),
        "measures": {
            "rs_ratio": draw(_measure_value),
            "rs_ratio_slope": draw(_measure_value),
            "relative_return": draw(_measure_value),
            "correlation": draw(st.one_of(st.none(), st.floats(
                min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False))),
            "beta": draw(_measure_value),
        },
        "benchmark": draw(_benchmark),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "aligned_candles": draw(st.integers(min_value=1, max_value=500)),
    }


def _assert_mirrors(entry, label):
    """The RS entry mirrors the source label verbatim, with no fabrication."""
    assert entry.get("available") is True
    # Categorical states copied verbatim (R8.1, R8.2).
    assert entry["index_direction"] == label["index_direction"]
    assert entry["relative_strength_state"] == label["relative_strength_state"]
    assert entry["alignment"] == label["alignment"]
    # Benchmark copied verbatim (R8.1, R8.2).
    assert entry["benchmark"] == label["benchmark"]
    # Each named Relative_Strength_Measure copied verbatim — present, no inference.
    measures = entry["measures"]
    assert set(measures.keys()) == set(_RS_MEASURE_FIELDS)
    for field in _RS_MEASURE_FIELDS:
        assert measures[field] == label["measures"][field]


# ─────────────────────────────────────────────────────────────────────────────
# Property 20: the defensibility relative-strength entry mirrors the tool result
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 20: The defensibility relative-strength entry mirrors the tool result without fabrication
@settings(max_examples=100, deadline=None)
@given(
    target=_rs_label(),
    earlier=st.lists(_rs_label(), min_size=0, max_size=3),
    style=_serialization_style,
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
)
def test_property_20_defensibility_relative_strength_entry_mirrors_tool_result(
    target, earlier, style, action
):
    """Validates: Requirements 8.1, 8.2

    For any most-recent ``get_relative_strength`` Relative_Strength_Label in
    message history, the relative-strength entry in the defensibility record
    copies index_direction / relative_strength_state / alignment, the named
    measures, and the benchmark verbatim (no inference, no substitution).
    Earlier labels are present first, so the test also confirms the MOST RECENT
    label is the one mirrored (R8.1).
    """
    # Earlier (stale) RS labels first, then the target as the LATEST one.
    messages = [
        StubToolMessage(content=_serialize(lbl, style), name=RS_TOOL)
        for lbl in earlier
    ]
    messages.append(StubToolMessage(content=_serialize(target, style), name=RS_TOOL))

    decision = {"action": action, "source": "declare_trade"}

    # ── Via the full record builder: record["relative_strength"] mirrors target ─
    record = build_defensibility_record(messages, decision, mode="FIND")
    _assert_mirrors(record["relative_strength"], target)

    # The mirror carries the LATEST label's context verbatim (most-recent wins).
    assert record["relative_strength"].get("symbol") == target["symbol"]
    assert record["relative_strength"].get("timeframe") == target["timeframe"]
    assert record["relative_strength"].get("aligned_candles") == target["aligned_candles"]

    # ── Via _relative_strength_entry over _latest_tool_results directly ──────
    entry = _relative_strength_entry(_latest_tool_results(messages))
    _assert_mirrors(entry, target)
