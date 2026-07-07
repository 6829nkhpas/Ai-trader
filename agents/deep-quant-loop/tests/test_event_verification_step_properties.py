"""Property-based test for the event-risk verification step (task 6.2).

Feature: earnings-event-risk-gate

This module implements design **Property 22: Exactly one event-risk verification
step with the correct outcome mapping**:

    For any decision, the built Verification_Steps contain exactly one event-risk
    step carrying the stable check identifier ``event-risk``, whose outcome is
    ``pass`` when the recorded Event_Risk is ``clear``, ``fail`` when
    ``through_event``, ``informational`` when ``imminent``, and ``not-evaluable``
    (with an unavailable indication and no fabricated risk) when the event entry
    is unavailable.

Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5.

The implementation under test lives in ``stream_events.py``:
  - ``_event_step(record)`` — maps the defensibility ``event`` entry to a single
    step under the fixed check id ``event-risk`` (R9.1-R9.5).
  - ``_derive_find_mode_steps(record)`` — FIND-mode derivation; appends exactly
    one ``_event_step(record)``.
  - ``build_verification_steps(decision)`` — surfaces exactly one event-risk step
    in both FIND mode (no ``validator_checks``) and VERIFY mode (an explicit
    ``validator_checks`` list).

The real LLM / graph is never invoked. The defensibility ``event`` entry is built
directly in the shape ``graph._event_entry`` produces: a usable Event_Assessment
``{"available": True, "event_risk": ..., "days_until_event": ..., "event_date":
..., "event_recommendation": ...}`` or an Unavailable_Marker
``{"available": False, "reason": ...}``.

The sys.path / import pattern mirrors ``tests/test_session_verification_step_properties.py``:
the service directory (one level up) is prepended to ``sys.path`` so
``stream_events`` is importable when pytest is run from anywhere.
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
    _event_step,
    _derive_find_mode_steps,
    build_verification_steps,
)

EVENT_CHECK = "event-risk"

# The Event_Risk -> outcome mapping the step must implement (R9.2-R9.4).
_EVENT_RISK_OUTCOME = {
    "clear": "pass",
    "through_event": "fail",
    "imminent": "informational",
}
# Outcomes that would betray a fabricated event_risk on the unavailable path.
_FABRICATED_OUTCOMES = set(_EVENT_RISK_OUTCOME.values())

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
    # presence must not change the single step's outcome mapping.
    if event_risk == "through_event" and draw(st.booleans()):
        entry["trade_held_through_event"] = (
            "The committed trade would be held through a scheduled event."
        )
    return entry


# An Unavailable_Marker entry: available False, only an optional reason (R9.5).
_unavailable_reason = st.one_of(
    st.none(),
    st.sampled_from(
        [
            "no event source configured",
            "no upcoming event known for RELIANCE",
            "event calendar API timed out",
            "gate disabled by configuration",
            "invalid timestamp: expected a finite epoch-millisecond number, got None",
        ]
    ),
)
_unavailable_event_entry = st.builds(
    lambda reason: ({"available": False, "reason": reason} if reason is not None
                    else {"available": False}),
    _unavailable_reason,
)

# An "available but unrecognized event_risk" entry must also be treated as
# unavailable (no fabricated outcome, R9.5).
_unrecognized_risk_entry = st.builds(
    lambda risk: {
        "available": True,
        "event_risk": risk,
        "days_until_event": 3,
        "event_date": "2024-06-30",
        "event_recommendation": "size_down",
    },
    st.one_of(st.none(), st.text(max_size=8).filter(lambda s: s not in _EVENT_RISK_OUTCOME)),
)

# Malformed / missing entries route to not-evaluable as well.
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

# Optional FIND-mode record fields the other checks read. Their presence/absence
# must not affect the single event-risk step. Crucially the record carries NO
# ``validator_checks`` so it routes through FIND mode.
_find_mode_extras = st.fixed_dictionaries(
    {},
    optional={
        "risk_reward": st.floats(min_value=0.0, max_value=10.0,
                                 allow_nan=False, allow_infinity=False),
        "volatility_basis": st.sampled_from(["stop >= 1.5x ATR", "n/a"]),
        "macro_trend_conflict": st.sampled_from(["Aligned with 1D trend", "n/a"]),
    },
)


def _only_event_step(steps):
    """Return the single event-risk step, asserting exactly one (R9.1)."""
    event_steps = [s for s in steps if s.get("check") == EVENT_CHECK]
    assert len(event_steps) == 1, (
        f"expected exactly one '{EVENT_CHECK}' step, got {len(event_steps)}"
    )
    return event_steps[0]


def _assert_outcome_matches_entry(step, entry):
    """Assert the step's outcome maps the entry per R9.2-R9.5."""
    assert step["check"] == EVENT_CHECK
    outcome = step.get("outcome")
    assert outcome  # always present

    event_risk = entry.get("event_risk") if isinstance(entry, dict) else None
    if (
        isinstance(entry, dict)
        and entry.get("available")
        and event_risk in _EVENT_RISK_OUTCOME
    ):
        # ── R9.2 / R9.3 / R9.4: event_risk maps to the exact outcome ─────────
        expected = _EVENT_RISK_OUTCOME[event_risk]
        # The outcome may carry a suffix; assert the primary token.
        primary = outcome.split()[0] if outcome else outcome
        assert primary == expected, (
            f"event_risk={event_risk!r} -> outcome {outcome!r}, "
            f"expected primary token {expected!r}"
        )
    else:
        # ── R9.5: unavailable -> not-evaluable, no fabricated risk ───────────
        assert outcome.startswith("not-evaluable"), (
            f"unavailable event risk must report not-evaluable, got {outcome!r}"
        )
        assert "unavailable" in outcome, (
            f"unavailable event outcome must carry an 'unavailable' "
            f"indication, got {outcome!r}"
        )
        # No fabricated pass/fail/informational outcome on the unavailable path.
        assert outcome not in _FABRICATED_OUTCOMES
        # And the step never invents an event_risk field.
        assert "event_risk" not in step


