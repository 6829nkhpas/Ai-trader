"""Property-based tests for IV skew over a per-strike IV map (options.py, task 4.6).

Feature: options-analytics-engine

This Hypothesis property exercises the deterministic volatility-surface summary
(:func:`options.compute_iv_skew`) across per-strike IV maps that mix finite,
non-null IV points with null / non-finite "junk" entries, asserting the
exclusion invariant the engine must satisfy:

  * Property 6 (2.4) — IV skew is computed only from non-null IV strikes: the
                       skew is derived from exactly the strikes whose IV is a
                       finite, non-null number, so adding or removing null-IV
                       strikes never changes the result, and the skew is null
                       whenever fewer than two such non-null IV points exist.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (options.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from options import compute_iv_skew  # noqa: E402

_SKEW_KEYS = ("put_minus_call", "slope", "atm_iv")


# ── Smart generators ─────────────────────────────────────────────────────────
# Finite strike keys in a realistic index-option ladder range. Distinct keys are
# guaranteed by ``st.dictionaries`` (which dedups on key), and we keep the null
# side disjoint from the non-null side below so a null entry never overwrites a
# genuine IV point.
_strike = st.floats(min_value=50.0, max_value=100_000.0,
                    allow_nan=False, allow_infinity=False)

# A finite, non-null IV — the only kind of value that contributes to the skew.
_finite_iv = st.floats(min_value=0.01, max_value=5.0,
                       allow_nan=False, allow_infinity=False)

# A "null" IV: any value the engine must EXCLUDE — None, non-finite floats,
# booleans (rejected by the finite check), and non-numeric junk.
_null_iv = st.one_of(
    st.none(),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
    st.booleans(),
    st.text(max_size=4),
)

# Spot price: finite (exercises every field) or non-finite (degrades the
# spot-relative fields to null) — the invariance must hold either way.
_spot = st.one_of(
    st.floats(min_value=50.0, max_value=100_000.0,
              allow_nan=False, allow_infinity=False),
    st.sampled_from([float("nan"), float("inf"), float("-inf")]),
)

# A map of genuine, non-null IV points (finite strike -> finite IV).
_nonnull_map = st.dictionaries(keys=_strike, values=_finite_iv, max_size=8)

# A map of strikes carrying a null / non-finite / non-numeric IV.
_null_map = st.dictionaries(keys=_strike, values=_null_iv, max_size=8)


def _disjoint_null(nonnull, null):
    """Drop any null entry whose strike collides with a genuine IV point.

    Keeping the two key sets disjoint guarantees that merging the null map into
    the genuine map only ADDS excluded strikes — it never overwrites a real IV
    point, which is precisely the "adding null-IV strikes" operation Property 6
    asserts is invariant.
    """
    return {k: v for k, v in null.items() if k not in nonnull}


# ─────────────────────────────────────────────────────────────────────────────
# Property 6 (2.4): adding null-IV strikes never changes the skew
# ─────────────────────────────────────────────────────────────────────────────

# Feature: options-analytics-engine, Property 6: IV skew is computed only from
# non-null IV strikes
@settings(max_examples=100)
@given(nonnull=_nonnull_map, null=_null_map, spot=_spot)
def test_property_6_skew_invariant_to_null_iv_strikes(nonnull, null, spot):
    """Feature: options-analytics-engine, Property 6: IV skew is computed only
    from non-null IV strikes — for any per-strike IV map, the skew is computed
    from exactly the strikes whose IV is a finite, non-null number, so adding
    (or removing) strikes whose IV is null / non-finite / non-numeric leaves the
    result identical.

    Validates: Requirements 2.4
    """
    null_extra = _disjoint_null(nonnull, null)

    # The genuine-only map and the genuine map AUGMENTED with null-IV strikes.
    baseline = compute_iv_skew(nonnull, spot)
    augmented = compute_iv_skew({**nonnull, **null_extra}, spot)

    # Adding null-IV strikes does not change the skew (identical result).
    assert augmented == baseline

    # Removing the null-IV strikes is the inverse operation: starting from the
    # augmented map and dropping exactly the null entries recovers the baseline.
    recovered = compute_iv_skew(
        {k: v for k, v in {**nonnull, **null_extra}.items() if k in nonnull},
        spot,
    )
    assert recovered == baseline

    # When two or more genuine IV points exist the result is a well-formed dict
    # whose fields are each a finite number or None; otherwise it is null.
    if len(nonnull) >= 2:
        assert isinstance(baseline, dict)
        assert set(baseline.keys()) == set(_SKEW_KEYS)
        for key in _SKEW_KEYS:
            value = baseline[key]
            assert value is None or (isinstance(value, float) and math.isfinite(value))
    else:
        assert baseline is None


# ─────────────────────────────────────────────────────────────────────────────
# Property 6 (2.4): null skew when fewer than two non-null IV points exist
# ─────────────────────────────────────────────────────────────────────────────

# A map with AT MOST one genuine IV point (zero or one finite-IV strike).
_sparse_nonnull = st.dictionaries(keys=_strike, values=_finite_iv, max_size=1)


# Feature: options-analytics-engine, Property 6: IV skew is computed only from
# non-null IV strikes
@settings(max_examples=100)
@given(nonnull=_sparse_nonnull, null=_null_map, spot=_spot)
def test_property_6_skew_null_below_two_nonnull_points(nonnull, null, spot):
    """Feature: options-analytics-engine, Property 6: IV skew is computed only
    from non-null IV strikes — when fewer than two non-null IV points exist
    (here zero or one genuine point, regardless of how many null-IV strikes pad
    the map), ``compute_iv_skew`` returns null.

    Validates: Requirements 2.4
    """
    null_extra = _disjoint_null(nonnull, null)
    merged = {**nonnull, **null_extra}

    # Only the genuine finite-IV strikes count; with fewer than two there is no
    # skew to compute, so the result is null no matter how many junk strikes pad
    # the map.
    assert compute_iv_skew(merged, spot) is None
