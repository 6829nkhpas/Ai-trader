"""Unit test for comparison metrics and the uniform default plan (backtest.py, task 10.6).

Feature: trade-management

This module is the EXAMPLE-BASED companion to the two comparison-mode property
tests (``test_tm_compare_signal_identity_properties.py`` — Property 24, and
``test_tm_compare_metrics_na_properties.py`` — Property 25). Where those exercise
universal invariants over generated inputs, this test pins concrete, auditable
behaviour of ``backtest.compare_management`` and the managed
``backtest.generate_and_score`` over a SMALL FIXED synthetic candle history:

  1. BOTH runs (``summary["managed"]`` and ``summary["unmanaged"]``) report a
     win-rate, an expectancy, and a downside measure: the keys are present, and
     each value is either a finite number (when the run produced closed trades)
     or the ``"n/a"`` sentinel (when it produced zero closed trades) — never a
     divide-by-zero (Requirements 12.2, 12.3).

  2. The managed run applies the configured default ``Management_Plan`` UNIFORMLY:
     every seeded/scored signal carries the SAME management style — the
     non-single ``scale-be-trail`` produced by
     ``trade_manager.default_management_plan`` under the resolved config — and a
     present/non-null serialized ``management_plan`` (Requirement 12.5). The
     unmanaged run, by contrast, scores every signal as a ``Single_Target_Trade``
     tagged ``tm:single``.

Validates: Requirements 12.2, 12.3, 12.5.

The test stays fully OFFLINE: a DETERMINISTIC synthetic candle series (a gently
rising price with a sinusoidal oscillation, sized to emit a handful of signals)
is passed directly to ``compare_management(..., candles=...)`` /
``generate_and_score`` and an explicit empty ``benchmark_candles=[]`` is supplied
so no Rust tool server / QuestDB is ever touched (a ``None`` benchmark series
would trigger a network fetch). With no benchmark series, relative strength
degrades to an honest Unavailable_Marker for every signal in both runs
identically, which does not affect signal generation or management.

The sys.path / import pattern mirrors the sibling comparison tests
(``tests/test_tm_compare_signal_identity_properties.py``): the service directory
(one level up) is prepended to ``sys.path`` so ``backtest`` / ``trade_manager``
are importable when pytest is run from anywhere.
"""

import math
import os
import sys
from dataclasses import replace

# Make the service package importable (backtest.py / trade_manager.py live one
# level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
import trade_manager  # noqa: E402
from backtest import BacktestConfig, generate_and_score  # noqa: E402

_NA = "n/a"

# A reduced-lookback config keeps each run fast while still satisfying the signal
# rules (which need >= max(ema_slow, ols_window) + 1 == 22 closes) and the
# generate_and_score guard (n >= lookback + 2). ``record_unresolved=True`` retains
# every generated signal so the "uniform plan" assertion sees every signal (not
# only the ones that resolved); the regime gate / RS filter / forecast filter
# stay disabled (defaults) so the only per-run difference is the management plan.
_CFG = BacktestConfig(lookback=30, cooldown_bars=2, profile_rows=12,
                      record_unresolved=True)


def _fixed_candle_series(n=120):
    """A DETERMINISTIC OHLCV candle series sized to emit a handful of signals.

    The close path is a gentle uptrend (``0.5`` per bar) with a sinusoidal
    oscillation (amplitude ``8``); the persistent uptrend keeps the EMA bias
    bullish while the oscillation repeatedly pulls price back toward the
    value-area low, firing the rule-set's trend-aligned pullback BUY signals. The
    series is fixed (no randomness) so the test is fully reproducible. OHLC
    ordering is well-formed and timestamps are strictly increasing and unique.
    """
    base_ts = 1_700_000_000_000
    closes = [100.0 + 0.5 * i + 8.0 * math.sin(i / 3.0) for i in range(n)]
    candles = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i > 0 else close
        high = max(open_, close) + 1.0
        low = min(open_, close) - 1.0
        candles.append({
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0,
            "timestamp_ms": base_ts + i * 60_000,
        })
    return candles


def _assert_metric_sane(value, closed_trades, label):
    """A reported metric is the "n/a" sentinel xor a finite number, per closure.

    With zero closed trades the metric must be the ``"n/a"`` sentinel (never a
    divide-by-zero); with closed trades it must be a finite, non-bool number.
    """
    if closed_trades == 0:
        assert value == _NA, f"{label}: expected '{_NA}' with 0 closed trades, got {value!r}"
    else:
        assert isinstance(value, (int, float)) and not isinstance(value, bool), (
            f"{label}: expected a number with {closed_trades} closed trades, got {value!r}"
        )
        assert math.isfinite(value), f"{label}: expected a finite number, got {value!r}"


