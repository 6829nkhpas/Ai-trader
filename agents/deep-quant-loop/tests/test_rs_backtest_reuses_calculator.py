"""Backtest reuses the authoritative relative-strength calculator (task 13.3).

Feature: relative-strength-context

Requirement 11.5: the backtest seeder must REUSE the same relative-strength
calculator the live ``get_relative_strength`` tool path uses — it must NOT
reimplement the relative-strength math. ``backtest.generate_and_score`` is
expected to:

  * resolve the relative-strength parameters via ``rs.resolve_rs_config()`` (the
    single shared resolver),
  * resolve the Benchmark_Index via ``rs.resolve_benchmark(...)``, and
  * classify each candidate signal's relative strength via
    ``rs.classify_relative_strength(...)`` over look-ahead-free symbol and
    benchmark windows,

rather than computing the RS-ratio / relative-return / correlation / beta inline.

This is an example-based unit test. It proves reuse two ways:

  1. Behaviorally — it wraps ``rs.classify_relative_strength`` (and
     ``rs.resolve_rs_config`` / ``rs.resolve_benchmark``) with counting spies,
     runs ``generate_and_score`` over a small, deterministic, OFFLINE synthetic
     candle series (with a matching benchmark series) engineered to emit at
     least one signal, and asserts the calculator was invoked (the backtest
     delegates to the rs module).
  2. Structurally — it confirms ``backtest`` imports the very same ``rs`` module
     object, and that the source of ``generate_and_score`` references
     ``rs.classify_relative_strength`` / ``rs.resolve_rs_config`` /
     ``rs.resolve_benchmark`` and does not reimplement the measure math.

No network, Rust tool server, or QuestDB is involved: candles are passed
directly to ``generate_and_score``.
"""

import inspect
import math
import os
import sys

# Make the service package importable (backtest.py / rs.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
import rs  # noqa: E402
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


def _make_benchmark_candles(symbol_candles: list) -> list:
    """A benchmark series sharing the symbol's timestamps (so they time-align).

    Uses the same epoch grid as the symbol candles with a distinct, smoother
    price path so ``rs.classify_relative_strength`` has common-timestamp candles
    to align and measure against.
    """
    candles = []
    for i, sc in enumerate(symbol_candles):
        close = 200.0 + i * 0.3 + 3.0 * math.sin(i / 11.0)
        openp = close - 0.2
        high = max(openp, close) + 0.8
        low = min(openp, close) - 0.8
        candles.append({
            "timestamp_ms": sc["timestamp_ms"],
            "open": openp,
            "high": high,
            "low": low,
            "close": close,
            "volume": 5000.0 + (i % 7) * 50.0,
        })
    return candles


def test_backtest_imports_the_shared_rs_module():
    """``backtest.rs`` is the SAME module object as the rs package."""
    assert backtest.rs is rs


def test_generate_and_score_delegates_to_rs_calculator(monkeypatch):
    """Behavioral proof of reuse: the backtest calls into ``rs``.

    Wrap ``resolve_rs_config``, ``resolve_benchmark``, and
    ``classify_relative_strength`` with spies, run the seeder over a
    signal-producing series with a matching benchmark series, and assert the
    backtest delegated classification to ``rs.classify_relative_strength`` (and
    resolved config/benchmark via the shared resolvers) rather than computing it
    inline.
    """
    resolve_cfg_calls = {"n": 0}
    resolve_bm_calls = {"n": 0}
    classify_calls = {"n": 0}

    real_resolve_cfg = rs.resolve_rs_config
    real_resolve_bm = rs.resolve_benchmark
    real_classify = rs.classify_relative_strength

    def resolve_cfg_spy(*args, **kwargs):
        resolve_cfg_calls["n"] += 1
        return real_resolve_cfg(*args, **kwargs)

    def resolve_bm_spy(*args, **kwargs):
        resolve_bm_calls["n"] += 1
        return real_resolve_bm(*args, **kwargs)

    def classify_spy(symbol_candles, benchmark_candles, *args, **kwargs):
        classify_calls["n"] += 1
        return real_classify(symbol_candles, benchmark_candles, *args, **kwargs)

    # Patch on the rs module; backtest references them as rs.<name>, so patching
    # the module attribute exercises the real delegation path.
    monkeypatch.setattr(rs, "resolve_rs_config", resolve_cfg_spy)
    monkeypatch.setattr(rs, "resolve_benchmark", resolve_bm_spy)
    monkeypatch.setattr(rs, "classify_relative_strength", classify_spy)

    cfg = BacktestConfig(lookback=40)
    candles = _make_signal_producing_candles()
    benchmark_candles = _make_benchmark_candles(candles)
    results = generate_and_score(
        candles, "TEST", "15m", cfg, benchmark_candles=benchmark_candles
    )

    # The series is engineered to produce signals; each signal triggers a
    # relative-strength classification.
    assert results, "expected the synthetic series to generate at least one signal"

    # The shared resolver is used exactly once per run (R11.5 / R12.6).
    assert resolve_cfg_calls["n"] == 1, (
        "backtest must resolve parameters via rs.resolve_rs_config"
    )

    # Every emitted signal delegates to the shared calculator.
    assert classify_calls["n"] >= 1, (
        "backtest must classify relative strength via rs.classify_relative_strength"
    )
    assert classify_calls["n"] >= len(results), (
        "each scored signal should have been classified via rs.classify_relative_strength"
    )


def test_generate_and_score_resolves_benchmark_via_rs_when_absent(monkeypatch):
    """When no benchmark is supplied, the run resolves it via ``rs.resolve_benchmark``.

    The caller may omit ``benchmark``; ``generate_and_score`` then resolves it
    once for the run through the shared resolver rather than hard-coding one.
    """
    resolve_bm_calls = {"n": 0}
    real_resolve_bm = rs.resolve_benchmark

    def resolve_bm_spy(*args, **kwargs):
        resolve_bm_calls["n"] += 1
        return real_resolve_bm(*args, **kwargs)

    monkeypatch.setattr(rs, "resolve_benchmark", resolve_bm_spy)

    cfg = BacktestConfig(lookback=40)
    candles = _make_signal_producing_candles()
    benchmark_candles = _make_benchmark_candles(candles)
    # benchmark intentionally NOT passed -> generate_and_score must resolve it.
    generate_and_score(candles, "TEST", "15m", cfg, benchmark_candles=benchmark_candles)

    assert resolve_bm_calls["n"] == 1, (
        "backtest must resolve the benchmark via rs.resolve_benchmark when none is given"
    )


def test_generate_and_score_source_references_rs_and_avoids_reimplementing_math():
    """Structural proof: the source delegates and does not reimplement measures."""
    src = inspect.getsource(generate_and_score)

    # Delegates to the shared rs module.
    assert "rs.classify_relative_strength" in src, (
        "generate_and_score must call rs.classify_relative_strength"
    )
    assert "rs.resolve_rs_config" in src, (
        "generate_and_score must resolve config via rs.resolve_rs_config"
    )
    assert "rs.resolve_benchmark" in src, (
        "generate_and_score must resolve the benchmark via rs.resolve_benchmark"
    )

    # Does not reimplement the relative-strength measure math inline. The
    # calculator owns the RS-ratio / relative-return / correlation / beta
    # computation; the backtest must not recompute them.
    lowered = src.lower()
    for forbidden in (
        "rs_ratio", "relative_return", "compute_correlation", "compute_beta",
        "def _rs_ratio", "def compute_rs",
    ):
        assert forbidden not in lowered, (
            f"generate_and_score should not reimplement relative-strength math "
            f"(found '{forbidden}')"
        )
