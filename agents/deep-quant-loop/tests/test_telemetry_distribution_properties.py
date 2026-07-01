"""Property-based test for the telemetry distribution helper (telemetry.py, task 6.7).

Feature: session-telemetry

This module implements design **Property 11: Distributions are well-formed and
null on an empty sample**:

    For any numeric sample (watch cycles, time-to-decision, or a cost proxy across
    sessions), the reported Distribution satisfies ``min(sample) <= mean <=
    max(sample)``, ``median`` lies within ``[min(sample), max(sample)]``, and
    ``max`` equals ``max(sample)``; on an empty sample every field (``mean``,
    ``median``, ``max``) is ``null``.

Validates: Requirements 4.4.

The test exercises ``_distribution`` directly. Because the helper filters its
input to observable *finite real numbers* (reusing ``_finite_number`` — booleans
and non-finite / non-numeric values are dropped), the generators deliberately mix
ints, floats, negatives, NaN/inf, booleans, and non-numeric values so both the
finite-value path and the "empty / all-invalid sample" path are covered. The
sys.path / import pattern mirrors ``tests/test_telemetry_config_robustness_properties.py``.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from telemetry import (  # noqa: E402
    DIST_COUNT,
    DIST_MAX,
    DIST_MEAN,
    DIST_MEDIAN,
    _distribution,
    _finite_number,
)


# ── Element strategies ────────────────────────────────────────────────────────
# Finite real numbers (the values _distribution keeps) intermixed with the values
# it drops: NaN/inf, booleans (int subclass, deliberately excluded), and
# non-numeric junk. This exercises the finite-filter and the all-invalid → empty
# fallback across the whole documented input space.
_finite_values = st.one_of(
    st.integers(min_value=-10_000, max_value=10_000),
    st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False),
)

_invalid_values = st.one_of(
    st.just(float("nan")),
    st.just(float("inf")),
    st.just(float("-inf")),
    st.booleans(),
    st.text(max_size=4),
    st.none(),
)

_sample_element = st.one_of(_finite_values, _invalid_values)

# A mixed sample as a list or tuple; sizes include empty and single-element.
_sample = st.one_of(
    st.lists(_sample_element, min_size=0, max_size=30),
    st.lists(_sample_element, min_size=0, max_size=30).map(tuple),
)


# Feature: session-telemetry, Property 11: Distributions are well-formed and null on an empty sample
@settings(max_examples=100, deadline=None)
@given(sample=_sample)
def test_property_11_distribution_well_formed_and_null_on_empty(sample):
    """Feature: session-telemetry, Property 11: Distributions are well-formed and
    null on an empty sample — for any numeric sample, ``_distribution`` never
    raises and returns a Distribution whose ``mean`` lies within ``[min, max]``,
    whose ``median`` lies within ``[min, max]``, and whose ``max`` equals the
    sample maximum over the finite values; over an empty / all-invalid sample
    every summary field is ``None`` and ``count`` is ``0``.

    Validates: Requirements 4.4
    """
    # The helper keeps only observable finite real numbers (drops bool / NaN /
    # inf / non-numeric), so the oracle mirrors that filter exactly.
    finite = [float(x) for x in sample if _finite_number(x)]

    result = _distribution(sample)  # never raises (R4.4, R8.3)

    # Shape: exactly the four documented keys.
    assert set(result.keys()) == {DIST_MEAN, DIST_MEDIAN, DIST_MAX, DIST_COUNT}

    # count always equals the number of finite values kept.
    assert result[DIST_COUNT] == len(finite)

    if not finite:
        # Empty / all-invalid sample: every summary field is null, count 0.
        assert result[DIST_MEAN] is None
        assert result[DIST_MEDIAN] is None
        assert result[DIST_MAX] is None
        assert result[DIST_COUNT] == 0
        return

    lo = min(finite)
    hi = max(finite)

    mean = result[DIST_MEAN]
    median = result[DIST_MEDIAN]
    reported_max = result[DIST_MAX]

    # Non-empty sample: every field is a finite float.
    assert isinstance(mean, float) and math.isfinite(mean)
    assert isinstance(median, float) and math.isfinite(median)
    assert isinstance(reported_max, float) and math.isfinite(reported_max)

    # min(sample) <= mean <= max(sample)
    assert lo <= mean <= hi

    # median lies within [min(sample), max(sample)]
    assert lo <= median <= hi

    # max equals max(sample) over the finite values.
    assert reported_max == hi
