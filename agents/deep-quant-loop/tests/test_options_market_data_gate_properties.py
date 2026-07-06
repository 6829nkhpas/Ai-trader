# Feature: options-agent-integration, Property 11: market_data_seen gating tracks usability
"""Property-based test for the market-data gate over `get_options_analytics` (graph.py, task 6.2).

Feature: options-agent-integration

This module implements design **Property 11: market_data_seen gating tracks
usability**:

    A usable ``get_options_analytics`` result (a full Options_Bias_Label — neither
    an error result nor an explicit Unavailable_Marker) sets the
    ``market_data_seen`` flag; an error result ({"error": ...}) or an
    Unavailable_Marker ({"unavailable": true, ...}) does NOT set the flag on its
    own; and a usable options result is treated EXACTLY like every other
    market-data tool (the gate is structural, not options-specific).

Validates: Requirements 4.2, 4.3.

The implementation under test lives in ``graph.py``:
  - ``MARKET_DATA_TOOL_NAMES`` (must contain ``get_options_analytics``)
  - ``_market_data_seen(messages)`` — the classifier used to maintain the flag
  - ``_tool_result_is_error`` / ``_tool_result_is_unavailable`` — the predicates

The latch itself is the expression maintained in ``call_model``:
``market_data_seen = bool(state.get("market_data_seen")) or _market_data_seen(messages)``.
The monotonicity check below models that latch directly.

The real LLM / Rust server is never invoked. A real LangChain ``ToolMessage``
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the tool result
— exactly the shape the gate code reads. Options results are serialized both as
JSON (``{"...": ...}``) and as Python dict-repr (``{'...': ...}``) strings, since
both quoting styles flow through the stack (the tool returns ``str(dict)`` /
``json``-style content).

The sys.path / import pattern mirrors the sibling
``tests/test_session_market_data_gate_properties.py`` module: the service
directory (one level up) is prepended to ``sys.path`` so ``graph`` is importable
when pytest is run from anywhere.
"""

import json
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st
from langchain_core.messages import ToolMessage

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import MARKET_DATA_TOOL_NAMES, _market_data_seen  # noqa: E402

OPTIONS_TOOL = "get_options_analytics"
# A sibling market-data tool used to confirm the options tool is treated
# identically by the structural gate.
SIBLING_TOOL = "get_session_context"


def _serialize(payload, style):
    """Serialize a result dict as a JSON string or a Python dict-repr string.

    The tool returns its result as a dict; the ToolMessage content is the
    serialized form. ``json`` mirrors ``json.dumps`` and ``repr`` mirrors
    ``str(dict)`` (single quotes, ``True``/``None`` tokens).
    """
    if style == "json":
        return json.dumps(payload)
    return repr(payload)


def _tool_message(content, name=OPTIONS_TOOL):
    """Build a LangChain ToolMessage exactly as the gate code reads it."""
    return ToolMessage(content=content, name=name, tool_call_id="call_options")


def _latch(prior, messages):
    """Model the flag latch maintained in ``call_model`` (graph.py).

    ``market_data_seen = bool(state.get('market_data_seen')) or
    _market_data_seen(messages)``.
    """
    return bool(prior) or _market_data_seen(messages)


# ── Strategies ───────────────────────────────────────────────────────────────
# Symbol/underlying restricted to tokens that can never contain the "error" or
# "unavailable" substrings, so the classification of a usable label is decided
# purely by its structure (not by incidental text in free-form fields).
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ", min_size=1, max_size=10)
_bias_state = st.sampled_from(["bullish", "bearish", "neutral"])
_alignment = st.sampled_from(["aligned", "misaligned", "neutral"])
_chain_context = st.sampled_from(["own-chain", "broad-market"])
_serialization_style = st.sampled_from(["json", "repr"])

# Numeric-or-null analytic values (finite floats or null).
_num_or_null = st.one_of(
    st.none(),
    st.floats(min_value=-1000.0, max_value=100000.0, allow_nan=False, allow_infinity=False),
)
_oi_direction = st.sampled_from(
    ["long_buildup", "short_buildup", "long_unwinding", "short_covering", "neutral"]
)


@st.composite
def _usable_options_content(draw):
    """A full Options_Bias_Label string — neither an error nor an Unavailable_Marker.

    Carries the bias fields (options_bias_state, alignment, chain_context) plus
    the analytics the tool merges in (pcr_oi, pcr_volume, max_pain, oi_buildup,
    oi_walls, iv_skew, futures_basis, underlying/spot).
    """
    payload = {
        "symbol": draw(_symbol),
        "underlying": draw(_symbol),
        "chain_context": draw(_chain_context),
        "options_bias_state": draw(_bias_state),
        "alignment": draw(_alignment),
        "pcr_oi": draw(_num_or_null),
        "pcr_volume": draw(_num_or_null),
        "max_pain": draw(_num_or_null),
        "spot": draw(_num_or_null),
        "futures_basis": draw(_num_or_null),
        "oi_buildup": {"call": draw(_oi_direction), "put": draw(_oi_direction)},
        "oi_walls": {"support": draw(_num_or_null), "resistance": draw(_num_or_null)},
        "iv_skew": draw(st.one_of(st.none(), st.builds(
            lambda v: {"put_minus_call": v}, _num_or_null
        ))),
    }
    return _serialize(payload, draw(_serialization_style))


