"""Property-based test for contract identity on conforming results/markers (tools.py, task 6.7).

Feature: volatility-aware-forecaster

This Hypothesis property exercises ``validate_contract``'s ``get_forecast``
branch:

  * Property 17 (5.6, 5.8) — ``validate_contract`` is the identity on conforming
    results and markers: for any generated conforming ``get_forecast``
    Forecast_Label, and for any Unavailable_Marker, ``validate_contract(
    "get_forecast", result)`` returns that result unchanged.

A conforming Forecast_Label carries a ``projected_direction`` drawn from its
fixed enum ({up, down, flat}), an ``up_probability`` finite number in
[0.0, 1.0], an ``expected_move_atr`` finite number or ``null``, a
``forecast_confidence`` finite number in [0.0, 1.0], a ``forecast_alignment``
drawn from the shared {aligned, misaligned, neutral} enum, and a ``measures``
object whose every named measure (drift / volatility / standardized_drift / atr)
is a finite number or ``null``. An Unavailable_Marker carries
``{"unavailable": true, "reason": ...}`` and (per AD-5) omits the forecast
fields.

The test asserts the call never raises and returns the *same object* unchanged
(both object identity and deep equality), pinning the contract's pass-through
behavior across the full conforming input space.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (tools.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from tools import (  # noqa: E402
    ALIGNMENT_VALUES,
    FORECAST_DIRECTIONS,
    _FORECAST_MEASURE_FIELDS,
    validate_contract,
)

# ── Generators ────────────────────────────────────────────────────────────────

# A finite number or null — exactly what each named forecast measure and the
# Expected_Move_ATR are allowed to be in a conforming label (R5.5, R5.6). Bools
# are excluded because the contract's ``_is_number`` rejects them.
_finite_number = st.floats(allow_nan=False, allow_infinity=False)
_measure_value = st.one_of(
    st.none(),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    _finite_number,
)

# up_probability / forecast_confidence: a finite number in [0.0, 1.0]. Generate
# the full closed interval (including the 0.0 and 1.0 bounds) plus integer 0/1 so
# the identity holds across every conforming bounded value.
_unit_interval = st.one_of(
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([0, 1]),
)

_timeframe = st.sampled_from(sorted({"1m", "5m", "10m", "15m", "1h", "4h", "1d"}))
_symbol = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=90), min_size=1, max_size=8
)


@st.composite
def _conforming_label(draw):
    """A conforming ``get_forecast`` Forecast_Label.

    ``projected_direction`` and ``forecast_alignment`` are drawn from their fixed
    enums, ``up_probability`` and ``forecast_confidence`` are finite numbers in
    [0.0, 1.0], ``expected_move_atr`` is a finite number or null, and the
    ``measures`` object carries each named measure as a finite number or null, so
    the label satisfies the contract that ``validate_contract`` enforces.
    """
    measures = {field: draw(_measure_value) for field in _FORECAST_MEASURE_FIELDS}
    label = {
        "projected_direction": draw(st.sampled_from(sorted(FORECAST_DIRECTIONS))),
        "up_probability": draw(_unit_interval),
        "expected_move_atr": draw(_measure_value),
        "forecast_confidence": draw(_unit_interval),
        "forecast_alignment": draw(st.sampled_from(sorted(ALIGNMENT_VALUES))),
        "measures": measures,
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }
    return label


@st.composite
def _unavailable_marker(draw):
    """An Unavailable_Marker ({"unavailable": true, "reason": ...}).

    Per AD-5 the marker omits the forecast fields; it is recognized as an honest
    non-fatal result and must pass through unchanged.
    """
    marker = {
        "unavailable": True,
        "reason": draw(
            st.text(min_size=0, max_size=80)
            | st.sampled_from(
                [
                    "insufficient data: 12 valid candles received, 30 required",
                    "candle retrieval timed out",
                    "no forecast measure could be computed",
                    "symbol candle retrieval failed",
                ]
            )
        ),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }
    return marker


_conforming_result = st.one_of(_conforming_label(), _unavailable_marker())


# ─────────────────────────────────────────────────────────────────────────────
# Property 17: validate_contract is the identity on conforming results & markers
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 17: validate_contract is the identity on conforming results and markers
@settings(max_examples=200, deadline=None)
@given(result=_conforming_result)
def test_property_17_validate_contract_is_identity_on_conforming_forecast(result):
    """Validates: Requirements 5.6, 5.8

    For any conforming Forecast_Label or any Unavailable_Marker,
    ``validate_contract("get_forecast", result)`` returns that result unchanged
    (object identity + deep equality) and never raises.
    """
    # Snapshot for an after-the-fact equality check (defends against any
    # accidental mutation of the input by the validator).
    import copy

    snapshot = copy.deepcopy(result)

    try:
        returned = validate_contract("get_forecast", result)
    except Exception as exc:  # pragma: no cover - failure path
        raise AssertionError(
            f"validate_contract raised {exc!r} on a conforming result/marker"
        )

    # Identity: the exact same object is passed through (the branch returns
    # ``payload`` unchanged; the marker path returns it via _has_honest_marker).
    assert returned is result, "validate_contract did not return the same object"

    # It is not flagged as a violation.
    assert not (
        isinstance(returned, dict) and "contract_violation" in returned
    ), "conforming result/marker was incorrectly flagged as a contract violation"

    # Unchanged: the returned object equals the pre-call snapshot.
    assert returned == snapshot, "validate_contract altered the input result"
