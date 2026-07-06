"""Backtest reuses the Trade_Manager exit simulator (task 10.3).

Feature: trade-management

Requirement 7.2 (and 7.1): the Backtest_Seeder must REUSE the same
``Trade_Manager`` the live journal-scoring path uses to score a managed seeded
trade — it must NOT reimplement the multi-leg fill / breakeven / trail exit
logic. Per design AD-2, ``trade_manager.simulate_plan`` is the single source of
truth for the exit-simulation math, and the backtest path only feeds it a
different candle window: the candles AT OR AFTER the signal's entry candle
(R7.1).

In ``backtest.generate_and_score`` a managed run (``cfg.manage_trades=True``):

  * resolves the Trade_Manager parameters via
    ``trade_manager.resolve_trade_manager_config()`` (the single shared resolver),
  * builds the uniform default plan via ``trade_manager.default_management_plan``,
    and
  * scores it by invoking ``trade_manager.simulate_plan`` (through
    ``_score_signal_managed``) against ``candles[i + 1:]`` — the bars after the
    entry bar's close — rather than reimplementing the exit logic inline.

This is an example-based unit test (not a property test). It proves reuse two
ways:

  1. Behaviorally — it wraps ``trade_manager.simulate_plan`` with a counting spy
     that records the plan and the candle window passed on each call, runs
     ``generate_and_score`` over a small, deterministic, OFFLINE synthetic candle
     series engineered to emit at least one signal, and asserts the simulator was
     invoked with a candle window that is strictly AT OR AFTER the signal's entry
     candle (the ``candles[i + 1:]`` future window).
  2. Structurally — it confirms ``backtest`` imports the very same
     ``trade_manager`` module object and that the managed-scoring source calls
     ``trade_manager.simulate_plan`` rather than reimplementing the exit math.

No network, Rust tool server, or QuestDB is involved: candles are passed
directly to ``generate_and_score``.
"""

import inspect
import math
import os
import sys

# Make the service package importable (backtest.py / trade_manager.py live one
# level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import backtest  # noqa: E402
import trade_manager  # noqa: E402
from backtest import BacktestConfig, generate_and_score  # noqa: E402


def _make_signal_producing_candles(n: int = 200) -> list:
    """Deterministic synthetic series engineered to emit at least one signal.

    A gentle uptrend (so the EMA bias flips around) with a periodic sharp dip
    drives price to/through the value-area edges, which is exactly what the
    backtest's rule set keys off. Fully offline and reproducible. This mirrors
    the generator used by the sibling ``test_backtest_reuses_*`` unit tests.
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


def test_backtest_imports_the_shared_trade_manager_module():
    """``backtest.trade_manager`` is the SAME module object as trade_manager."""
    assert backtest.trade_manager is trade_manager


def test_generate_and_score_managed_delegates_to_simulate_plan(monkeypatch):
    """Behavioral proof of reuse: the managed backtest calls into the simulator.

    Wrap ``trade_manager.simulate_plan`` with a spy that records the plan and the
    candle window on every call, run the seeder with ``manage_trades=True`` over a
    signal-producing series, and assert:

      * the simulator was invoked at least once (the managed run delegates), and
      * every candle window passed to it is the future window AT OR AFTER the
        signal's entry candle — i.e. the bars are a contiguous suffix of the input
        starting immediately after the entry bar, and every passed timestamp is
        strictly greater than the entry candle's timestamp (R7.1).
    """
    calls = []
    real_simulate = trade_manager.simulate_plan

    def simulate_spy(plan, candles, config):
        calls.append({"plan": plan, "candles": candles})
        return real_simulate(plan, candles, config)

    # Patch on the trade_manager module; backtest references it as
    # ``trade_manager.simulate_plan`` (inside ``_score_signal_managed``), so
    # patching the module attribute exercises the real delegation path.
    monkeypatch.setattr(trade_manager, "simulate_plan", simulate_spy)

    cfg = BacktestConfig(lookback=40, manage_trades=True)
    candles = _make_signal_producing_candles()
    generate_and_score(candles, "TEST", "15m", cfg)

    # The series is engineered to produce signals; a managed run scores every
    # signal through the Trade_Manager.
    assert calls, (
        "managed backtest must invoke trade_manager.simulate_plan at least once "
        "(it must reuse the simulator, not reimplement the exit logic)"
    )

    for call in calls:
        passed = call["candles"]
        plan = call["plan"]

        # The window is a non-empty contiguous suffix of the original candle
        # list. Because ``generate_and_score`` slices ``candles[i + 1:]`` (and
        # caps it to ``max_horizon_bars``) without copying the dicts, the first
        # passed candle is the SAME object as some ``candles[k]``.
        assert passed, "simulate_plan should receive a non-empty candle window"
        k = next(
            (idx for idx, c in enumerate(candles) if c is passed[0]),
            None,
        )
        assert k is not None, "passed window must come from the input candles"
        # The window starts AFTER the entry candle, so it cannot start at index 0.
        assert k >= 1, "managed window must begin after the entry candle"

        # It is exactly the contiguous suffix window starting at the entry+1 bar.
        assert passed == candles[k:k + len(passed)], (
            "the candle window must be the future[i + 1:] suffix (capped to the "
            "horizon), not a reimplemented or reshuffled window"
        )

        entry_candle = candles[k - 1]
        # Every candle handed to the simulator occurs at/after the entry candle —
        # strictly after, since the window begins one bar past the entry bar.
        assert all(
            c["timestamp_ms"] > entry_candle["timestamp_ms"] for c in passed
        ), "every simulated candle must be at/after the signal's entry candle (R7.1)"

        # The plan the simulator scores is anchored to that entry candle's close
        # (the signal bar), confirming the window is the post-entry future.
        assert round(entry_candle["close"], 4) == plan.entry, (
            "the simulated plan's entry must match the signal's entry candle close"
        )


def test_managed_scoring_source_calls_simulate_plan_and_avoids_reimplementing():
    """Structural proof: the managed-scoring path delegates to the simulator.

    The managed window is scored inside ``_score_signal_managed`` (called by
    ``generate_and_score``), so the ``trade_manager.simulate_plan`` call lives
    there; ``generate_and_score`` builds the plan and the ``candles[i + 1:]``
    future window and delegates scoring to it.
    """
    managed_src = inspect.getsource(backtest._score_signal_managed)
    gen_src = inspect.getsource(generate_and_score)

    # The exit simulation is performed by the shared Trade_Manager.
    assert "trade_manager.simulate_plan" in managed_src, (
        "_score_signal_managed must score via trade_manager.simulate_plan"
    )

    # generate_and_score resolves the shared config, builds the default plan, and
    # feeds the future window (candles[i + 1:]) to the managed scorer.
    assert "trade_manager.resolve_trade_manager_config" in gen_src, (
        "generate_and_score must resolve the Trade_Manager config via the shared "
        "resolver"
    )
    assert "trade_manager.default_management_plan" in gen_src, (
        "generate_and_score must build the managed plan via "
        "trade_manager.default_management_plan"
    )
    assert "candles[i + 1:]" in gen_src, (
        "generate_and_score must score the future window at/after the entry bar"
    )
