# Feature: earnings-event-risk-gate, Property 23: The event-risk verification step precedes the DECISION event
"""Property-based test for event-risk step ordering (task 6.3).

Feature: earnings-event-risk-gate

This module implements design **Property 23: The event-risk verification step
precedes the DECISION event**:

    For any decision, the event sequence emitted by ``decision_events`` places
    the event-risk ``VERIFICATION_STEP`` before the ``DECISION`` event of that
    run.

Validates: Requirements 9.6.

The implementation under test lives in ``stream_events.py``:
  - ``decision_events(decision)`` — yields every ``VERIFICATION_STEP`` tuple
    (one of which is the event-risk step, check id ``event-risk``) and then the
    ``DECISION`` tuple, so verification steps precede the decision (R9.6).
  - ``build_verification_steps(decision)`` — surfaces exactly one event-risk
    step in both FIND mode (no ``validator_checks``) and VERIFY mode (an
    explicit ``validator_checks`` list).

The real LLM / graph is never invoked. The defensibility ``event`` entry is
built directly in the shape ``graph._event_entry`` produces: a usable
Event_Assessment ``{"available": True, "event_risk": ..., "days_until_event":
..., "event_date": ..., "event_recommendation": ...}`` or an Unavailable_Marker
``{"available": False, "reason": ...}``.

The sys.path / import pattern mirrors
``tests/test_event_verification_step_properties.py``: the service directory
(one level up) is prepended to ``sys.path`` so ``stream_events`` is importable
when pytest is run from anywhere.
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

EVENT_CHECK = "event-risk"

_RECOMMENDATIONS = ["proceed", "size_down", "shorten_horizon", "stand_aside"]


# ── Strategies ───────────────────────────────────────────────────────────────
_days_value = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=90.0, allow_nan=False, allow_infinity=False),
    st.integers(min_value=0, max_value=90),
)

_event_date_value = st.sampled_from(
    ["2024-01-15", "2024-06-30", "2025-02-01", "2025-12-31"]
)


@st.composite
def _available_event_entry(draw):
    """A usable event entry across all three Event_Risk values (R9.2-9.4)."""
    event_risk = draw(st.sampled_from(["clear", "through_event", "imminent"]))
    entry = {
        "available": True,
        "event_risk": event_risk,
        "days_until_event": draw(_days_value),
        "event_date": draw(_event_date_value),
        "event_recommendation": draw(st.sampled_from(_RECOMMENDATIONS)),
    }
    # A through-event directional trade may carry the held-through statement; its
    # presence must not change the single step's ordering before the DECISION.
    if event_risk == "through_event" and draw(st.booleans()):
        entry["trade_held_through_event"] = (
            "The committed trade would be held through a scheduled event."
        )
    return entry


# An Unavailable_Marker entry: available False, only an optional reason (R9.5).
_unavailable_event_entry = st.builds(
    lambda reason: ({"available": False, "reason": reason} if reason is not None
                    else {"available": False}),
    st.one_of(
        st.none(),
        st.sampled_from(
            [
                "no event source configured",
                "no upcoming event known for RELIANCE",
                "event calendar API timed out",
                "gate disabled by configuration",
            ]
        ),
    ),
)

# An "available but unrecognized event_risk" entry routes to not-evaluable too.
_unrecognized_risk_entry = st.builds(
    lambda risk: {
        "available": True,
        "event_risk": risk,
        "days_until_event": 3,
        "event_date": "2024-06-30",
        "event_recommendation": "size_down",
    },
    st.one_of(
        st.none(),
        st.text(max_size=8).filter(
            lambda s: s not in {"clear", "through_event", "imminent"}
        ),
    ),
)

# Malformed / missing entries route to a not-evaluable event-risk step as well.
_degenerate_event_entry = st.one_of(
    st.none(),
    st.just({}),
    st.text(max_size=6),
    st.integers(),
)

_event_entry = st.one_of(
    _available_event_entry(),
    _unavailable_event_entry,
    _unrecognized_risk_entry,
    _degenerate_event_entry,
)

# Optional FIND-mode record fields the sibling checks read. Their presence must
# not affect the event-step-before-DECISION ordering.
_find_mode_extras = st.fixed_dictionaries(
    {},
    optional={
        "risk_reward": st.floats(min_value=0.0, max_value=10.0,
                                 allow_nan=False, allow_infinity=False),
        "volatility_basis": st.sampled_from(["stop >= 1.5x ATR", "n/a"]),
        "macro_trend_conflict": st.sampled_from(["Aligned with 1D trend", "n/a"]),
    },
)

# An explicit VERIFY-mode validator_checks list (routes through VERIFY surfacing).
_validator_checks = st.lists(
    st.fixed_dictionaries({
        "check": st.sampled_from(["risk-reward", "macro-trend-alignment", "level-alignment"]),
        "outcome": st.sampled_from(["pass", "fail", "informational"]),
    }),
    max_size=4,
)


def _names(events):
    """Materialize the ordered list of event names from decision_events()."""
    return [name for name, _payload in events]


def _event_step_indices(events):
    """Indices of event-risk VERIFICATION_STEP events in the emitted sequence."""
    return [
        i for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP
        and isinstance(payload, dict)
        and payload.get("check") == EVENT_CHECK
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Property 23: the event-risk verification step precedes the DECISION event
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 23: The event-risk verification step precedes the DECISION event
@settings(max_examples=25, deadline=None)
@given(
    event=_event_entry,
    extras=_find_mode_extras,
    checks=st.one_of(st.none(), _validator_checks),
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
    conviction=st.integers(min_value=0, max_value=10),
)
def test_property_23_event_step_precedes_decision(event, extras, checks, action, conviction):
    """Validates: Requirements 9.6

    For any decision (FIND mode with no validator_checks, or VERIFY mode with an
    explicit validator_checks list), driving ``decision_events`` yields exactly
    one event-risk ``VERIFICATION_STEP`` and a single ``DECISION`` event, with
    the event-risk step strictly before the DECISION event.
    """
    record = dict(extras)
    record["event"] = event
    if checks is not None:
        record["validator_checks"] = checks

    decision = {
        "action": action,
        "conviction_score": conviction,
        "reason": "test rationale",
        "defensibility": record,
    }

    events = list(decision_events(decision))
    names = _names(events)

    # A structured decision dict always produces exactly one DECISION event.
    decision_indices = [i for i, n in enumerate(names) if n == DECISION]
    assert len(decision_indices) == 1, f"expected exactly one DECISION, got {len(decision_indices)}"
    decision_index = decision_indices[0]

    # Exactly one event-risk verification step is present.
    event_indices = _event_step_indices(events)
    assert len(event_indices) == 1, (
        f"expected exactly one '{EVENT_CHECK}' step, got {len(event_indices)}"
    )

    # ── R9.6: the event-risk step precedes the DECISION event ────────────────
    assert event_indices[0] < decision_index, (
        f"event-risk step at index {event_indices[0]} must precede the DECISION "
        f"event at index {decision_index}"
    )

    # The DECISION is the terminal event of the run (every verification step,
    # not just the event-risk one, comes before it).
    assert decision_index == len(events) - 1
    assert all(
        names[i] == VERIFICATION_STEP for i in range(decision_index)
    ), "every event before the DECISION must be a VERIFICATION_STEP"


# Feature: earnings-event-risk-gate, Property 23: The event-risk verification step precedes the DECISION event
def test_property_23_explicit_ordering_states():
    """Validates: Requirements 9.6

    A non-Hypothesis exhaustive check across the mandated event-risk states
    (each Event_Risk value and an unavailable marker), in both FIND and VERIFY
    mode, confirming the event-risk step is emitted before the DECISION event.
    """
    base = {
        "days_until_event": 3,
        "event_date": "2024-06-30",
        "event_recommendation": "size_down",
    }
    event_entries = [
        {"available": True, "event_risk": "clear", **base},
        {"available": True, "event_risk": "through_event", **base},
        {"available": True, "event_risk": "imminent", **base},
        {"available": False, "reason": "no event source configured"},
    ]
    # FIND mode (no validator_checks) and VERIFY mode (explicit list).
    mode_records = [
        {},
        {"validator_checks": [{"check": "risk-reward", "outcome": "pass"}]},
    ]

    for entry in event_entries:
        for mode in mode_records:
            record = dict(mode)
            record["event"] = entry
            decision = {"action": "BUY", "conviction_score": 7, "defensibility": record}

            events = list(decision_events(decision))
            names = [name for name, _ in events]

            decision_index = names.index(DECISION)
            event_idx = _event_step_indices(events)
            assert len(event_idx) == 1
            assert event_idx[0] < decision_index