# ─────────────────────────────────────────────────────────────────────────────
# Property 22: exactly one event-risk verification step + correct outcome mapping
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 22: Exactly one event-risk verification step with the correct outcome mapping
@settings(max_examples=25, deadline=None)
@given(
    event=_event_entry,
    extras=_find_mode_extras,
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
)
def test_property_22_event_verification_step_outcome_mapping(event, extras, action):
    """Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5

    For any event entry shape (each Event_Risk value, an unavailable marker, an
    unrecognized event_risk, or a malformed entry):

      * ``_event_step`` returns a single step under the stable check id
        ``event-risk`` whose outcome maps event_risk correctly (pass / fail /
        informational), or ``not-evaluable`` (with an 'unavailable' indication,
        no fabricated risk) when unavailable;
      * FIND-mode derivation (``_derive_find_mode_steps``) contains EXACTLY ONE
        event-risk step with that same outcome;
      * VERIFY-mode surfacing (``build_verification_steps`` over a record with an
        explicit ``validator_checks`` list) contains EXACTLY ONE event-risk step
        with that same outcome.
    """
    record = dict(extras)
    record["event"] = event

    # ── Direct mapping via _event_step (R9.1-R9.5) ───────────────────────────
    direct = _event_step(record)
    _assert_outcome_matches_entry(direct, event)
    expected_outcome = direct["outcome"]

    # ── FIND mode: build_verification_steps routes here (no validator_checks) ─
    find_decision = {"action": action, "defensibility": record}
    find_steps = build_verification_steps(find_decision)
    find_step = _only_event_step(find_steps)
    assert find_step["outcome"] == expected_outcome
    _assert_outcome_matches_entry(find_step, event)

    # ── FIND mode: the raw derivation also yields exactly one event-risk step ─
    derived_steps = _derive_find_mode_steps(record)
    derived_step = _only_event_step(derived_steps)
    assert derived_step["outcome"] == expected_outcome

    # ── VERIFY mode: an explicit validator_checks list surfaces exactly one ──
    verify_record = dict(record)
    verify_record["validator_checks"] = [
        {"check": "risk-reward", "outcome": "pass", "detail": "RR=2.5"},
        {"check": "macro-trend-alignment", "outcome": "informational"},
    ]
    verify_decision = {"action": action, "defensibility": verify_record}
    verify_steps = build_verification_steps(verify_decision)
    verify_step = _only_event_step(verify_steps)
    assert verify_step["outcome"] == expected_outcome
    _assert_outcome_matches_entry(verify_step, event)


# Feature: earnings-event-risk-gate, Property 22: Exactly one event-risk verification step with the correct outcome mapping
def test_property_22_explicit_state_table():
    """Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5

    A non-Hypothesis exhaustive check of the four mandated states (available
    clear / through_event / imminent, and unavailable), confirming the exact
    outcome and that the unavailable path never fabricates an event_risk.
    """
    base = {
        "days_until_event": 3,
        "event_date": "2024-06-30",
        "event_recommendation": "size_down",
    }
    cases = [
        ({"available": True, "event_risk": "clear", **base}, "pass"),
        ({"available": True, "event_risk": "through_event", **base}, "fail"),
        ({"available": True, "event_risk": "imminent", **base}, "informational"),
        ({"available": False, "reason": "no event source configured"}, "not-evaluable"),
    ]

    for entry, want in cases:
        record = {"event": entry}
        step = _event_step(record)
        assert step["check"] == EVENT_CHECK
        if want == "not-evaluable":
            assert step["outcome"].startswith("not-evaluable")
            assert "unavailable" in step["outcome"]
            assert step["outcome"] not in _FABRICATED_OUTCOMES
            assert "event_risk" not in step
        else:
            assert step["outcome"].split()[0] == want

        # The derivation surfaces exactly one event-risk step with that outcome.
        derived = _derive_find_mode_steps(record)
        only = _only_event_step(derived)
        assert only["outcome"] == step["outcome"]

        # VERIFY mode surfaces exactly one event-risk step with that outcome.
        verify_record = dict(record)
        verify_record["validator_checks"] = [
            {"check": "risk-reward", "outcome": "pass", "detail": "RR=2.5"},
        ]
        verify_steps = build_verification_steps(
            {"action": "BUY", "defensibility": verify_record}
        )
        verify_only = _only_event_step(verify_steps)
        assert verify_only["outcome"] == step["outcome"]