def _assert_run_reports_all_metrics(run, label):
    """Assert one run reports present, sane win-rate / expectancy / downside (R12.2, R12.3)."""
    for key in ("win_rate", "expectancy", "downside"):
        assert key in run, f"{label}: missing metric key {key!r}"

    closed = run["closed_trades"]
    assert isinstance(closed, int) and closed >= 0, f"{label}: bad closed_trades {closed!r}"

    # win_rate and expectancy follow the closure rule exactly (R12.2).
    _assert_metric_sane(run["win_rate"], closed, f"{label}.win_rate")
    _assert_metric_sane(run["expectancy"], closed, f"{label}.expectancy")

    # downside is "n/a" with zero closed trades OR when there is no losing
    # population; otherwise it is a finite number (R12.3).
    downside = run["downside"]
    if closed == 0:
        assert downside == _NA, f"{label}.downside: expected '{_NA}' with 0 closed, got {downside!r}"
    else:
        assert downside == _NA or (
            isinstance(downside, (int, float)) and not isinstance(downside, bool)
            and math.isfinite(downside)
        ), f"{label}.downside: expected '{_NA}' or a finite number, got {downside!r}"


def test_compare_management_reports_metrics_for_both_runs():
    """Validates: Requirements 12.2, 12.3

    Over a small fixed candle history, ``compare_management`` reports a win-rate,
    an expectancy, and a downside measure for BOTH the managed and the unmanaged
    run: each key is present and each value is either a finite number (when the
    run produced closed trades) or the ``"n/a"`` sentinel (zero closed trades).
    """
    candles = _fixed_candle_series()

    summary = backtest.compare_management(
        "TESTSYM", "1d", candles=candles, benchmark_candles=[], cfg=_CFG,
    )

    # Both runs are present in the summary.
    assert "managed" in summary and "unmanaged" in summary

    _assert_run_reports_all_metrics(summary["unmanaged"], "unmanaged")
    _assert_run_reports_all_metrics(summary["managed"], "managed")

    # The fixed series is built to emit several signals that resolve, so both runs
    # actually exercise the NUMERIC metric path (not only the n/a sentinel) — this
    # makes the "metrics are reported" assertion meaningful rather than vacuous.
    assert summary["unmanaged"]["closed_trades"] > 0, (
        "expected the fixed series to produce closed unmanaged trades"
    )
    assert summary["managed"]["closed_trades"] > 0, (
        "expected the fixed series to produce closed managed trades"
    )


def test_managed_run_applies_default_plan_uniformly():
    """Validates: Requirements 12.5

    The managed run applies the configured default ``Management_Plan`` UNIFORMLY:
    every seeded/scored signal carries the SAME non-single managed style (the
    ``scale-be-trail`` produced by ``trade_manager.default_management_plan`` under
    the resolved config) and a present/non-null serialized ``management_plan``.
    The unmanaged run, by contrast, tags every signal ``tm:single``.
    """
    candles = _fixed_candle_series()

    unmanaged = generate_and_score(
        candles, "TESTSYM", "1d", replace(_CFG, manage_trades=False), benchmark_candles=[],
    )
    managed = generate_and_score(
        candles, "TESTSYM", "1d", replace(_CFG, manage_trades=True), benchmark_candles=[],
    )

    # The fixed series emits a handful of signals in both runs (so "uniform across
    # all signals" is a non-vacuous claim).
    assert len(managed) > 1, "expected the fixed series to produce several managed signals"
    assert len(unmanaged) == len(managed), (
        f"signal count differs: unmanaged={len(unmanaged)} managed={len(managed)}"
    )

    # The expected uniform style is whatever the default plan collapses to under
    # the resolved Trade_Manager config — derived from the SAME helpers the
    # managed run uses (no hard-coded string), so this stays correct if the
    # documented defaults change. It must be a genuine multi-leg managed style
    # (not "single") so the contrast with the unmanaged run is real.
    tm_config = trade_manager.resolve_trade_manager_config()
    expected_style = trade_manager.management_style_tag(
        trade_manager.default_management_plan("BUY", 100.0, 98.0, 1.0, tm_config)
    )
    assert expected_style != "single", (
        f"the default management plan must be a multi-leg managed style, got {expected_style!r}"
    )
    assert expected_style == "scale-be-trail", (
        "the default plan (scale-out + breakeven + trail) is expected to collapse "
        f"to 'scale-be-trail', got {expected_style!r}"
    )

    # ── Managed run: uniform default plan on EVERY signal ────────────────────
    for idx, r in enumerate(managed):
        decision = r["decision"]

        mgmt = decision.get("defensibility", {}).get("management")
        assert isinstance(mgmt, dict), f"managed signal {idx}: missing management defensibility entry"
        assert mgmt.get("style") == expected_style, (
            f"managed signal {idx}: style {mgmt.get('style')!r} != uniform default {expected_style!r}"
        )

        # A present, non-null serialized plan that round-trips back to a plan
        # whose style matches (the persistence boundary the journal re-scores from).
        plan_json = decision.get("management_plan")
        assert plan_json is not None, f"managed signal {idx}: management_plan is null"
        round_tripped = trade_manager.plan_from_json(plan_json)
        assert round_tripped is not None, f"managed signal {idx}: management_plan failed to deserialize"
        assert trade_manager.management_style_tag(round_tripped) == expected_style, (
            f"managed signal {idx}: persisted plan style does not match the uniform default"
        )

    # ── Unmanaged run: every signal is the single-target trade (tm:single) ───
    for idx, r in enumerate(unmanaged):
        mgmt = r["decision"].get("defensibility", {}).get("management")
        assert isinstance(mgmt, dict), f"unmanaged signal {idx}: missing management defensibility entry"
        assert mgmt.get("style") == "single", (
            f"unmanaged signal {idx}: expected 'single', got {mgmt.get('style')!r}"
        )
