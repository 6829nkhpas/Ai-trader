"""Property-based test for non-finite candle exclusion (forecaster.py, task 2.3).

Feature: volatility-aware-forecaster

This module implements design **Property 12: Non-finite candles are excluded
without affecting the result**:

    A candle carrying a non-finite (NaN / +/-inf) or non-numeric (None / string /
    bool / container) OHLCV field is excluded from EVERY estimation computation.
    So interleaving such corrupt candles anywhere within an otherwise-valid candle
    sequence does not change the result of any estimation function, and none of
    them raise:

        compute_log_returns(clean, lookback)  == compute_log_returns(polluted, lookback)
        compute_drift(clean, config)          == compute_drift(polluted, config)
        compute_volatility(clean, config)     == compute_volatility(polluted, config)
        compute_atr(clean, period)            == compute_atr(polluted, period)

    where "polluted" is the clean candle sequence with extra guaranteed-invalid
    candles interleaved at arbitrary positions (the valid candles keep their
    original relative order).

Validates: Requirements 4.2.

The estimation functions (``compute_log_returns`` / ``compute_drift`` /
``compute_volatility`` / ``compute_atr``) all read candles through
``regime._valid_ohlc_rows``, which drops candles whose ``open`` / ``high`` /
``low`` / ``close`` (or a present ``volume``) is non-finite/non-numeric. The
property exercises that shared exclusion behaviour end-to-end and also confirms
the calls leave their inputs unmodified (purity).

The strategies and sys.path bootstrap mirror the sibling
``test_of_nonfinite_exclusion_properties.py`` and
``test_rs_nonfinite_exclusion_properties.py`` modules.
"""

import copy
import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (forecaster.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import forecaster  # noqa: E402
from forecaster import (  # noqa: E402
    compute_atr,
    compute_drift,
    compute_log_returns,
    compute_volatility,
    resolve_forecaster_config,
)

# Resolve the documented-default configuration once; its drift/vol lookback (20)
# and ATR period (14) drive the estimation calls. Reusing the single resolved
# configuration on every call makes any difference attributable solely to the
# interleaved bad candles.
_CONFIG = resolve_forecaster_config()

# Enough valid candles to make every estimate computable: the most demanding
# estimate needs ``max(drift_lookback, vol_lookback) + 1`` (= 21) closes, and the
# ATR needs ``atr_period + 1`` (= 15) rows; an upper bound keeps generation cheap.
_MIN_VALID = 35
_MAX_VALID = 70


# ── Generators ────────────────────────────────────────────────────────────────

# Finite, positive, bounded close prices. Bounded well away from overflow while
# spanning a realistic price range; strictly positive so the log-return window is
# never rejected for a non-positive close.
_price = st.floats(
    min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False
)


@st.composite
def _valid_candle(draw):
    """A dict OHLCV candle whose every field is finite and ``high >= low``.

    ``regime._parse_ohlc`` accepts this candle (every estimate includes it).
    High/low bracket the open/close so the record is a plausible bar.
    """
    open_ = draw(_price)
    close = draw(_price)
    high = max(open_, close) + 1.0
    low = max(min(open_, close) - 1.0, 0.5)
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": draw(st.floats(min_value=0.0, max_value=1e9,
                                 allow_nan=False, allow_infinity=False)),
    }


# Values that make an OHLCV field non-finite or non-numeric. Each guarantees the
# carrying candle is excluded by ``regime._parse_ohlc`` (NaN/inf fail
# ``isfinite``; None/str/bool/containers are non-numeric — note ``bool`` is
# excluded by the repo's finite-number convention).
_bad_value = st.sampled_from(
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        None,
        "not-a-number",
        "12.5",
        "",
        True,
        False,
        [],
        {},
    ]
)


