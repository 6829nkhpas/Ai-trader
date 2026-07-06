"""Unit test: ``calibrate_forecast`` reuses the forecaster (backtest.py, task 15.2).

Feature: volatility-aware-forecaster

Requirement 12.5: the backtest calibration (reliability) measurement must REUSE
the same forecaster the live ``get_forecast`` tool path uses — it must NOT
reimplement the forecast math. ``backtest.calibrate_forecast`` is expected to:

  * resolve the forecaster parameters via ``forecaster.resolve_forecaster_config()``
    (the single shared resolver), and
  * compute each bar's forecast via ``forecaster.forecast`` over a POINT-IN-TIME
    candle slice ``candles[: i + 1]`` (the window at or before bar ``i`` — no
    look-ahead),

rather than recomputing the logistic / drift / volatility math inline.

These are plain example-based unit tests (not property-based). They prove reuse
two ways:

  1. Structurally — ``backtest`` imports the very same ``forecaster`` module
     object, and the source of ``calibrate_forecast`` references
     ``forecaster.forecast`` / ``resolve_forecaster_config`` / ``candles[: i + 1]``
     and does not reimplement the logistic/drift math.
  2. Behaviorally — a counting spy that *wraps* the real ``forecaster.forecast``
     confirms ``calibrate_forecast`` actually calls the forecaster multiple times
     and that every call receives a point-in-time prefix of the candle history
     (length ``i + 1`` for increasing ``i``, never the full future), and that the
     returned report carries ``bins`` / ``calibration_error`` / ``total_records``.

No network, Rust tool server, or QuestDB is involved: candles are passed directly
to ``calibrate_forecast`` via ``candles=...``.
"""

import inspect
import os
import sys
from unittest import mock

# Make the service package importable (backtest.py / forecaster.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
import forecaster  # noqa: E402
from backtest import calibrate_forecast  # noqa: E402


def _make_sufficient_candles(n: int = 45) -> list:
    """A deterministic, valid OHLCV series long enough to clear the gate.

    The default forecaster config requires ``max(min_candles=30,
    largest_lookback=21) == 30`` valid candles before a usable Forecast_Label is
    produced; 45 candles means the later bars clear the gate and yield several
    usable (Up_Probability, realized-next-bar) calibration records. A gentle
    uptrend with a small deterministic wobble keeps closes positive and the
    returns non-degenerate (so it is neither an insufficient-data marker nor a
    zero-variance short-circuit).
    """
    candles = []
    base_ts = 1_700_000_000_000
    price = 100.0
    for i in range(n):
        price = price + 0.5 + (1.0 if i % 3 == 0 else -0.5)
        open_ = price - 0.3
        close = price
        high = max(open_, close) + 1.0
        low = min(open_, close) - 1.0
        candles.append({
            "timestamp_ms": base_ts + i * 900_000,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0 + (i % 5) * 100.0,
        })
    return candles


def test_backtest_imports_the_shared_forecaster_module():
    """``backtest.forecaster`` is the SAME module object as the forecaster package."""
    assert backtest.forecaster is forecaster


def test_calibrate_forecast_source_reuses_forecaster_and_avoids_reimplementing_math():
    """Structural proof: the source delegates to the forecaster point-in-time.

    Validates: Requirements 12.5
    """
    src = inspect.getsource(backtest.calibrate_forecast)

    # Delegates to the shared forecaster module rather than reimplementing it.
    assert "forecaster.forecast" in src, (
        "calibrate_forecast must call forecaster.forecast"
    )
    assert "resolve_forecaster_config" in src, (
        "calibrate_forecast must resolve config via forecaster.resolve_forecaster_config"
    )

    # The forecast is computed from a point-in-time prefix (look-ahead-free).
    assert "candles[: i + 1]" in src, (
        "calibrate_forecast must forecast over the point-in-time slice candles[: i + 1]"
    )

    # Does not reimplement the forecast math inline. The forecaster owns the
    # logistic Up_Probability and the drift / volatility estimation; the
    # calibration must not recompute them.
    # Note: reading the forecaster's output (``up_probability = fc_result.get(
    # "up_probability")``) is delegation, not reimplementation, so the forbidden
    # tokens below target the forecaster's MATH primitives (the function-call /
    # logistic forms), never the bare field-name assignment.
    lowered = src.lower()
    for forbidden in (
        "compute_drift(", "compute_volatility(", "up_probability(",
        "classify_direction(", "conditioned_drift(", "forecast_confidence(",
        "logistic", "1 / (1 + exp", "1.0 / (1.0 +", "math.exp(", "prob_scale",
    ):
        assert forbidden not in lowered, (
            f"calibrate_forecast should not reimplement the forecast math "
            f"(found '{forbidden}')"
        )


def test_calibrate_forecast_delegates_to_forecaster_point_in_time():
    """Behavioral proof of reuse: each call is a point-in-time prefix.

    Wrap the real ``forecaster.forecast`` with a counting spy
    (``wraps=forecaster.forecast``) so the genuine forecast math still runs, then
    run ``calibrate_forecast`` over a small synthetic series passed directly via
    ``candles=...`` (offline). Assert the forecaster was invoked multiple times
    and that every call received a point-in-time prefix of the candle history
    (length ``i + 1`` for increasing ``i``, never the full future).

    Validates: Requirements 12.5
    """
    candles = _make_sufficient_candles(45)

    spy = mock.MagicMock(wraps=forecaster.forecast)
    with mock.patch.object(backtest.forecaster, "forecast", spy):
        report = calibrate_forecast("TEST", "15m", candles=candles)

    # The forecaster is consulted repeatedly — one forecast per bar with a valid
    # next bar (range(n - 1)).
    assert spy.call_count >= 2, (
        "calibrate_forecast must call forecaster.forecast for multiple bars"
    )
    assert spy.call_count == len(candles) - 1, (
        "calibrate_forecast should forecast once per bar with a valid next bar"
    )

    # Every call receives a point-in-time prefix: the candle argument has length
    # i + 1 for increasing i (1, 2, 3, ...), is the exact prefix of the full
    # history, and is NEVER the full future (length == len(candles)).
    n = len(candles)
    for i, call in enumerate(spy.call_args_list):
        passed_candles = call.args[0] if call.args else call.kwargs.get("candles")
        assert isinstance(passed_candles, list)
        assert len(passed_candles) == i + 1, (
            "each forecast must use the point-in-time window candles[: i + 1]"
        )
        assert len(passed_candles) < n, (
            "no forecast may see the full future candle history (look-ahead)"
        )
        assert passed_candles == candles[: i + 1], (
            "the forecast window must be the exact point-in-time prefix"
        )

    # The returned reliability report is well-formed.
    assert isinstance(report, dict)
    assert "bins" in report
    assert "calibration_error" in report
    assert "total_records" in report
    assert isinstance(report["bins"], list)
    # The later bars clear the gate, so at least one usable prediction was paired
    # with its realized next-bar direction.
    assert report["total_records"] >= 1, (
        "a sufficient series should yield at least one calibration record"
    )
