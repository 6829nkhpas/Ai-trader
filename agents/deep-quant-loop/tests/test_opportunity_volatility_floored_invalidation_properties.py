"""Property-based test for volatility-floored invalidation (opportunity.py, task 4.3).

Feature: adaptive-opportunity-engine

This module implements design **Property 11: Re-armed invalidation level is
volatility-floored**:

    For any direction, reference price, and ATR, ``volatility_floored_invalidation``
    returns an invalidation level on the opposite side of the reference whose
    distance from the reference is at least the volatility floor, so a noise-level
    stop cannot immediately re-trip.

Validates: Requirements 4.3.

``volatility_floored_invalidation(direction, ref_price, proposed_inv, atr)`` returns
an invalidation level on the OPPOSITE side of ``ref_price`` from the thesis
direction — BELOW the reference for an ``above``/long thesis, ABOVE it for a
``below``/short thesis — at least ``VOL_FLOOR_ATR_MULT * atr`` away. A
``proposed_inv`` already on the correct side and at least the floor away is kept;
otherwise the level is pushed out to exactly the floor distance. It returns
``None`` on unusable input: an unrecognized ``direction``, a non-finite
``ref_price``, or a missing / non-finite / non-positive ``atr``.

The property asserts two things:

  * **Usable input** (recognized direction, finite reference, positive finite
    ATR): a level is always returned (never ``None``), it sits strictly on the
    OPPOSITE side of the reference from the thesis direction, and its distance
    from the reference is at least ``VOL_FLOOR_ATR_MULT * atr`` — regardless of an
    arbitrary ``proposed_inv``.
  * **Unusable input** (each documented failure mode): ``None`` is returned.

``VOL_FLOOR_ATR_MULT`` is imported from the module rather than hardcoded. The
sys.path / import pattern mirrors the sibling deep-quant-loop opportunity property
tests.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (opportunity.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from opportunity import (  # noqa: E402
    VOL_FLOOR_ATR_MULT,
    volatility_floored_invalidation,
)

# ── Direction spellings (mirrors opportunity._DIRECTION_ABOVE/_BELOW) ─────────
_ABOVE_SPELLINGS = ["above", "up", "long", "buy", "  Above  ", "LONG", "Buy"]
_BELOW_SPELLINGS = ["below", "down", "short", "sell", "  Below  ", "SHORT", "Sell"]


def _side_of(direction):
    """Map a recognized spelling to its canonical side (test-local oracle)."""
    token = direction.strip().lower()
    if token in {"above", "up", "long", "buy"}:
        return "above"
    return "below"


# ── Generators over the documented usable input space ─────────────────────────

_recognized_direction = st.sampled_from(_ABOVE_SPELLINGS + _BELOW_SPELLINGS)

# Finite reference prices spanning positive, negative, and zero.
_finite_ref = st.floats(
    min_value=-1_000_000.0, max_value=1_000_000.0,
    allow_nan=False, allow_infinity=False,
)

# Strictly-positive finite ATR (a usable volatility scale).
_positive_atr = st.floats(
    min_value=1e-3, max_value=100_000.0,
    allow_nan=False, allow_infinity=False,
)

# An arbitrary proposed invalidation: None, finite floats anywhere (either side,
# too close, or far), and non-numeric / non-finite garbage the caller might pass.
_arbitrary_proposed = st.one_of(
    st.none(),
    st.floats(min_value=-2_000_000.0, max_value=2_000_000.0,
              allow_nan=False, allow_infinity=False),
    st.sampled_from([float("nan"), float("inf"), float("-inf"), "x", True, None]),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 11 (task 4.3): usable input -> opposite side, at least the floor away
# ─────────────────────────────────────────────────────────────────────────────

# Feature: adaptive-opportunity-engine, Property 11: For any recognized direction, finite reference price, and positive finite ATR (and arbitrary proposed_inv), volatility_floored_invalidation returns a non-None level on the OPPOSITE side of the reference from the thesis direction whose distance from the reference is at least VOL_FLOOR_ATR_MULT * atr.
@settings(max_examples=300, deadline=None)
@given(
    direction=_recognized_direction,
    ref_price=_finite_ref,
    proposed_inv=_arbitrary_proposed,
    atr=_positive_atr,
)
def test_property_11_usable_input_is_volatility_floored(direction, ref_price, proposed_inv, atr):
    """Feature: adaptive-opportunity-engine, Property 11: Re-armed invalidation
    level is volatility-floored — on usable input a level is always returned, it
    sits strictly on the opposite side of the reference from the thesis direction,
    and its distance from the reference is at least the volatility floor.

    Validates: Requirements 4.3
    """
    level = volatility_floored_invalidation(direction, ref_price, proposed_inv, atr)

    # ── Usable input always yields a level (never None). ──────────────────────
    assert level is not None
    assert isinstance(level, float)
    assert math.isfinite(level)

    floor = VOL_FLOOR_ATR_MULT * float(atr)
    # Float-subtraction slack scaled to the magnitudes involved so "at least the
    # floor" is not defeated by rounding at large prices.
    tol = 1e-6 * (abs(ref_price) + floor + 1.0)

    if _side_of(direction) == "above":
        # Long/bullish thesis -> invalidation sits BELOW the reference.
        assert level < ref_price, (
            f"expected level below ref for {direction!r}: level={level}, ref={ref_price}"
        )
        distance = ref_price - level
    else:
        # Short/bearish thesis -> invalidation sits ABOVE the reference.
        assert level > ref_price, (
            f"expected level above ref for {direction!r}: level={level}, ref={ref_price}"
        )
        distance = level - ref_price

    # ── A noise-level stop cannot immediately re-trip: distance >= floor. ─────
    assert distance >= floor - tol, (
        f"distance {distance} below volatility floor {floor} "
        f"(direction={direction!r}, ref={ref_price}, proposed={proposed_inv!r}, atr={atr})"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 11 (task 4.3): unusable input -> None
# ─────────────────────────────────────────────────────────────────────────────

# Each sub-strategy guarantees at least one documented unusable dimension while
# the others stay valid, so None is the required result in every case.
_bad_direction = st.sampled_from(["", "sideways", "flat", "None", "123", None, 1, 3.14, True])
_nonfinite_ref = st.sampled_from([float("nan"), float("inf"), float("-inf"), None, "100", True])
_bad_atr = st.one_of(
    st.none(),
    st.sampled_from([float("nan"), float("inf"), float("-inf"), 0.0, -0.0, "5", True]),
    st.floats(min_value=-100_000.0, max_value=-1e-6, allow_nan=False, allow_infinity=False),
)

_unusable_inputs = st.one_of(
    # 1. Unrecognized / non-string direction (rest valid).
    st.tuples(_bad_direction, _finite_ref, _arbitrary_proposed, _positive_atr),
    # 2. Non-finite / non-numeric reference (direction + atr valid).
    st.tuples(_recognized_direction, _nonfinite_ref, _arbitrary_proposed, _positive_atr),
    # 3. Missing / non-finite / non-positive ATR (direction + ref valid).
    st.tuples(_recognized_direction, _finite_ref, _arbitrary_proposed, _bad_atr),
)


# Feature: adaptive-opportunity-engine, Property 11: volatility_floored_invalidation returns None on the documented unusable inputs — an unrecognized/non-string direction, a non-finite reference price, or a missing/non-finite/non-positive ATR.
@settings(max_examples=300, deadline=None)
@given(args=_unusable_inputs)
def test_property_11_unusable_input_returns_none(args):
    """Feature: adaptive-opportunity-engine, Property 11: On any documented
    unusable input (unrecognized direction, non-finite reference, or missing /
    non-finite / non-positive ATR) the function returns None rather than
    fabricating a level.

    Validates: Requirements 4.3
    """
    direction, ref_price, proposed_inv, atr = args
    assert volatility_floored_invalidation(direction, ref_price, proposed_inv, atr) is None
