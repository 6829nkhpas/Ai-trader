"""Property-based test for contract identity on conforming results/markers (tools.py, task 6.7).

Feature: order-flow-context

This Hypothesis property exercises ``validate_contract``'s ``get_order_flow``
branch:

  * Property 18 (5.6, 5.8) — ``validate_contract`` is the identity on conforming
    results and markers: for any generated conforming ``get_order_flow``
    Order_Flow_Label, and for any Unavailable_Marker, ``validate_contract(
    "get_order_flow", result)`` returns that result unchanged.

A conforming Order_Flow_Label carries the two categorical states drawn from
their fixed enums (``order_flow_state`` in {buying, selling, balanced},
``alignment`` in {aligned, misaligned, neutral}), a ``measures`` object whose
every named proxy measure (candle_delta / cvd_proxy / up_volume / down_volume /
buying_pressure_ratio) is a finite number or ``null``, a ``tick_ofi`` that is a
finite number or ``null``, and a boolean ``live_tick_contributed`` flag. An
Unavailable_Marker carries ``{"unavailable": true, "reason": ...}`` and (per
AD-5) omits ``order_flow_state`` / ``alignment``.

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
    ORDER_FLOW_STATES,
    _OF_MEASURE_FIELDS,
    validate_contract,
)

# ── Generators ────────────────────────────────────────────────────────────────

# A finite number or null — exactly what each named Order_Flow_Proxy_Measure and
# the Tick_OFI are allowed to be in a conforming label (R5.5, R5.6). Bools are
# excluded because the contract's ``_is_number`` rejects them.
_finite_number = st.floats(allow_nan=False, allow_infinity=False)
_measure_value = st.one_of(
    st.none(),
    st.integers(min_value=-1_000_000, max_value=1_000_000),
    _finite_number,
)

# Tick_OFI is documented in [-1.0, 1.0] but the contract only requires
# finite-number-or-null; generate the broader finite-or-null space so the
# identity holds across every conforming tick_ofi value.
_tick_ofi_value = st.one_of(
    st.none(),
    st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    _finite_number,
)

_timeframe = st.sampled_from(sorted({"1m", "5m", "10m", "15m", "1h", "4h", "1d"}))
_symbol = st.text(
    alphabet=st.characters(min_codepoint=65, max_codepoint=90), min_size=1, max_size=8
)


@st.composite
def _conforming_label(draw):
    """A conforming ``get_order_flow`` Order_Flow_Label.

    Both categorical states are drawn from their fixed enums, the ``measures``
    object carries each named proxy measure as a finite number or null,
    ``tick_ofi`` is a finite number or null, and ``live_tick_contributed`` is a
    boolean, so the label satisfies the contract that ``validate_contract``
    enforces.
    """
    measures = {field: draw(_measure_value) for field in _OF_MEASURE_FIELDS}
    label = {
        "order_flow_state": draw(st.sampled_from(sorted(ORDER_FLOW_STATES))),
        "alignment": draw(st.sampled_from(sorted(ALIGNMENT_VALUES))),
        "measures": measures,
        "tick_ofi": draw(_tick_ofi_value),
        "live_tick_contributed": draw(st.booleans()),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }
    return label


@st.composite
def _unavailable_marker(draw):
    """An Unavailable_Marker ({"unavailable": true, "reason": ...}).

    Per AD-5 the marker omits Order_Flow_State / Alignment; it is recognized as
    an honest non-fatal result and must pass through unchanged.
    """
    marker = {
        "unavailable": True,
        "reason": draw(
            st.text(min_size=0, max_size=80)
            | st.sampled_from(
                [
                    "insufficient data: 12 valid candles received, 20 required",
                    "candle retrieval timed out",
                    "no order-flow measure could be computed",
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
# Property 18: validate_contract is the identity on conforming results & markers
# ─────────────────────────────────────────────────────────────────────────────

# Feature: order-flow-context, Property 18: validate_contract is the identity on conforming results and markers
@settings(max_examples=200, deadline=None)
@given(result=_conforming_result)
def test_property_18_validate_contract_is_identity_on_conforming_of(result):
    """Validates: Requirements 5.6, 5.8

    For any conforming Order_Flow_Label or any Unavailable_Marker,
    ``validate_contract("get_order_flow", result)`` returns that result unchanged
    (object identity + deep equality) and never raises.
    """
    # Snapshot for an after-the-fact equality check (defends against any
    # accidental mutation of the input by the validator).
    import copy

    snapshot = copy.deepcopy(result)

    try:
        returned = validate_contract("get_order_flow", result)
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
