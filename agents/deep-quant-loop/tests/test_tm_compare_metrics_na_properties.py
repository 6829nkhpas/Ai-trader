"""Property-based test for not-applicable comparison metrics on zero closed
trades (backtest.py ``_management_run_metrics``, task 10.5).

Feature: trade-management

This module implements design **Property 25: Comparison metrics are
not-applicable on zero closed trades**:

    For any results list containing NO closed trades — i.e. every entry has a
    status drawn from {``expired``, ``open``} (or the list is empty) — the
    ``_management_run_metrics`` helper reports ``win_rate == "n/a"``,
    ``expectancy == "n/a"`` and ``downside == "n/a"`` (with ``closed_trades ==
    0``) rather than dividing by zero, and never raises (Requirement 12.4).

    Complement: for any results list carrying AT LEAST ONE closed trade — a
    status of ``win``/``loss`` with a numeric ``r_multiple`` — the win_rate and
    expectancy are NUMERIC (not the ``"n/a"`` sentinel), so the sentinel is used
    exactly when (and only when) there are zero closed trades.

Validates: Requirements 12.4.

Implementation under test: ``backtest._management_run_metrics(results)``. A
trade is *closed* when its ``status`` is ``win`` or ``loss`` AND it carries a
finite numeric ``r_multiple``; ``expired``/``open`` entries (and entries with a
non-numeric ``r_multiple``) are excluded from every metric.

Strategy: two complementary generators.

  * ``_no_closed_results`` — lists (including the empty list) whose entries all
    carry a status drawn from {``expired``, ``open``} (never ``win``/``loss``),
    so there are provably zero closed trades regardless of any ``r_multiple``
    value present -> assert all three metrics are the ``"n/a"`` sentinel.
  * ``_with_closed_results`` — lists carrying at least one ``win``/``loss`` entry
    with a finite numeric ``r_multiple`` (mixed in with arbitrary
    ``expired``/``open`` noise) -> assert win_rate and expectancy are numeric.

The sys.path / import pattern mirrors the sibling backtest property tests
(``tests/test_backtest_lookahead_properties.py`` and the TM ``tests/test_tm_*``
modules): the service directory (one level up) is prepended to ``sys.path`` so
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

from backtest import _management_run_metrics  # noqa: E402

_NA = "n/a"

# Finite numeric R-multiples, including zero and negatives, exercising the
# winner (> 0) / loser (<= 0) split. Bounded to keep examples readable.
_r_multiple = st.floats(
    min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False
)

# Arbitrary values that may appear in an ``r_multiple`` slot for a NON-closed
# trade — including the kinds of non-numeric junk that must never be counted as
# closed. (Closure is gated on status AND numeric r_multiple, so for expired /
# open entries the r_multiple is irrelevant.)
_any_r_value = st.one_of(
    st.none(),
    st.just(_NA),
    st.text(max_size=4),
    st.booleans(),  # bool is excluded by _is_num even though it's an int subclass
    _r_multiple,
)


def _entry(status, r_value):
    """Build a single result dict in the shape _management_run_metrics reads."""
    return {"status": status, "r_multiple": r_value}


# A non-closed entry: status is strictly expired/open (never win/loss), so it is
# excluded from every metric regardless of its r_multiple.
_non_closed_entry = st.builds(
    _entry,
    st.sampled_from(["expired", "open"]),
    _any_r_value,
)

# A genuinely closed entry: a win/loss status carrying a finite numeric R.
_closed_entry = st.builds(
    _entry,
    st.sampled_from(["win", "loss"]),
    _r_multiple,
)


@st.composite
def _no_closed_results(draw):
    """A results list (possibly empty) with NO closed trades."""
    return draw(st.lists(_non_closed_entry, min_size=0, max_size=12))


@st.composite
def _with_closed_results(draw):
    """A results list with AT LEAST ONE closed trade, mixed with noise."""
    closed = draw(st.lists(_closed_entry, min_size=1, max_size=8))
    noise = draw(st.lists(_non_closed_entry, min_size=0, max_size=8))
    combined = closed + noise
    # Shuffle deterministically via a drawn permutation so closed trades are not
    # always positioned first (order must not affect the n/a determination).
    draw(st.randoms(use_true_random=False)).shuffle(combined)
    return combined


# ─────────────────────────────────────────────────────────────────────────────
# Property 25: comparison metrics are not-applicable on zero closed trades
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 25: Comparison metrics are not-applicable on zero closed trades
@settings(max_examples=200, deadline=None)
@given(results=_no_closed_results())
def test_property_25_zero_closed_trades_report_not_applicable(results):
    """Feature: trade-management, Property 25: Comparison metrics are
    not-applicable on zero closed trades.

    With no closed trades, win_rate / expectancy / downside are all the "n/a"
    sentinel, closed_trades is 0, and the call never raises (R12.4)."""
    metrics = _management_run_metrics(results)

    assert metrics["closed_trades"] == 0
    assert metrics["win_rate"] == _NA
    assert metrics["expectancy"] == _NA
    assert metrics["downside"] == _NA


# Feature: trade-management, Property 25: Comparison metrics are not-applicable on zero closed trades
@settings(max_examples=200, deadline=None)
@given(results=_with_closed_results())
def test_property_25_with_closed_trades_metrics_are_numeric(results):
    """Complement of Property 25: when at least one closed trade is present,
    win_rate and expectancy are numeric (never the "n/a" sentinel), so the
    sentinel marks exactly the zero-closed-trades case (R12.4)."""
    metrics = _management_run_metrics(results)

    assert metrics["closed_trades"] >= 1
    win_rate = metrics["win_rate"]
    expectancy = metrics["expectancy"]

    assert win_rate != _NA
    assert expectancy != _NA
    assert isinstance(win_rate, (int, float)) and not isinstance(win_rate, bool)
    assert isinstance(expectancy, (int, float)) and not isinstance(expectancy, bool)
    assert math.isfinite(win_rate) and math.isfinite(expectancy)
    # win_rate is a fraction of closed trades, so it lies in [0, 1].
    assert 0.0 <= win_rate <= 1.0
