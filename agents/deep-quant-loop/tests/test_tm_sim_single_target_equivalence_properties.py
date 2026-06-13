"""Model-based property test for single-target equivalence (trade_manager.py, task 3.14).

Feature: trade-management

This module implements design **Property 13: Single-target equivalence
(non-breaking guarantee)** (Key Design Decisions AD-3 "a Single_Target_Trade is
a degenerate Management_Plan" and AD-4 "worst-case resolution never flatters the
plan"):

    For any directional bracket (entry, initial stop, single take-profit) and
    candle sequence, simulating the degenerate one-leg plan built by
    ``single_target_plan(entry, stop_loss, take_profit)`` via ``simulate_plan``
    yields a ``Realized_R`` equal to the EXISTING single-target scoring —
    ``+risk_reward`` on a target-first fill, ``-1.0`` on a stop-first fill, and
    ``open`` when neither level is reached — matching the legacy
    ``journal._score_one`` / ``backtest._score_signal`` reference implementation.

Validates: Requirements 3.6, 14.5.

Model-based construction
------------------------
This is a MODEL-BASED property: rather than asserting hand-computed outcomes, it
re-implements the legacy single-target scorer as a small reference (mirroring
``journal._score_one`` and ``backtest._score_signal`` exactly) and asserts the
degenerate ``single_target_plan`` simulation AGREES with that reference on
status and ``Realized_R`` for every generated case.

The legacy reference logic (identical in ``journal._score_one`` and
``backtest._score_signal``):

    * ``risk = abs(entry - stop_loss)``; if ``risk <= 0`` the trade is unscorable.
    * ``rr = abs(take_profit - entry) / risk`` (the risk-reward).
    * Walk candles in chronological order. For the FIRST candle that touches a
      level (BUY: ``hit_tp = high >= tp``, ``hit_sl = low <= sl``; SELL mirror):
          - a candle touching BOTH the stop and the target is a LOSS at
            ``-1.0`` (conservative straddle handling — worst case);
          - else a target touch is a WIN at ``+round(rr, 4)``;
          - else a stop touch is a LOSS at ``-1.0``.
    * If no candle reaches either level the trade is unresolved (open / None).

The reference re-uses ``trade_manager._clean_candles`` so it walks EXACTLY the
candle window and ascending-timestamp ordering ``simulate_plan`` walks — this
isolates the comparison to the scoring DECISION (target-first / stop-first /
straddle / unreached), not candle-cleaning differences. The legacy ``r_multiple``
column rounds the win to 4 decimals (``round(rr, 4)``); the simulator returns the
exact ``rr``, so wins are compared within ``1e-4`` while losses (``-1.0``) and the
open case are compared exactly.

Generated cases deliberately span all four legacy outcomes — target-first (win),
stop-first (loss), straddle (loss), and unreached (open) — plus a fully RANDOM
candle-sequence mode for a true model-based cross-check over arbitrary inputs.

The sys.path / import pattern mirrors the sibling TM property tests
``tests/test_tm_sim_straddle_properties.py`` and
``tests/test_tm_sim_realized_r_properties.py``.
"""

