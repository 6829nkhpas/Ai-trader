"""Property-based test for look-ahead-free backtest session classification
(backtest.py ``generate_and_score``, task 9.2).

Feature: session-expiry-awareness

This module implements design **Property 24: Backtest session classification
reuses the calculator and is look-ahead-free**:

    For any candle history and any signal index, the Session_Label the
    Backtest_Seeder assigns to that signal equals ``session.classify_session``
    applied to that signal's OWN candle timestamp, so that altering or removing
    any other candles in the history does not change the assigned Session_Label.

Validates: Requirements 11.1, 11.6.

Implementation under test: ``backtest.generate_and_score(candles, symbol,
timeframe, cfg)``. For every bar ``i`` that emits a signal it classifies the
session via ``session.classify_session(candles[i]["timestamp_ms"], config, ...)``
— the timestamp of the signal's OWN candle — and writes the labelled entry to
``decision["defensibility"]["session"]`` through ``_session_defensibility_entry``.

Three complementary, robust assertions prove reuse + no look-ahead:

  1. REUSE (delegation): spy on ``session.classify_session`` to confirm the
     seeder DELEGATES to the pure calculator (it does not reimplement the date
     math, R11.1/R11.6), and that the session entry attached to each seeded trade
     equals ``_session_defensibility_entry`` applied to the calculator's output
     for that signal's OWN candle timestamp.

  2. OWN-CANDLE ONLY: every timestamp handed to ``classify_session`` is exactly
     the ``timestamp_ms`` of some candle in the history (the signal bar's own
     candle) — never a value synthesised from, or dependent on, any later candle.

  3. INVARIANCE TO OTHER CANDLES (the literal property): removing later candles
     (truncation) and altering the OHLCV of all candles strictly AFTER a signal
     both leave that signal's assigned Session_Label unchanged.

The sys.path / import pattern mirrors the other backtest property tests: the
service directory (one level up) is prepended to ``sys.path`` so ``backtest`` /
``session`` are importable when pytest is run from anywhere.
"""

