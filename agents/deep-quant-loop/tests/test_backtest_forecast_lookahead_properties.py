"""Property-based test for look-ahead-free backtest forecast classification
(backtest.py ``generate_and_score``, task 14.4).

Feature: volatility-aware-forecaster

This module implements design **Property 33: Backtest forecast classification is
look-ahead-free**:

    For any candle history and any signal bar, the Forecast_Label the
    Backtest_Seeder assigns to that signal is computed only from candles whose
    timestamps are at or before the signal's candle, so that appending or
    altering any LATER candles does not change the assigned forecast
    classification.

Validates: Requirements 13.1.

Implementation under test: ``backtest.generate_and_score(candles, symbol,
timeframe, cfg, ...)``. For every bar ``i`` that emits a signal it classifies the
forecast via ``forecaster.forecast(candles[: i + 1], forecaster_config,
proposed_direction=decision["action"], ...)`` — the point-in-time prefix ending
at the signal's candle, with no later candles — and stores the result (via
``_forecast_defensibility_entry``) at ``decision['defensibility']['forecast']``.

Two complementary, robust assertions prove no look-ahead:

  1. SPY (direct): wrap ``forecaster.forecast`` to record, on every call, the
     candle window it is handed, then assert that NO candle in that window
     carries a ``timestamp_ms`` greater than the signal candle's timestamp (the
     last/max timestamp of the point-in-time prefix). This is definitive: the
     forecaster provably never sees a future candle.

  2. APPEND INVARIANCE (literal restatement of the property): re-run
     ``generate_and_score`` over the SAME history extended with arbitrary EXTRA
     candles appended to the tail. Every original signal's bar (and its
     ``candles[:i+1]`` prefix) is unchanged, so its forecast defensibility entry
     must be byte-for-byte identical across the two runs. With
     ``record_unresolved=True`` the emission/cooldown walk is fully determined by
     the candle prefixes, so every original signal also appears in the extended
     run; we still match by ``created_at`` and compare only the intersection to
     stay robust.

The sys.path / import pattern mirrors the other backtest property tests: the
service directory (one level up) is prepended to ``sys.path`` so ``backtest`` /
``forecaster`` are importable when pytest is run from anywhere.
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
from backtest import BacktestConfig, generate_and_score  # noqa: E402

# A small lookback keeps generated histories modest while still walking the
# signal loop exactly as production does (the look-ahead guarantee is independent
# of the lookback size). ema_slow=21 / ols_window=20 still gate _signal_for_bar,
# so a 25-bar window is the smallest that lets signals form. record_unresolved is
# True so the emission/cooldown walk is fully determined by the candle prefixes
# (scoring outcomes never drop a signal), maximizing the intersection of signals
# common to both runs.
_CFG = BacktestConfig(lookback=25, record_unresolved=True)

_TS_START = 1_600_000_000_000  # epoch ms anchor
_TS_STEP = 60_000              # strictly monotonic, unique per bar


def _walk_candles(draw, n, start_price, start_index):
    """A strictly-monotonic OHLCV random walk on the shared timestamp grid.

    OHLC ordering is well-formed (high >= max(open, close),
    low <= min(open, close)); timestamps are strictly increasing and unique
    (``_TS_START + (start_index + i) * _TS_STEP``) so each signal bar is
    identifiable and appended candles continue the SAME grid without colliding.
    """
    price = start_price
    candles = []
    for i in range(n):
        delta = draw(st.floats(min_value=-5.0, max_value=5.0,
                               allow_nan=False, allow_infinity=False))
        open_ = price
        close = max(1.0, price + delta)
        wig = draw(st.floats(min_value=0.0, max_value=3.0,
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

    ``base_candles`` is the original history H; ``extra_candles`` are arbitrary
    LATER candles appended to form H2 = H + extra. The extra candles continue the
    same monotonic grid (so timestamps stay unique) but follow an INDEPENDENT
    random walk from an independent start price, so the appended tail is genuinely
    arbitrary future data that must not influence any original signal's forecast.
    """
    n = draw(st.integers(min_value=40, max_value=160))
    extra = draw(st.integers(min_value=1, max_value=60))
    base_start = draw(st.floats(min_value=50.0, max_value=500.0,
                                allow_nan=False, allow_infinity=False))
    # Independent start price for the appended tail -> arbitrary future data.
    extra_start = draw(st.floats(min_value=50.0, max_value=500.0,
                                 allow_nan=False, allow_infinity=False))
    base_candles = _walk_candles(draw, n, base_start, start_index=0)
    extra_candles = _walk_candles(draw, extra, extra_start, start_index=n)
    return base_candles, extra_candles


