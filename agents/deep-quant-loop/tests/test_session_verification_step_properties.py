"""Property-based test for the session verification step (task 6.2).

Feature: session-expiry-awareness

This module implements design **Property 20: Exactly one session verification
step with the correct outcome mapping**:

    For any decision, the built Verification_Steps contain exactly one session
    step carrying the stable check identifier ``session``, whose outcome is
    ``pass`` when the recorded Time_Favorability is ``favorable``, ``fail`` when
    ``unfavorable``, ``informational`` when ``neutral``, and ``not-evaluable``
    (with an unavailable indication and no fabricated favorability) when the
    session entry is unavailable.

Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5.

The implementation under test lives in ``stream_events.py``:
  - ``_session_step(record)`` — maps the defensibility ``session`` entry to a
    single step under the fixed check id ``session`` (R9.1-R9.5).
  - ``_derive_find_mode_steps(record)`` — FIND-mode derivation; appends exactly
    one ``_session_step(record)``.
  - ``build_verification_steps(decision)`` — surfaces exactly one session step
    in both FIND mode (no ``validator_checks``) and VERIFY mode (an explicit
    ``validator_checks`` list).

The real LLM / graph is never invoked. The defensibility ``session`` entry is
built directly in the shape ``graph._session_entry`` produces: a usable label
``{"available": True, "session_phase": ..., "time_favorability": ...,
"expiry_context": {...}, "minutes_since_open": ..., "minutes_until_close": ...}``
or an Unavailable_Marker ``{"available": False, "reason": ...}``.

The sys.path / import pattern mirrors ``tests/test_rs_verification_step_properties.py``:
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
    _session_step,
    _derive_find_mode_steps,
    build_verification_steps,
)

SESSION_CHECK = "session"

# The Time_Favorability -> outcome mapping the step must implement (R9.2-R9.4).
_FAVORABILITY_OUTCOME = {
    "favorable": "pass",
    "unfavorable": "fail",
    "neutral": "informational",
}
# Outcomes that would betray a fabricated favorability on the unavailable path.
_FABRICATED_OUTCOMES = set(_FAVORABILITY_OUTCOME.values())

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
    """A usable session entry across all three Time_Favorability values (R9.2-9.4)."""
    favorability = draw(st.sampled_from(["favorable", "unfavorable", "neutral"]))
    return {
        "available": True,
        "session_phase": draw(st.sampled_from(_PHASES)),
        "time_favorability": favorability,
        "minutes_since_open": draw(_minutes_value),
        "minutes_until_close": draw(_minutes_value),
        "expiry_context": draw(_expiry_context()),
    }


# An Unavailable_Marker entry: available False, only an optional reason (R9.5).
_unavailable_reason = st.one_of(
    st.none(),
    st.sampled_from(
        [
            "invalid timestamp: expected a finite epoch-millisecond number, got None",
            "candle retrieval timed out",
            "no reference candle available for RELIANCE/15m",
        ]
    ),
)
_unavailable_session_entry = st.builds(
    lambda reason: ({"available": False, "reason": reason} if reason is not None
                    else {"available": False}),
    _unavailable_reason,
)

# An "available but unrecognized favorability" entry must also be treated as
# unavailable (no fabricated outcome, R9.5).
_unrecognized_favorability_entry = st.builds(
    lambda fav: {
        "available": True,
        "session_phase": "morning",
        "time_favorability": fav,
        "minutes_since_open": 30.0,
        "minutes_until_close": 300.0,
        "expiry_context": {"is_expiry_day": False, "days_until_expiry": 2},
    },
    st.one_of(st.none(), st.text(max_size=8).filter(lambda s: s not in _FAVORABILITY_OUTCOME)),
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
# must not affect the single session step. Crucially the record carries NO
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


def _only_session_step(steps):
    """Return the single session step, asserting exactly one (R9.1)."""
    session_steps = [s for s in steps if s.get("check") == SESSION_CHECK]
    assert len(session_steps) == 1, (
        f"expected exactly one '{SESSION_CHECK}' step, got {len(session_steps)}"
    )
    return session_steps[0]


def _assert_outcome_matches_entry(step, entry):
    """Assert the step's outcome maps the entry per R9.2-R9.5."""
    assert step["check"] == SESSION_CHECK
    outcome = step.get("outcome")
    assert outcome  # always present

    favorability = entry.get("time_favorability") if isinstance(entry, dict) else None
    if (
        isinstance(entry, dict)
        and entry.get("available")
        and favorability in _FAVORABILITY_OUTCOME
    ):
        # ── R9.2 / R9.3 / R9.4: favorability maps to the exact outcome ───────
        expected = _FAVORABILITY_OUTCOME[favorability]
        assert outcome == expected, (
            f"time_favorability={favorability!r} -> outcome {outcome!r}, "
            f"expected {expected!r}"
        )
    else:
        # ── R9.5: unavailable -> not-evaluable, no fabricated favorability ───
        assert outcome.startswith("not-evaluable"), (
            f"unavailable session must report not-evaluable, got {outcome!r}"
        )
        assert "unavailable" in outcome, (
            f"unavailable session outcome must carry an 'unavailable' "
            f"indication, got {outcome!r}"
        )
        # No fabricated pass/fail/informational outcome on the unavailable path.
        assert outcome not in _FABRICATED_OUTCOMES
        # And the step never invents a favorability field.
        assert "time_favorability" not in step


