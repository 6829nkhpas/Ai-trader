"""Property-based test for the misaligned-directional opposition statement
(graph.py, task 7.3).

Feature: options-agent-integration

This module implements design **Property 13: A committed directional trade
against a misaligned bias is flagged**:

    For any usable options entry whose ``alignment`` is ``misaligned`` together
    with a committed Declared_Trade whose action is ``BUY`` or ``SELL``, the
    defensibility record includes an explicit statement that the committed trade
    fights the prevailing options positioning; no such statement is added for a
    ``HOLD``, an ``aligned``/``neutral`` alignment, or an unavailable entry.

Validates: Requirements 6.4.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` —
    assembles the record whose ``"options"`` entry (built by ``_options_entry``)
    gains the ``trade_opposes_options`` key only when the options Alignment is
    ``misaligned`` and the committed action is directional (BUY/SELL). The
    statement is also surfaced in the human-readable ``summary``.

Options context is a filter / defensibility surface only: building the record
NEVER modifies the committed decision's action or execution levels (entry,
stop-loss, take-profit) — R10.3. This test asserts that too.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads. Tool results are
serialized both as JSON and as Python dict-repr strings, since both quoting
styles flow through the stack.

The sys.path / import pattern mirrors
``tests/test_forecast_defensibility_opposition_properties.py``: the service
directory (one level up) is prepended to ``sys.path`` so ``graph`` is importable.
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


@st.composite
def _options_label(draw, alignment=None):
    """Draw a conforming usable Options_Bias_Label, optionally pinning alignment."""
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
        "alignment": draw(_alignment) if alignment is None else alignment,
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


# ─────────────────────────────────────────────────────────────────────────────
# Property 13: a committed directional trade against a misaligned bias is flagged
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 13: A committed directional trade against a misaligned bias is flagged
@settings(max_examples=200, deadline=None)
@given(
    label=_options_label(),
    style=_serialization_style,
    action=_action,
    entry=_level,
    stop_loss=_level,
    take_profit=_level,
)
def test_property_13_misaligned_directional_records_opposition_statement(
    label, style, action, entry, stop_loss, take_profit
):
    """Validates: Requirements 6.4

    The ``trade_opposes_options`` opposition statement is present in the options
    entry exactly when the most-recent Alignment is ``misaligned`` AND the
    committed action is directional (BUY or SELL); it is absent for a HOLD action
    or any non-misaligned alignment (aligned/neutral). Building the record never
    raises, a present statement is surfaced in the record summary, and the
    committed decision's action and execution levels (entry/stop_loss/
    take_profit) are never modified by record-building (R10.3).
    """
    messages = [StubToolMessage(content=_serialize(label, style), name=OPTIONS_TOOL)]
    decision = {
        "action": action,
        "source": "declare_trade",
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }
    decision_before = copy.deepcopy(decision)

    record = build_defensibility_record(messages, decision, mode="FIND")
    options = record["options"]

    # The options label is usable, so the entry mirrors it (available is True).
    assert options.get("available") is True

    should_oppose = label["alignment"] == "misaligned" and action in ("BUY", "SELL")

    if should_oppose:
        # Statement present and a non-empty string (R6.4).
        statement = options.get("trade_opposes_options")
        assert isinstance(statement, str) and statement.strip(), (
            f"expected a non-empty opposition statement for action={action}, "
            f"alignment={label['alignment']}, got {statement!r}"
        )
        # It explicitly declares the committed trade fights options positioning (R6.4).
        assert "fights the prevailing" in statement
        # It is surfaced in the human-readable summary too.
        assert statement in record["summary"]
    else:
        # Statement absent for HOLD or for aligned/neutral alignment (R6.4).
        assert "trade_opposes_options" not in options, (
            f"opposition statement must be absent for action={action}, "
            f"alignment={label['alignment']}, got "
            f"{options.get('trade_opposes_options')!r}"
        )

    # Options context NEVER modifies the committed decision's action or execution
    # levels (R10.3): the decision dict is untouched by record-building.
    assert decision == decision_before, (
        "record-building must not modify the committed decision; "
        f"before={decision_before!r} after={decision!r}"
    )


# Feature: options-agent-integration, Property 13: A committed directional trade against a misaligned bias is flagged
@settings(max_examples=100, deadline=None)
@given(
    marker=_options_unavailable_marker(),
    style=_serialization_style,
    action=_action,
    entry=_level,
    stop_loss=_level,
    take_profit=_level,
)
def test_property_13_unavailable_entry_never_records_opposition_statement(
    marker, style, action, entry, stop_loss, take_profit
):
    """Validates: Requirements 6.4

    When the most-recent get_options_analytics result is an Unavailable_Marker,
    the options entry is recorded as unavailable and NEVER carries a
    ``trade_opposes_options`` statement — regardless of the committed action
    (including BUY/SELL). The committed decision is left untouched (R10.3).
    """
    messages = [StubToolMessage(content=_serialize(marker, style), name=OPTIONS_TOOL)]
    decision = {
        "action": action,
        "source": "declare_trade",
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }
    decision_before = copy.deepcopy(decision)

    record = build_defensibility_record(messages, decision, mode="FIND")
    options = record["options"]

    # An unavailable marker yields an unavailable entry with no fabricated label.
    assert options.get("available") is False
    assert "trade_opposes_options" not in options, (
        f"unavailable options entry must never carry an opposition statement, got "
        f"{options.get('trade_opposes_options')!r}"
    )
    # No statement leaks into the summary for an unavailable entry.
    assert "fights the prevailing" not in record["summary"]

    # The decision dict is untouched by record-building (R10.3).
    assert decision == decision_before, (
        "record-building must not modify the committed decision; "
        f"before={decision_before!r} after={decision!r}"
    )
