"""Property-based test for insufficient-candle unavailability (forecaster.py, task 4.6).

Feature: volatility-aware-forecaster

This module implements design **Property 13: Insufficient valid candles yield an
Unavailable_Marker with counts**:

    For any candle sequence whose count of *valid* candles is fewer than the
    configured minimum required for the longest lookback
    (``required = max(config.min_candles, config.largest_lookback)``),
    ``forecast(candles, config, ...)`` returns an Unavailable_Marker
    (``{"unavailable": true, "reason": ...}``) whose reason identifies the
    insufficient-data condition and includes BOTH the count of valid candles
    received and the configured minimum required, omits
    ``projected_direction`` / ``up_probability`` / ``expected_move_atr`` /
    ``forecast_confidence`` / ``forecast_alignment`` rather than fabricating them,
    leaves its input unmodified, and never raises.

Validates: Requirements 4.1, 6.2.

``forecast`` reads candles through ``regime._valid_ohlc_rows`` to count the
*valid* candles, then gates on ``required = max(min_candles, largest_lookback)``.
The strategy below draws ``n_valid`` strictly below that gate, builds exactly
``n_valid`` well-formed OHLCV candles, then pads the sequence with an arbitrary
number of guaranteed-invalid (non-finite / non-numeric) candles interleaved at
arbitrary positions — so the *valid* count stays exactly ``n_valid`` while the
raw length may exceed ``required``. This covers both the "too few candles to
begin with" route and the "enough rows, but too few that are valid" route.

The sys.path / import pattern and the candle generators mirror the sibling
``test_forecaster_*_properties.py`` modules (notably the determinism and
non-finite-exclusion tests). Config comes from ``resolve_forecaster_config()``
as the task requires.
"""

import copy
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (forecaster.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from forecaster import forecast, resolve_forecaster_config  # noqa: E402

# The documented-default configuration drives the sufficiency gate. With the
# defaults (min_candles=30, largest_lookback=21) the gate ``required`` is 30, so
# every generated sequence below carries fewer than 30 valid candles.
_CONFIG = resolve_forecaster_config()
_REQUIRED = max(_CONFIG.min_candles, _CONFIG.largest_lookback)

# The five projection fields an Unavailable_Marker must omit (Requirement 6.3).
_PROJECTION_FIELDS = (
    "projected_direction",
    "up_probability",
    "expected_move_atr",
    "forecast_confidence",
    "forecast_alignment",
)


# ── Generators ──────────────────────────────────────────────────────────────

# Finite, positive, bounded close prices: a well-formed candle is accepted by
# ``regime._parse_ohlc`` and therefore counts as a valid candle.
_price = st.floats(
    min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False
)


@st.composite
def _valid_candle(draw):
    """A dict OHLCV candle whose every field is finite and ``high >= low``."""
    open_ = draw(_price)
    close = draw(_price)
    high = max(open_, close) + 1.0
    low = max(min(open_, close) - 1.0, 0.5)
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": draw(
            st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False)
        ),
    }


# Values that make an OHLCV field non-finite or non-numeric, guaranteeing the
# carrying candle is excluded by ``regime._parse_ohlc`` (so it never adds to the
# valid count).
_bad_value = st.sampled_from(
    [float("nan"), float("inf"), float("-inf"), None, "not-a-number", "12.5", "", True, [], {}]
)


@st.composite
def _bad_candle(draw):
    """A candle guaranteed to be excluded: a required OHLCV field is invalid."""
    candle = dict(draw(_valid_candle()))
    field = draw(st.sampled_from(["open", "high", "low", "close"]))
    candle[field] = draw(_bad_value)
    if draw(st.booleans()):
        candle["volume"] = draw(_bad_value)
    return candle


@st.composite
def _insufficient_candles(draw):
    """Draw ``(candles, n_valid)`` with too few *valid* candles for the gate.

    ``n_valid`` is drawn strictly below ``_REQUIRED`` (``0 <= n_valid <
    required``). Exactly ``n_valid`` well-formed candles are built, then an
    arbitrary number of guaranteed-invalid candles are interleaved at arbitrary
    positions — so the valid count is exactly ``n_valid`` regardless of the raw
    length.
    """
    n_valid = draw(st.integers(min_value=0, max_value=_REQUIRED - 1))
    candles = [draw(_valid_candle()) for _ in range(n_valid)]

    # Interleave guaranteed-invalid padding candles at arbitrary positions so the
    # raw length can exceed ``required`` while the valid count stays ``n_valid``.
    bad_candles = draw(st.lists(_bad_candle(), max_size=20))
    for bad in bad_candles:
        idx = draw(st.integers(min_value=0, max_value=len(candles)))
        candles.insert(idx, bad)

    return candles, n_valid


# A proposed trade direction (or its absence): the insufficient-data marker must
# arise regardless of the proposed direction.
_proposed_direction = st.one_of(
    st.none(),
    st.sampled_from(["up", "down", "buy", "sell", "long", "short", "hold", "", "BUY", "Sell"]),
)


# ─────────────────────────────────────────────────────────────────────────────
# Property 13: Insufficient valid candles yield an Unavailable_Marker with counts
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 13: Insufficient valid candles yield an Unavailable_Marker with counts
@settings(max_examples=200, deadline=None)
@given(data=_insufficient_candles(), proposed_direction=_proposed_direction)
def test_property_13_insufficient_candles_unavailable(data, proposed_direction):
    """Feature: volatility-aware-forecaster, Property 13: Insufficient valid
    candles yield an Unavailable_Marker with counts.

    For any candle sequence whose valid-candle count is below the configured gate
    ``max(min_candles, largest_lookback)``, ``forecast`` returns an
    Unavailable_Marker whose reason identifies the insufficient-data condition
    and includes BOTH the received valid count and the required count, omits the
    five projection fields, leaves its input unmodified, and never raises.

    Validates: Requirements 4.1, 6.2
    """
    candles, n_valid = data

    # Snapshot the input to confirm purity (no mutation while forecasting).
    candles_snapshot = copy.deepcopy(candles)

    # Must never raise (Requirements 4.1, 6.2): the call itself is the assertion.
    result = forecast(
        candles,
        _CONFIG,
        proposed_direction=proposed_direction,
        symbol="RELIANCE",
        timeframe="15m",
    )

    # The result is an honest Unavailable_Marker.
    assert isinstance(result, dict)
    assert result.get("unavailable") is True, (
        f"expected an Unavailable_Marker for {n_valid} valid candles "
        f"(< required {_REQUIRED}); got {result!r}"
    )

    # The reason identifies the insufficient-data condition (Requirement 4.1) and
    # cites BOTH the received valid count and the required count (Requirements
    # 4.1, 6.2).
    reason = result.get("reason", "")
    assert isinstance(reason, str)
    assert "insufficient" in reason.lower(), (
        f"reason should identify the insufficient-data condition; got {reason!r}"
    )
    assert str(n_valid) in reason, (
        f"reason should include the received valid count {n_valid}; got {reason!r}"
    )
    assert str(_REQUIRED) in reason, (
        f"reason should include the required count {_REQUIRED}; got {reason!r}"
    )

    # An Unavailable_Marker omits the five projection fields rather than
    # fabricating them (Requirement 6.3 referenced by 6.2 / design AD-5).
    for field in _PROJECTION_FIELDS:
        assert field not in result, (
            f"Unavailable_Marker must omit {field!r}; got {result!r}"
        )

    # The input is left unmodified (purity).
    assert candles == candles_snapshot, "forecast mutated its candle input"
