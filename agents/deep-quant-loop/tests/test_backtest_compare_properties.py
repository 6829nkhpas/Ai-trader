"""Property-based test for comparison-mode consistency and metrics (backtest.py, task 13.6).

Feature: regime-detection-gate

This module implements design **Property 27: Comparison-mode runs are consistent
and metrics are well-defined**:

    ``compare()`` runs the backtest WITH and WITHOUT the regime gate over the
    IDENTICAL candle history and IDENTICAL setup rules (only ``regime_gate_enabled``
    differs). Because the gate only ever DROPS signals (those whose regime
    favorability is the available ``unfavorable`` label), the with-gate seeded set
    is a SUBSET of the without-gate set, hence
    ``with_gate.signals_scored <= without_gate.signals_scored``. Each run's
    win-rate is ``winning_closed_trades / closed_trades`` — a float in [0.0, 1.0]
    — and expectancy is the mean realized R-multiple over closed trades, both
    reported as ``"n/a"`` when a run produced zero closed trades. Both runs are
    computed over the same history, so the reported ``candles`` count is identical.

Validates: Requirements 10.3, 10.4, 10.7.

The test stays fully OFFLINE: synthetic candle series are passed directly to
``compare(..., candles=...)`` so no Rust tool server / QuestDB is touched. To make
the gate actually drop some signals (so the subset relationship is exercised
non-trivially), ``regime.classify_regime`` is monkeypatched with a DETERMINISTIC
fake whose favorability depends only on the candle slice it is given. Determinism
of the fake is essential: ``compare`` classifies the same point-in-time slices in
both runs, so an identical favorability must come back each time for the two runs
to walk identical history.

The sys.path / import pattern mirrors ``tests/test_regime_determinism_properties.py``:
the service directory (one level up) is prepended to ``sys.path`` so ``backtest``
and ``regime`` are importable when pytest is run from anywhere.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (backtest.py / regime.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
import regime as regime_mod  # noqa: E402
from backtest import BacktestConfig  # noqa: E402

# A reduced-lookback config keeps each ``compare`` run fast while still satisfying
# the signal rules (which need >= max(ema_slow, ols_window) + 1 == 22 closes) and
# the generate_and_score guard (n >= lookback + 2). Identical for both runs — the
# only difference compare() introduces is the regime gate flag.
_CFG = BacktestConfig(lookback=30, cooldown_bars=2, profile_rows=12)

_FAVORABILITIES = ("favorable", "unfavorable", "neutral")


def _fake_classify(candles, config, symbol=None, timeframe=None):
    """Deterministic stand-in for ``regime.classify_regime``.

    Returns a Regime_Label whose favorability is a pure function of the LAST
    candle's close in the provided slice, cycling through favorable / unfavorable
    / neutral so the with-gate run drops the ``unfavorable`` signals. Being a pure
    function of the input slice guarantees both compare() runs receive identical
    labels for identical point-in-time slices (so they walk identical history).
    """
    last = candles[-1] if candles else {}
    close = last.get("close") if isinstance(last, dict) else None
    if not isinstance(close, (int, float)) or isinstance(close, bool) or not math.isfinite(close):
        return {"unavailable": True, "reason": "no usable close in slice"}
    bucket = int(abs(close) * 100) % 3
    return {
        "trend_state": "trending",
        "volatility_state": "normal",
        "favorability": _FAVORABILITIES[bucket],
        "measures": {"adx": 25.0, "efficiency_ratio": 0.5},
        "symbol": symbol,
        "timeframe": timeframe,
    }


@st.composite
def _candle_series(draw):
    """A synthetic OHLCV random-walk candle series long enough to emit signals.

    Prices drift via small per-bar steps so EMA-fast/slow crossings, value-area
    edges, and over-extensions all occur, letting the deterministic rule set emit
    a realistic mix of signals (some resolving as wins, some as losses).
    """
    n = draw(st.integers(min_value=40, max_value=140))
    price = draw(st.floats(min_value=50.0, max_value=500.0,
                           allow_nan=False, allow_infinity=False))
    base_ts = 1_700_000_000_000
    candles = []
    for i in range(n):
        step = draw(st.floats(min_value=-4.0, max_value=4.0,
                              allow_nan=False, allow_infinity=False))
        new_price = max(1.0, price + step)
        o, c = price, new_price
        up = draw(st.floats(min_value=0.0, max_value=2.5, allow_nan=False, allow_infinity=False))
        dn = draw(st.floats(min_value=0.0, max_value=2.5, allow_nan=False, allow_infinity=False))
        hi = max(o, c) + up
        lo = max(0.5, min(o, c) - dn)
        candles.append({
            "open": o, "high": hi, "low": lo, "close": c,
            "volume": draw(st.floats(min_value=0.0, max_value=1e6,
                                     allow_nan=False, allow_infinity=False)),
            "timestamp_ms": base_ts + i * 60_000,
        })
        price = new_price
    return candles


def _assert_run_metrics_well_defined(run, label):
    """Assert one run's metrics obey Requirements 10.4 / 10.7."""
    closed = run["closed_trades"]
    winning = run["winning_closed_trades"]
    win_rate = run["win_rate"]
    expectancy = run["expectancy"]

    assert isinstance(closed, int) and closed >= 0, f"{label}: bad closed_trades {closed!r}"
    assert isinstance(winning, int) and 0 <= winning <= closed, (
        f"{label}: winning_closed_trades {winning!r} out of range for closed {closed!r}"
    )

    if closed == 0:
        # Zero closed trades => both metrics are not-applicable (R10.7), never a
        # division by zero.
        assert win_rate == "n/a", f"{label}: expected win_rate 'n/a' for 0 closed, got {win_rate!r}"
        assert expectancy == "n/a", f"{label}: expected expectancy 'n/a' for 0 closed, got {expectancy!r}"
    else:
        # win_rate is a float in [0,1] equal to winning/closed (R10.4).
        assert isinstance(win_rate, float), f"{label}: win_rate not a float: {win_rate!r}"
        assert 0.0 <= win_rate <= 1.0, f"{label}: win_rate {win_rate!r} outside [0,1]"
        assert abs(win_rate - winning / closed) <= 1e-4, (
            f"{label}: win_rate {win_rate!r} != winning/closed {winning / closed!r}"
        )
        # expectancy is a finite float (mean realized R).
        assert isinstance(expectancy, float) and math.isfinite(expectancy), (
            f"{label}: expectancy not a finite float: {expectancy!r}"
        )


