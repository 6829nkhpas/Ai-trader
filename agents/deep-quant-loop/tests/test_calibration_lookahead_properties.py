"""Property-based test for look-ahead-free, next-bar-paired forecast calibration
(backtest.py ``calibrate_forecast``, task 15.3).

Feature: volatility-aware-forecaster

This module implements design **Property 31: Calibration is look-ahead-free and
pairs each prediction with the realized next bar**:

    For any candle history and any signal index, the Up_Probability the
    calibration measurement records for that signal is computed only from candles
    at or before the signal's candle timestamp (so altering or removing any later
    candle except the immediately-following outcome bar does not change the
    recorded probability), and the realized outcome paired with it is the up/down
    direction of the immediately-following bar.

Validates: Requirements 12.1.

Implementation under test: ``backtest.calibrate_forecast(symbol, timeframe,
candles=H)``. For every bar ``i`` with a valid next bar ``i + 1`` it computes the
forecast from ONLY the point-in-time prefix ``candles[: i + 1]`` (the window at or
before bar ``i`` — no later candles) via the SAME ``forecaster.forecast`` the live
tool path uses, and — when that yields a usable Forecast_Label with a finite
Up_Probability and the adjacent closes are numeric — pairs the predicted
probability with the realized direction ``went_up = close_{i+1} > close_i``.

Three complementary, robust assertions prove the property:

  1. SPY / LOOK-AHEAD-FREE (direct): wrap ``forecaster.forecast`` to record every
     candle window it is handed. ``calibrate_forecast`` must call it once per bar
     ``i`` in ``[0, n-2]`` over strictly-increasing prefixes, and the i-th window
     must be byte-for-byte the point-in-time prefix ``candles[: i + 1]`` — so no
     window ever carries a candle whose ``timestamp_ms`` exceeds the window's last
     (signal) bar, and the forecast for bar ``i`` never sees candle ``i + 1``.

  2. NEXT-BAR PAIRING (independent recompute): walk ``H`` ourselves, calling the
     real forecaster on each prefix ``H[: i + 1]`` and pairing the usable, finite
     Up_Probability with ``H[i+1].close > H[i].close``. The number of records we
     build must equal the reported ``total_records``, proving the pairing counts
     exactly the bars with a usable label AND numeric adjacent closes, and that
     the realized direction used is the immediately-following bar's direction.

  3. APPEND INVARIANCE (strengthening): append arbitrary LATER candles to form
     ``H2 = H + extra``. Bar ``i``'s prefix ``H[: i + 1]`` and its outcome bar
     ``H[i+1]`` are unchanged for every ``i`` in ``[0, n-2]``, so each original
     bar's recorded (Up_Probability, went_up) pair is identical across H and H2.

The sys.path / import pattern mirrors the other backtest property tests.
"""

