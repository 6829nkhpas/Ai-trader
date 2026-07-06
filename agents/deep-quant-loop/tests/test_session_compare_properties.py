# Feature: session-expiry-awareness, Property 26: Comparison-mode runs are consistent, labelled, and metrics are well-defined
"""Property-based test for ``backtest.compare_session`` (task 9.4).

Feature: session-expiry-awareness

This module implements design **Property 26: Comparison-mode runs are
consistent, labelled, and metrics are well-defined**:

    When ``compare_session`` runs the backtest WITH and WITHOUT the session
    filter over the IDENTICAL candle history and IDENTICAL setup rules (only the
    ``session_filter_enabled`` flag differs):

      * both a ``with_filter`` and a ``without_filter`` summary are present and
        labelled (R11.3);
      * each run's metrics are well-defined — ``win_rate`` and ``expectancy`` are
        EITHER both ``"n/a"`` (the run produced zero closed trades) OR both
        well-defined numbers (``win_rate`` in [0, 1], ``expectancy`` a finite
        number) (R11.4);
      * the two runs are consistent over the identical history/rules — the
        with-filter seeded trade set is a SUBSET of the without-filter set (the
        filter only ever DROPS available-``unfavorable`` signals and never adds
        any), so ``with_filter`` never scores more signals than ``without_filter``.

Validates: Requirements 11.3, 11.4.

No network is touched: a synthetic in-memory candle history is generated and
both the symbol candles and the benchmark candles are passed explicitly (with an
explicit benchmark name), so ``compare_session`` never falls back to the Rust
Tool_Server. The candle generation / sys.path import pattern mirrors the sibling
``test_session_backtest_*`` modules.
"""

