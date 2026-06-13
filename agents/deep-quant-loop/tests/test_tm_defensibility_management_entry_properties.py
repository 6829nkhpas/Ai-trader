"""Property-based test for the defensibility management entry (graph.py, task 12.2).

Feature: trade-management

This module implements design **Property 20: Defensibility management entry
fidelity**:

    For any committed trade, the management entry built by
    ``build_defensibility_record`` (via ``_management_entry``) contains EXACTLY
    the declared plan's scale-out legs, breakeven trigger, and trailing rule;
    every simulated exit it cites appears in the ``Trade_Manager``'s
    ``Exit_Breakdown`` (nothing is fabricated); and a ``Single_Target_Trade`` is
    recorded as single-target with no scale-out legs.

Validates: Requirements 9.1, 9.2, 9.3.

The implementation under test is ``graph._management_entry(decision, action,
levels, results, atr_14)``:

  * a committed BUY/SELL carrying a declared ``management_plan`` dict is coerced
    via the SAME ``_coerce_management_plan`` merge ``declare_trade`` used to
    validate it, so the entry cites the exact committed plan (R9.1, R9.2);
  * a committed BUY/SELL with no declared plan collapses to the degenerate
    single-target plan via ``trade_manager.single_target_plan`` and is recorded
    as single-target without fabricating scale-out legs (R9.3);
  * where candles are available (a ``get_candles`` result in ``results``), the
    plan is scored by ``trade_manager.simulate_plan`` and the resulting
    Exit_Breakdown + Realized_R are cited VERBATIM (R9.1, R9.2).

The test calls ``_management_entry`` directly with constructed inputs (no LLM,
no Rust server), reconstructs the SAME ManagementPlan the helper builds (via the
re-exported ``graph._coerce_management_plan`` / ``trade_manager.single_target_plan``),
and asserts the recorded plan fields equal the declared plan and that the cited
simulated fields equal a direct ``trade_manager.simulate_plan`` call with the
SAME resolved config (the simulator being a pure, deterministic function, the
two calls produce byte-identical output, so equality is EXACT).

The sys.path / import pattern mirrors the sibling defensibility property test
``tests/test_rs_defensibility_mirror_properties.py``.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (graph.py / trade_manager.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import trade_manager  # noqa: E402
from graph import (  # noqa: E402
    _management_entry,
    _coerce_management_plan,
)


# ── Strategies ────────────────────────────────────────────────────────────────
_side = st.sampled_from(["BUY", "SELL"])
_with_candles = st.booleans()


@st.composite
def _managed_plan_dict(draw, side, entry, stop):
    """Draw a declared ``management_plan`` dict (legs / breakeven / trailing).

    Targets are strictly ordered on the profit side (above entry for a BUY,
    below for a SELL); leg fractions lie in ``(0.0, 1.0]`` and are normalized so
    their sum is at most ``1.0``. The breakeven (when present) is either a price
    strictly between entry and the first target, or an R-multiple; the trailing
    (when present) is either an ATR multiple or an R increment. Base bracket
    fields are intentionally OMITTED so the helper's ``_coerce_management_plan``
    merge fills them from the declare_trade arguments — exactly as in production.
    """
    n = draw(st.integers(min_value=1, max_value=3))
    step = draw(st.floats(min_value=1.0, max_value=20.0, allow_nan=False, allow_infinity=False))
    if side == "BUY":
        targets = [entry + (j + 1) * step for j in range(n)]
        first_target = targets[0]
    else:
        targets = [entry - (j + 1) * step for j in range(n)]
        first_target = targets[0]

    raw = [
        draw(st.floats(min_value=0.1, max_value=1.0, allow_nan=False, allow_infinity=False))
        for _ in range(n)
    ]
    total = draw(st.floats(min_value=0.2, max_value=1.0, allow_nan=False, allow_infinity=False))
    scale = total / sum(raw)
    fractions = [r * scale for r in raw]

    legs = [{"target": t, "fraction": f} for t, f in zip(targets, fractions)]

    plan = {"legs": legs}

    # Optional breakeven: a price strictly between entry and the first target, or
    # an R-multiple of progress.
    be_kind = draw(st.sampled_from(["none", "price", "r_multiple"]))
    if be_kind == "price":
        # Midpoint between entry and the first target is strictly between them.
        be_price = (entry + first_target) / 2.0
        plan["breakeven"] = {"price": be_price}
    elif be_kind == "r_multiple":
        plan["breakeven"] = {
            "r_multiple": draw(st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False))
        }

    # Optional trailing: an ATR multiple or a fixed R increment.
    trail_kind = draw(st.sampled_from(["none", "atr_multiple", "r_increment"]))
    if trail_kind == "atr_multiple":
        plan["trailing"] = {
            "atr_multiple": draw(st.floats(min_value=0.1, max_value=3.0, allow_nan=False, allow_infinity=False))
        }
    elif trail_kind == "r_increment":
        plan["trailing"] = {
            "r_increment": draw(st.floats(min_value=0.1, max_value=2.0, allow_nan=False, allow_infinity=False))
        }

    return plan


@st.composite
def _candle(draw, ts):
    """One OHLCV candle dict with low <= open/close <= high and a timestamp."""
    a = draw(st.floats(min_value=40.0, max_value=1100.0, allow_nan=False, allow_infinity=False))
    b = draw(st.floats(min_value=40.0, max_value=1100.0, allow_nan=False, allow_infinity=False))
    low, high = (a, b) if a <= b else (b, a)
    o = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    c = draw(st.floats(min_value=low, max_value=high, allow_nan=False, allow_infinity=False))
    return {
        "open": o,
        "high": high,
        "low": low,
        "close": c,
        "volume": draw(st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)),
        "timestamp_ms": ts,
    }


@st.composite
def _candles(draw):
    """A chronologically-stamped list of OHLCV candles (possibly empty)."""
    m = draw(st.integers(min_value=0, max_value=6))
    return [draw(_candle(1000 * (i + 1))) for i in range(m)]


@st.composite
def _cases(draw):
    """Build one defensibility-management scenario.

    Returns a dict describing a committed managed OR single-target trade, with or
    without candles in scope, plus the constructed ``_management_entry`` inputs.
    """
    side = draw(_side)
    is_managed = draw(st.booleans())
    has_candles = draw(_with_candles)

    entry = draw(st.floats(min_value=50.0, max_value=1000.0, allow_nan=False, allow_infinity=False))
    stop_distance = draw(st.floats(min_value=1.0, max_value=40.0, allow_nan=False, allow_infinity=False))
    if side == "BUY":
        stop = entry - stop_distance
        take = entry + draw(st.floats(min_value=1.0, max_value=80.0, allow_nan=False, allow_infinity=False))
    else:
        stop = entry + stop_distance
        take = entry - draw(st.floats(min_value=1.0, max_value=80.0, allow_nan=False, allow_infinity=False))

    atr_14 = draw(st.one_of(
        st.none(),
        st.floats(min_value=0.1, max_value=50.0, allow_nan=False, allow_infinity=False),
    ))

    plan_dict = draw(_managed_plan_dict(side, entry, stop)) if is_managed else None
    candles = draw(_candles()) if has_candles else []

    return {
        "side": side,
        "is_managed": is_managed,
        "entry": entry,
        "stop": stop,
        "take": take,
        "atr_14": atr_14,
        "plan_dict": plan_dict,
        "candles": candles,
    }


def _legs_of(plan):
    return [{"target": leg.target, "fraction": leg.fraction} for leg in (plan.legs or ())]


def _breakeven_of(plan):
    if plan.breakeven is None:
        return None
    return {"price": plan.breakeven.price, "r_multiple": plan.breakeven.r_multiple}


def _trailing_of(plan):
    if plan.trailing is None:
        return None
    return {"atr_multiple": plan.trailing.atr_multiple, "r_increment": plan.trailing.r_increment}


def _exit_breakdown_of(sim_result):
    return [
        {
            "index": f.index,
            "price": f.price,
            "fraction": f.fraction,
            "leg_r": f.leg_r,
            "timestamp_ms": f.timestamp_ms,
            "kind": f.kind,
        }
        for f in sim_result.fills
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Property 20 (task 12.2): Defensibility management entry fidelity
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 20: Defensibility management entry fidelity
@settings(max_examples=200, deadline=None)
@given(case=_cases())
def test_property_20_defensibility_management_entry_fidelity(case):
    """Feature: trade-management, Property 20: Defensibility management entry
    fidelity — for any committed managed trade, the management entry's recorded
    legs / breakeven / trailing EXACTLY match the declared plan (no fabrication,
    no omission), and where candles are supplied the cited Exit_Breakdown +
    Realized_R EXACTLY equal a direct ``trade_manager.simulate_plan`` call. A
    single-target trade is recorded as single-target (style ``single``, one leg
    at fraction 1.0) with NO fabricated scale-out legs.

    Validates: Requirements 9.1, 9.2, 9.3
    """
    side = case["side"]
    entry_px = case["entry"]
    stop_px = case["stop"]
    take_px = case["take"]
    atr_14 = case["atr_14"]
    candles = case["candles"]

    decision = {"action": side, "source": "declare_trade"}
    if case["is_managed"]:
        decision["management_plan"] = case["plan_dict"]
    levels = {"entry": entry_px, "stop_loss": stop_px, "take_profit": take_px}
    results = {"get_candles": candles} if candles else {}

    entry = _management_entry(decision, side, levels, results, atr_14)

    # A committed directional trade with finite entry/stop always yields an entry.
    assert entry is not None
    assert entry.get("available") is True
    assert entry["action"] == side

    # ── Reconstruct the SAME plan the helper builds ──────────────────────────
    if case["is_managed"]:
        expected_plan = _coerce_management_plan(
            case["plan_dict"], side, entry_px, stop_px, atr_14
        )
    else:
        expected_plan = trade_manager.single_target_plan(entry_px, stop_px, take_px)
    assert expected_plan is not None

    # ── Plan fidelity: legs / breakeven / trailing EXACTLY match (R9.1, R9.2) ─
    assert entry["entry"] == expected_plan.entry
    assert entry["initial_stop"] == expected_plan.initial_stop
    assert entry["legs"] == _legs_of(expected_plan)
    assert entry["breakeven"] == _breakeven_of(expected_plan)
    assert entry["trailing"] == _trailing_of(expected_plan)
    assert entry["atr_14"] == expected_plan.atr_14
    # The style is the single fixed-enumeration value for the committed plan.
    assert entry["style"] == trade_manager.management_style_tag(expected_plan)

    if case["is_managed"]:
        # No fabrication / no omission: the recorded legs are exactly the declared
        # legs (same count, same target/fraction, same order).
        declared_legs = case["plan_dict"]["legs"]
        assert entry["legs"] == declared_legs
    else:
        # A Single_Target_Trade is recorded as single-target WITHOUT fabricating
        # scale-out legs (R9.3): style ``single``, exactly one leg at fraction 1.0,
        # and no breakeven / trailing.
        assert entry["style"] == "single"
        assert len(entry["legs"]) == 1
        assert entry["legs"][0]["fraction"] == 1.0
        assert entry["legs"][0]["target"] == take_px
        assert entry["breakeven"] is None
        assert entry["trailing"] is None

    # ── Simulated-exit fidelity (R9.1, R9.2) ─────────────────────────────────
    if candles:
        # The helper scored the plan; the cited Exit_Breakdown + Realized_R must
        # EXACTLY equal a direct simulate_plan call with the SAME resolved config
        # (the simulator is pure & deterministic -> byte-identical output).
        direct = trade_manager.simulate_plan(
            expected_plan, candles, trade_manager.resolve_trade_manager_config()
        )
        assert entry["status"] == direct.status
        assert entry["realized_r"] == direct.realized_r
        assert entry["residual_fraction"] == direct.residual_fraction
        assert entry["exit_breakdown"] == _exit_breakdown_of(direct)
        # Every cited exit appears in the Trade_Manager's Exit_Breakdown — nothing
        # is fabricated (R9.2).
        assert len(entry["exit_breakdown"]) == len(direct.fills)
    else:
        # With no candles in scope the helper records the PLAN only — it never
        # fabricates an exit (R9.2).
        assert "status" not in entry
        assert "realized_r" not in entry
        assert "exit_breakdown" not in entry
