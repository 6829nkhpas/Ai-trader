"""Property-based test for Time_Favorability totality (session.py, task 2.9).

Feature: session-expiry-awareness

This module implements design **Property 7: Time_Favorability is a total
function with the expiry override**:

    For any Session_Phase and Expiry_Context, ``derive_time_favorability``
    returns exactly one value drawn from ``favorable`` / ``unfavorable`` /
    ``neutral``, equal to the phase's base favorability except that an
    ``is_expiry_day`` candle in the ``afternoon`` or ``closing`` phase is
    down-weighted to ``unfavorable``; so every (phase, expiry-day) combination
    maps to exactly one Time_Favorability.

Validates: Requirements 1.5, 2.3.

The strategies below enumerate / generate every (Session_Phase x is_expiry_day)
combination — the seven phases crossed with the expiry-day flag — mixed with
arbitrary phase strings (to confirm totality for unrecognized phases) and
arbitrary, internally consistent ``SessionConfig`` values. The property asserts
that the result is always one of the three favorability values (totality), that
it equals the base mapping on non-expiry days, and that it is overridden to
``unfavorable`` for an expiry-day ``afternoon`` / ``closing`` candle.

The sys.path / import pattern mirrors the existing session/regime test modules:
the service directory (one level up) is prepended to ``sys.path`` so ``session``
is importable when pytest is run from anywhere.
"""

