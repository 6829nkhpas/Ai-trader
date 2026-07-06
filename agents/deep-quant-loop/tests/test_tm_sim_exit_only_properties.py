"""Property-based test that the simulator output is exit-only
(trade_manager.py ``simulate_plan``, task 3.15).

Feature: trade-management

This module implements design **Property 28: Simulator output is exit-only**:

    For any ``ManagementPlan``, candle sequence, and configuration, the
    ``SimulationResult`` is purely an EXIT description — it never carries a
    BUY / SELL / HOLD entry decision. Concretely:

      * ``status`` is one of ``{"resolved", "open", "invalid"}``;
      * ``realized_r`` is a ``float`` or ``None`` (never a decision token);
      * every ``fill`` is an exit record whose ``kind`` is in the fixed
        exit-kind set ``{"target", "stop", "breakeven-stop", "trail-stop"}``;
      * the result carries NO attribute conveying a trade direction / decision
        (no ``action`` / ``decision`` / ``side`` / ``signal`` field), so the
        simulator structurally cannot emit an entry decision (Requirement 14.1).

Validates: Requirements 14.1.

Strategy: generate varied plans (BUY/SELL, single-target, multi-leg scale-out,
with/without a breakeven trigger, with/without a trailing rule) and candle lists
whose price band overlaps the plan band so targets / stops are reached on many
examples (exercising the resolved, open, and invalid outcomes alike). For every
result we assert the structural exit-only contract regardless of which outcome
was produced.

The sys.path / import and strategy patterns mirror the sibling TM property tests
``tests/test_tm_sim_purity_properties.py`` and
``tests/test_tm_sim_order_invariance_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (trade_manager.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from trade_manager import (  # noqa: E402
    BreakevenTrigger,
    LegFill,
    ManagementPlan,
    ScaleOutLeg,
    SimulationResult,
    TrailingStop,
    resolve_trade_manager_config,
    simulate_plan,
)

# A single resolved configuration from the documented defaults; the exit-only
# contract holds independent of the configured parameter values.
_CONFIG = resolve_trade_manager_config()

# The allowed status set and the fixed exit-kind set the simulator may emit.
_ALLOWED_STATUS = {"resolved", "open", "invalid"}
_ALLOWED_FILL_KINDS = {"target", "stop", "breakeven-stop", "trail-stop"}

# Attribute names that would convey a trade direction / entry decision. The
# simulator must NOT expose any of these on its result (Requirement 14.1).
_FORBIDDEN_DECISION_ATTRS = ("action", "decision", "side", "signal")

# Entry-decision tokens that must never appear as a result/field value.
_DECISION_TOKENS = {"BUY", "SELL", "HOLD"}


# ── Plan strategies ───────────────────────────────────────────────────────────
# Finite, well-behaved floats in a band that overlaps the candle band below so
# targets / stops are reached on many examples (resolved), not just open ones.
_price = st.floats(
    min_value=0.0,
    max_value=200.0,
    allow_nan=False,
    allow_infinity=False,
    width=64,
)

# Leg fraction in (0.0, 1.0]; multiple legs may oversubscribe, which the
# simulator clamps against the remaining size — the contract must hold regardless.
_fraction = st.floats(
    min_value=1e-3,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
    width=64,
)

_leg = st.builds(ScaleOutLeg, target=_price, fraction=_fraction)
# One leg (single-target) up to several legs (scale-out).
_legs = st.lists(_leg, min_size=1, max_size=4).map(tuple)

# Breakeven: absent, price-form, or r_multiple-form (Requirement 1.4).
_breakeven = st.one_of(
    st.none(),
    st.builds(BreakevenTrigger, price=_price, r_multiple=st.none()),
    st.builds(
        BreakevenTrigger,
        price=st.none(),
        r_multiple=st.floats(
            min_value=1e-3, max_value=10.0, allow_nan=False, allow_infinity=False
        ),
    ),
)

# Trailing: absent, ATR-multiple-form, or R-increment-form.
_trailing = st.one_of(
    st.none(),
    st.builds(
        TrailingStop,
        atr_multiple=st.floats(
            min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False
        ),
        r_increment=st.none(),
    ),
    st.builds(
        TrailingStop,
        atr_multiple=st.none(),
        r_increment=st.floats(
            min_value=1e-3, max_value=10.0, allow_nan=False, allow_infinity=False
        ),
    ),
)

_atr_14 = st.one_of(
    st.none(),
    st.floats(min_value=1e-3, max_value=50.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def _plans(draw):
    """Build a varied finite ``ManagementPlan`` (BUY/SELL, scale-out, breakeven,
    trailing) exercising every optional field."""
    return ManagementPlan(
        action=draw(st.sampled_from(["BUY", "SELL"])),
        entry=draw(_price),
        initial_stop=draw(_price),
        legs=draw(_legs),
        breakeven=draw(_breakeven),
        trailing=draw(_trailing),
        atr_14=draw(_atr_14),
    )


# ── Candle strategy ───────────────────────────────────────────────────────────
# Well-formed OHLCV dicts (low <= open/close <= high) spanning the plan band so
# targets and stops are actually reached on many examples.
@st.composite
def _candle(draw, timestamp_ms):
    low = draw(_price)
    high = draw(
        st.floats(min_value=low, max_value=200.0, allow_nan=False, allow_infinity=False)
    )
    open_ = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    close = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    volume = draw(st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False))
    return {
        "timestamp_ms": timestamp_ms,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


@st.composite
def _plan_and_candles(draw):
    """Draw a plan and a list of candles spanning a range of outcomes
    (resolved / open / invalid)."""
    plan = draw(_plans())
    timestamps = draw(
        st.lists(st.integers(min_value=1, max_value=10_000_000), min_size=0, max_size=25)
    )
    candles = [draw(_candle(ts)) for ts in timestamps]
    return plan, candles


# ─────────────────────────────────────────────────────────────────────────────
# Property 28 (task 3.15): Simulator output is exit-only
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 28: Simulator output is exit-only
@settings(max_examples=50, deadline=None)
@given(case=_plan_and_candles())
def test_property_28_simulator_output_is_exit_only(case):
    """Feature: trade-management, Property 28: Simulator output is exit-only —
    for any plan, candles, and config, the ``SimulationResult`` is purely an exit
    description: ``status`` is in ``{resolved, open, invalid}``, ``realized_r`` is
    a float or ``None``, every ``fill.kind`` is in the exit-kind set, and the
    result structurally carries NO trade-direction / decision attribute
    (``action`` / ``decision`` / ``side`` / ``signal``) and never a BUY/SELL/HOLD
    token.

    Validates: Requirements 14.1
    """
    plan, candles = case

    result = simulate_plan(plan, candles, _CONFIG)

    # The simulator returns ONLY a SimulationResult — not a decision object.
    assert isinstance(result, SimulationResult)

    # status is always one of the allowed exit/lifecycle states.
    assert result.status in _ALLOWED_STATUS, f"unexpected status: {result.status!r}"

    # realized_r is a float or None (never a decision token / other type).
    assert result.realized_r is None or isinstance(result.realized_r, float), (
        f"realized_r is neither float nor None: {result.realized_r!r}"
    )

    # Structural exit-only guarantee: the result carries NO attribute conveying a
    # trade direction / entry decision.
    for attr in _FORBIDDEN_DECISION_ATTRS:
        assert not hasattr(result, attr), (
            f"SimulationResult unexpectedly exposes a decision attribute '{attr}'"
        )

    # Every fill is an exit record (a LegFill) whose kind is a pure exit kind —
    # never a BUY/SELL/HOLD entry decision.
    for fill in result.fills:
        assert isinstance(fill, LegFill)
        assert fill.kind in _ALLOWED_FILL_KINDS, (
            f"fill.kind {fill.kind!r} is not an exit kind {_ALLOWED_FILL_KINDS}"
        )
        # A fill likewise exposes no direction/decision attribute.
        for attr in _FORBIDDEN_DECISION_ATTRS:
            assert not hasattr(fill, attr), (
                f"LegFill unexpectedly exposes a decision attribute '{attr}'"
            )

    # Belt-and-suspenders: no string-valued field on the result is a BUY/SELL/HOLD
    # decision token. (The plan's own ``action`` is an INPUT and is intentionally
    # not echoed onto the result.)
    assert result.status not in _DECISION_TOKENS
    for fill in result.fills:
        assert fill.kind not in _DECISION_TOKENS
