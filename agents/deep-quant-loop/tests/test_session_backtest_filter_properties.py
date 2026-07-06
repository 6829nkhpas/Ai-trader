"""Property-based test for the backtest session filter (backtest.py, task 9.3).

Feature: session-expiry-awareness

This module implements design **Property 25: The enabled filter excludes
unfavorable signals and retains unavailable ones**:

    With the session filter enabled, the with-filter drop predicate
    ``backtest._signal_is_session_unfavorable`` returns ``True`` for EXACTLY the
    signals whose session result is an AVAILABLE ``unfavorable`` label, and
    ``False`` for every other signal — ``favorable``, ``neutral``, an
    Unavailable_Marker, and an absent session entry — so the with-filter run
    drops exactly the available-``unfavorable`` signals while RETAINING every
    ``Unavailable_Marker`` signal (R11.5).

Validates: Requirements 11.2, 11.5.

The strategy drives the predicate two complementary ways:

  * **Integration path** — generate arbitrary candle timestamps (valid epoch
    milliseconds spanning years/weekdays/times, plus invalid timestamps) and
    arbitrary internally-consistent ``SessionConfig`` values, classify each with
    the SAME ``session.classify_session`` the live tool path uses, build the
    seeded-trade defensibility entry via ``backtest._session_defensibility_entry``
    (exactly as ``generate_and_score`` does), and assert the predicate fires iff
    the entry is an available ``unfavorable`` label. This exercises the real
    favorable / neutral / unfavorable / unavailable space the classifier produces.
  * **Enumerated path** — directly construct decisions carrying every
    favorability value, the unavailable marker, and the absent-entry case, to
    pin the drop/retain contract explicitly.

The sys.path / import pattern mirrors the sibling ``test_session_*`` modules.
"""

