# Feature: session-expiry-awareness, Property 21: The session verification step precedes the DECISION event
"""Property-based test for session-step ordering (task 6.3).

Feature: session-expiry-awareness

This module implements design **Property 21: The session verification step
precedes the DECISION event**:

    For any decision, the event sequence emitted by ``decision_events`` places
    the session ``VERIFICATION_STEP`` before the ``DECISION`` event of that run.

Validates: Requirements 9.6.

The implementation under test lives in ``stream_events.py``:
  - ``decision_events(decision)`` — yields every ``VERIFICATION_STEP`` tuple
    (one of which is the session step, check id ``session``) and then the
    ``DECISION`` tuple, so verification steps precede the decision (R9.6).
  - ``build_verification_steps(decision)`` — surfaces exactly one session step
    in both FIND mode (no ``validator_checks``) and VERIFY mode (an explicit
    ``validator_checks`` list).

The real LLM / graph is never invoked. The defensibility ``session`` entry is
built directly in the shape ``graph._session_entry`` produces: a usable label
``{"available": True, "session_phase": ..., "time_favorability": ...,
"expiry_context": {...}, "minutes_since_open": ..., "minutes_until_close": ...}``
or an Unavailable_Marker ``{"available": False, "reason": ...}``.

The sys.path / import pattern mirrors
``tests/test_session_verification_step_properties.py``: the service directory
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

SESSION_CHECK = "session"

_PHASES = [
    "pre_open", "opening", "morning", "midday", "afternoon", "closing", "post_close",
]


# ── Strategies ───────────────────────────────────────────────────────────────
_minutes_value = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=400.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def _expiry_context(draw):
    """An Expiry_Context dict carrying the expiry-day flag and days-until."""
    flag = draw(st.booleans())
    days = 0 if flag else draw(st.integers(min_value=1, max_value=6))
    return {"is_expiry_day": flag, "days_until_expiry": days}


@st.composite
def _available_session_entry(draw):
    """A usable session entry across all three Time_Favorability values."""
    favorability = draw(st.sampled_from(["favorable", "unfavorable", "neutral"]))
    return {
        "available": True,
        "session_phase": draw(st.sampled_from(_PHASES)),
        "time_favorability": favorability,
        "minutes_since_open": draw(_minutes_value),
        "minutes_until_close": draw(_minutes_value),
        "expiry_context": draw(_expiry_context()),
    }


# An Unavailable_Marker entry: available False, only an optional reason.
_unavailable_session_entry = st.builds(
    lambda reason: ({"available": False, "reason": reason} if reason is not None
                    else {"available": False}),
    st.one_of(
        st.none(),
        st.sampled_from(
            [
                "invalid timestamp: expected a finite epoch-millisecond number, got None",
                "candle retrieval timed out",
                "no reference candle available for RELIANCE/15m",
            ]
        ),
    ),
)

# Malformed / missing entries route to a not-evaluable session step too.
_degenerate_session_entry = st.one_of(
    st.none(),
    st.just({}),
    st.text(max_size=6),
    st.integers(),
)

_session_entry = st.one_of(
    _available_session_entry(),
    _unavailable_session_entry,
    _degenerate_session_entry,
)

# Optional FIND-mode record fields the sibling checks read. Their presence must
# not affect the session-step-before-DECISION ordering.
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


def _session_step_indices(events):
    """Indices of session VERIFICATION_STEP events in the emitted sequence."""
    return [
        i for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP
        and isinstance(payload, dict)
        and payload.get("check") == SESSION_CHECK
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Property 21: the session verification step precedes the DECISION event
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 21: The session verification step precedes the DECISION event
@settings(max_examples=200, deadline=None)
@given(
    session=_session_entry,
    extras=_find_mode_extras,
    checks=st.one_of(st.none(), _validator_checks),
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
    conviction=st.integers(min_value=0, max_value=10),
)
def test_property_21_session_step_precedes_decision(session, extras, checks, action, conviction):
    """Validates: Requirements 9.6

    For any decision (FIND mode with no validator_checks, or VERIFY mode with an
    explicit validator_checks list), driving ``decision_events`` yields exactly
    one session ``VERIFICATION_STEP`` and a single ``DECISION`` event, with the
    session step strictly before the DECISION event.
    """
    record = dict(extras)
    record["session"] = session
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

    # Exactly one session verification step is present.
    session_indices = _session_step_indices(events)
    assert len(session_indices) == 1, (
        f"expected exactly one '{SESSION_CHECK}' step, got {len(session_indices)}"
    )

    # ── R9.6: the session step precedes the DECISION event ───────────────────
    assert session_indices[0] < decision_index, (
        f"session step at index {session_indices[0]} must precede the DECISION "
        f"event at index {decision_index}"
    )

    # The DECISION is the terminal event of the run (every verification step,
    # not just the session one, comes before it).
    assert decision_index == len(events) - 1
    assert all(
        names[i] == VERIFICATION_STEP for i in range(decision_index)
    ), "every event before the DECISION must be a VERIFICATION_STEP"


# Feature: session-expiry-awareness, Property 21: The session verification step precedes the DECISION event
def test_property_21_explicit_ordering_states():
    """Validates: Requirements 9.6

    A non-Hypothesis exhaustive check across the mandated session states (each
    Time_Favorability value and an unavailable marker), in both FIND and VERIFY
    mode, confirming the session step is emitted before the DECISION event.
    """
    base_label = {
        "session_phase": "morning",
        "minutes_since_open": 30.0,
        "minutes_until_close": 300.0,
        "expiry_context": {"is_expiry_day": False, "days_until_expiry": 2},
    }
    session_entries = [
        {"available": True, "time_favorability": "favorable", **base_label},
        {"available": True, "time_favorability": "unfavorable", **base_label},
        {"available": True, "time_favorability": "neutral", **base_label},
        {"available": False, "reason": "invalid timestamp"},
    ]
    # FIND mode (no validator_checks) and VERIFY mode (explicit list).
    mode_records = [
        {},
        {"validator_checks": [{"check": "risk-reward", "outcome": "pass"}]},
    ]

    for entry in session_entries:
        for mode in mode_records:
            record = dict(mode)
            record["session"] = entry
            decision = {"action": "BUY", "conviction_score": 7, "defensibility": record}

            events = list(decision_events(decision))
            names = [name for name, _ in events]

            decision_index = names.index(DECISION)
            session_idx = _session_step_indices(events)
            assert len(session_idx) == 1
            assert session_idx[0] < decision_index
