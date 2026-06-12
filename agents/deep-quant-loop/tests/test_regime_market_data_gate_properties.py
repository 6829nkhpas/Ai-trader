"""Property-based test for the market-data gate over `get_market_regime` (graph.py, task 7.3).

Feature: regime-detection-gate

This module implements design **Property 17: The market-data gate classifies
regime results correctly and stays monotone**:

    A usable ``get_market_regime`` result (a full Regime_Label — neither an error
    result nor an explicit Unavailable_Marker) sets the ``market_data_seen``
    flag; an error result or an Unavailable_Marker does NOT set the flag on its
    own; and once the flag has latched true within a run it stays true regardless
    of any subsequent error / unavailable regime results.

Validates: Requirements 5.4, 5.5, 5.6.

The implementation under test lives in ``graph.py``:
  - ``MARKET_DATA_TOOL_NAMES`` (must contain ``get_market_regime``)
  - ``_market_data_seen(messages)`` — the classifier used to maintain the flag
  - ``_tool_result_is_error`` / ``_tool_result_is_unavailable`` — the predicates

The latch itself is the expression maintained in ``call_model``:
``market_data_seen = bool(state.get("market_data_seen")) or _market_data_seen(messages)``.
The monotonicity property below models that latch directly.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the gate code reads. Regime tool results are
serialized both as JSON (``{"...": ...}``) and as Python dict-repr
(``{'...': ...}``) strings, since both quoting styles flow through the stack.

The sys.path / import pattern mirrors ``tests/test_loop_routing.py``: the service
directory (one level up) is prepended to ``sys.path`` so ``graph`` is importable
when pytest is run from anywhere.
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
from graph import MARKET_DATA_TOOL_NAMES, _market_data_seen  # noqa: E402

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


def _latch(prior, messages):
    """Model the flag latch maintained in ``call_model`` (graph.py).

    ``market_data_seen = bool(state.get('market_data_seen')) or
    _market_data_seen(messages)``.
    """
    return bool(prior) or _market_data_seen(messages)


# ── Strategies ───────────────────────────────────────────────────────────────
# Symbol/timeframe restricted to tokens that can never contain the "error" or
# "unavailable" substrings, so the classification of a usable label is decided
# purely by its structure (not by incidental text in free-form fields).
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
def _usable_regime_content(draw):
    """A full Regime_Label result string — neither error nor Unavailable_Marker."""
    payload = {
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
    return _serialize(payload, draw(_serialization_style))


@st.composite
def _error_regime_content(draw):
    """An error result string for the regime tool (carries an ``error`` key)."""
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "error": draw(
            st.sampled_from(
                [
                    "Failed to retrieve candles from Rust server: timeout",
                    "connection refused",
                    "contract_violation",
                    "no data",
                ]
            )
        ),
    }
    return _serialize(payload, draw(_serialization_style))


@st.composite
def _unavailable_regime_content(draw):
    """An Unavailable_Marker result string for the regime tool."""
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "unavailable": True,
        "reason": draw(
            st.sampled_from(
                [
                    "insufficient data: 18 valid candles received, 50 required",
                    "retrieval timeout",
                    "no measure could be computed",
                ]
            )
        ),
    }
    return _serialize(payload, draw(_serialization_style))


# ─────────────────────────────────────────────────────────────────────────────
# Property 17: market-data gate classification and monotonicity
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 17
@settings(max_examples=200, deadline=None)
@given(
    usable=_usable_regime_content(),
    error=_error_regime_content(),
    unavailable=_unavailable_regime_content(),
    prior_seen=st.booleans(),
    trailing=st.lists(
        st.one_of(_error_regime_content(), _unavailable_regime_content()),
        min_size=0,
        max_size=5,
    ),
)
def test_property_17_market_data_gate_classification_and_monotonicity(
    usable, error, unavailable, prior_seen, trailing
):
    """Validates: Requirements 5.4, 5.5, 5.6

    (5.4) A usable ``get_market_regime`` result sets ``market_data_seen``.
    (5.5) An error-only or unavailable-only regime result does NOT set it.
    (5.6) Once the flag has latched true, it stays true regardless of subsequent
          error / unavailable regime results.
    """
    # Precondition: the regime tool participates in the market-data gate at all.
    assert REGIME_TOOL in MARKET_DATA_TOOL_NAMES

    usable_msg = StubToolMessage(content=usable, name=REGIME_TOOL)
    error_msg = StubToolMessage(content=error, name=REGIME_TOOL)
    unavailable_msg = StubToolMessage(content=unavailable, name=REGIME_TOOL)

    # ── R5.4: a usable regime label, on its own, satisfies the gate ──────────
    assert _market_data_seen([usable_msg]) is True

    # ── R5.5: an error-only or unavailable-only regime result does NOT ───────
    assert _market_data_seen([error_msg]) is False
    assert _market_data_seen([unavailable_msg]) is False
    # Even both together (still no usable data) leave the flag unset.
    assert _market_data_seen([error_msg, unavailable_msg]) is False

    # The classifying predicates back this up directly.
    assert graph._tool_result_is_error(error) is True
    assert graph._tool_result_is_unavailable(unavailable) is True
    assert graph._tool_result_is_error(usable) is False
    assert graph._tool_result_is_unavailable(usable) is False

    # ── R5.6: monotonicity of the latch ──────────────────────────────────────
    # Build a trailing run of error/unavailable regime messages (no usable data).
    trailing_msgs = [StubToolMessage(content=c, name=REGIME_TOOL) for c in trailing]

    # The trailing messages alone never satisfy the gate (no usable data).
    assert _market_data_seen(trailing_msgs) is False

    # Once the flag is already true (prior_seen=True), it stays true regardless
    # of subsequent error/unavailable results.
    if prior_seen:
        assert _latch(prior_seen, trailing_msgs) is True

    # A usable result latches the flag true, and it remains true through any
    # number of subsequent error/unavailable regime results.
    latched = _latch(False, [usable_msg])
    assert latched is True
    assert _latch(latched, trailing_msgs) is True
    assert _latch(latched, [error_msg, unavailable_msg] + trailing_msgs) is True