# Feature: regime-detection-gate, Property 27
@settings(max_examples=120, deadline=None)
@given(candles=_candle_series())
def test_property_27_comparison_consistency_and_metrics(candles):
    """Validates: Requirements 10.3, 10.4, 10.7

    Over identical history/rules, the with-gate seeded set is a subset of the
    without-gate set (signals_scored ordering), both runs report the same candle
    count, and each run's win-rate / expectancy are well-defined (a float in
    [0,1] / a finite float, or both ``"n/a"`` when zero trades closed).
    """
    # Deterministic regime labelling so the gate drops a well-defined subset and
    # both compare() runs see identical favorability for identical slices. Patched
    # manually (not via the function-scoped monkeypatch fixture) so Hypothesis can
    # reuse the patch across all generated examples without a health-check warning.
    expected_count = len(candles)
    _orig_classify = regime_mod.classify_regime
    regime_mod.classify_regime = _fake_classify
    try:
        result = backtest.compare("TESTSYM", "1d", candles=candles, cfg=_CFG)
    finally:
        regime_mod.classify_regime = _orig_classify

    # Identical history => the reported candle count matches the input exactly and
    # is shared by both runs.
    assert result["candles"] == expected_count, (
        f"reported candles {result['candles']!r} != input {expected_count!r}"
    )

    with_gate = result["with_gate"]
    without_gate = result["without_gate"]

    # Subset relationship: the gate only removes (never adds) signals (R10.4).
    assert with_gate["signals_scored"] <= without_gate["signals_scored"], (
        f"with_gate signals {with_gate['signals_scored']} exceeds "
        f"without_gate {without_gate['signals_scored']}"
    )

    # Per-run metrics are well-defined (R10.4) with the zero-closed-trades case
    # reported as 'n/a' (R10.7).
    _assert_run_metrics_well_defined(with_gate, "with_gate")
    _assert_run_metrics_well_defined(without_gate, "without_gate")