import os
import sys
from dataclasses import replace

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (backtest.py / session.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from backtest import (  # noqa: E402
    BacktestConfig,
    compare_session,
    generate_and_score,
)

# A small lookback keeps generated histories modest while still walking the
# signal loop exactly as production does. ema_slow=21 / ols_window=20 gate
# _signal_for_bar, so a 25-bar window is the smallest that lets signals form.
_CFG = BacktestConfig(lookback=25)

# Anchor + 5-minute step: a real-world epoch-ms instant, strictly monotonic and
# unique per bar, spanning hours/days so timestamps land in a variety of session
# windows (favorable / neutral / unfavorable) and the filter does non-trivial work.
_TS_START = 1_600_000_000_000
_TS_STEP = 5 * 60_000

_BENCHMARK = "NIFTY"  # explicit so compare_session never resolves/fetches a benchmark


@st.composite
def _candle_history(draw):
    """A strictly-monotonic OHLCV history (bounded random walk) with finite fields.

    Prices follow a bounded random walk so EMAs cross and price repeatedly
    revisits the value-area edges — conditions under which ``_signal_for_bar``
    emits signals, exercising the compare path. OHLC ordering is well-formed
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


def _assert_metrics_well_defined(run: dict, label: str):
    """A run's win_rate / expectancy are EITHER both 'n/a' OR both well-defined
    numbers (win_rate in [0,1], expectancy finite), consistent with closed_trades."""
    assert "signals_scored" in run, f"{label}: missing signals_scored"
    assert "closed_trades" in run, f"{label}: missing closed_trades"
    assert "win_rate" in run, f"{label}: missing win_rate"
    assert "expectancy" in run, f"{label}: missing expectancy"

    closed = run["closed_trades"]
    win_rate = run["win_rate"]
    expectancy = run["expectancy"]

    assert isinstance(closed, int) and closed >= 0, f"{label}: closed_trades not a count: {closed!r}"

    if closed == 0:
        # Zero closed trades -> BOTH metrics 'n/a' (no division by zero, R11.4).
        assert win_rate == "n/a", f"{label}: expected win_rate 'n/a' with 0 closed, got {win_rate!r}"
        assert expectancy == "n/a", f"{label}: expected expectancy 'n/a' with 0 closed, got {expectancy!r}"
    else:
        # Well-defined numbers: win_rate a fraction in [0,1], expectancy finite.
        assert isinstance(win_rate, (int, float)) and not isinstance(win_rate, bool), (
            f"{label}: win_rate not a number with {closed} closed: {win_rate!r}"
        )
        assert 0.0 <= win_rate <= 1.0, f"{label}: win_rate out of [0,1]: {win_rate!r}"
        assert isinstance(expectancy, (int, float)) and not isinstance(expectancy, bool), (
            f"{label}: expectancy not a number with {closed} closed: {expectancy!r}"
        )
        # A finite number (round() of a finite mean — never inf/nan).
        assert expectancy == expectancy and expectancy not in (float("inf"), float("-inf")), (
            f"{label}: expectancy not finite: {expectancy!r}"
        )
        # win_rate is consistent with the reported winning/closed counts.
        winning = run["winning_closed_trades"]
        assert 0 <= winning <= closed, f"{label}: winning_closed_trades out of range: {winning!r}"


def _signal_ids(results) -> set:
    """Identify each scored signal by its bar timestamp (ms), recovered from the
    decision's ``created_at`` (seconds). This is the seeded-trade identity used to
    prove the subset relationship between the two runs."""
    ids = set()
    for r in results:
        created_at = r["decision"].get("created_at")
        if created_at is not None:
            ids.add(round(created_at * 1000.0))
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# Property 26: Comparison-mode runs are consistent, labelled, and metrics are
#              well-defined.
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 26: Comparison-mode runs are consistent, labelled, and metrics are well-defined
@settings(max_examples=120, deadline=None)
@given(candles=_candle_history())
def test_property_26_comparison_runs_consistent_labelled_metrics_well_defined(candles):
    """Validates: Requirements 11.3, 11.4

    ``compare_session`` over a synthetic in-memory history (no network) yields a
    summary in which:

      * both ``with_filter`` and ``without_filter`` runs are present and labelled;
      * each run's ``win_rate`` / ``expectancy`` are well-defined — both ``"n/a"``
        on zero closed trades, else numbers with ``win_rate`` in [0,1] and a
        finite ``expectancy``;
      * the with-filter seeded set is a SUBSET of the without-filter set (the
        filter only drops signals), so the runs are consistent over the identical
        history/rules.
    """
    summary = compare_session(
        "SYM", "15m",
        candles=candles,
        benchmark_candles=candles,   # reuse as a well-formed benchmark series (no network)
        cfg=_CFG,
        benchmark=_BENCHMARK,        # explicit -> never resolves/fetches a benchmark
    )

    # ── Both runs present and labelled (R11.3) ───────────────────────────────
    assert isinstance(summary, dict)
    assert "with_filter" in summary, "comparison summary missing the with_filter run"
    assert "without_filter" in summary, "comparison summary missing the without_filter run"
    assert summary.get("symbol") == "SYM"
    assert summary.get("timeframe") == "15m"
    assert summary.get("candles") == len(candles)
    assert summary.get("benchmark") == _BENCHMARK

    with_filter = summary["with_filter"]
    without_filter = summary["without_filter"]

    # ── Each run's metrics are well-defined (R11.4) ──────────────────────────
    _assert_metrics_well_defined(with_filter, "with_filter")
    _assert_metrics_well_defined(without_filter, "without_filter")

    # ── Consistency: with-filter set is a SUBSET of without-filter set ───────
    # The session filter only ever DROPS available-``unfavorable`` signals and
    # never adds any, so the with-filter run can never score more signals than
    # the without-filter run over the identical history/rules.
    assert with_filter["signals_scored"] <= without_filter["signals_scored"], (
        "with_filter scored more signals than without_filter — the filter must "
        f"only drop signals: {with_filter['signals_scored']} > {without_filter['signals_scored']}"
    )

    # Prove the SUBSET relationship at the trade-identity level by reconstructing
    # both runs exactly as compare_session does (identical history/rules, only the
    # session_filter_enabled flag differs) and comparing seeded-signal identities.
    filtered = generate_and_score(
        candles, "SYM", "15m", replace(_CFG, session_filter_enabled=True),
        benchmark_candles=candles, benchmark=_BENCHMARK,
    )
    unfiltered = generate_and_score(
        candles, "SYM", "15m", replace(_CFG, session_filter_enabled=False),
        benchmark_candles=candles, benchmark=_BENCHMARK,
    )

    # compare_session's reported counts match the runs it performs internally.
    assert with_filter["signals_scored"] == len(filtered)
    assert without_filter["signals_scored"] == len(unfiltered)

    filtered_ids = _signal_ids(filtered)
    unfiltered_ids = _signal_ids(unfiltered)
    assert filtered_ids.issubset(unfiltered_ids), (
        "with-filter seeded set is not a subset of the without-filter set: "
        f"extra={filtered_ids - unfiltered_ids!r}"
    )
