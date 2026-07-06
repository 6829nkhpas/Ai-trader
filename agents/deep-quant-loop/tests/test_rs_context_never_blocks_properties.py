"""Property-based test for the relative-strength scope boundary (graph.py, task 8.5).

Feature: relative-strength-context

This module implements design **Property 35: The relative-strength context never
modifies or blocks a committed decision**:

    For any committed Deep_Quant_Agent decision (arbitrary BUY/SELL/HOLD action
    and arbitrary execution levels — entry / stop_loss / take_profit /
    conviction) and ANY ``get_relative_strength`` Relative_Strength_Label present
    in message history (including a ``misaligned`` one), assembling the
    defensibility record via ``build_defensibility_record`` leaves the
    committed decision's action and execution levels UNCHANGED. The
    relative-strength context's effect is limited to prompt guidance and
    defensibility surfacing; it never modifies, overrides, replaces, or blocks
    the committed decision (R13.4, R13.5).

Validates: Requirements 13.4, 13.5.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` reads
    the most-recent ``get_relative_strength`` result and attaches a
    relative-strength entry (with an opposition statement when ``misaligned`` +
    a directional action) WITHOUT touching the decision's action or levels.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads.

The sys.path / import pattern mirrors
``tests/test_rs_defensibility_mirror_properties.py``: the service directory (one
level up) is prepended to ``sys.path`` so ``graph`` is importable when pytest is
run from anywhere.
"""

import copy
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
from graph import build_defensibility_record  # noqa: E402

RS_TOOL = "get_relative_strength"

# The committed-decision fields the relative-strength context must never touch.
_EXECUTION_LEVEL_FIELDS = ("entry", "stop_loss", "take_profit", "conviction")


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
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_benchmark = st.sampled_from(["NIFTY 50", "BANKNIFTY", "NIFTY IT", "FINNIFTY"])
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_index_direction = st.sampled_from(["up", "down", "flat"])
_relative_strength_state = st.sampled_from(["leader", "inline", "laggard"])
# Include "misaligned" so the opposition-statement branch is exercised too.
_alignment = st.sampled_from(["aligned", "misaligned", "neutral"])
_serialization_style = st.sampled_from(["json", "repr"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)
# Finite execution levels (a committed plan carries real numbers).
_level_value = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)


@st.composite
def _rs_label(draw):
    """Draw a conforming Relative_Strength_Label of any alignment."""
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


@st.composite
def _committed_decision(draw):
    """Draw a committed decision with an arbitrary action and execution levels."""
    decision = {
        "action": draw(_action),
        "source": "declare_trade",
    }
    for field in _EXECUTION_LEVEL_FIELDS:
        decision[field] = draw(_level_value)
    return decision


# ─────────────────────────────────────────────────────────────────────────────
# Property 35: the relative-strength context never modifies or blocks a decision
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 35: The relative-strength context never modifies or blocks a committed decision
@settings(max_examples=100, deadline=None)
@given(
    decision=_committed_decision(),
    label=_rs_label(),
    style=_serialization_style,
)
def test_property_35_relative_strength_context_never_modifies_or_blocks_decision(
    decision, label, style
):
    """Validates: Requirements 13.4, 13.5

    Assembling the defensibility record over a committed decision and ANY
    relative-strength label (including ``misaligned``) leaves the decision's
    action and execution levels (entry / stop_loss / take_profit / conviction)
    UNCHANGED. The relative-strength context is a defensibility surface only — it
    never modifies, overrides, replaces, or blocks the committed decision.
    """
    messages = [StubToolMessage(content=_serialize(label, style), name=RS_TOOL)]

    # Snapshot the committed decision's action and execution levels BEFORE.
    action_before = decision["action"]
    levels_before = {field: decision[field] for field in _EXECUTION_LEVEL_FIELDS}
    decision_snapshot = copy.deepcopy(decision)

    record = build_defensibility_record(messages, decision, mode="FIND")

    # ── The committed decision is not modified or blocked (R13.4, R13.5) ─────
    # Action unchanged.
    assert decision["action"] == action_before
    # Every execution level unchanged.
    for field in _EXECUTION_LEVEL_FIELDS:
        assert decision[field] == levels_before[field]
    # The whole committed decision object is untouched (no field added/removed).
    assert decision == decision_snapshot

    # ── The record surfaces, never overrides, the committed decision ─────────
    # build_defensibility_record reports the committed action verbatim; the
    # relative-strength context did not replace it with a different action.
    assert record["action"] == action_before
    # The record's recorded execution levels mirror the committed ones (the
    # context did not rewrite entry / stop_loss / take_profit).
    assert record["levels"] == {
        "entry": levels_before["entry"],
        "stop_loss": levels_before["stop_loss"],
        "take_profit": levels_before["take_profit"],
    }
    # A relative-strength entry is present as defensibility surfacing only — its
    # presence (including any misaligned opposition statement) never blocks the
    # decision, which remains exactly as committed.
    assert "relative_strength" in record