# ─────────────────────────────────────────────────────────────────────────────
# Property 20: exactly one session verification step + correct outcome mapping
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 20: Exactly one session verification step with the correct outcome mapping
@settings(max_examples=200, deadline=None)
@given(
    session=_session_entry,
    extras=_find_mode_extras,
    action=st.sampled_from(["BUY", "SELL", "HOLD"]),
)
def test_property_20_session_verification_step_outcome_mapping(session, extras, action):
    """Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5

    For any session entry shape (each Time_Favorability value, an unavailable
    marker, an unrecognized favorability, or a malformed entry):

      * ``_session_step`` returns a single step under the stable check id
        ``session`` whose outcome maps favorability correctly (pass / fail /
        informational), or ``not-evaluable`` (with an 'unavailable' indication,
        no fabricated favorability) when unavailable;
      * FIND-mode derivation (``_derive_find_mode_steps``) contains EXACTLY ONE
        session step with that same outcome;
      * VERIFY-mode surfacing (``build_verification_steps`` over a record with an
        explicit ``validator_checks`` list) contains EXACTLY ONE session step
        with that same outcome.
    """
    record = dict(extras)
    record["session"] = session

    # ── Direct mapping via _session_step (R9.1-R9.5) ─────────────────────────
    direct = _session_step(record)
    _assert_outcome_matches_entry(direct, session)
    expected_outcome = direct["outcome"]

    # ── FIND mode: build_verification_steps routes here (no validator_checks) ─
    find_decision = {"action": action, "defensibility": record}
    find_steps = build_verification_steps(find_decision)
    find_step = _only_session_step(find_steps)
    assert find_step["outcome"] == expected_outcome
    _assert_outcome_matches_entry(find_step, session)

    # ── FIND mode: the raw derivation also yields exactly one session step ───
    derived_steps = _derive_find_mode_steps(record)
    derived_step = _only_session_step(derived_steps)
    assert derived_step["outcome"] == expected_outcome

    # ── VERIFY mode: an explicit validator_checks list surfaces exactly one ──
    verify_record = dict(record)
    verify_record["validator_checks"] = [
        {"check": "risk-reward", "outcome": "pass", "detail": "RR=2.5"},
        {"check": "macro-trend-alignment", "outcome": "informational"},
    ]
    verify_decision = {"action": action, "defensibility": verify_record}
    verify_steps = build_verification_steps(verify_decision)
    verify_step = _only_session_step(verify_steps)
    assert verify_step["outcome"] == expected_outcome
    _assert_outcome_matches_entry(verify_step, session)


# Feature: session-expiry-awareness, Property 20: Exactly one session verification step with the correct outcome mapping
def test_property_20_explicit_state_table():
    """Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5

    A non-Hypothesis exhaustive check of the four mandated states (available
    favorable / unfavorable / neutral, and unavailable), confirming the exact
    outcome and that the unavailable path never fabricates a favorability.
    """
    base = {
        "session_phase": "morning",
        "minutes_since_open": 30.0,
        "minutes_until_close": 300.0,
        "expiry_context": {"is_expiry_day": False, "days_until_expiry": 2},
    }
    cases = [
        ({"available": True, "time_favorability": "favorable", **base}, "pass"),
        ({"available": True, "time_favorability": "unfavorable", **base}, "fail"),
        ({"available": True, "time_favorability": "neutral", **base}, "informational"),
        ({"available": False, "reason": "invalid timestamp"}, "not-evaluable"),
    ]

    for entry, want in cases:
        record = {"session": entry}
        step = _session_step(record)
        assert step["check"] == SESSION_CHECK
        if want == "not-evaluable":
            assert step["outcome"].startswith("not-evaluable")
            assert "unavailable" in step["outcome"]
            assert step["outcome"] not in _FABRICATED_OUTCOMES
            assert "time_favorability" not in step
        else:
            assert step["outcome"] == want

        # The derivation surfaces exactly one session step with that outcome.
        derived = _derive_find_mode_steps(record)
        only = _only_session_step(derived)
        assert only["outcome"] == step["outcome"]