@st.composite
def _error_options_content(draw):
    """An error result string for the options tool (carries an ``error`` key)."""
    payload = {
        "symbol": draw(_symbol),
        "error": draw(
            st.sampled_from(
                [
                    "symbol must be a non-empty string",
                    "contract_violation",
                    "options_bias_state not in enum",
                    "connection refused",
                ]
            )
        ),
    }
    return _serialize(payload, draw(_serialization_style))


@st.composite
def _unavailable_options_content(draw):
    """An Unavailable_Marker result string for the options tool.

    Per AD-6 / R3.2, ``options_bias_state`` and ``alignment`` are omitted.
    """
    payload = {
        "symbol": draw(_symbol),
        "underlying": draw(_symbol),
        "chain_context": draw(_chain_context),
        "unavailable": True,
        "reason": draw(
            st.sampled_from(
                [
                    "no option chain snapshot available",
                    "outside market hours",
                    "unsubscribed underlying",
                    "spot unavailable",
                ]
            )
        ),
    }
    return _serialize(payload, draw(_serialization_style))


# ─────────────────────────────────────────────────────────────────────────────
# Property 11: market_data_seen gating tracks usability
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-agent-integration, Property 11: market_data_seen gating tracks usability
@settings(max_examples=200, deadline=None)
@given(
    usable=_usable_options_content(),
    error=_error_options_content(),
    unavailable=_unavailable_options_content(),
    prior_seen=st.booleans(),
    trailing=st.lists(
        st.one_of(_error_options_content(), _unavailable_options_content()),
        min_size=0,
        max_size=5,
    ),
)
def test_property_11_options_market_data_gate_tracks_usability(
    usable, error, unavailable, prior_seen, trailing
):
    """Validates: Requirements 4.2, 4.3

    (4.2) A usable ``get_options_analytics`` result (a full Options_Bias_Label)
          sets ``market_data_seen``.
    (4.3) An error result or an Unavailable_Marker does NOT set the flag on the
          basis of that result. Once the flag has latched true within a run it
          stays true regardless of subsequent error / unavailable results
          (monotonicity), and a usable options result is classified exactly like
          every other market-data tool.
    """
    # Precondition: the options tool participates in the gate at all (R4.1).
    assert OPTIONS_TOOL in MARKET_DATA_TOOL_NAMES

    usable_msg = _tool_message(usable)
    error_msg = _tool_message(error)
    unavailable_msg = _tool_message(unavailable)

    # ── R4.2: a usable options label, on its own, satisfies the gate ─────────
    assert _market_data_seen([usable_msg]) is True

    # ── R4.3: an error-only or unavailable-only options result does NOT ──────
    assert _market_data_seen([error_msg]) is False
    assert _market_data_seen([unavailable_msg]) is False
    # Even both together (still no usable data) leave the flag unset.
    assert _market_data_seen([error_msg, unavailable_msg]) is False

    # The classifying predicates back this up directly.
    assert graph._tool_result_is_error(error) is True
    assert graph._tool_result_is_unavailable(unavailable) is True
    assert graph._tool_result_is_error(usable) is False
    assert graph._tool_result_is_unavailable(usable) is False

    # ── Treated EXACTLY like another market-data tool ────────────────────────
    # The same serialized content carried by a sibling market-data tool name
    # classifies identically — the gate is structural, not options-specific.
    assert SIBLING_TOOL in MARKET_DATA_TOOL_NAMES
    assert _market_data_seen([_tool_message(usable, name=SIBLING_TOOL)]) is True
    assert _market_data_seen([_tool_message(error, name=SIBLING_TOOL)]) is False
    assert _market_data_seen([_tool_message(unavailable, name=SIBLING_TOOL)]) is False

    # ── R4.3: monotonicity of the latch ──────────────────────────────────────
    # Build a trailing run of error/unavailable options messages (no usable data).
    trailing_msgs = [_tool_message(c) for c in trailing]

    # The trailing messages alone never satisfy the gate (no usable data).
    assert _market_data_seen(trailing_msgs) is False

    # Once the flag is already true (prior_seen=True), it stays true regardless
    # of subsequent error/unavailable results.
    if prior_seen:
        assert _latch(prior_seen, trailing_msgs) is True

    # A usable result latches the flag true, and it remains true through any
    # number of subsequent error/unavailable options results.
    latched = _latch(False, [usable_msg])
    assert latched is True
    assert _latch(latched, trailing_msgs) is True
    assert _latch(latched, [error_msg, unavailable_msg] + trailing_msgs) is True

    # iff direction: the gate is True iff at least one usable Options_Bias_Label
    # is present — a usable result anywhere in a mixed sequence satisfies it,
    # regardless of position.
    assert _market_data_seen(trailing_msgs + [usable_msg]) is True
    assert _market_data_seen([error_msg, usable_msg, unavailable_msg]) is True