import os
import sys
from datetime import time as dtime

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (session.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from session import (  # noqa: E402
    FAVORABLE,
    NEUTRAL,
    UNFAVORABLE,
    PHASE_AFTERNOON,
    PHASE_CLOSING,
    PHASE_MIDDAY,
    PHASE_MORNING,
    PHASE_OPENING,
    PHASE_POST_CLOSE,
    PHASE_PRE_OPEN,
    SessionConfig,
    derive_time_favorability,
)

# The three valid Time_Favorability values (the codomain of the derivation).
_FAVORABILITY_VALUES = {FAVORABLE, UNFAVORABLE, NEUTRAL}

# The seven Session_Phase values that the classifier can produce.
_PHASES = [
    PHASE_PRE_OPEN,
    PHASE_OPENING,
    PHASE_MORNING,
    PHASE_MIDDAY,
    PHASE_AFTERNOON,
    PHASE_CLOSING,
    PHASE_POST_CLOSE,
]

# The phases the expiry override down-weights to ``unfavorable`` on an
# expiry-day candle (the expiry-afternoon chop window) — Requirement 2.3.
_EXPIRY_OVERRIDE_PHASES = {PHASE_AFTERNOON, PHASE_CLOSING}

# The authoritative base-favorability mapping transcribed directly from the
# design's "Time_Favorability derivation" table. This is the expectation the
# property checks against for non-expiry days.
#
#   pre_open    -> neutral
#   opening     -> unfavorable
#   morning     -> favorable
#   midday      -> neutral
#   afternoon   -> favorable
#   closing     -> neutral
#   post_close  -> neutral
_BASE_MAPPING = {
    PHASE_PRE_OPEN: NEUTRAL,
    PHASE_OPENING: UNFAVORABLE,
    PHASE_MORNING: FAVORABLE,
    PHASE_MIDDAY: NEUTRAL,
    PHASE_AFTERNOON: FAVORABLE,
    PHASE_CLOSING: NEUTRAL,
    PHASE_POST_CLOSE: NEUTRAL,
}


# ─────────────────────────────────────────────────────────────────────────────
# Strategies
# ─────────────────────────────────────────────────────────────────────────────

# Phases: the seven valid values plus arbitrary strings to confirm the function
# is total for *every* input (an unrecognized phase must still return a single
# valid favorability rather than raise or return garbage).
_phase = st.sampled_from(_PHASES)
_arbitrary_phase = st.text(max_size=12)
_phase_or_garbage = st.one_of(_phase, _arbitrary_phase)


@st.composite
def _time_of_day(draw):
    """An arbitrary valid 24h time-of-day."""
    return dtime(
        draw(st.integers(min_value=0, max_value=23)),
        draw(st.integers(min_value=0, max_value=59)),
    )


@st.composite
def _config(draw):
    """An arbitrary, internally consistent ``SessionConfig``.

    The favorability derivation depends only on the phase and the expiry-day
    flag (not on any config field), so any valid config object suffices; we
    still vary it to confirm the derivation is independent of configuration.
    """
    tz = draw(
        st.sampled_from(
            ["Asia/Kolkata", "UTC", "America/New_York", "Europe/London"]
        )
    )
    open_minutes = draw(st.integers(min_value=0, max_value=23 * 60))
    close_minutes = draw(st.integers(min_value=open_minutes + 1, max_value=24 * 60 - 1))
    return SessionConfig(
        timezone=tz,
        open_time=dtime(open_minutes // 60, open_minutes % 60),
        close_time=dtime(close_minutes // 60, close_minutes % 60),
        opening_minutes=draw(st.integers(min_value=0, max_value=120)),
        closing_minutes=draw(st.integers(min_value=0, max_value=120)),
        midday_start=draw(_time_of_day()),
        midday_end=draw(_time_of_day()),
        expiry_weekday=draw(st.integers(min_value=0, max_value=6)),
    )


@st.composite
def _expiry_context(draw, is_expiry_day=None):
    """An Expiry_Context dict with ``is_expiry_day`` and ``days_until_expiry``.

    When ``is_expiry_day`` is supplied the flag is fixed; otherwise it is drawn.
    ``days_until_expiry`` is kept consistent (0 on the expiry day, in [1, 6]
    otherwise) but the derivation only reads ``is_expiry_day``.
    """
    flag = draw(st.booleans()) if is_expiry_day is None else is_expiry_day
    days = 0 if flag else draw(st.integers(min_value=1, max_value=6))
    return {"is_expiry_day": flag, "days_until_expiry": days}


def _expected_favorability(phase, is_expiry_day):
    """The favorability the design mandates for (phase, is_expiry_day)."""
    if is_expiry_day and phase in _EXPIRY_OVERRIDE_PHASES:
        return UNFAVORABLE
    return _BASE_MAPPING.get(phase, NEUTRAL)


# ─────────────────────────────────────────────────────────────────────────────
# Property 7: Time_Favorability is a total function with the expiry override
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 7: Time_Favorability is a total function with the expiry override
@settings(max_examples=300, deadline=None)
@given(
    phase=_phase_or_garbage,
    is_expiry_day=st.booleans(),
    config=_config(),
)
def test_property_7_favorability_is_total_with_expiry_override(
    phase, is_expiry_day, config
):
    """Validates: Requirements 1.5, 2.3

    For any Session_Phase (recognized or arbitrary) and any Expiry_Context,
    ``derive_time_favorability`` returns exactly one of the three favorability
    values (totality); on a non-expiry day a recognized phase matches the base
    mapping; and an expiry-day ``afternoon`` / ``closing`` candle is overridden
    to ``unfavorable``.
    """
    expiry_context = {
        "is_expiry_day": is_expiry_day,
        "days_until_expiry": 0 if is_expiry_day else 3,
    }
    result = derive_time_favorability(phase, expiry_context, config)

    # Totality: every (phase, expiry-day) combination maps to exactly one value
    # of the three-value favorability enumeration — never None, never an
    # exception, never a stray string.
    assert result in _FAVORABILITY_VALUES

    # Determinism: repeated derivation with identical inputs is identical.
    assert derive_time_favorability(phase, expiry_context, config) == result

    if phase in _BASE_MAPPING:
        # A recognized phase must match the authoritative mapping + override.
        assert result == _expected_favorability(phase, is_expiry_day)

        if not is_expiry_day:
            # Non-expiry day: exactly the base favorability, no override.
            assert result == _BASE_MAPPING[phase]
        elif phase in _EXPIRY_OVERRIDE_PHASES:
            # Expiry-day afternoon/closing: overridden to unfavorable.
            assert result == UNFAVORABLE
        else:
            # Expiry day, non-override phase: the base value is unchanged.
            assert result == _BASE_MAPPING[phase]


@settings(max_examples=100, deadline=None)
@given(data=st.data())
def test_property_7_every_phase_expiry_combination_is_covered(data):
    """Validates: Requirements 1.5, 2.3

    Exhaustively assert all fourteen (Session_Phase x is_expiry_day)
    combinations map to exactly the favorability the design dictates, confirming
    the derivation is total and covers every cell, under an arbitrary config.
    """
    config = data.draw(_config())
    covered = set()
    for phase in _PHASES:
        for is_expiry_day in (False, True):
            expiry_context = data.draw(_expiry_context(is_expiry_day=is_expiry_day))
            result = derive_time_favorability(phase, expiry_context, config)

            assert result in _FAVORABILITY_VALUES
            assert result == _expected_favorability(phase, is_expiry_day)
            covered.add((phase, is_expiry_day))

    # All 7 phases x 2 expiry-day flags = 14 cells were exercised.
    assert len(covered) == len(_PHASES) * 2 == 14


def test_property_7_full_combination_mapping():
    """Validates: Requirements 1.5, 2.3

    A non-Hypothesis exhaustive check of the full (phase x expiry-day) table
    against the design mapping, documenting the expected favorability for every
    cell and confirming the expiry override applies only to afternoon/closing.
    """
    config = SessionConfig(
        timezone="Asia/Kolkata",
        open_time=dtime(9, 15),
        close_time=dtime(15, 30),
        opening_minutes=15,
        closing_minutes=30,
        midday_start=dtime(11, 30),
        midday_end=dtime(13, 30),
        expiry_weekday=3,
    )

    expected = {
        (PHASE_PRE_OPEN, False): NEUTRAL,
        (PHASE_PRE_OPEN, True): NEUTRAL,
        (PHASE_OPENING, False): UNFAVORABLE,
        (PHASE_OPENING, True): UNFAVORABLE,
        (PHASE_MORNING, False): FAVORABLE,
        (PHASE_MORNING, True): FAVORABLE,
        (PHASE_MIDDAY, False): NEUTRAL,
        (PHASE_MIDDAY, True): NEUTRAL,
        (PHASE_AFTERNOON, False): FAVORABLE,
        (PHASE_AFTERNOON, True): UNFAVORABLE,   # expiry override
        (PHASE_CLOSING, False): NEUTRAL,
        (PHASE_CLOSING, True): UNFAVORABLE,     # expiry override
        (PHASE_POST_CLOSE, False): NEUTRAL,
        (PHASE_POST_CLOSE, True): NEUTRAL,
    }

    for (phase, is_expiry_day), want in expected.items():
        ctx = {"is_expiry_day": is_expiry_day, "days_until_expiry": 0 if is_expiry_day else 2}
        got = derive_time_favorability(phase, ctx, config)
        assert got == want, f"({phase}, expiry={is_expiry_day}) -> {got!r}, want {want!r}"
        assert got in _FAVORABILITY_VALUES