import os
import sys
from datetime import datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (session.py / backtest.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import session  # noqa: E402
from session import SessionConfig  # noqa: E402
from backtest import (  # noqa: E402
    _session_defensibility_entry,
    _signal_is_session_unfavorable,
)

# DST-free timezones keep classification stable across the generated date range
# while still exercising the configured-timezone conversion path.
_DST_FREE_TZS = ["Asia/Kolkata", "UTC", "Asia/Tokyo", "Asia/Dubai", "Asia/Karachi"]

_FAVORABILITIES = ["favorable", "unfavorable", "neutral"]


@st.composite
def _time_of_day(draw):
    """An arbitrary valid 24h time-of-day."""
    return dtime(
        draw(st.integers(min_value=0, max_value=23)),
        draw(st.integers(min_value=0, max_value=59)),
    )


@st.composite
def _config(draw):
    """An arbitrary, internally consistent ``SessionConfig`` (open < close)."""
    open_minutes = draw(st.integers(min_value=0, max_value=23 * 60))
    close_minutes = draw(st.integers(min_value=open_minutes + 1, max_value=24 * 60 - 1))
    return SessionConfig(
        timezone=draw(st.sampled_from(_DST_FREE_TZS)),
        open_time=dtime(open_minutes // 60, open_minutes % 60),
        close_time=dtime(close_minutes // 60, close_minutes % 60),
        opening_minutes=draw(st.integers(min_value=0, max_value=120)),
        closing_minutes=draw(st.integers(min_value=0, max_value=120)),
        midday_start=draw(_time_of_day()),
        midday_end=draw(_time_of_day()),
        expiry_weekday=draw(st.integers(min_value=0, max_value=6)),
    )


@st.composite
def _timestamp_ms(draw):
    """A candle timestamp: usually a valid epoch-ms across years/weekdays/times,
    occasionally an invalid value so ``classify_session`` yields an
    Unavailable_Marker (the RETAIN case)."""
    kind = draw(st.integers(min_value=0, max_value=9))
    if kind == 0:
        # Invalid timestamps -> Unavailable_Marker from the classifier.
        return draw(st.sampled_from([None, float("nan"), float("inf"), "x", {}]))
    date = draw(
        st.dates(
            min_value=datetime(2018, 1, 1).date(),
            max_value=datetime(2035, 12, 31).date(),
        )
    )
    hour = draw(st.integers(min_value=0, max_value=23))
    minute = draw(st.integers(min_value=0, max_value=59))
    dt = datetime(date.year, date.month, date.day, hour, minute, tzinfo=ZoneInfo("UTC"))
    return int(dt.timestamp() * 1000)


def _expected(entry: dict) -> bool:
    """The predicate's specification: drop iff AVAILABLE and ``unfavorable``."""
    return entry.get("available") is True and entry.get("time_favorability") == "unfavorable"


# ─────────────────────────────────────────────────────────────────────────────
# Property 25 (integration): classifier -> entry -> predicate
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 25: The enabled filter excludes unfavorable signals and retains unavailable ones
@settings(max_examples=300, deadline=None)
@given(timestamp_ms=_timestamp_ms(), config=_config())
def test_property_25_filter_excludes_unfavorable_retains_unavailable(timestamp_ms, config):
    """Validates: Requirements 11.2, 11.5

    Classify an arbitrary candle timestamp with the same ``classify_session`` the
    live path uses, build the seeded-trade session entry exactly as the seeder
    does, and assert ``_signal_is_session_unfavorable`` drops EXACTLY the
    available-``unfavorable`` signals and retains every other signal — including
    every Unavailable_Marker.
    """
    session_result = session.classify_session(timestamp_ms, config, symbol="SYM", timeframe="15m")
    entry = _session_defensibility_entry(session_result)
    decision = {"defensibility": {"session": entry}}

    dropped = _signal_is_session_unfavorable(decision)

    # The predicate fires iff the entry is an available ``unfavorable`` label.
    assert dropped == _expected(entry), (
        f"predicate {dropped} disagrees with spec for entry {entry!r} "
        f"(from result {session_result!r})"
    )

    # An invalid timestamp produced an Unavailable_Marker -> the entry is not
    # available -> the signal is RETAINED, never dropped on the basis of the
    # session being unavailable (R11.5).
    is_unavailable_marker = isinstance(session_result, dict) and session_result.get("unavailable") is True
    if is_unavailable_marker:
        assert entry.get("available") is False
        assert dropped is False, "Unavailable_Marker signals must be retained (R11.5)"

    # A usable label is dropped strictly when its favorability is ``unfavorable``;
    # ``favorable`` / ``neutral`` are retained (R11.2).
    if entry.get("available") is True:
        fav = entry.get("time_favorability")
        if fav == "unfavorable":
            assert dropped is True
        else:
            assert dropped is False, f"{fav!r} signals must be retained"


# ─────────────────────────────────────────────────────────────────────────────
# Property 25 (enumerated): every entry shape pinned explicitly
# ─────────────────────────────────────────────────────────────────────────────

@st.composite
def _decision_with_entry(draw):
    """Build a decision whose session entry covers every shape:
    available favorable/neutral/unfavorable, an Unavailable_Marker, and absent."""
    kind = draw(
        st.sampled_from(
            ["available", "unavailable", "absent_session", "absent_defensibility", "empty_entry"]
        )
    )
    if kind == "available":
        fav = draw(st.sampled_from(_FAVORABILITIES))
        entry = {
            "available": True,
            "session_phase": "afternoon",
            "time_favorability": fav,
        }
        return {"defensibility": {"session": entry}}, fav == "unfavorable"
    if kind == "unavailable":
        entry = {"available": False, "reason": draw(st.text(max_size=20))}
        return {"defensibility": {"session": entry}}, False
    if kind == "absent_session":
        return {"defensibility": {}}, False
    if kind == "absent_defensibility":
        return {}, False
    # empty_entry: present but with no available/favorability keys.
    return {"defensibility": {"session": {}}}, False


# Feature: session-expiry-awareness, Property 25: The enabled filter excludes unfavorable signals and retains unavailable ones
@settings(max_examples=200, deadline=None)
@given(case=_decision_with_entry())
def test_property_25_enumerated_drop_retain_contract(case):
    """Validates: Requirements 11.2, 11.5

    Pin the drop/retain contract for every entry shape: only an AVAILABLE
    ``unfavorable`` entry is dropped; favorable, neutral, unavailable, and any
    absent/empty entry are retained.
    """
    decision, expected_dropped = case
    assert _signal_is_session_unfavorable(decision) is expected_dropped


# ─────────────────────────────────────────────────────────────────────────────
# Property 25 (sanity): the classifier really produces all favorabilities so the
# integration test above exercises a non-trivial input space.
# ─────────────────────────────────────────────────────────────────────────────

def test_property_25_classifier_produces_each_favorability():
    """Sanity guard: the default config yields favorable, unfavorable, and
    neutral labels, so the integration property is not vacuously testing only one
    branch. Not a correctness property — a coverage guard for Property 25."""
    config = session.resolve_session_config()
    seen = set()
    # Walk a week of 5-minute candles in the configured timezone.
    base = datetime(2024, 1, 1, tzinfo=ZoneInfo(config.timezone))
    for day in range(7):
        for minute in range(0, 24 * 60, 5):
            dt = base + timedelta(days=day, minutes=minute)
            ts = int(dt.timestamp() * 1000)
            result = session.classify_session(ts, config)
            fav = result.get("time_favorability")
            if fav is not None:
                seen.add(fav)
    assert {"favorable", "unfavorable", "neutral"}.issubset(seen), (
        f"expected all three favorabilities to be reachable, saw {seen}"
    )
