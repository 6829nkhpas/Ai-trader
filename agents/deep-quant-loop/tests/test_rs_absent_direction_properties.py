"""Property-based test for absent-direction neutral alignment (rs.py, task 3.7).

Feature: relative-strength-context

This module implements design **Property 7: Absent proposed direction yields a
neutral alignment with the other fields present**:

    For any symbol candle sequence, benchmark candle sequence, and resolved
    ``RSConfig``, invoking ``classify_relative_strength`` with
    ``proposed_direction=None`` (no proposed trade direction) returns a result
    that, *whenever it is a Relative_Strength_Label* (rather than an
    Unavailable_Marker), reports ``alignment == "neutral"`` while still carrying
    the ``index_direction``, the ``relative_strength_state``, and the named
    ``measures``.

Validates: Requirements 1.9.

The strategy biases toward *classifiable* candle pairs — clean OHLCV records
over a shared, sufficiently long timestamp grid with a small resolved config —
so the Relative_Strength_Label path is reached on the vast majority of examples
(the property is vacuously satisfied on the rarer Unavailable_Marker path). The
sys.path / import pattern mirrors the sibling ``test_rs_*_properties.py``
modules: the service directory (one level up) is prepended to ``sys.path`` so
``rs`` is importable when pytest runs from anywhere.
"""

import os
import sys

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from rs import RSConfig, classify_relative_strength  # noqa: E402

# Expected enumerations (the design's total mapping ranges), pinned locally so
# the test asserts against the spec rather than whatever the calculator emits.
_INDEX_DIRECTIONS = {"up", "down", "flat"}
_RELATIVE_STRENGTH_STATES = {"leader", "inline", "laggard"}
_RS_MEASURE_FIELDS = ("rs_ratio", "rs_ratio_slope", "relative_return",
                      "correlation", "beta")

# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# Finite prices kept in a sane, non-degenerate band so generated sequences
# frequently reach the Relative_Strength_Label path.
_finite_price = st.floats(
    min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False
)


@st.composite
def _clean_candle(draw, ts):
    """A well-formed OHLCV candle dict at timestamp ``ts`` with finite fields."""
    a = draw(_finite_price)
    b = draw(_finite_price)
    c = draw(_finite_price)
    d = draw(_finite_price)
    low = min(a, b, c, d)
    high = max(a, b, c, d)
    open_ = draw(st.floats(min_value=low, max_value=high, allow_nan=False,
                           allow_infinity=False))
    close = draw(st.floats(min_value=low, max_value=high, allow_nan=False,
                           allow_infinity=False))
    return {
        "timestamp_ms": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": draw(st.floats(min_value=0.0, max_value=1e9, allow_nan=False,
                                 allow_infinity=False)),
    }


@st.composite
def _classifiable_pair(draw):
    """A symbol/benchmark candle pair that frequently reaches the label path.

    Both sequences share one ascending timestamp grid (so they time-align fully)
    of length comfortably above the configured gate, and every candle is a clean
    OHLCV record, so the calculator can compute measures and emit a
    Relative_Strength_Label on the vast majority of examples. The rarer
    Unavailable_Marker path (e.g. degenerate prices) leaves the property
    vacuously satisfied.
    """
    n = draw(st.integers(min_value=35, max_value=70))
    base = draw(st.integers(min_value=0, max_value=10_000))
    step = draw(st.integers(min_value=1, max_value=60))
    timestamps = [base + i * step for i in range(n)]
    symbol_candles = [draw(_clean_candle(ts)) for ts in timestamps]
    benchmark_candles = [draw(_clean_candle(ts)) for ts in timestamps]
    return symbol_candles, benchmark_candles


@st.composite
def _config(draw):
    """A valid ``RSConfig`` with a small gate so the label path is reachable.

    Lookbacks / windows / ``min_candles`` are kept small relative to the
    generated sequence length so the configured gate
    (``max(min_candles, largest_lookback)``) is frequently cleared. The strict
    ``laggard_cutoff < leader_cutoff`` ordering the resolver guarantees is
    preserved.
    """
    leader = draw(st.floats(min_value=-0.5, max_value=1.0, allow_nan=False,
                            allow_infinity=False))
    laggard = draw(st.floats(min_value=-1.0, max_value=leader - 0.001,
                             allow_nan=False, allow_infinity=False))
    return RSConfig(
        lookback=draw(st.integers(min_value=2, max_value=20)),
        corr_window=draw(st.integers(min_value=2, max_value=20)),
        leader_cutoff=leader,
        laggard_cutoff=laggard,
        index_flat_band=draw(st.floats(min_value=0.0, max_value=1.0,
                                       allow_nan=False, allow_infinity=False)),
        min_candles=draw(st.integers(min_value=2, max_value=25)),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Property 7: Absent proposed direction yields a neutral alignment with the
#             other fields present
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 7: Absent proposed direction yields a neutral alignment with the other fields present
@settings(max_examples=100, deadline=None,
          suppress_health_check=[HealthCheck.large_base_example])
@given(pair=_classifiable_pair(), config=_config())
def test_property_7_absent_direction_yields_neutral_alignment(pair, config):
    """Feature: relative-strength-context, Property 7: Absent proposed direction
    yields a neutral alignment with the other fields present.

    When ``classify_relative_strength`` is called with ``proposed_direction=None``
    and it returns a Relative_Strength_Label, the Alignment is reported as
    ``neutral`` while the Index_Direction, the Relative_Strength_State, and the
    named measures are all present.

    Validates: Requirements 1.9
    """
    symbol_candles, benchmark_candles = pair

    result = classify_relative_strength(
        symbol_candles,
        benchmark_candles,
        config,
        proposed_direction=None,
        symbol="RELIANCE",
        benchmark="NIFTY 50",
        timeframe="15m",
    )

    assert isinstance(result, dict)

    # Property is about the label path; the Unavailable_Marker path satisfies it
    # vacuously (no alignment/states are present, by design — Requirement 5.3).
    if result.get("unavailable"):
        return

    # A label was returned: the Alignment must be neutral for an absent
    # proposed direction (Requirement 1.9).
    assert result["alignment"] == "neutral", (
        f"absent proposed direction must yield neutral alignment, "
        f"got {result['alignment']!r}"
    )

    # ...while the Index_Direction, the Relative_Strength_State, and the named
    # measures remain present (Requirement 1.9).
    assert result["index_direction"] in _INDEX_DIRECTIONS, (
        f"index_direction {result.get('index_direction')!r} not in "
        f"{_INDEX_DIRECTIONS}"
    )
    assert result["relative_strength_state"] in _RELATIVE_STRENGTH_STATES, (
        f"relative_strength_state {result.get('relative_strength_state')!r} not "
        f"in {_RELATIVE_STRENGTH_STATES}"
    )

    measures = result["measures"]
    assert isinstance(measures, dict)
    for field in _RS_MEASURE_FIELDS:
        assert field in measures, f"measure {field!r} missing from label"
        value = measures[field]
        assert value is None or isinstance(value, (int, float)), (
            f"measure {field!r} must be a finite number or null, got {value!r}"
        )