import copy
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (backtest.py / session.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
import session  # noqa: E402
from backtest import BacktestConfig, generate_and_score  # noqa: E402

# A small lookback keeps generated histories modest while still walking the
# signal loop exactly as production does (the look-ahead guarantee is independent
# of the lookback size). ema_slow=21 / ols_window=20 still gate _signal_for_bar,
# so a 25-bar window is the smallest that lets signals form.
_CFG = BacktestConfig(lookback=25)

_TS_START = 1_600_000_000_000  # epoch ms anchor (a real-world instant)
_TS_STEP = 5 * 60_000          # 5-minute bars: strictly monotonic, unique per bar


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


def _session_by_signal_ts(results):
    """Map each scored signal's bar timestamp (ms) -> its assigned session entry.

    The decision's ``created_at`` is ``timestamp_ms / 1000`` (seconds), so we
    re-multiply to recover the signal candle's own millisecond timestamp.
    """
    out = {}
    for r in results:
        decision = r["decision"]
        created_at = decision.get("created_at")
        if created_at is None:
            continue
        ts_ms = round(created_at * 1000.0)
        out[ts_ms] = decision.get("defensibility", {}).get("session")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Property 24: Backtest session classification reuses the calculator and is
#              look-ahead-free
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 24: Backtest session classification reuses the calculator and is look-ahead-free
@settings(max_examples=150, deadline=None)
@given(candles=_candle_history())
def test_property_24_backtest_session_classification_reuses_calculator_and_is_lookahead_free(candles):
    """Validates: Requirements 11.1, 11.6

    The Backtest_Seeder DELEGATES session classification to the pure
    ``session.classify_session`` calculator (proved by spying on it), feeds it
    ONLY the signal's own candle timestamp (proved by checking every recorded
    timestamp against the candle set), and the resulting Session_Label is
    invariant to every other candle in the history (proved by truncation and
    future-candle mutation leaving each label unchanged).
    """
    candle_ts_set = {c["timestamp_ms"] for c in candles}
    candles_snapshot = copy.deepcopy(candles)

    # ── (1) SPY: record the timestamp + result handed to every classify call ──
    recorded = []  # list of (ts_ms, result)
    original_classify = session.classify_session

    def _spy(timestamp_ms, config, symbol=None, timeframe=None):
        result = original_classify(
            timestamp_ms, config, symbol=symbol, timeframe=timeframe
        )
        recorded.append((timestamp_ms, copy.deepcopy(result)))
        return result

    session.classify_session = _spy
    try:
        results_full = generate_and_score(candles, "SYM", "1d", _CFG)
    finally:
        session.classify_session = original_classify

    # The seeder must have delegated to the calculator for at least every signal
    # that was scored (reuse, not reimplementation — R11.1/R11.6). If no signal
    # formed this run there is nothing to prove here; the invariance checks below
    # are likewise vacuously satisfied.
    sessions_full = _session_by_signal_ts(results_full)

    # ── (2) OWN-CANDLE ONLY: each classified timestamp is a candle's own ts ────
    for ts_ms, _result in recorded:
        assert ts_ms in candle_ts_set, (
            "classify_session received a timestamp that is not any candle's own "
            f"timestamp_ms (look-ahead / fabricated input): {ts_ms!r}"
        )

    # ── REUSE: the attached entry equals the calculator output mapped through ──
    # ``_session_defensibility_entry`` for that signal's OWN candle timestamp.
    result_by_ts = dict(recorded)  # last write per ts; classify is deterministic
    for ts_ms, sess_entry in sessions_full.items():
        assert ts_ms in result_by_ts, (
            f"a seeded signal at ts={ts_ms} had no corresponding classify_session "
            "call — the seeder did not reuse the calculator for it"
        )
        expected_entry = backtest._session_defensibility_entry(result_by_ts[ts_ms])
        assert sess_entry == expected_entry, (
            "seeded session entry does not equal the calculator output mapped "
            f"through _session_defensibility_entry at ts={ts_ms}: "
            f"entry={sess_entry!r} expected={expected_entry!r}"
        )

    # generate_and_score must not have mutated the caller's candle history.
    assert candles == candles_snapshot, "generate_and_score mutated its input candles"

    n = len(candles)

    # ── (3a) TRUNCATION INVARIANCE: removing later candles can't change a label ─
    if n >= _CFG.lookback + 4:
        trunc_n = n - max(1, n // 5)               # drop the tail
        trunc_n = max(trunc_n, _CFG.lookback + 2)  # keep the loop runnable
        if trunc_n < n:
            prefix = candles[:trunc_n]
            results_prefix = generate_and_score(prefix, "SYM", "1d", _CFG)
            sessions_prefix = _session_by_signal_ts(results_prefix)

            for ts_ms in set(sessions_full) & set(sessions_prefix):
                assert sessions_full[ts_ms] == sessions_prefix[ts_ms], (
                    "session label changed when later candles were removed "
                    f"(look-ahead) at signal ts={ts_ms}: "
                    f"full={sessions_full[ts_ms]!r} prefix={sessions_prefix[ts_ms]!r}"
                )

    # ── (3b) FUTURE-MUTATION INVARIANCE: altering candles strictly AFTER a ─────
    # signal must not change that signal's session label. Mutate the OHLCV (NOT
    # the timestamps, so signal identity is preserved) of every candle after a
    # cut index, re-run, and require every signal at or before the cut to keep
    # its label. Signals depend only on candles at/before their own bar, so any
    # signal whose ts <= cut_ts is emitted in both runs and must be unchanged.
    if n >= _CFG.lookback + 4:
        cut = n - max(1, n // 4)
        cut = max(cut, _CFG.lookback + 1)
        if cut < n:
            cut_ts = candles[cut - 1]["timestamp_ms"]
            mutated = copy.deepcopy(candles)
            for j in range(cut, n):
                c = mutated[j]
                # Perturb prices/volume while keeping OHLC ordering well-formed
                # and the timestamp (identity) untouched.
                c["open"] = c["open"] + 7.0
                c["close"] = c["close"] + 5.0
                c["high"] = max(c["open"], c["close"]) + 2.0
                c["low"] = max(0.5, min(c["open"], c["close"]) - 2.0)
                c["volume"] = c["volume"] + 123.0

            results_mut = generate_and_score(mutated, "SYM", "1d", _CFG)
            sessions_mut = _session_by_signal_ts(results_mut)

            for ts_ms, entry in sessions_full.items():
                if ts_ms <= cut_ts and ts_ms in sessions_mut:
                    assert entry == sessions_mut[ts_ms], (
                        "session label changed when LATER candles were altered "
                        f"(look-ahead) at signal ts={ts_ms}: "
                        f"orig={entry!r} mutated={sessions_mut[ts_ms]!r}"
                    )