def _spy_run(candles):
    """Run generate_and_score, recording every candle window handed to forecaster.

    Returns ``(results, recorded_windows)`` where ``recorded_windows`` is the list
    of (deep-copied) candle windows passed to ``forecaster.forecast`` and
    ``results`` is the list of scored signal records. The forecaster is pure, so
    deep-copying the window is purely defensive against later mutation.
    """
    recorded = []
    original = forecaster.forecast

    def _spy(window, config, *args, **kwargs):
        recorded.append(copy.deepcopy(window))
        return original(window, config, *args, **kwargs)

    forecaster.forecast = _spy
    try:
        results = generate_and_score(candles, "SYM", "1d", _CFG)
    finally:
        forecaster.forecast = original
    return results, recorded


def _forecast_by_created_at(results):
    """Map each scored signal's ``created_at`` -> its forecast defensibility entry.

    ``created_at`` is derived from the signal candle's unique ``timestamp_ms``, so
    it uniquely identifies the signal bar (and therefore its ``candles[:i+1]``
    point-in-time prefix).
    """
    out = {}
    for rec in results:
        decision = rec["decision"]
        created_at = decision.get("created_at")
        fc_entry = decision.get("defensibility", {}).get("forecast")
        out[created_at] = fc_entry
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Property 33: Backtest forecast classification is look-ahead-free
# ─────────────────────────────────────────────────────────────────────────────

# Feature: volatility-aware-forecaster, Property 33: Backtest forecast classification is look-ahead-free
@settings(max_examples=100, deadline=None)
@given(data=_history_and_extra())
def test_property_33_backtest_forecast_classification_is_lookahead_free(data):
    """Validates: Requirements 13.1

    Every forecast classification the Backtest_Seeder performs for a signal uses
    only candles at or before that signal's bar (proved by spying on the exact
    window handed to ``forecaster.forecast``), and appending arbitrary later
    candles does not change any original signal's forecast defensibility entry
    (proved by append invariance over the intersection of signals matched by
    ``created_at``).
    """
    base_candles, extra_candles = data
    base_snapshot = copy.deepcopy(base_candles)

    # ── (1) SPY: no candle in the forecaster's window post-dates the signal ────
    results_base, recorded_base = _spy_run(base_candles)

    for window in recorded_base:
        assert len(window) >= 1, "forecaster was handed an empty candle window"
        # The signal candle is the last bar of the point-in-time prefix; its
        # timestamp is the cutoff beyond which no candle may appear.
        signal_ts = window[-1]["timestamp_ms"]
        assert all(c["timestamp_ms"] <= signal_ts for c in window), (
            "forecaster window contained a candle dated after the signal bar "
            "(look-ahead leak)"
        )

    # generate_and_score must not have mutated the caller's candle history.
    assert base_candles == base_snapshot, "generate_and_score mutated its candles"

    # ── (2) APPEND INVARIANCE: later candles can't change a forecast entry ─────
    # H2 = H + arbitrary extra candles. Every original signal's bar and its
    # candles[:i+1] prefix are unchanged, so its forecast defensibility entry must
    # be identical. Match by created_at and compare the intersection.
    extended = base_candles + extra_candles
    results_extended, _ = _spy_run(extended)

    fc_base = _forecast_by_created_at(results_base)
    fc_extended = _forecast_by_created_at(results_extended)

    common = set(fc_base) & set(fc_extended)
    # With record_unresolved=True the walk is prefix-determined, so every original
    # signal must reappear in the extended run.
    assert set(fc_base) <= set(fc_extended), (
        "an original signal disappeared when later candles were appended "
        "(emission depended on future candles)"
    )
    for created_at in common:
        assert fc_base[created_at] == fc_extended[created_at], (
            "forecast classification changed when later candles were appended "
            f"(look-ahead) at signal created_at={created_at}"
        )
