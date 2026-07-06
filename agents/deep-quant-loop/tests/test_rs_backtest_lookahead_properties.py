"""Property-based test for look-ahead-free backtest relative-strength
classification (backtest.py ``generate_and_score``, task 13.4).

Feature: relative-strength-context

This module implements design **Property 27: Backtest relative-strength
classification is look-ahead-free**:

    For any symbol candle history, any benchmark candle series, and any signal
    index, the Relative_Strength_Label the Backtest_Seeder assigns to that signal
    is computed only from symbol candles and benchmark candles whose timestamps
    are at or before the signal's candle timestamp, so that altering or removing
    any later candles does not change the assigned classification inputs.

Validates: Requirements 11.1.

Implementation under test: ``backtest.generate_and_score(candles, symbol,
timeframe, cfg, benchmark_candles, benchmark)``. For every bar ``i`` that emits a
signal it classifies relative strength via
``rs.classify_relative_strength(candles[: i + 1],
_candles_at_or_before(benchmark_candles, candles[i]["timestamp_ms"]), ...)`` —
the symbol prefix ending at the signal's candle and the benchmark rows whose
``timestamp_ms`` is at or before that signal candle's timestamp.

Two complementary, robust assertions prove no look-ahead:

  1. SPY (direct): wrap ``rs.classify_relative_strength`` to record, on every
     call, the symbol_window and benchmark_window it is handed, then assert that
     NO candle in EITHER window carries a ``timestamp_ms`` greater than the
     signal candle's timestamp (the last/max timestamp of the symbol prefix).
     This is definitive: the classifier provably never sees a future candle.

  2. TRUNCATION INVARIANCE (literal restatement of the property): re-run
     ``generate_and_score`` over a shortened prefix of the same history (symbol
     AND benchmark) and assert that, for every signal emitted by BOTH runs
     (matched by its signal bar timestamp), the classification INPUTS (the exact
     symbol_window and benchmark_window handed to the classifier) are identical —
     i.e. appending later candles beyond a signal does not change that signal's
     classification inputs.

The sys.path / import pattern mirrors the other backtest property tests: the
service directory (one level up) is prepended to ``sys.path`` so ``backtest`` /
``rs`` are importable when pytest is run from anywhere.
"""