@st.composite
def _bad_candle(draw):
    """A candle guaranteed to be excluded: at least one OHLCV field carries a
    non-finite / non-numeric value.

    ``open`` / ``high`` / ``low`` / ``close`` are each required by
    ``regime._parse_ohlc`` — corrupting any one guarantees the candle is dropped
    from every estimate (Requirement 4.2). A present-but-bad ``volume`` is also a
    valid exclusion trigger, so it is optionally corrupted too.
    """
    candle = dict(draw(_valid_candle()))
    field = draw(st.sampled_from(["open", "high", "low", "close"]))
    candle[field] = draw(_bad_value)
    if draw(st.booleans()):
        candle["volume"] = draw(_bad_value)
    return candle


@st.composite
def _clean_and_polluted(draw):
    """Produce ``(clean_candles, polluted_candles)``.

    ``polluted_candles`` is ``clean_candles`` with 0..15 guaranteed-invalid
    candles inserted at arbitrary positions, so the valid candles retain their
    original relative order.
    """
    n = draw(st.integers(min_value=_MIN_VALID, max_value=_MAX_VALID))
    clean = [draw(_valid_candle()) for _ in range(n)]

    polluted = list(clean)
    bad_candles = draw(st.lists(_bad_candle(), max_size=15))
    for bad in bad_candles:
        idx = draw(st.integers(min_value=0, max_value=len(polluted)))
        polluted.insert(idx, bad)

    return clean, polluted


def _equal(a, b):
    """Equality for an estimate result (``None`` or a finite float).

    NaN never appears (the estimators return finite floats or ``None``), but the
    comparison is NaN-safe defensively.
    """
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return True
    return a == b


# ─────────────────────────────────────────────────────────────────────────────
# Property 12 (task 2.3): Non-finite candles are excluded without affecting result
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 12: Non-finite candles are excluded without affecting the result
@settings(max_examples=150, deadline=None)
@given(data=_clean_and_polluted())
def test_property_12_non_finite_candles_excluded(data):
    """Feature: volatility-aware-forecaster, Property 12: Non-finite candles are
    excluded without affecting the result — for a valid candle sequence and any
    interleaving of candles carrying non-finite / non-numeric OHLCV fields, each
    estimation function (``compute_log_returns`` / ``compute_drift`` /
    ``compute_volatility`` / ``compute_atr``) returns a result equal to the
    result of computing on only the valid candles, and never raises.

    Validates: Requirements 4.2
    """
    clean, polluted = data

    # Snapshot the inputs so we can confirm the calls did not mutate them
    # (exclusion must be non-destructive — Requirement 4.2 / purity).
    clean_snapshot = copy.deepcopy(clean)
    polluted_snapshot = copy.deepcopy(polluted)

    drift_lookback = _CONFIG.drift_lookback
    vol_lookback = _CONFIG.vol_lookback
    atr_period = _CONFIG.atr_period

    # Log-returns over both the drift and the volatility lookback must be
    # identical whether or not the corrupt candles were interleaved.
    assert compute_log_returns(polluted, drift_lookback) == \
        compute_log_returns(clean, drift_lookback)
    assert compute_log_returns(polluted, vol_lookback) == \
        compute_log_returns(clean, vol_lookback)

    # Each higher-level estimate must yield an identical result on the polluted
    # sequence as on the clean sequence, and neither call may raise.
    assert _equal(compute_drift(polluted, _CONFIG), compute_drift(clean, _CONFIG))
    assert _equal(compute_volatility(polluted, _CONFIG),
                  compute_volatility(clean, _CONFIG))
    assert _equal(compute_atr(polluted, atr_period), compute_atr(clean, atr_period))

    # The volatility estimate is strictly non-negative whenever it is computable
    # (Requirement 1.3) — a sanity check on the value that survived exclusion.
    vol = compute_volatility(clean, _CONFIG)
    if vol is not None:
        assert vol >= 0.0

    # Neither call may mutate its inputs (purity).
    assert clean == clean_snapshot
    assert polluted == polluted_snapshot
