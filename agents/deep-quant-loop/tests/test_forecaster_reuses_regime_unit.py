"""Unit test: ``forecast`` reuses ``regime.classify_regime`` (forecaster.py, task 4.10).

Feature: volatility-aware-forecaster

Requirements 2.1, 2.5: the Volatility_Aware_Forecaster must obtain its trend
classification from the EXISTING Regime_Classifier rather than reimplementing the
regime math. Concretely, ``forecaster.forecast`` is expected to obtain its trend
state by calling ``regime.classify_regime`` (via the internal
``_regime_trend_state`` helper, which calls
``regime.classify_regime(candles, regime.resolve_regime_config())``).

These are plain example-based unit tests (not property-based). They prove reuse
two ways:

  1. Behaviorally — a counting spy that *wraps* the real
     ``forecaster.regime.classify_regime`` confirms ``forecast`` actually calls
     the classifier (at least once) when given a sufficient candle sequence, and
     a ``MagicMock`` returning a fixed Regime_Label confirms the label's
     ``regime_trend_state`` reflects the classifier's ``trend_state`` — i.e. the
     forecaster reads its trend state FROM the classifier, not from reimplemented
     regime logic.
  2. Structurally — the source of the regime helper references
     ``regime.classify_regime`` / ``regime.resolve_regime_config`` rather than
     recomputing ADX / choppiness regime measures inline.

The candle generator, sys.path / import pattern, and config-via-
``resolve_forecaster_config()`` mirror the sibling forecaster test modules. No
network, Rust tool server, or QuestDB is involved: candles are passed directly to
``forecast``.
"""

import inspect
import os
import sys
from unittest import mock

# Make the service package importable (forecaster.py / regime.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import forecaster  # noqa: E402
import regime  # noqa: E402
from forecaster import forecast, resolve_forecaster_config  # noqa: E402

_FORECAST_DIRECTIONS = {"up", "down", "flat"}


def _make_sufficient_candles(n: int = 60) -> list:
    """A deterministic, valid OHLCV series long enough to clear the gate.

    The default config requires ``max(min_candles=30, largest_lookback=21) == 30``
    valid candles; 60 comfortably clears it. A gentle uptrend with a small wobble
    keeps the closes positive and the returns non-degenerate so a real forecast
    (not an insufficient-data marker, not a zero-variance short-circuit) is
    produced.
    """
    candles = []
    base_ts = 1_700_000_000_000
    price = 100.0
    for i in range(n):
        # Gentle drift plus a deterministic wobble keeps variance positive.
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


def test_forecaster_imports_the_shared_regime_module():
    """``forecaster.regime`` is the SAME module object as the regime package."""
    assert forecaster.regime is regime


def test_forecast_calls_regime_classify_regime():
    """Behavioral proof of reuse: ``forecast`` delegates to ``regime.classify_regime``.

    Wrap the real classifier with a counting spy (``wraps=regime.classify_regime``)
    so the genuine regime math still runs, then assert ``forecast`` invoked it at
    least once for a sufficient candle sequence — i.e. it obtains its trend state
    from the classifier rather than reimplementing regime math.

    Validates: Requirements 2.1, 2.5
    """
    config = resolve_forecaster_config()
    candles = _make_sufficient_candles()

    spy = mock.MagicMock(wraps=regime.classify_regime)
    with mock.patch.object(forecaster.regime, "classify_regime", spy):
        result = forecast(candles, config, proposed_direction="up")

    # The forecaster obtained its trend state by calling the shared classifier.
    assert spy.call_count >= 1, (
        "forecast must obtain its trend state via regime.classify_regime"
    )

    # A sufficient, non-degenerate series yields a usable Forecast_Label (not an
    # insufficient-data marker), and the label records a regime trend state.
    assert isinstance(result, dict)
    assert not result.get("unavailable"), (
        "a sufficient candle series should produce a Forecast_Label, not a marker"
    )
    assert result["projected_direction"] in _FORECAST_DIRECTIONS
    assert "regime_trend_state" in result


def test_forecast_regime_trend_state_reflects_classifier_output():
    """The label's ``regime_trend_state`` mirrors the mocked classifier's ``trend_state``.

    Replace ``regime.classify_regime`` with a ``MagicMock`` returning a fixed
    Regime_Label whose ``trend_state`` is ``"trending"``; the resulting
    Forecast_Label must record that exact trend state — proving the forecaster
    reads its trend state FROM the classifier rather than computing its own.

    Validates: Requirements 2.1, 2.5
    """
    config = resolve_forecaster_config()
    candles = _make_sufficient_candles()

    fixed_label = {
        "trend_state": "trending",
        "volatility_state": "normal",
        "favorability": "favorable",
        "measures": {},
        "candles_used": len(candles),
    }
    stub = mock.MagicMock(return_value=dict(fixed_label))
    with mock.patch.object(forecaster.regime, "classify_regime", stub):
        result = forecast(candles, config, proposed_direction="up")

    stub.assert_called()
    assert isinstance(result, dict)
    assert not result.get("unavailable")
    assert result["regime_trend_state"] == "trending", (
        "forecast must take its trend state from regime.classify_regime's output"
    )


def test_regime_helper_source_delegates_and_avoids_reimplementing_regime_math():
    """Structural proof: the regime helper delegates and does not reimplement measures."""
    src = inspect.getsource(forecaster._regime_trend_state)

    # Delegates to the shared regime module.
    assert "regime.classify_regime" in src, (
        "_regime_trend_state must call regime.classify_regime"
    )
    assert "regime.resolve_regime_config" in src, (
        "_regime_trend_state must resolve config via regime.resolve_regime_config"
    )

    # Does not reimplement the regime measure math inline. The classifier owns
    # ADX / choppiness computation; the forecaster must not recompute them.
    lowered = src.lower()
    for forbidden in ("adx", "choppiness", "vol_pctl"):
        assert forbidden not in lowered, (
            f"_regime_trend_state should not reimplement regime math (found '{forbidden}')"
        )