import math
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (trade_manager.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from trade_manager import (  # noqa: E402
    _clean_candles,
    resolve_trade_manager_config,
    simulate_plan,
    single_target_plan,
)

# Tolerances: losses (-1.0) and target R are exact in the simulator; the legacy
# reference rounds a WIN's R to 4 decimals (round(rr, 4)) for storage, so wins
# are compared within 1e-4.
_EXACT_TOL = 1e-9
_WIN_TOL = 1e-4


# ── Reference scorer: mirrors legacy journal._score_one / backtest._score_signal ──
def _reference_single_target_score(action, entry, stop_loss, take_profit, candles):
    """Legacy single-target outcome for a directional bracket + candles.

    Mirrors ``journal._score_one`` / ``backtest._score_signal`` exactly: the
    conservative fill model where the FIRST candle to touch a level decides the
    outcome and a candle straddling both the stop and the target is a loss
    (worst-case). Walks the SAME cleaned, ascending-timestamp candle rows the
    simulator walks (via ``trade_manager._clean_candles``) so the comparison
    isolates the scoring decision.

    Returns ``(status, realized_r)`` where ``status`` is one of
    ``"win" | "loss" | "open" | "invalid"`` and ``realized_r`` is the legacy
    R-multiple (``+round(rr, 4)`` win, ``-1.0`` loss, ``None`` otherwise).
    """
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return ("invalid", None)
    rr = abs(take_profit - entry) / risk

    for (_ts, _o, hi, lo, _cl) in _clean_candles(candles):
        if action == "BUY":
            hit_tp = hi >= take_profit
            hit_sl = lo <= stop_loss
        else:  # SELL — mirror image
            hit_tp = lo <= take_profit
            hit_sl = hi >= stop_loss
        if hit_sl and hit_tp:
            return ("loss", -1.0)
        if hit_tp:
            return ("win", round(rr, 4))
        if hit_sl:
            return ("loss", -1.0)

    return ("open", None)


# ── Building-block strategies ─────────────────────────────────────────────────
_entry = st.floats(min_value=50.0, max_value=1000.0, allow_nan=False, allow_infinity=False)
_dist = st.floats(min_value=1.0, max_value=40.0, allow_nan=False, allow_infinity=False)


def _neutral_candle(entry, ts):
    """A candle that touches neither level (a doji at entry).

    For a bracket with ``stop < entry < tp`` (BUY) or ``tp < entry < stop``
    (SELL), a candle whose whole range sits at ``entry`` reaches no level, so it
    is a no-op for both the simulator and the reference — used to exercise
    candle iteration before the decisive candle.
    """
    return {"open": entry, "high": entry, "low": entry, "close": entry,
            "volume": 100.0, "timestamp_ms": ts}


@st.composite
def _bracket_and_candles(draw):
    """Build a directional bracket + candle sequence spanning every outcome.

    Draws a ``scenario`` so generated cases collectively cover the four legacy
    outcomes — ``target_first`` (win), ``stop_first`` (loss), ``straddle`` (loss),
    ``unreached`` (open) — plus a fully ``random`` candle sequence for a true
    model-based cross-check. For the constructed scenarios, optional leading
    neutral candles exercise pre-decision iteration. Returns
    ``(action, entry, stop_loss, take_profit, candles)``.
    """
    side = draw(st.sampled_from(["BUY", "SELL"]))
    entry = draw(_entry)
    d = draw(_dist)        # entry -> initial stop distance (the risk)
    rdist = draw(_dist)    # entry -> take-profit distance (the reward)

    if side == "BUY":
        stop = entry - d
        tp = entry + rdist
    else:
        stop = entry + d
        tp = entry - rdist

    scenario = draw(st.sampled_from(
        ["target_first", "stop_first", "straddle", "unreached", "random"]
    ))

    # Optional leading neutral candles (touch nothing) before the decisive one.
    n_lead = draw(st.integers(min_value=0, max_value=3))
    candles = [_neutral_candle(entry, 1000 + i) for i in range(n_lead)]
    ts = 1000 + n_lead

    if scenario == "random":
        # Arbitrary OHLC candles spanning the stop/target region; both the
        # simulator and the reference must agree by construction (model-based).
        lo_bound = min(stop, tp, entry) - 5.0
        hi_bound = max(stop, tp, entry) + 5.0
        n = draw(st.integers(min_value=1, max_value=8))
        for i in range(n):
            a = draw(st.floats(min_value=lo_bound, max_value=hi_bound,
                               allow_nan=False, allow_infinity=False))
            b = draw(st.floats(min_value=lo_bound, max_value=hi_bound,
                               allow_nan=False, allow_infinity=False))
            o = draw(st.floats(min_value=lo_bound, max_value=hi_bound,
                               allow_nan=False, allow_infinity=False))
            c = draw(st.floats(min_value=lo_bound, max_value=hi_bound,
                               allow_nan=False, allow_infinity=False))
            high = max(a, b, o, c)
            low = min(a, b, o, c)
            candles.append({"open": o, "high": high, "low": low, "close": c,
                            "volume": 100.0, "timestamp_ms": ts + i})
        return side, entry, stop, tp, candles

    # Constructed decisive candle for the three resolving scenarios + unreached.
    if side == "BUY":
        if scenario == "target_first":      # high reaches tp, low above stop
            decisive = {"high": tp, "low": entry}
        elif scenario == "stop_first":      # low reaches stop, high below tp
            decisive = {"high": entry, "low": stop}
        elif scenario == "straddle":        # reaches BOTH -> worst-case loss
            decisive = {"high": tp, "low": stop}
        else:                               # unreached -> open
            decisive = {"high": entry, "low": entry}
    else:  # SELL — mirror image
        if scenario == "target_first":      # low reaches tp, high below stop
            decisive = {"high": entry, "low": tp}
        elif scenario == "stop_first":      # high reaches stop, low above tp
            decisive = {"high": stop, "low": entry}
        elif scenario == "straddle":        # reaches BOTH -> worst-case loss
            decisive = {"high": stop, "low": tp}
        else:                               # unreached -> open
            decisive = {"high": entry, "low": entry}

    candles.append({
        "open": entry,
        "high": decisive["high"],
        "low": decisive["low"],
        "close": entry,
        "volume": 100.0,
        "timestamp_ms": ts,
    })

    # A trailing neutral candle for unreached, to confirm "still open" after more
    # candles that also touch nothing.
    if scenario == "unreached":
        candles.append(_neutral_candle(entry, ts + 1))

    return side, entry, stop, tp, candles


# ─────────────────────────────────────────────────────────────────────────────
# Property 13 (task 3.14): Single-target equivalence (non-breaking guarantee)
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 13: Single-target equivalence (non-breaking guarantee)
@settings(max_examples=300, deadline=None)
@given(case=_bracket_and_candles())
def test_property_13_single_target_equivalence(case):
    """Feature: trade-management, Property 13: Single-target equivalence
    (non-breaking guarantee) — for any directional bracket and candle sequence,
    ``simulate_plan(single_target_plan(...))`` agrees with the legacy
    single-target scorer (``journal._score_one`` / ``backtest._score_signal``):
    ``+risk_reward`` target-first, ``-1.0`` stop-first (and straddle), and
    ``open`` when neither level is reached.

    Validates: Requirements 3.6, 14.5
    """
    action, entry, stop, tp, candles = case

    # The degenerate one-leg plan that models today's single bracket (AD-3).
    plan = single_target_plan(entry, stop, tp)
    # single_target_plan infers the action from the bracket geometry; it must
    # match the directional bracket we built.
    assert plan.action == action

    config = resolve_trade_manager_config()
    result = simulate_plan(plan, candles, config)
    ref_status, ref_r = _reference_single_target_score(action, entry, stop, tp, candles)

    if ref_status == "open":
        # Neither level reached -> the simulator reports open and fabricates no
        # exit, no Realized_R (Requirement 3.1).
        assert result.status == "open", (
            f"expected open, got {result.status} (ref={ref_status}, candles={candles})"
        )
        assert result.realized_r is None
        return

    # The reference scored an outcome -> the simulator must fully resolve the
    # degenerate single-leg plan.
    assert result.status == "resolved", (
        f"expected resolved, got {result.status} (ref={ref_status}, candles={candles})"
    )
    assert result.realized_r is not None
    # One leg at fraction 1.0: the whole position closes in a single fill.
    assert math.isclose(result.residual_fraction + sum(
        f.fraction for f in result.fills if f.index != -1), 1.0, abs_tol=_EXACT_TOL
    ) or math.isclose(sum(f.fraction for f in result.fills), 1.0, abs_tol=_EXACT_TOL)

    if ref_status == "win":
        # Target-first fill -> +risk_reward. The legacy column rounds rr to 4
        # decimals; the simulator returns the exact rr, so compare within 1e-4
        # and require a strictly positive outcome.
        assert result.realized_r > 0.0
        assert math.isclose(result.realized_r, ref_r, abs_tol=_WIN_TOL, rel_tol=1e-9), (
            f"win realized_r={result.realized_r} != legacy {ref_r}"
        )
    else:  # ref_status == "loss" (stop-first OR straddle worst-case)
        assert math.isclose(result.realized_r, -1.0, abs_tol=_EXACT_TOL), (
            f"loss realized_r={result.realized_r} != -1.0"
        )
        assert math.isclose(ref_r, -1.0, abs_tol=_EXACT_TOL)
