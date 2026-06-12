"""Backtest reuses the authoritative regime classifier (task 13.3).

Feature: regime-detection-gate

Requirement 10.5: the backtest seeder must REUSE the same regime classifier the
live ``get_market_regime`` tool path uses — it must NOT reimplement the regime
math. ``backtest.generate_and_score`` is expected to:

  * resolve the regime thresholds via ``regime.resolve_regime_config()`` (the
    single shared resolver), and
  * classify each candidate signal's regime via ``regime.classify_regime(...)``
    over a point-in-time candle slice,

rather than computing ADX / choppiness / volatility measures inline.

This is an example-based unit test. It proves reuse two ways:

  1. Behaviorally — it wraps ``regime.classify_regime`` and
     ``regime.resolve_regime_config`` with counting spies, runs
     ``generate_and_score`` over a small, deterministic, OFFLINE synthetic candle
     series engineered to emit at least one signal, and asserts the spies were
     invoked (the backtest delegates to the regime module).
  2. Structurally — it confirms ``backtest`` imports the very same ``regime``
     module object, and that the source of ``generate_and_score`` references
     ``regime.classify_regime`` / ``regime.resolve_regime_config`` and does not
     reimplement the measure math (no inline ADX / choppiness computation).

No network, Rust tool server, or QuestDB is involved: candles are passed
directly to ``generate_and_score``.
"""

import inspect
import math
import os
import sys

# Make the service package importable (backtest.py / regime.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
import regime  # noqa: E402
from backtest import BacktestConfig, generate_and_score  # noqa: E402


def _make_signal_producing_candles(n: int = 200) -> list:
    """Deterministic synthetic series engineered to emit at least one signal.

    A gentle uptrend (so the EMA bias flips around) with a periodic sharp dip
    drives price to/through the value-area edges, which is exactly what the
    backtest's rule set keys off. Fully offline and reproducible.
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


def test_backtest_imports_the_shared_regime_module():
    """``backtest.regime`` is the SAME module object as the regime package."""
    assert backtest.regime is regime


def test_generate_and_score_delegates_to_regime_classifier(monkeypatch):
    """Behavioral proof of reuse: the backtest calls into ``regime``.

    Wrap ``resolve_regime_config`` and ``classify_regime`` with spies, run the
    seeder over a signal-producing series, and assert the backtest delegated
    classification to ``regime.classify_regime`` (and resolved config via
    ``regime.resolve_regime_config``) rather than computing it inline.
    """
    resolve_calls = {"n": 0}
    classify_calls = {"n": 0, "slice_lengths": []}

    real_resolve = regime.resolve_regime_config
    real_classify = regime.classify_regime

    def resolve_spy(*args, **kwargs):
        resolve_calls["n"] += 1
        return real_resolve(*args, **kwargs)

    def classify_spy(candles, *args, **kwargs):
        classify_calls["n"] += 1
        classify_calls["slice_lengths"].append(len(candles))
        return real_classify(candles, *args, **kwargs)

    # Patch on the regime module; backtest references them as regime.<name>,
    # so patching the module attribute exercises the real delegation path.
    monkeypatch.setattr(regime, "resolve_regime_config", resolve_spy)
    monkeypatch.setattr(regime, "classify_regime", classify_spy)

    cfg = BacktestConfig(lookback=40)
    candles = _make_signal_producing_candles()
    results = generate_and_score(candles, "TEST", "15m", cfg)

    # The series is engineered to produce signals; each signal triggers a
    # point-in-time classification.
    assert results, "expected the synthetic series to generate at least one signal"

    # The shared resolver is used exactly once per run (R10.5 / R11.6).
    assert resolve_calls["n"] == 1, "backtest must resolve thresholds via regime.resolve_regime_config"

    # Every emitted signal delegates to the shared classifier.
    assert classify_calls["n"] >= 1, "backtest must classify regimes via regime.classify_regime"
    assert classify_calls["n"] >= len(results), (
        "each scored signal should have been classified via regime.classify_regime"
    )

    # Classification uses a point-in-time slice (look-ahead-free, R10.1): each
    # slice is a non-empty prefix no longer than the full candle history.
    assert all(0 < ln <= len(candles) for ln in classify_calls["slice_lengths"])


def test_generate_and_score_source_references_regime_and_avoids_reimplementing_math():
    """Structural proof: the source delegates and does not reimplement measures."""
    src = inspect.getsource(generate_and_score)

    # Delegates to the shared regime module.
    assert "regime.classify_regime" in src, (
        "generate_and_score must call regime.classify_regime"
    )
    assert "regime.resolve_regime_config" in src, (
        "generate_and_score must resolve config via regime.resolve_regime_config"
    )

    # Does not reimplement the regime measure math inline. The classifier owns
    # ADX / choppiness / volatility-percentile computation; the backtest must not
    # recompute them.
    lowered = src.lower()
    for forbidden in ("adx", "choppiness", "_choppiness", "def _adx", "vol_pctl"):
        assert forbidden not in lowered, (
            f"generate_and_score should not reimplement regime math (found '{forbidden}')"
        )
