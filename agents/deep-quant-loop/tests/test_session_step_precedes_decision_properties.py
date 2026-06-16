"""Property-based test for session-step ordering (stream_events.py, task 6.3).

Feature: session-expiry-awareness

This module implements design **Property 21: The session verification step
precedes the DECISION event**:

    When ``decision_events`` expands a committed decision into its ordered event
    tuples, the session ``VERIFICATION_STEP`` (the step carrying the stable
    check id ``session``) is emitted strictly before the ``DECISION`` event of
    that run.

Validates: Requirements 9.6.

The implementation under test lives in ``stream_events.py``:
  - ``decision_events(decision)`` — yields every ``VERIFICATION_STEP`` tuple
    followed by the ``DECISION`` tuple (R9.6). Verification steps precede the
    decision so the observed order reflects the self-verification protocol
    running before the trade is finalized.
  - ``_session_step`` / ``build_verification_steps`` — supply exactly one
    ``session`` step among those verification steps.

The real LLM / graph is never invoked. A committed decision is built directly
with a ``defensibility`` record carrying a ``session`` entry in its various
states (a usable label across all three Time_Favorability values, an
Unavailable_Marker, an unrecognized favorability, and malformed/missing
entries), plus the action + rationale fields ``build_decision_event`` reads, so
``decision_events`` produces both a session ``VERIFICATION_STEP`` and a
``DECISION``. The session entry is built in the shape ``graph._session_entry``
produces: ``{"available": True, "session_phase": ..., "time_favorability": ...,
"expiry_context": {...}, "minutes_since_open": ..., "minutes_until_close": ...}``
or ``{"available": False, "reason": ...}``.

The sys.path / import pattern mirrors the sibling ``test_session_*`` modules: the
service directory (one level up) is prepended to ``sys.path`` so
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
    DECISION,
    VERIFICATION_STEP,
    decision_events,
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

# An "available but unrecognized favorability" entry (routes to not-evaluable).
_unrecognized_favorability_entry = st.builds(
    lambda fav: {
        "available": True,
        "session_phase": "morning",
        "time_favorability": fav,
        "minutes_since_open": 30.0,
        "minutes_until_close": 300.0,
        "expiry_context": {"is_expiry_day": False, "days_until_expiry": 2},
    },
    st.one_of(st.none(), st.text(max_size=8)),
)

# Malformed / missing entries route to not-evaluable as well.
_degenerate_session_entry = st.one_of(
    st.none(),
    st.just({}),
    st.text(max_size=6),
    st.integers(),
)

_session_entry = st.one_of(
    _available_session_entry(),
    _unavailable_session_entry,
    _unrecognized_favorability_entry,
    _degenerate_session_entry,
)

# Optional FIND-mode record fields the other checks read. Their presence/absence
# must not affect ordering. A record with NO ``validator_checks`` routes through
# FIND mode; a record WITH a ``validator_checks`` list routes through VERIFY mode
# — the session step must precede the DECISION on either path.
_find_mode_extras = st.fixed_dictionaries(
    {},
    optional={
        "risk_reward": st.floats(min_value=0.0, max_value=10.0,
                                 allow_nan=False, allow_infinity=False),
        "volatility_basis": st.sampled_from(["stop >= 1.5x ATR", "n/a"]),
        "macro_trend_conflict": st.sampled_from(["Aligned with 1D trend", "n/a"]),
    },
)

_validator_checks = st.one_of(
    st.none(),  # FIND mode (no validator_checks)
    st.just(  # VERIFY mode (explicit validator_checks)
        [
            {"check": "risk-reward", "outcome": "pass", "detail": "RR=2.5"},
            {"check": "macro-trend-alignment", "outcome": "informational"},
        ]
    ),
)


def _event_names(events):
    return [name for name, _ in events]


# ─────────────────────────────────────────────────────────────────────────────
# Property 21: the session verification step precedes the DECISION event
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 21: The session verification step precedes the DECISION event
@settings(max_examples=200, deadline=None)
@given(
    session=_session_entry,
    extras=_find_mode_extras,
    validator_checks=_validator_checks,
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
    conviction=st.integers(min_value=0, max_value=10),
    rationale=st.text(max_size=40),
)
def test_property_21_session_step_precedes_decision(
    session, extras, validator_checks, action, conviction, rationale
):
    """Validates: Requirements 9.6

    For any committed decision whose defensibility record carries a session
    entry (in any of its label / unavailable / unrecognized / malformed states),
    on both the FIND-mode path (no ``validator_checks``) and the VERIFY-mode path
    (an explicit ``validator_checks`` list):

      * ``decision_events`` emits exactly one session ``VERIFICATION_STEP`` and
        exactly one ``DECISION`` event;
      * the session ``VERIFICATION_STEP`` is emitted strictly before the
        ``DECISION`` event;
      * every ``VERIFICATION_STEP`` (not only the session one) precedes the
        ``DECISION`` event, confirming the documented step-then-decision order.
    """
    record = dict(extras)
    record["session"] = session
    if validator_checks is not None:
        record["validator_checks"] = validator_checks

    decision = {
        "action": action,
        "conviction_score": conviction,
        "setup_validation": rationale or None,
        "reason": "forced HOLD" if not rationale else None,
        "defensibility": record,
    }

    events = list(decision_events(decision))
    names = _event_names(events)

    # ── A DECISION is always emitted for a structured decision (R16.7). ──────
    assert names.count(DECISION) == 1, (
        f"expected exactly one DECISION event, got {names.count(DECISION)}"
    )
    decision_index = names.index(DECISION)

    # ── The DECISION is the final event; every verification step precedes it. ─
    assert decision_index == len(names) - 1, (
        f"DECISION must be the last event, found at {decision_index} of "
        f"{len(names)} events: {names}"
    )
    for i, name in enumerate(names):
        if name == VERIFICATION_STEP:
            assert i < decision_index, (
                f"VERIFICATION_STEP at {i} must precede DECISION at "
                f"{decision_index}: {names}"
            )

    # ── Exactly one session VERIFICATION_STEP exists, before the DECISION. ───
    session_indices = [
        i
        for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP
        and isinstance(payload, dict)
        and payload.get("check") == SESSION_CHECK
    ]
    assert len(session_indices) == 1, (
        f"expected exactly one '{SESSION_CHECK}' VERIFICATION_STEP, got "
        f"{len(session_indices)}"
    )
    assert session_indices[0] < decision_index, (
        f"session VERIFICATION_STEP at {session_indices[0]} must precede "
        f"DECISION at {decision_index}: {names}"
    )


# Feature: session-expiry-awareness, Property 21: The session verification step precedes the DECISION event
def test_property_21_explicit_ordering_example():
    """Validates: Requirements 9.6

    A concrete, non-Hypothesis check: a committed BUY whose session entry is a
    usable ``unfavorable`` label yields a session step (outcome ``fail``) strictly
    before the DECISION event, and the DECISION is the terminal event.
    """
    decision = {
        "action": "BUY",
        "conviction_score": 6,
        "setup_validation": "Long continuation against value-area support.",
        "defensibility": {
            "session": {
                "available": True,
                "session_phase": "opening",
                "time_favorability": "unfavorable",
                "minutes_since_open": 3.0,
                "minutes_until_close": 372.0,
                "expiry_context": {"is_expiry_day": False, "days_until_expiry": 2},
            },
        },
    }

    events = list(decision_events(decision))
    names = _event_names(events)

    assert DECISION in names
    decision_index = names.index(DECISION)
    assert decision_index == len(names) - 1

    session_index = next(
        i
        for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP
        and isinstance(payload, dict)
        and payload.get("check") == SESSION_CHECK
    )
    assert session_index < decision_index
    # The session step carried its mapped outcome (sanity: unfavorable -> fail).
    assert events[session_index][1]["outcome"] == "fail"
