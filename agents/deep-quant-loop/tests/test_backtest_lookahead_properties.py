"""Property-based test for look-ahead-free backtest regime classification
(backtest.py ``generate_and_score``, task 13.4).

Feature: regime-detection-gate

This module implements design **Property 25: Backtest regime classification is
look-ahead-free**:

    For any candle history and any signal index, the Regime_Label the
    Backtest_Seeder assigns to that signal is computed only from candles at or
    before the signal's candle timestamp, so that altering or removing any later
    candles does not change the assigned Regime_Label.

Validates: Requirements 10.1.

Implementation under test: ``backtest.generate_and_score(candles, symbol,
timeframe, cfg)``. For every bar ``i`` that emits a signal it classifies the
regime via ``regime.classify_regime(candles[: i + 1], ...)`` — the point-in-time
prefix ending at the signal's candle.

Two complementary, robust assertions prove no look-ahead:

  1. SPY (direct): wrap ``regime.classify_regime`` to record the candle slice it
     is handed on every call, then assert each recorded slice is EXACTLY the
     prefix ``candles[: k]`` (so it carries NO candle with a timestamp later than
     the signal bar's timestamp). This is definitive: the classifier provably
     never sees a future candle.

  2. TRUNCATION INVARIANCE (literal restatement of the property): re-run
     ``generate_and_score`` over a shortened prefix of the same history and
     assert that, for every signal emitted by BOTH runs (matched by its signal
     bar timestamp), the assigned Regime_Label is identical — i.e. removing the
     later candles did not change the label. (Labels are attached before
     scoring, so a signal need only appear in both runs to be comparable; the
     intersection is asserted, signals unique to one run are ignored.)

The sys.path / import pattern mirrors the other regime property tests: the
service directory (one level up) is prepended to ``sys.path`` so ``backtest`` /
``regime`` are importable when pytest is run from anywhere.
"""

import copy
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (backtest.py / regime.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
import regime  # noqa: E402
from backtest import BacktestConfig, generate_and_score  # noqa: E402

# A small lookback keeps generated histories modest while still walking the
# signal loop exactly as production does (the look-ahead guarantee is independent
# of the lookback size). ema_slow=21 / ols_window=20 still gate _signal_for_bar,
# so a 25-bar window is the smallest that lets signals form.
_CFG = BacktestConfig(lookback=25)

_TS_START = 1_600_000_000_000  # epoch ms anchor
_TS_STEP = 60_000              # strictly monotonic, unique per bar


@st.composite
def _candle_history(draw):
    """A strictly-monotonic OHLCV history (random walk) with finite fields.

    Prices follow a bounded random walk so EMAs cross and price repeatedly
    revisits the value-area edges — conditions under which ``_signal_for_bar``
    emits signals, exercising the classify path. OHLC ordering is well-formed
    (high >= max(open, close), low <= min(open, close)) and timestamps are
    strictly increasing and unique so each signal bar is identifiable.
    """
    n = draw(st.integers(min_value=40, max_value=160))
    price = draw(st.floats(min_value=50.0, max_value=500.0,
                           allow_nan=False, allow_infinity=False))
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


def _labels_by_signal_ts(results):
    """Map each scored signal's bar timestamp -> its assigned regime entry."""
    out = {}
    for r in results:
        decision = r["decision"]
        ts = decision.get("created_at")
        out[ts] = decision.get("defensibility", {}).get("regime")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Property 25: Backtest regime classification is look-ahead-free
# ─────────────────────────────────────────────────────────────────────────────

# Feature: regime-detection-gate, Property 25
@settings(max_examples=150, deadline=None)
@given(candles=_candle_history())
def test_property_25_backtest_classification_is_lookahead_free(candles):
    """Validates: Requirements 10.1

    Every regime classification the Backtest_Seeder performs for a signal uses
    only candles at or before that signal's bar (proved by spying on the exact
    slice handed to ``classify_regime``), and removing later candles does not
    change the Regime_Label assigned to a signal at an earlier bar (proved by
    truncation invariance over the intersection of signals).
    """
    candles_snapshot = copy.deepcopy(candles)

    # ── (1) SPY: capture the exact candle slice handed to every classify call ──
    recorded_slices = []
    original_classify = regime.classify_regime

    def _spy(candle_arg, config, symbol=None, timeframe=None):
        # Deep-copy so a later (hypothetical) mutation can't retroactively alter
        # what we recorded; classify_regime itself is pure so this is defensive.
        recorded_slices.append(copy.deepcopy(candle_arg))
        return original_classify(candle_arg, config, symbol=symbol, timeframe=timeframe)

    regime.classify_regime = _spy
    try:
        results_full = generate_and_score(candles, "SYM", "1d", _CFG)
    finally:
        regime.classify_regime = original_classify

    n = len(candles)
    for sl in recorded_slices:
        k = len(sl)
        assert k >= 1, "classifier was handed an empty slice"
        assert k <= n, "classifier slice longer than the full history"
        # The slice must be EXACTLY the point-in-time prefix candles[:k]: it
        # carries no candle drawn from a later index than the signal bar (k-1).
        assert sl == candles[:k], (
            "classify_regime received a non-prefix slice (look-ahead leak): "
            f"len={k}"
        )
        # Equivalently, no candle in the slice has a timestamp later than the
        # signal bar's timestamp (the slice's last candle).
        signal_bar_ts = sl[-1]["timestamp_ms"]
        assert all(c["timestamp_ms"] <= signal_bar_ts for c in sl), (
            "slice contained a candle dated after the signal bar (look-ahead)"
        )

    # generate_and_score must not have mutated the caller's candle history.
    assert candles == candles_snapshot, "generate_and_score mutated its input candles"

    # ── (2) TRUNCATION INVARIANCE: appending later candles can't change a label ─
    # Re-run over a strictly shorter prefix (still long enough to walk the loop)
    # and require the Regime_Label of any signal emitted by BOTH runs to match.
    if n >= _CFG.lookback + 4:
        trunc_n = n - max(1, n // 5)              # drop the tail
        trunc_n = max(trunc_n, _CFG.lookback + 2)  # keep the loop runnable
        if trunc_n < n:
            prefix = candles[:trunc_n]
            results_prefix = generate_and_score(prefix, "SYM", "1d", _CFG)

            labels_full = _labels_by_signal_ts(results_full)
            labels_prefix = _labels_by_signal_ts(results_prefix)

            common_ts = set(labels_full) & set(labels_prefix)
            for ts in common_ts:
                assert labels_full[ts] == labels_prefix[ts], (
                    "regime label changed when later candles were removed "
                    f"(look-ahead) at signal ts={ts}: "
                    f"full={labels_full[ts]!r} prefix={labels_prefix[ts]!r}"
                )
