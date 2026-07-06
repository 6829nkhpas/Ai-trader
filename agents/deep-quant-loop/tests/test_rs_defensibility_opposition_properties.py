"""Property-based test for the misaligned-directional opposition statement
(graph.py, task 8.4).

Feature: relative-strength-context

This module implements design **Property 22: A misaligned directional trade
records the opposition statement**:

    When the most-recent ``get_relative_strength`` Alignment is ``misaligned``
    AND the committed decision's action is BUY or SELL, the relative-strength
    entry built by ``build_defensibility_record`` includes an explicit
    ``trade_opposes_relative_strength`` statement (a non-empty string) declaring
    that the committed trade fights the index or trades a laggard against its
    benchmark. Conversely, that statement is ABSENT for a HOLD action or for any
    non-misaligned Alignment (aligned / neutral).

Validates: Requirements 8.4.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` —
    assembles the record whose ``"relative_strength"`` entry gains the
    ``trade_opposes_relative_strength`` key only when alignment is ``misaligned``
    and the action is directional (BUY/SELL). The statement is also surfaced in
    the human-readable ``summary``.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads. Tool results are
serialized both as JSON and as Python dict-repr strings, since both quoting
styles flow through the stack.

The sys.path / import pattern mirrors
``tests/test_rs_defensibility_mirror_properties.py``: the service directory (one
level up) is prepended to ``sys.path`` so ``graph`` is importable.
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

from graph import build_defensibility_record  # noqa: E402

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
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# A measure value is a finite number or null (None), per the RS contract.
_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _rs_label(draw, alignment=None):
    """Draw a conforming Relative_Strength_Label, optionally pinning alignment."""
    return {
        "index_direction": draw(_index_direction),
        "relative_strength_state": draw(_relative_strength_state),
        "alignment": draw(_alignment) if alignment is None else alignment,
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


# ─────────────────────────────────────────────────────────────────────────────
# Property 22: a misaligned directional trade records the opposition statement
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 22: A misaligned directional trade records the opposition statement
@settings(max_examples=100, deadline=None)
@given(
    label=_rs_label(),
    style=_serialization_style,
    action=_action,
)
def test_property_22_misaligned_directional_records_opposition_statement(
    label, style, action
):
    """Validates: Requirements 8.4

    The ``trade_opposes_relative_strength`` opposition statement is present in
    the relative-strength entry exactly when the most-recent alignment is
    ``misaligned`` AND the committed action is directional (BUY or SELL); it is
    absent for a HOLD action or any non-misaligned alignment (aligned/neutral).
    Building the record never raises, and a present statement is surfaced in the
    record summary.
    """
    messages = [StubToolMessage(content=_serialize(label, style), name=RS_TOOL)]
    decision = {"action": action, "source": "declare_trade"}

    record = build_defensibility_record(messages, decision, mode="FIND")
    relative_strength = record["relative_strength"]

    # The RS label is usable, so the entry mirrors it (available is True).
    assert relative_strength.get("available") is True

    should_oppose = label["alignment"] == "misaligned" and action in ("BUY", "SELL")

    if should_oppose:
        # Statement present and a non-empty string (R8.4).
        statement = relative_strength.get("trade_opposes_relative_strength")
        assert isinstance(statement, str) and statement.strip(), (
            f"expected a non-empty opposition statement for action={action}, "
            f"alignment={label['alignment']}, got {statement!r}"
        )
        # It explicitly declares the trade fights the index / a laggard (R8.4).
        assert "fights the index" in statement or "laggard" in statement
        # It is surfaced in the human-readable summary too.
        assert statement in record["summary"]
    else:
        # Statement absent for HOLD or for aligned/neutral alignment (R8.4).
        assert "trade_opposes_relative_strength" not in relative_strength, (
            f"opposition statement must be absent for action={action}, "
            f"alignment={label['alignment']}, got "
            f"{relative_strength.get('trade_opposes_relative_strength')!r}"
        )
