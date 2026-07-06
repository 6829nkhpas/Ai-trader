"""Property-based test for the calibration (reliability) binning helper
(backtest.py ``_calibration_from_records``, task 15.4).

Feature: volatility-aware-forecaster

This module implements design **Property 32: Calibration binning reports correct
per-bin statistics, a scalar error, and not-applicable empty bins**:

    The ``[0, 1]`` probability range is partitioned into ``bins`` equal-width
    bins; each record is placed in bin ``min(int(up_probability * bins), bins-1)``.
    Every NON-EMPTY bin reports its ``count``, the ``mean_predicted`` (mean of the
    bin's up-probabilities), and the ``realized_up_fraction`` (fraction of records
    whose ``went_up`` is True). Every EMPTY bin reports ``count`` 0 and the
    ``"n/a"`` sentinel for both statistics — never a number, never a divide by
    zero. The scalar ``calibration_error`` is the mean absolute difference between
    ``mean_predicted`` and ``realized_up_fraction`` over the non-empty bins, and is
    the same sentinel when EVERY bin is empty. ``total_records`` is the count of
    valid records and there are always exactly ``bins`` bin entries.

Validates: Requirements 12.2, 12.3, 12.4.

Implementation under test: the pure helper ``backtest._calibration_from_records``
(no candles / network needed). This test recomputes the expected binning
independently and asserts the helper agrees and never raises.

The sys.path / import pattern mirrors the other backtest property tests in this
directory: the service directory (one level up) is prepended to ``sys.path`` so
``backtest`` is importable when pytest is run from anywhere.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (backtest.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from backtest import _calibration_from_records, _CALIBRATION_NA  # noqa: E402

_TOL = 1e-6  # the helper rounds reported statistics to 6 decimals

# A record: a finite up_probability in [0, 1] paired with a realized went_up bool.
# Probabilities are drawn from a coarse-ish range so some bins are naturally left
# empty for many (bins, records) combinations.
_record = st.fixed_dictionaries({
    "up_probability": st.floats(min_value=0.0, max_value=1.0,
                                allow_nan=False, allow_infinity=False),
    "went_up": st.booleans(),
})


@settings(max_examples=200)
@given(
    bins=st.integers(min_value=1, max_value=20),
    # min_size 0 guarantees the generator can produce the no-records case (every
    # bin empty), exercising the divide-by-zero / all-"n/a" path.
    records=st.lists(_record, min_size=0, max_size=60),
)
# Feature: volatility-aware-forecaster, Property 32: Calibration binning reports correct per-bin statistics, a scalar error, and not-applicable empty bins
def test_calibration_binning_statistics_scalar_error_and_empty_bins(bins, records):
    # The helper must never raise on any tolerated input.
    report = _calibration_from_records(records, bins)

    # ── Independently recompute the expected per-bin partition. ──────────────
    bin_ps = [[] for _ in range(bins)]      # up-probabilities placed in each bin
    bin_ups = [[] for _ in range(bins)]     # went_up bools placed in each bin
    for rec in records:
        p = rec["up_probability"]
        idx = int(p * bins)
        if idx >= bins:                     # the p == 1.0 edge lands in last bin
            idx = bins - 1
        bin_ps[idx].append(p)
        bin_ups[idx].append(bool(rec["went_up"]))

    expected_total = sum(len(ps) for ps in bin_ps)

    # ── Top-level shape. ─────────────────────────────────────────────────────
    assert set(report.keys()) == {"bins", "calibration_error", "total_records"}
    assert report["total_records"] == expected_total == len(records)
    # Exactly ``bins`` bin entries, in ascending bin order.
    assert len(report["bins"]) == bins

    expected_abs_errors = []
    width = 1.0 / bins
    for k in range(bins):
        entry = report["bins"][k]
        assert set(entry.keys()) == {
            "lower", "upper", "count", "mean_predicted", "realized_up_fraction",
        }
        # Bin edges are the equal-width partition of [0, 1] in ascending order.
        assert math.isclose(entry["lower"], k * width, abs_tol=1e-9)
        assert math.isclose(entry["upper"], (k + 1) * width, abs_tol=1e-9)

        ps = bin_ps[k]
        ups = bin_ups[k]
        if not ps:
            # Empty bin: count 0 and the sentinel for both stats — never a number,
            # so no division by zero ever occurred.
            assert entry["count"] == 0
            assert entry["mean_predicted"] == _CALIBRATION_NA
            assert entry["realized_up_fraction"] == _CALIBRATION_NA
            assert not isinstance(entry["mean_predicted"], (int, float))
            assert not isinstance(entry["realized_up_fraction"], (int, float))
        else:
            expected_mean = sum(ps) / len(ps)
            expected_frac = sum(1 for u in ups if u) / len(ups)
            assert entry["count"] == len(ps)
            assert isinstance(entry["mean_predicted"], float)
            assert isinstance(entry["realized_up_fraction"], float)
            assert math.isclose(entry["mean_predicted"], expected_mean, abs_tol=_TOL)
            assert math.isclose(entry["realized_up_fraction"], expected_frac,
                                abs_tol=_TOL)
            expected_abs_errors.append(abs(expected_mean - expected_frac))

    # ── Scalar calibration error. ────────────────────────────────────────────
    if expected_abs_errors:
        expected_err = sum(expected_abs_errors) / len(expected_abs_errors)
        assert isinstance(report["calibration_error"], float)
        assert math.isclose(report["calibration_error"], expected_err, abs_tol=_TOL)
    else:
        # Every bin empty: the sentinel, never a number (no divide by zero).
        assert report["calibration_error"] == _CALIBRATION_NA
        assert not isinstance(report["calibration_error"], (int, float))
