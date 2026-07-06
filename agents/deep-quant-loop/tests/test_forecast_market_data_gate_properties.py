"""Property-based test for the market-data gate over `get_forecast` (graph.py, task 8.3).

Feature: volatility-aware-forecaster

This module implements design **Property 22: The market-data gate classifies
forecast results correctly and stays monotone**:

    A usable ``get_forecast`` result (a full Forecast_Label — neither an error
    result nor an explicit Unavailable_Marker) sets the ``market_data_seen``
    flag; an error result or an Unavailable_Marker does NOT set the flag on its
    own; and once the flag has latched true within a run it stays true regardless
    of any subsequent error / unavailable forecast results.

Validates: Requirements 6.4, 7.4, 7.5.

The implementation under test lives in ``graph.py``:
  - ``MARKET_DATA_TOOL_NAMES`` (must contain ``get_forecast``)
  - ``_market_data_seen(messages)`` — the classifier used to maintain the flag
  - ``_tool_result_is_error`` / ``_tool_result_is_unavailable`` — the predicates

The latch itself is the expression maintained in ``call_model``:
``market_data_seen = bool(state.get("market_data_seen")) or _market_data_seen(messages)``.
The monotonicity property below models that latch directly.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the gate code reads. Forecast tool results
are serialized both as JSON (``{"...": ...}``) and as Python dict-repr
(``{'...': ...}``) strings, since both quoting styles flow through the stack.

The sys.path / import pattern mirrors ``tests/test_rs_market_data_gate_properties.py``.
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
_projected_direction = st.sampled_from(["up", "down", "flat"])
_alignment = st.sampled_from(["aligned", "misaligned", "neutral"])
_trend_state = st.sampled_from(["trending", "ranging", "transitional"])
_serialization_style = st.sampled_from(["json", "repr"])

# A probability in [0.0, 1.0].
_probability = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
# A measure value is a finite number or null (None), per the forecast contract.
_measure_value = st.one_of(
    st.none(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)


@st.composite
def _usable_forecast_content(draw):
    """A full Forecast_Label string — neither error nor Unavailable_Marker."""
    payload = {
        "projected_direction": draw(_projected_direction),
        "up_probability": draw(_probability),
        "expected_move_atr": draw(_measure_value),
        "forecast_confidence": draw(_probability),
        "forecast_alignment": draw(_alignment),
        "measures": {
            "drift": draw(_measure_value),
            "volatility": draw(st.one_of(st.none(), st.floats(min_value=0.0, max_value=1e6,
                                                               allow_nan=False, allow_infinity=False))),
            "standardized_drift": draw(_measure_value),
            "atr": draw(_measure_value),
        },
        "regime_trend_state": draw(_trend_state),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }
    return _serialize(payload, draw(_serialization_style))


@st.composite
def _error_forecast_content(draw):
    """An error result string for the forecast tool (carries an ``error`` key)."""
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
def _unavailable_forecast_content(draw):
    """An Unavailable_Marker result string for the forecast tool."""
    payload = {
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
        "unavailable": True,
        "reason": draw(
            st.sampled_from(
                [
                    "insufficient data: 12 valid candles available, 31 required",
                    "retrieval timeout",
                    "candle retrieval failed",
                    "no usable returns could be computed",
                ]
            )
        ),
    }
    return _serialize(payload, draw(_serialization_style))


# ─────────────────────────────────────────────────────────────────────────────
# Property 22: market-data gate classification and monotonicity
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 22: The market-data gate classifies forecast results correctly and stays monotone
@settings(max_examples=100, deadline=None)
@given(
    usable=_usable_forecast_content(),
    error=_error_forecast_content(),
    unavailable=_unavailable_forecast_content(),
    prior_seen=st.booleans(),
    trailing=st.lists(
        st.one_of(_error_forecast_content(), _unavailable_forecast_content()),
        min_size=0,
        max_size=5,
    ),
)
def test_property_22_forecast_market_data_gate_classification_and_monotonicity(
    usable, error, unavailable, prior_seen, trailing
):
    """Validates: Requirements 6.4, 7.4, 7.5

    (7.4) A usable ``get_forecast`` result sets ``market_data_seen``.
    (7.5) An error-only or unavailable-only forecast result does NOT set it.
    (6.4) An unavailable forecast is a non-blocking missing input; on its own it
          neither satisfies the gate nor forces a decision.
          Once the flag has latched true, it stays true regardless of subsequent
          error / unavailable forecast results (monotonicity).
    """
    # Precondition: the forecast tool participates in the gate at all.
    assert FORECAST_TOOL in MARKET_DATA_TOOL_NAMES

    usable_msg = StubToolMessage(content=usable, name=FORECAST_TOOL)
    error_msg = StubToolMessage(content=error, name=FORECAST_TOOL)
    unavailable_msg = StubToolMessage(content=unavailable, name=FORECAST_TOOL)

    # ── R7.4: a usable forecast label, on its own, satisfies the gate ─────────
    assert _market_data_seen([usable_msg]) is True

    # ── R7.5 / R6.4: an error-only or unavailable-only forecast does NOT ──────
    assert _market_data_seen([error_msg]) is False
    assert _market_data_seen([unavailable_msg]) is False
    # Even both together (still no usable data) leave the flag unset.
    assert _market_data_seen([error_msg, unavailable_msg]) is False

    # The classifying predicates back this up directly.
    assert graph._tool_result_is_error(error) is True
    assert graph._tool_result_is_unavailable(unavailable) is True
    assert graph._tool_result_is_error(usable) is False
    assert graph._tool_result_is_unavailable(usable) is False

    # ── R7.5: monotonicity of the latch ──────────────────────────────────────
    # Build a trailing run of error/unavailable forecast messages (no usable data).
    trailing_msgs = [StubToolMessage(content=c, name=FORECAST_TOOL) for c in trailing]

    # The trailing messages alone never satisfy the gate (no usable data).
    assert _market_data_seen(trailing_msgs) is False

    # Once the flag is already true (prior_seen=True), it stays true regardless
    # of subsequent error/unavailable results.
    if prior_seen:
        assert _latch(prior_seen, trailing_msgs) is True

    # A usable result latches the flag true, and it remains true through any
    # number of subsequent error/unavailable forecast results.
    latched = _latch(False, [usable_msg])
    assert latched is True
    assert _latch(latched, trailing_msgs) is True
    assert _latch(latched, [error_msg, unavailable_msg] + trailing_msgs) is True

    # iff direction: a usable result anywhere in a mixed sequence satisfies the
    # gate (at least one usable result present), regardless of position.
    assert _market_data_seen(trailing_msgs + [usable_msg]) is True
    assert _market_data_seen([error_msg, usable_msg, unavailable_msg]) is True
