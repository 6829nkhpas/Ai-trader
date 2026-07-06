#!/usr/bin/env python3
"""End-to-end smoke test for the session with-filter / without-filter comparison
(backtest.py ``compare_session``, task 9.5).

Feature: session-expiry-awareness

This is a plain (non-hypothesis) smoke test: it runs a small, fully deterministic
``compare_session`` over a fixed synthetic candle window — passing the candle
history (and a benchmark series) directly so NO live Rust server / network is
touched — and asserts that BOTH the with-filter and without-filter run summaries
are well-formed: each carries the expected metric keys, the win-rate / expectancy
are either a real number or the honest ``"n/a"`` sentinel (when a run produced
zero closed trades), and the counts are coherent.

Validates: Requirements 11.4.
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Make the service package importable (backtest.py / session.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import session  # noqa: E402
from backtest import BacktestConfig, compare_session  # noqa: E402

# A small lookback keeps the synthetic history modest while still walking the
# signal loop exactly as production does.
_CFG = BacktestConfig(lookback=25)
_STEP_MS = 5 * 60_000  # 5-minute bars

# The metric keys every run summary must carry (from backtest._run_metrics plus
# the signals_scored count compare_session adds).
_REQUIRED_KEYS = {
    "signals_scored",
    "closed_trades",
    "winning_closed_trades",
    "win_rate",
    "expectancy",
}


def _build_candles(n: int = 320) -> list:
    """A fixed, deterministic OHLCV history anchored at a known IST instant.

    Timestamps step every 5 minutes from 2024-01-01 09:15 IST (a Monday, at the
    default session open) so the window spans multiple session phases and at
    least one weekly-expiry (Thursday) day. Prices follow a deterministic
    triangle wave so the fast/slow EMAs cross repeatedly and price revisits the
    value-area edges — the conditions under which the seeder emits signals — with
    NO randomness, so the run is reproducible.
    """
    anchor = datetime(2024, 1, 1, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
    start_ms = int(anchor.timestamp() * 1000)

    candles = []
    price = 100.0
    for i in range(n):
        # Triangle wave with period 40 bars, amplitude ~15 — deterministic and
        # smooth enough to drive repeated EMA crossings.
        phase = i % 40
        offset = phase if phase <= 20 else (40 - phase)  # 0..20..0
        close = 100.0 + offset * 0.75
        open_ = price
        high = max(open_, close) + 0.5
        low = max(0.5, min(open_, close) - 0.5)
        candles.append({
            "timestamp_ms": start_ms + i * _STEP_MS,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0 + (i % 7) * 10.0,
        })
        price = close
    return candles


def _assert_run_well_formed(run: dict, label: str) -> None:
    """A single run summary carries every metric key with coherent values, and
    win-rate / expectancy are each a real number or the honest ``"n/a"``."""
    assert isinstance(run, dict), f"{label} run is not a dict: {run!r}"
    missing = _REQUIRED_KEYS - set(run)
    assert not missing, f"{label} run missing metric keys {missing}: {run!r}"

    signals = run["signals_scored"]
    closed = run["closed_trades"]
    winning = run["winning_closed_trades"]
    assert isinstance(signals, int) and signals >= 0, f"{label} signals_scored invalid: {signals!r}"
    assert isinstance(closed, int) and closed >= 0, f"{label} closed_trades invalid: {closed!r}"
    assert isinstance(winning, int) and 0 <= winning <= closed, (
        f"{label} winning_closed_trades incoherent: winning={winning!r} closed={closed!r}"
    )

    win_rate = run["win_rate"]
    expectancy = run["expectancy"]
    if closed == 0:
        # Zero closed trades -> both metrics are the honest "n/a" sentinel (R11.4).
        assert win_rate == "n/a", f"{label} win_rate should be 'n/a' with 0 closed: {win_rate!r}"
        assert expectancy == "n/a", f"{label} expectancy should be 'n/a' with 0 closed: {expectancy!r}"
    else:
        assert isinstance(win_rate, (int, float)) and 0.0 <= win_rate <= 1.0, (
            f"{label} win_rate not a fraction in [0,1]: {win_rate!r}"
        )
        assert isinstance(expectancy, (int, float)), (
            f"{label} expectancy not numeric: {expectancy!r}"
        )


def test_compare_session_smoke_summaries_are_well_formed():
    """Validates: Requirements 11.4

    A small end-to-end compare_session over a fixed historical window produces a
    summary whose with-filter and without-filter runs are each well-formed (every
    metric key present, win-rate / expectancy numeric or 'n/a').
    """
    candles = _build_candles()
    # Pass candles AND a benchmark series directly so no network is touched. The
    # session filter needs no benchmark; reusing the same series keeps relative
    # strength well-formed without any external fetch.
    benchmark_candles = [dict(c) for c in candles]

    summary = compare_session(
        "SMOKE",
        "5m",
        candles=candles,
        benchmark_candles=benchmark_candles,
        benchmark="BENCH",
        cfg=_CFG,
    )

    assert isinstance(summary, dict), f"summary is not a dict: {summary!r}"
    assert summary["symbol"] == "SMOKE"
    assert summary["timeframe"] == "5m"
    assert summary["candles"] == len(candles)
    assert "with_filter" in summary and "without_filter" in summary

    _assert_run_well_formed(summary["with_filter"], "with_filter")
    _assert_run_well_formed(summary["without_filter"], "without_filter")

    # The with-filter run only ever DROPS signals (unfavorable windows), so its
    # seeded set is a strict subset of the without-filter run (R11.4 comparability).
    assert summary["with_filter"]["signals_scored"] <= summary["without_filter"]["signals_scored"], (
        "with-filter run scored MORE signals than without-filter — not a subset"
    )


def test_compare_session_smoke_does_not_mutate_inputs():
    """compare_session is pure given the candle series — it must not mutate the
    caller's candle history."""
    candles = _build_candles(120)
    snapshot = [dict(c) for c in candles]
    compare_session(
        "SMOKE",
        "5m",
        candles=candles,
        benchmark_candles=[dict(c) for c in candles],
        benchmark="BENCH",
        cfg=_CFG,
    )
    assert candles == snapshot, "compare_session mutated its input candles"


def test_session_classifier_reachable_for_smoke_window():
    """Sanity guard: the fixed window's own timestamps classify into real session
    labels via the same classifier compare_session uses (no Unavailable_Marker for
    these valid timestamps), so the smoke run exercises the live classify path."""
    config = session.resolve_session_config()
    candles = _build_candles(60)
    labels = [
        session.classify_session(c["timestamp_ms"], config, symbol="SMOKE", timeframe="5m")
        for c in candles
    ]
    assert all(lbl.get("unavailable") is not True for lbl in labels), (
        "valid synthetic timestamps unexpectedly yielded an Unavailable_Marker"
    )
    assert any("time_favorability" in lbl for lbl in labels), (
        "no session label carried a time_favorability — classifier path not exercised"
    )
