"""Property-based test for trade-management verification-step / decision
ordering (stream_events.py, task 13.3).

Feature: trade-management

This module implements design **Property 22: Verification step precedes the
decision**:

    For any committed decision, the event sequence emitted by ``decision_events``
    places the trade-management ``VERIFICATION_STEP`` (the step carrying the
    stable check id ``trade-management``) BEFORE the ``DECISION`` event of that
    run.

Validates: Requirements 10.5.

The implementation under test lives in ``stream_events.py``:
  - ``decision_events(decision)`` — yields ``(event_name, payload)`` tuples:
    every ``VERIFICATION_STEP`` (built by ``build_verification_steps``) first,
    then the single ``DECISION`` event (built by ``build_decision_event``).
  - ``build_verification_steps`` derives exactly one trade-management step from
    the decision's ``defensibility`` record via ``_trade_management_step`` (check
    id ``trade-management``), in both FIND mode (no ``validator_checks``) and
    VERIFY mode (``validator_checks`` present).

No LLM / graph / Rust server is invoked — ``decision_events`` is a pure function
of the decision dict. The decision's ``defensibility`` record is generated with
a ``management`` entry in the managed form (an active multi-leg style), the
single-target form (style ``single``), the invalid form (a simulated ``invalid``
status), and the absent form (``available`` falsy / a non-dict / missing /
unrecognized style), covering the full outcome space (R10.2-R10.4) while never
changing the ordering guarantee under test (R10.5). Other defensibility content
(forecast / regime / relative-strength entries) and ``validator_checks`` are
sometimes included so both branches of ``build_verification_steps`` are
exercised.

The sys.path / import pattern mirrors
``tests/test_forecast_step_precedes_decision_properties.py``: the service
directory (one level up) is prepended to ``sys.path`` so ``stream_events`` is
importable when pytest is run from anywhere.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (stream_events.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from stream_events import (  # noqa: E402
    decision_events,
    VERIFICATION_STEP,
    DECISION,
)

TRADE_MANAGEMENT_CHECK_ID = "trade-management"


# ── Strategies ───────────────────────────────────────────────────────────────
_action = st.sampled_from(["BUY", "SELL", "HOLD"])
# The fixed active-management styles (everything other than ``single``); these
# drive the trade-management step to a ``pass`` outcome (R10.2).
_active_style = st.sampled_from(
    ["scale", "scale-be", "scale-trail", "scale-be-trail", "be", "trail"]
)
_price = st.floats(min_value=1.0, max_value=1e5, allow_nan=False, allow_infinity=False)
_fraction = st.floats(
    min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False
)


@st.composite
def _legs(draw):
    """A small list of scale-out legs (target, fraction)."""
    n = draw(st.integers(min_value=1, max_value=3))
    return [
        {"target": draw(_price), "fraction": draw(_fraction)} for _ in range(n)
    ]


@st.composite
def _managed_entry(draw):
    """An available management entry for an active multi-leg plan (-> pass)."""
    return {
        "available": True,
        "style": draw(_active_style),
        "action": draw(st.sampled_from(["BUY", "SELL"])),
        "entry": draw(_price),
        "initial_stop": draw(_price),
        "legs": draw(_legs()),
        "breakeven": draw(
            st.one_of(st.none(), st.fixed_dictionaries({"price": _price, "r_multiple": st.none()}))
        ),
        "trailing": draw(
            st.one_of(
                st.none(),
                st.fixed_dictionaries({"atr_multiple": _price, "r_increment": st.none()}),
            )
        ),
        "atr_14": draw(st.one_of(st.none(), _price)),
    }


@st.composite
def _single_target_entry(draw):
    """An available single-target management entry (style ``single`` -> informational)."""
    return {
        "available": True,
        "style": "single",
        "action": draw(st.sampled_from(["BUY", "SELL"])),
        "entry": draw(_price),
        "initial_stop": draw(_price),
        "legs": [{"target": draw(_price), "fraction": 1.0}],
        "breakeven": None,
        "trailing": None,
        "atr_14": draw(st.one_of(st.none(), _price)),
    }


@st.composite
def _invalid_entry(draw):
    """An available entry whose simulated status is ``invalid`` (-> fail)."""
    return {
        "available": True,
        "style": draw(_active_style),
        "status": "invalid",
        "action": draw(st.sampled_from(["BUY", "SELL"])),
        "entry": draw(_price),
        "initial_stop": draw(_price),
        "legs": draw(_legs()),
        "breakeven": None,
        "trailing": None,
        "atr_14": draw(st.one_of(st.none(), _price)),
    }


# Absent / not-evaluable management: an explicit unavailable marker, a non-dict
# entry, a missing entry, or an available entry with an unrecognized style — all
# of which drive the trade-management step to a non-pass outcome (R10.4) without
# changing the ordering guarantee under test (R10.5).
_absent_entry = st.one_of(
    st.just({"available": False, "reason": "no candles in scope"}),
    st.just({"available": False}),
    st.just({"available": True, "style": "unknown"}),  # unrecognized active style
    st.just({"available": True}),  # available but no style
    st.none(),  # entry omitted entirely
    st.just("not-a-dict"),  # non-dict entry
)

_management_entry = st.one_of(
    _managed_entry(),
    _single_target_entry(),
    _invalid_entry(),
    _absent_entry,
)


@st.composite
def _validator_check(draw):
    """A single VERIFY-mode validator check (never the trade-management check)."""
    return {
        "check": draw(
            st.sampled_from(["risk-reward", "volatility-stop", "level-alignment"])
        ),
        "outcome": draw(st.sampled_from(["pass", "fail", "informational"])),
    }


@st.composite
def _decision(draw):
    """Draw a committed decision whose defensibility record carries a management entry."""
    management = draw(_management_entry)
    record = {}
    if management is not None:
        record["management"] = management

    # Sometimes include other defensibility content so the FIND-mode derivation
    # exercises the full sibling-step set alongside the trade-management step.
    if draw(st.booleans()):
        record["forecast"] = {"available": False, "reason": "insufficient candles"}

    # Sometimes include VERIFY-mode validator_checks so build_verification_steps
    # exercises the VERIFY branch (which appends the trade-management step) as
    # well as the FIND branch (which derives all checks including it).
    if draw(st.booleans()):
        record["validator_checks"] = draw(
            st.lists(_validator_check(), min_size=0, max_size=4)
        )

    return {
        "action": draw(_action),
        "conviction_score": draw(st.integers(min_value=0, max_value=10)),
        "setup_validation": draw(st.text(max_size=40)),
        "reason": draw(st.text(max_size=40)),
        "source": "declare_trade",
        "defensibility": record,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 22: the trade-management verification step precedes the DECISION event
# ─────────────────────────────────────────────────────────────────────────────

# Feature: trade-management, Property 22: Verification step precedes the decision
@settings(max_examples=50, deadline=None)
@given(decision=_decision())
def test_property_22_tm_step_precedes_decision_event(decision):
    """Validates: Requirements 10.5

    For any committed decision, ``decision_events`` emits exactly one
    trade-management ``VERIFICATION_STEP`` (check id ``trade-management``) and
    exactly one ``DECISION`` event, and the trade-management step is ordered
    strictly before the ``DECISION`` event of that run.
    """
    events = list(decision_events(decision))

    # Indices of the trade-management VERIFICATION_STEP(s) and the DECISION event(s).
    tm_indices = [
        i
        for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP
        and payload.get("check") == TRADE_MANAGEMENT_CHECK_ID
    ]
    decision_indices = [i for i, (name, _) in enumerate(events) if name == DECISION]

    # Exactly one trade-management step exists (R10.1) and exactly one DECISION.
    assert len(tm_indices) == 1, (
        f"expected exactly one trade-management step, found {len(tm_indices)} in {events!r}"
    )
    assert len(decision_indices) == 1, (
        f"expected exactly one DECISION event, found {len(decision_indices)} in {events!r}"
    )

    # The trade-management VERIFICATION_STEP precedes the DECISION event (R10.5).
    assert tm_indices[0] < decision_indices[0], (
        f"trade-management step at index {tm_indices[0]} must precede DECISION "
        f"at index {decision_indices[0]} in {events!r}"
    )