import copy
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (backtest.py / rs.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
import rs  # noqa: E402
from backtest import BacktestConfig, generate_and_score  # noqa: E402

# A small lookback keeps generated histories modest while still walking the
# signal loop exactly as production does (the look-ahead guarantee is independent
# of the lookback size). ema_slow=21 / ols_window=20 still gate _signal_for_bar,
# so a 25-bar window is the smallest that lets signals form.
_CFG = BacktestConfig(lookback=25)

_TS_START = 1_600_000_000_000  # epoch ms anchor
_TS_STEP = 60_000              # strictly monotonic, unique per bar


def _walk_candles(draw, n, start_price):
    """A strictly-monotonic OHLCV random walk on the shared timestamp grid.

    OHLC ordering is well-formed (high >= max(open, close),
    low <= min(open, close)); timestamps are strictly increasing and unique so
    each signal bar is identifiable. The grid (``_TS_START + i * _TS_STEP``) is
    SHARED by the symbol and benchmark series so the calculator's time-alignment
    finds common timestamps and can produce available labels.
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
            "timestamp_ms": _TS_START + i * _TS_STEP,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
        price = close
    return candles


@st.composite
def _symbol_and_benchmark(draw):
    """A (symbol_candles, benchmark_candles) pair on a shared timestamp grid.

    Both series share the same length and timestamps so the relative-strength
    calculator finds common-timestamp candles to align (exercising the classify
    path), while their independent random walks keep the resulting labels varied.
    """
    n = draw(st.integers(min_value=40, max_value=160))
    sym_start = draw(st.floats(min_value=50.0, max_value=500.0,
                               allow_nan=False, allow_infinity=False))
    bench_start = draw(st.floats(min_value=50.0, max_value=500.0,
                                 allow_nan=False, allow_infinity=False))
    symbol_candles = _walk_candles(draw, n, sym_start)
    benchmark_candles = _walk_candles(draw, n, bench_start)
    return symbol_candles, benchmark_candles


def _inputs_by_signal_ts(recorded):
    """Map each call's signal bar timestamp -> (symbol_window, benchmark_window).

    The signal bar timestamp is the LAST (max) timestamp of the symbol prefix the
    classifier was handed — i.e. the signal candle itself.
    """
    out = {}
    for symbol_window, benchmark_window in recorded:
        signal_ts = symbol_window[-1]["timestamp_ms"]
        out[signal_ts] = (symbol_window, benchmark_window)
    return out


def _run_with_spy(symbol_candles, benchmark_candles):
    """Run generate_and_score, recording every (symbol_window, benchmark_window).

    Returns the list of recorded (symbol_window, benchmark_window) pairs (deep
    copies, so later mutation can't retroactively alter them; the classifier is
    pure so this is purely defensive).
    """
    recorded = []
    original = rs.classify_relative_strength

    def _spy(symbol_window, benchmark_window, config, **kwargs):
        recorded.append((copy.deepcopy(symbol_window), copy.deepcopy(benchmark_window)))
        return original(symbol_window, benchmark_window, config, **kwargs)

    rs.classify_relative_strength = _spy
    try:
        generate_and_score(symbol_candles, "SYM", "1d", _CFG,
                           benchmark_candles=benchmark_candles, benchmark="BENCH")
    finally:
        rs.classify_relative_strength = original
    return recorded


# ─────────────────────────────────────────────────────────────────────────────
# Property 27: Backtest relative-strength classification is look-ahead-free
# ─────────────────────────────────────────────────────────────────────────────

# Feature: relative-strength-context, Property 27: Backtest relative-strength classification is look-ahead-free
@settings(max_examples=100, deadline=None)
@given(series=_symbol_and_benchmark())
def test_property_27_backtest_rs_classification_is_lookahead_free(series):
    """Validates: Requirements 11.1

    Every relative-strength classification the Backtest_Seeder performs for a
    signal uses only symbol/benchmark candles at or before that signal's bar
    (proved by spying on the exact windows handed to
    ``rs.classify_relative_strength``), and appending later candles beyond a
    signal does not change that signal's classification inputs (proved by
    truncation invariance over the intersection of signals).
    """
    symbol_candles, benchmark_candles = series
    symbol_snapshot = copy.deepcopy(symbol_candles)
    benchmark_snapshot = copy.deepcopy(benchmark_candles)

    # ── (1) SPY: no candle in either window post-dates the signal candle ───────
    recorded_full = _run_with_spy(symbol_candles, benchmark_candles)

    for symbol_window, benchmark_window in recorded_full:
        assert len(symbol_window) >= 1, "classifier was handed an empty symbol window"
        # The signal candle is the last bar of the point-in-time symbol prefix;
        # its timestamp is the cutoff beyond which no candle may appear.
        signal_ts = symbol_window[-1]["timestamp_ms"]
        assert all(c["timestamp_ms"] <= signal_ts for c in symbol_window), (
            "symbol_window contained a candle dated after the signal bar "
            "(look-ahead leak)"
        )
        assert all(c["timestamp_ms"] <= signal_ts for c in benchmark_window), (
            "benchmark_window contained a candle dated after the signal bar "
            "(look-ahead leak)"
        )

    # generate_and_score must not have mutated the caller's candle histories.
    assert symbol_candles == symbol_snapshot, "generate_and_score mutated its symbol candles"
    assert benchmark_candles == benchmark_snapshot, "generate_and_score mutated its benchmark candles"

    # ── (2) TRUNCATION INVARIANCE: appending later candles can't change inputs ─
    # Re-run over a strictly shorter prefix (symbol AND benchmark) and require the
    # classification INPUTS of any signal emitted by BOTH runs to be identical.
    n = len(symbol_candles)
    if n >= _CFG.lookback + 4:
        trunc_n = n - max(1, n // 5)              # drop the tail
        trunc_n = max(trunc_n, _CFG.lookback + 2)  # keep the loop runnable
        if trunc_n < n:
            recorded_prefix = _run_with_spy(
                symbol_candles[:trunc_n], benchmark_candles[:trunc_n]
            )

            inputs_full = _inputs_by_signal_ts(recorded_full)
            inputs_prefix = _inputs_by_signal_ts(recorded_prefix)

            common_ts = set(inputs_full) & set(inputs_prefix)
            for ts in common_ts:
                assert inputs_full[ts] == inputs_prefix[ts], (
                    "relative-strength classification inputs changed when later "
                    f"candles were removed (look-ahead) at signal ts={ts}"
                )
