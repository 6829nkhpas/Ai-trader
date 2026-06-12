"""Example-based smoke test for comparison-mode backtest (backtest.py, task 14.3).

Feature: regime-detection-gate

This is a DETERMINISTIC, example-based smoke test (NOT a property test) that
exercises ``backtest.compare()`` end-to-end, fully OFFLINE: a fixed, hand-built
OHLCV candle fixture is passed directly via ``compare(..., candles=...)`` so no
Rust tool server / QuestDB is touched.

It asserts the documented comparison-mode contract:
  * the result carries both ``with_gate`` and ``without_gate`` summaries, each
    with the documented keys;
  * ``with_gate.signals_scored <= without_gate.signals_scored`` — the with-gate
    seeded set is a SUBSET of the without-gate set because the gate only ever
    DROPS signals whose regime favorability is the available ``unfavorable``
    label (Req 10.2, 10.4);
  * both runs report the SAME ``candles`` count (identical history);
  * each run's ``win_rate`` / ``expectancy`` are well-defined — a float (with
    win_rate in [0.0, 1.0]) or the string ``"n/a"`` when zero trades closed
    (Req 10.4 / 10.7).

To make the gate actually EXCLUDE at least one signal (so the subset
relationship is non-trivial), ``backtest.regime.classify_regime`` is monkeypatched
with a DETERMINISTIC fake whose favorability depends only on the candle slice it
is given. Determinism matters: ``compare`` classifies the same point-in-time
slices in both runs, so an identical favorability must come back each time for
the two runs to walk identical history.

The sys.path / import pattern mirrors the sibling tests (service directory one
level up is prepended to ``sys.path`` so ``backtest`` / ``regime`` import cleanly).
"""

import math
import os
import random
import sys

# Make the service package importable (backtest.py / regime.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
import regime as regime_mod  # noqa: E402
from backtest import BacktestConfig  # noqa: E402

# Reduced-lookback config keeps the run fast while still satisfying the signal
# rules (need >= max(ema_slow=21, ols_window=20) + 1 == 22 closes per window) and
# the generate_and_score guard (n >= lookback + 2). Identical for both runs —
# compare() only flips the regime gate flag between them.
_CFG = BacktestConfig(lookback=30, cooldown_bars=2, profile_rows=12)

_DOC_RUN_KEYS = {
    "signals_scored",
    "closed_trades",
    "winning_closed_trades",
    "win_rate",
    "expectancy",
}

_FAVORABILITIES = ("favorable", "unfavorable", "neutral")


def _build_fixture():
    """Build a fixed, deterministic OHLCV candle fixture.

    A SEEDED random walk (fixed seed -> identical bytes on every run) produces a
    price path with enough dispersion for the deterministic rule set to emit
    several signals (EMA crossings, value-area-edge pullbacks, occasional
    over-extensions). Using a fixed seed keeps the fixture fully reproducible — it
    is effectively a hand-pinned constant series, just expressed compactly.
    """
    rng = random.Random(20240517)
    base_ts = 1_700_000_000_000
    price = 150.0
    candles = []
    for i in range(120):
        step = rng.uniform(-4.0, 4.0)
        new_price = max(1.0, price + step)
        o, c = price, new_price
        hi = max(o, c) + rng.uniform(0.0, 2.5)
        lo = max(0.5, min(o, c) - rng.uniform(0.0, 2.5))
        candles.append({
            "timestamp_ms": base_ts + i * 86_400_000,  # 1d bars
            "open": round(o, 4),
            "high": round(hi, 4),
            "low": round(lo, 4),
            "close": round(c, 4),
            "volume": round(rng.uniform(1e3, 1e6), 2),
        })
        price = new_price
    return candles


def _fake_classify(candles, config, symbol=None, timeframe=None):
    """Deterministic stand-in for ``regime.classify_regime``.

    Favorability is a pure function of the LAST candle's close in the provided
    slice, cycling favorable / unfavorable / neutral so the with-gate run drops
    the ``unfavorable`` signals. Being a pure function of the slice guarantees
    both compare() runs receive identical labels for identical point-in-time
    slices (so they walk identical history).
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


def _assert_run_well_formed(run, label):
    """Assert one run carries the documented keys with well-defined metrics."""
    assert _DOC_RUN_KEYS.issubset(run.keys()), (
        f"{label}: missing documented keys; got {sorted(run.keys())}"
    )

    closed = run["closed_trades"]
    winning = run["winning_closed_trades"]
    win_rate = run["win_rate"]
    expectancy = run["expectancy"]

    assert isinstance(closed, int) and closed >= 0, f"{label}: bad closed_trades {closed!r}"
    assert isinstance(winning, int) and 0 <= winning <= closed, (
        f"{label}: winning_closed_trades {winning!r} out of range for closed {closed!r}"
    )

    if closed == 0:
        # Zero closed trades => both metrics not-applicable (R10.7), never a
        # division by zero.
        assert win_rate == "n/a", f"{label}: expected win_rate 'n/a', got {win_rate!r}"
        assert expectancy == "n/a", f"{label}: expected expectancy 'n/a', got {expectancy!r}"
    else:
        assert isinstance(win_rate, float), f"{label}: win_rate not a float: {win_rate!r}"
        assert 0.0 <= win_rate <= 1.0, f"{label}: win_rate {win_rate!r} outside [0,1]"
        assert isinstance(expectancy, float) and math.isfinite(expectancy), (
            f"{label}: expectancy not a finite float: {expectancy!r}"
        )


def test_comparison_mode_smoke():
    """Validates: Requirements 10.2, 10.4

    A comparison-mode backtest over a fixed candle fixture produces with-gate and
    without-gate summaries with the expected subset relationship and well-defined
    metrics, fully offline.
    """
    candles = _build_fixture()
    expected_count = len(candles)

    # Deterministic regime labelling so the gate drops a well-defined subset and
    # both compare() runs see identical favorability for identical slices.
    _orig_classify = regime_mod.classify_regime
    regime_mod.classify_regime = _fake_classify
    try:
        result = backtest.compare("SYM", "1d", candles=candles, cfg=_CFG)
    finally:
        regime_mod.classify_regime = _orig_classify

    # Both summaries are present.
    assert "with_gate" in result and "without_gate" in result, (
        f"result missing with_gate/without_gate: {sorted(result.keys())}"
    )
    with_gate = result["with_gate"]
    without_gate = result["without_gate"]

    # Documented keys + well-defined metrics for each run (R10.4 / R10.7).
    _assert_run_well_formed(with_gate, "with_gate")
    _assert_run_well_formed(without_gate, "without_gate")

    # The fixture is built to emit several signals, so the without-gate run is
    # non-empty — otherwise the subset relationship would be vacuous.
    assert without_gate["signals_scored"] >= 1, (
        "fixture produced no signals; cannot exercise the comparison"
    )

    # Subset relationship: the gate only ever DROPS signals (R10.2 / R10.4).
    assert with_gate["signals_scored"] <= without_gate["signals_scored"], (
        f"with_gate signals {with_gate['signals_scored']} exceeds "
        f"without_gate {without_gate['signals_scored']}"
    )

    # Both runs are computed over the identical history => identical candle count.
    assert result["candles"] == expected_count, (
        f"reported candles {result['candles']!r} != input {expected_count!r}"
    )
