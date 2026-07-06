"""Property-based test that an Unavailable_Marker carries no fabricated states
(rs.py ``classify_relative_strength``, task 3.10).

Feature: relative-strength-context

This module implements design **Property 12: An Unavailable_Marker never carries
fabricated states**:

    For every path on which ``classify_relative_strength`` returns an
    Unavailable_Marker (too few aligned candles, all-null measures, empty
    sequences, or timestamp-mismatched sequences with no common bars), the
    result flags ``unavailable`` with a non-empty ``reason`` and OMITS
    ``index_direction`` / ``relative_strength_state`` / ``alignment`` rather than
    populating them with default / placeholder / otherwise fabricated values.

Validates: Requirements 5.3.

A candle is a dict-like OHLCV record carrying ``timestamp_ms`` plus
open/high/low/close/volume (matching how ``rs.py`` reads candles via
``c.get(...)``). The generator drives ``classify_relative_strength`` down each
distinct unavailable path with NO mocking — the calculator is pure:

  * ``insufficient`` — fewer aligned candles than the sufficiency gate requires,
                       so the gate fires (Requirements 3.1, 5.2).
  * ``all-null``     — enough aligned candles to clear the gate, but the
                       benchmark closes are all zero, so every measure has a
                       zero denominator and is ``None`` (Requirement 3.6).
  * ``empty``        — both sequences empty -> zero aligned candles.
  * ``mismatched``   — symbol and benchmark timestamps are disjoint, so there are
                       zero common-timestamp (aligned) candles (Requirement 3.7).

The sys.path / import pattern mirrors the sibling ``test_rs_*_properties.py``
modules.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (rs.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from rs import (  # noqa: E402
    classify_relative_strength,
    resolve_rs_config,
)

# The categorical state fields that an Unavailable_Marker must NEVER carry
# (Requirement 5.3): they are omitted, never defaulted or fabricated.
_FABRICATABLE_STATE_FIELDS = ("index_direction", "relative_strength_state", "alignment")

# The default resolved config gates classification on
# ``max(min_candles=30, largest_lookback=max(20, 30) + 1 = 31) = 31`` aligned
# candles. "Insufficient" means fewer than that; "all-null" needs at least that
# many (so it clears the gate and instead trips the all-null measure path).
_CONFIG = resolve_rs_config()
_REQUIRED = max(_CONFIG.min_candles, _CONFIG.largest_lookback)


def _candle(ts, price):
    """A single valid, finite OHLCV candle at ``price`` with timestamp ``ts``."""
    return {
        "timestamp_ms": ts,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": 1000.0,
    }


@st.composite
def _insufficient(draw):
    """Aligned but too-short: fewer than the gate's required aligned candles.

    Both sequences share the same timestamps and carry well-formed finite OHLCV;
    only the count is short, so the sufficiency gate fires (R3.1, R5.2).
    """
    n = draw(st.integers(min_value=0, max_value=_REQUIRED - 1))
    base = draw(
        st.floats(min_value=10.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)
    )
    symbol = [_candle(i, base + i) for i in range(n)]
    benchmark = [_candle(i, base + 2 * i + 1) for i in range(n)]
    return symbol, benchmark


@st.composite
def _all_null(draw):
    """Enough aligned candles to clear the gate, but every measure is ``None``.

    The benchmark closes are all zero, so the RS ratio, relative return,
    correlation, beta, and index return each hit a zero denominator and become
    ``None``; ``classify_relative_strength`` then returns an Unavailable_Marker
    citing "no relative-strength measure could be computed" (R3.6).
    """
    n = draw(st.integers(min_value=_REQUIRED, max_value=_REQUIRED + 30))
    base = draw(
        st.floats(min_value=10.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)
    )
    symbol = [_candle(i, base + i) for i in range(n)]
    benchmark = [_candle(i, 0.0) for i in range(n)]
    return symbol, benchmark


# Both sequences empty -> zero aligned candles.
_empty = st.just(([], []))


@st.composite
def _mismatched(draw):
    """Disjoint timestamps -> zero common-timestamp (aligned) candles (R3.7).

    Both sequences are individually long enough to clear the gate, but they share
    no timestamp, so time-alignment yields zero aligned candles and the
    sufficiency gate fires.
    """
    n = draw(st.integers(min_value=_REQUIRED, max_value=_REQUIRED + 30))
    base = draw(
        st.floats(min_value=10.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)
    )
    # Symbol timestamps 0..n-1; benchmark timestamps far away (10_000+), so the
    # intersection is empty regardless of n.
    symbol = [_candle(i, base + i) for i in range(n)]
    benchmark = [_candle(10_000 + i, base + i) for i in range(n)]
    return symbol, benchmark


_scenarios = st.one_of(_insufficient(), _all_null(), _empty, _mismatched())

# Proposed direction, including absent / non-directional values — none of which
# may cause a fabricated state to appear on an unavailable result.
_DIRECTION = st.one_of(
    st.none(),
    st.sampled_from(["BUY", "SELL", "HOLD", "buy", "sell", "", "weird"]),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 12: An Unavailable_Marker never carries fabricated states
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 12: An Unavailable_Marker never carries fabricated states
@settings(max_examples=100, deadline=None)
@given(
    scenario=_scenarios,
    proposed_direction=_DIRECTION,
)
def test_property_12_unavailable_marker_carries_no_fabricated_states(
    scenario, proposed_direction
):
    """Feature: relative-strength-context, Property 12: An Unavailable_Marker
    never carries fabricated states.

    For every path that drives ``classify_relative_strength`` to an
    Unavailable_Marker (insufficient aligned candles, all-null measures, empty
    sequences, or timestamp-mismatched sequences), the result flags
    ``unavailable`` with a non-empty ``reason`` and OMITS ``index_direction`` /
    ``relative_strength_state`` / ``alignment`` rather than populating them with
    default / placeholder / fabricated values. Never raises.

    Validates: Requirements 5.3
    """
    symbol_candles, benchmark_candles = scenario

    result = classify_relative_strength(
        symbol_candles,
        benchmark_candles,
        _CONFIG,
        proposed_direction=proposed_direction,
        symbol="RELIANCE",
        benchmark="NIFTY 50",
        timeframe="15m",
    )

    # The calculator must never raise and always return a dict.
    assert isinstance(result, dict), f"calculator result is not a dict: {result!r}"

    # Every scenario here is engineered to be unavailable.
    assert result.get("unavailable") is True, (
        f"expected an Unavailable_Marker, got: {result!r}"
    )

    # An honest marker must cite a (non-empty) reason for being unavailable.
    reason = result.get("reason")
    assert isinstance(reason, str) and reason.strip(), (
        f"Unavailable_Marker is missing a non-empty reason: {result!r}"
    )

    # The core assertion: the marker must OMIT the categorical state fields — no
    # default, placeholder, or otherwise fabricated index_direction /
    # relative_strength_state / alignment (Requirement 5.3).
    for field in _FABRICATABLE_STATE_FIELDS:
        assert field not in result, (
            f"Unavailable_Marker fabricated '{field}'={result.get(field)!r} "
            f"(must be omitted): {result!r}"
        )
