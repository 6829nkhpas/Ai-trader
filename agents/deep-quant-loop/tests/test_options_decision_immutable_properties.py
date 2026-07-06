"""Property-based test that options context never mutates a committed decision
(graph.py, task 7.4).

Feature: options-agent-integration

This module implements design **Property 20: Options context never mutates a
committed decision**:

    For any committed decision, building the defensibility options entry and the
    options verification step leaves the decision's action and execution levels
    (entry, stop-loss, take-profit) unchanged.

Validates: Requirements 10.3.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` —
    assembles the defensibility record whose ``"options"`` entry (built by
    ``_options_entry``) is a pure READ of the most-recent get_options_analytics
    tool result. Options context is a filter / defensibility surface only: it
    NEVER modifies, overrides, or blocks the committed decision's action or
    execution levels (entry, stop-loss, take-profit).

``build_defensibility_record`` itself only READS the committed decision — it
adds ``decision["defensibility"]`` only when invoked via ``_finalize_decision``,
NOT inside ``build_defensibility_record``. This test therefore asserts that
``build_defensibility_record`` leaves the committed decision dict completely
untouched: deep-copy the decision before the call and assert it is deep-equal
afterward, across arbitrary options labels (aligned / misaligned / neutral,
available / unavailable) and arbitrary committed decisions (BUY / SELL / HOLD
with arbitrary entry / stop_loss / take_profit / conviction_score / action).

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads. Tool results are
serialized both as JSON and as Python dict-repr strings, since both quoting
styles flow through the stack.

The sys.path / import pattern mirrors
``tests/test_options_opposition_statement_properties.py``: the service directory
(one level up) is prepended to ``sys.path`` so ``graph`` is importable.
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

from graph import build_defensibility_record  # noqa: E402

OPTIONS_TOOL = "get_options_analytics"


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
# Symbol/underlying restricted to tokens that can never contain the "error" or
# "unavailable" substrings, so the result is classified purely by its structure.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_options_bias_state = st.sampled_from(["bullish", "bearish", "neutral"])
_alignment = st.sampled_from(["aligned", "misaligned", "neutral"])
_chain_context = st.sampled_from(["own-chain", "broad-market"])
_serialization_style = st.sampled_from(["json", "repr"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# Numeric-or-null analytics fields per the options contract.
_numeric_or_null = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)
# Execution levels carried by the committed decision.
_level = st.floats(min_value=1.0, max_value=1e5, allow_nan=False, allow_infinity=False)
_conviction = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@st.composite
def _options_label(draw):
    """Draw a conforming usable Options_Bias_Label with arbitrary bias/alignment."""
    return {
        "symbol": draw(_symbol),
        "underlying": draw(_symbol),
        "chain_context": draw(_chain_context),
        "pcr_oi": draw(_numeric_or_null),
        "pcr_volume": draw(_numeric_or_null),
        "max_pain": draw(_numeric_or_null),
        "oi_buildup": {"call": draw(_numeric_or_null), "put": draw(_numeric_or_null)},
        "oi_walls": {"support": draw(_numeric_or_null), "resistance": draw(_numeric_or_null)},
        "iv_skew": {"put_minus_call": draw(_numeric_or_null)},
        "futures_basis": draw(_numeric_or_null),
        "spot": draw(st.floats(min_value=1.0, max_value=1e5, allow_nan=False, allow_infinity=False)),
        "options_bias_state": draw(_options_bias_state),
        "alignment": draw(_alignment),
    }


@st.composite
def _options_unavailable_marker(draw):
    """Draw a conforming get_options_analytics Unavailable_Marker (omits the label)."""
    return {
        "symbol": draw(_symbol),
        "underlying": draw(_symbol),
        "chain_context": draw(_chain_context),
        "unavailable": True,
        "reason": draw(st.sampled_from([
            "option chain unavailable",
            "no expiry resolved",
            "analytics could not be computed",
        ])),
    }


# Either a usable label or an unavailable marker — Property 20 must hold across
# available AND unavailable options context.
_options_result = st.one_of(_options_label(), _options_unavailable_marker())


@st.composite
def _committed_decision(draw):
    """Draw an arbitrary committed decision (BUY/SELL/HOLD) with execution levels."""
    return {
        "action": draw(_action),
        "source": draw(st.sampled_from(["declare_trade", "agent", "verify"])),
        "entry": draw(_level),
        "stop_loss": draw(_level),
        "take_profit": draw(_level),
        "conviction_score": draw(_conviction),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 20: options context never mutates a committed decision
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 20: Options context never mutates a committed decision
@settings(max_examples=300, deadline=None)
@given(
    result=_options_result,
    style=_serialization_style,
    decision=_committed_decision(),
    mode=st.sampled_from(["FIND", "VERIFY"]),
)
def test_property_20_options_context_never_mutates_committed_decision(
    result, style, decision, mode
):
    """Validates: Requirements 10.3

    For any committed decision and any options context (a usable label with an
    arbitrary bias/alignment, or an Unavailable_Marker), building the
    defensibility record — which constructs the options entry and the
    opposition statement that the options verification step consumes — leaves the
    committed decision's action and execution levels (entry, stop-loss,
    take-profit) unchanged. ``build_defensibility_record`` only READS the
    decision; it adds ``decision["defensibility"]`` only via
    ``_finalize_decision``, never here. The decision dict must therefore be
    deep-equal before and after the call (and never raise).
    """
    messages = [StubToolMessage(content=_serialize(result, style), name=OPTIONS_TOOL)]
    decision_before = copy.deepcopy(decision)

    # Building the record reads the committed decision and the options entry; it
    # must never raise and must never mutate the decision.
    record = build_defensibility_record(messages, decision, mode=mode)

    # The record is built (sanity) and carries an options entry.
    assert isinstance(record, dict)
    assert "options" in record

    # R10.3: the committed decision's action and execution levels are unchanged.
    assert decision == decision_before, (
        "build_defensibility_record must not mutate the committed decision; "
        f"before={decision_before!r} after={decision!r}"
    )
    # Be explicit about the protected fields named by Property 20.
    assert decision["action"] == decision_before["action"]
    assert decision["entry"] == decision_before["entry"]
    assert decision["stop_loss"] == decision_before["stop_loss"]
    assert decision["take_profit"] == decision_before["take_profit"]
    # build_defensibility_record itself never attaches the defensibility record
    # to the decision (that is _finalize_decision's job).
    assert "defensibility" not in decision
