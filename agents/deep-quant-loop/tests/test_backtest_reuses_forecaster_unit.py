"""Backtest reuses the authoritative Volatility_Aware_Forecaster (task 14.3).

Feature: volatility-aware-forecaster

Requirement 13.5: the Backtest_Seeder must REUSE the same forecaster functions
the live ``get_forecast`` tool path uses — it must NOT reimplement the
drift / volatility / regime-conditioned-blend / logistic-probability math.
``backtest.generate_and_score`` is expected to:

  * resolve the forecaster parameters via ``forecaster.resolve_forecaster_config()``
    (the single shared resolver), and
  * classify each candidate signal's forecast via ``forecaster.forecast(...)``
    over a point-in-time candle slice ``candles[: i + 1]`` (no look-ahead),

rather than computing the forecast math inline.

This is an example-based unit test. It proves reuse two ways:

  1. Structurally — it confirms ``backtest`` imports the very same ``forecaster``
     module object, and that the source of ``generate_and_score`` references
     ``forecaster.forecast`` over the point-in-time slice ``candles[: i + 1]`` and
     ``forecaster.resolve_forecaster_config`` and does NOT reimplement the
     drift / volatility / logistic-probability math.
  2. Behaviorally — it wraps ``forecaster.forecast`` with a ``MagicMock`` spy
     (``wraps`` the real function so behaviour is unchanged), runs
     ``generate_and_score`` over a small, deterministic, OFFLINE synthetic candle
     series engineered to emit at least one signal, and asserts the spy was
     invoked with a point-in-time slice (the ``candles`` argument is a non-empty
     prefix no longer than the full series — never the full future).

No network, Rust tool server, or QuestDB is involved: candles are passed
directly to ``generate_and_score``.
"""

import inspect
import math
import os
import sys
from unittest import mock

# Make the service package importable (backtest.py / forecaster.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
import forecaster  # noqa: E402
from backtest import BacktestConfig, generate_and_score  # noqa: E402


def _make_signal_producing_candles(n: int = 200) -> list:
    """Deterministic synthetic series engineered to emit at least one signal.

    A gentle uptrend (so the EMA bias flips around) with a periodic sharp dip
    drives price to/through the value-area edges, which is exactly what the
    backtest's rule set keys off. Fully offline and reproducible. Mirrors the
    sibling regime-reuse test's generator.
    """
    candles = []
    base_ts = 1_700_000_000_000
    for i in range(n):
        trend = i * 0.5
        wobble = 5.0 * math.sin(i / 7.0)
        dip = -8.0 if (i % 40) in (0, 1, 2) else 0.0
        close = 100.0 + trend + wobble + dip
        openp = close - 0.3
        high = max(openp, close) + 1.0
        low = min(openp, close) - 1.0
        candles.append({
            "timestamp_ms": base_ts + i * 900_000,
            "open": openp,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0 + (i % 5) * 100.0,
        })
    return candles


def test_backtest_imports_the_shared_forecaster_module():
    """``backtest.forecaster`` is the SAME module object as the forecaster package."""
    assert backtest.forecaster is forecaster


def test_generate_and_score_source_references_forecaster_and_avoids_reimplementing_math():
    """Structural proof: the source delegates and does not reimplement the math."""
    src = inspect.getsource(generate_and_score)

    # Delegates to the shared forecaster module.
    assert "forecaster.forecast" in src, (
        "generate_and_score must call forecaster.forecast"
    )
    assert "forecaster.resolve_forecaster_config" in src, (
        "generate_and_score must resolve config via forecaster.resolve_forecaster_config"
    )

    # Uses a point-in-time slice (look-ahead-free, R13.1): the forecast sees only
    # candles at or before the signal bar.
    assert "candles[: i + 1]" in src, (
        "generate_and_score must forecast over the point-in-time slice candles[: i + 1]"
    )

    # Does not reimplement the forecaster math inline. The forecaster owns the
    # drift / volatility / regime-conditioned-blend / logistic-probability
    # computation; the backtest must not recompute it. These tokens are the
    # forecaster's internal math primitives — they belong in forecaster.py, never
    # inlined here. (Note: a comment may legitimately MENTION "Up_Probability" in
    # the context of journal persistence, so the field name itself is not a
    # forbidden token; the math primitives below are.)
    lowered = src.lower()
    for forbidden in (
        "math.exp",
        "prob_scale",
        "compute_drift(",
        "compute_volatility(",
        "conditioned_drift(",
        "up_probability(",
        "classify_direction(",
        "forecast_confidence(",
        "logistic",
        "1 / (1 +",
        "1.0 / (1.0 +",
    ):
        assert forbidden not in lowered, (
            f"generate_and_score should not reimplement forecast math (found '{forbidden}')"
        )


def test_generate_and_score_delegates_to_forecaster_forecast():
    """Behavioral proof of reuse: the backtest calls into ``forecaster.forecast``.

    Wrap ``forecaster.forecast`` with a ``MagicMock(wraps=...)`` spy (real
    behaviour preserved), run the seeder over a signal-producing series, and
    assert the backtest delegated classification to ``forecaster.forecast`` over a
    point-in-time slice rather than computing it inline.
    """
    candles = _make_signal_producing_candles()
    cfg = BacktestConfig(lookback=40)

    spy = mock.MagicMock(wraps=forecaster.forecast)
    # backtest references it as ``forecaster.forecast``, so patching the module
    # attribute exercises the real delegation path.
    with mock.patch.object(forecaster, "forecast", spy):
        results = generate_and_score(candles, "TEST", "15m", cfg)

    # The series is engineered to produce signals; each signal triggers a
    # point-in-time forecast.
    assert results, "expected the synthetic series to generate at least one signal"

    # Every emitted signal delegates to the shared forecaster.
    assert spy.call_count >= 1, "backtest must classify forecasts via forecaster.forecast"
    assert spy.call_count >= len(results), (
        "each scored signal should have been classified via forecaster.forecast"
    )

    # Each call uses a point-in-time slice (look-ahead-free, R13.1): the candles
    # argument is a non-empty prefix no longer than the full history — i.e. the
    # window at/before the signal bar, never the full forward series.
    n = len(candles)
    for call in spy.call_args_list:
        passed_candles = call.args[0] if call.args else call.kwargs.get("candles")
        assert isinstance(passed_candles, list) and passed_candles, (
            "forecaster.forecast must receive a non-empty candle window"
        )
        assert len(passed_candles) <= n, (
            "forecaster.forecast must receive a point-in-time slice, not a superset"
        )
        # The slice must be a genuine prefix of the full series (the bars at or
        # before the signal), not the full series including future bars.
        assert passed_candles == candles[: len(passed_candles)], (
            "forecaster.forecast must receive the candles[: i + 1] prefix (no look-ahead)"
        )

    # The signal's direction is passed as proposed_direction so the alignment is
    # computed against the trade (mirrors the live tool path).
    assert any(
        (call.kwargs.get("proposed_direction") in ("BUY", "SELL"))
        for call in spy.call_args_list
    ), "the signal's direction must be passed to forecaster.forecast as proposed_direction"