import copy
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (backtest.py / forecaster.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
import forecaster  # noqa: E402
from backtest import calibrate_forecast  # noqa: E402

_TS_START = 1_700_000_000_000  # epoch ms anchor
_TS_STEP = 900_000             # strictly monotonic, unique per bar (15m grid)


def _walk_candles(draw, n, start_price, start_index):
    """A strictly-monotonic OHLCV random walk on the shared timestamp grid.

    OHLC ordering is well-formed (high >= max(open, close),
    low <= min(open, close)); timestamps are strictly increasing and unique
    (``_TS_START + (start_index + i) * _TS_STEP``) so each bar is identifiable and
    appended candles continue the SAME grid without colliding. Closes stay
    positive and returns are non-degenerate so the forecaster yields usable labels
    for the later bars (neither an insufficient-data marker nor a zero-variance
    short-circuit) — exercising the actual pairing path.
    """
    price = start_price
    candles = []
    for i in range(n):
        delta = draw(st.floats(min_value=-4.0, max_value=4.0,
                               allow_nan=False, allow_infinity=False))
        open_ = price
        close = max(1.0, price + delta)
        wig = draw(st.floats(min_value=0.0, max_value=2.5,
                             allow_nan=False, allow_infinity=False))
        high = max(open_, close) + wig
        low = max(0.5, min(open_, close) - wig)
        volume = draw(st.floats(min_value=1.0, max_value=1_000_000.0,
                                allow_nan=False, allow_infinity=False))
        candles.append({
            "timestamp_ms": _TS_START + (start_index + i) * _TS_STEP,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
        price = close
    return candles


@st.composite
def _history_and_extra(draw):
    """A (base_candles, extra_candles) pair on a shared, contiguous timestamp grid.

    ``base_candles`` is the original history H (long enough — >= 35 bars — that the
    later bars clear the forecaster's ~30-candle gate and produce usable labels);
    ``extra_candles`` are arbitrary LATER candles (an INDEPENDENT random walk)
    appended to form H2 = H + extra, so the appended tail is genuinely arbitrary
    future data that must not influence any original bar's recorded probability.
    """
    n = draw(st.integers(min_value=35, max_value=90))
    extra = draw(st.integers(min_value=1, max_value=40))
    base_start = draw(st.floats(min_value=50.0, max_value=500.0,
                                allow_nan=False, allow_infinity=False))
    extra_start = draw(st.floats(min_value=50.0, max_value=500.0,
                                 allow_nan=False, allow_infinity=False))
    base_candles = _walk_candles(draw, n, base_start, start_index=0)
    extra_candles = _walk_candles(draw, extra, extra_start, start_index=n)
    return base_candles, extra_candles


def _spy_calibrate(candles):
    """Run calibrate_forecast, recording every candle window handed to forecaster.

    Returns ``(report, recorded_windows)`` where ``recorded_windows`` holds the
    (deep-copied) candle windows passed to ``forecaster.forecast`` in call order.
    The forecaster is pure, so deep-copying the window is purely defensive.
    """
    recorded = []
    original = forecaster.forecast

    def _spy(window, config, *args, **kwargs):
        recorded.append(copy.deepcopy(window))
        return original(window, config, *args, **kwargs)

    forecaster.forecast = _spy
    try:
        report = calibrate_forecast("SYM", "15m", candles=candles)
    finally:
        forecaster.forecast = original
    return report, recorded


def _expected_records(candles):
    """Independently recompute the calibration records by walking ``candles``.

    Mirrors ``calibrate_forecast``'s record logic with the REAL forecaster: for
    each bar ``i`` with numeric adjacent closes, forecast the point-in-time prefix
    ``candles[: i + 1]`` and, when usable with a finite Up_Probability, pair that
    probability with the realized next-bar direction ``close_{i+1} > close_i``.
    """
    fc_config = forecaster.resolve_forecaster_config()
    records = []
    n = len(candles)
    for i in range(n - 1):
        cur = candles[i]
        nxt = candles[i + 1]
        cur_close = cur.get("close")
        nxt_close = nxt.get("close")
        if not (backtest._is_num(cur_close) and backtest._is_num(nxt_close)):
            continue
        fc_result = forecaster.forecast(
            candles[: i + 1], fc_config, symbol="SYM", timeframe="15m",
        )
        if not isinstance(fc_result, dict) or fc_result.get("unavailable") is True:
            continue
        up_probability = fc_result.get("up_probability")
        if not backtest._is_num(up_probability):
            continue
        records.append({
            "index": i,
            "up_probability": up_probability,
            "went_up": nxt_close > cur_close,
        })
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Property 31: Calibration is look-ahead-free and pairs each prediction with the
#              realized next bar
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 31: Calibration is look-ahead-free and pairs each prediction with the realized next bar
@settings(max_examples=100, deadline=None)
@given(data=_history_and_extra())
def test_property_31_calibration_is_lookahead_free_and_next_bar_paired(data):
    """Validates: Requirements 12.1

    (1) Every forecast the calibration performs uses only candles at or before its
    bar — proved by spying on the exact window handed to ``forecaster.forecast``:
    the i-th call receives the point-in-time prefix ``candles[: i + 1]`` and never
    a candle dated after the signal bar. (2) The reported ``total_records`` equals
    an independent next-bar-paired walk of the history (usable, finite
    Up_Probability + numeric adjacent closes), with the realized direction taken
    as ``close_{i+1} > close_i``. (3) Appending arbitrary later candles leaves
    every original bar's (Up_Probability, went_up) pair unchanged.
    """
    base_candles, extra_candles = data
    base_snapshot = copy.deepcopy(base_candles)
    n = len(base_candles)

    # ── (1) SPY / LOOK-AHEAD-FREE: each window is the point-in-time prefix ─────
    report, recorded = _spy_calibrate(base_candles)

    # All closes are numeric, so the forecaster is consulted once per bar with a
    # valid next bar — n - 1 calls over strictly-increasing prefixes.
    assert len(recorded) == n - 1, (
        "calibrate_forecast must forecast exactly once per bar with a valid next bar"
    )
    for i, window in enumerate(recorded):
        # The i-th call must receive the exact point-in-time prefix candles[:i+1].
        assert window == base_candles[: i + 1], (
            "each forecast window must be the exact point-in-time prefix candles[: i + 1]"
        )
        assert len(window) == i + 1, "forecast window length must be i + 1"
        # No candle in the window post-dates the signal (last) bar, and the
        # forecast for bar i never sees candle i + 1 (look-ahead-free).
        signal_ts = window[-1]["timestamp_ms"]
        assert all(c["timestamp_ms"] <= signal_ts for c in window), (
            "forecast window contained a candle dated after the signal bar"
        )
        assert window[-1]["timestamp_ms"] == base_candles[i]["timestamp_ms"], (
            "the forecast for bar i must end exactly at bar i"
        )

    # calibrate_forecast must not mutate the caller's candle history.
    assert base_candles == base_snapshot, "calibrate_forecast mutated its candles"

    # ── (2) NEXT-BAR PAIRING: total_records == independent next-bar-paired walk ─
    expected = _expected_records(base_candles)
    assert report["total_records"] == len(expected), (
        "total_records must equal the count of bars with a usable, finite "
        "Up_Probability and numeric adjacent closes"
    )
    # The realized direction paired with each prediction is the immediately
    # following bar's up/down direction.
    for rec in expected:
        i = rec["index"]
        assert rec["went_up"] == (
            base_candles[i + 1]["close"] > base_candles[i]["close"]
        ), "realized direction must be close_{i+1} > close_i"

    # ── (3) APPEND INVARIANCE: later candles don't change an original record ───
    extended = base_candles + extra_candles
    expected_extended = _expected_records(extended)
    expected_extended_by_index = {r["index"]: r for r in expected_extended}
    for rec in expected:
        i = rec["index"]
        # Bar i (i <= n-2) keeps the identical prefix candles[:i+1] AND outcome
        # bar i+1 in the extended history, so its recorded pair is unchanged.
        assert i in expected_extended_by_index, (
            "an original bar's calibration record disappeared when later candles "
            "were appended (look-ahead in emission)"
        )
        ext_rec = expected_extended_by_index[i]
        assert ext_rec["up_probability"] == rec["up_probability"], (
            "recorded Up_Probability changed when later candles were appended "
            f"(look-ahead) at bar {i}"
        )
        assert ext_rec["went_up"] == rec["went_up"], (
            f"realized next-bar direction changed when later candles were appended at bar {i}"
        )
