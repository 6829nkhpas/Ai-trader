# Feature: session-expiry-awareness, Property 18: Absent session context is recorded as unavailable
#
# For any message history containing no usable get_session_context result, the
# session entry of the Defensibility_Record is marked unavailable and contains no
# Session_Phase, minutes, Expiry_Context, or Time_Favorability substitute values.
"""Property-based test for the absent-session defensibility entry (graph.py, task 5.4).

Feature: session-expiry-awareness

This module implements design **Property 18: Absent session context is recorded
as unavailable**:

    For any message history containing no usable ``get_session_context`` result,
    the session entry of the Defensibility_Record is marked unavailable and
    contains no Session_Phase, minutes, Expiry_Context, or Time_Favorability
    substitute values.

Validates: Requirements 8.3.

The implementation under test lives in ``graph.py``:
  - ``_session_entry(results)`` — the pure read of the ``_latest_tool_results``
    map. It returns ``{"available": False, "reason": <str>}`` (with NO fabricated
    ``session_phase`` / ``time_favorability`` / ``expiry_context`` / minutes) when
    the map carries no usable ``get_session_context`` Session_Label — i.e. the
    key is absent, the value is not a dict, the value is an Unavailable_Marker
    (``{"unavailable": true, ...}``), or the value is a label missing or carrying
    an out-of-enum ``session_phase`` / ``time_favorability`` or a malformed
    ``expiry_context``.

``results`` is the map produced by ``_latest_tool_results(messages)``; this test
drives ``_session_entry`` directly with generated maps that lack a usable session
label, which is exactly the state produced by a message history containing no
usable ``get_session_context`` result.

The sys.path / import pattern mirrors the other ``tests/`` property modules: the
service directory (one level up) is prepended to ``sys.path`` so ``graph`` is
importable when pytest is run from anywhere. The real LLM / Rust server is never
invoked.
"""

import os
import sys

from hypothesis import assume, given, settings
from hypothesis import strategies as st

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import SESSION_PHASES, TIME_FAVORABILITY  # noqa: E402

SESSION_TOOL = "get_session_context"

# Substitute fields a usable Session_Label would carry. When the session context
# is absent/unavailable, NONE of these may appear in the entry (R8.3 — no
# fabrication).
SUBSTITUTE_KEYS = (
    "session_phase",
    "minutes_since_open",
    "minutes_until_close",
    "expiry_context",
    "time_favorability",
)

# Other tool names used as harmless noise in the results map so we prove
# ``_session_entry`` looks ONLY at the get_session_context slot.
_OTHER_TOOL_NAMES = [
    "get_market_regime",
    "get_relative_strength",
    "get_forecast",
    "get_candles",
    "get_support_resistance",
]


# ── Helpers ──────────────────────────────────────────────────────────────────
def _is_usable_label(value) -> bool:
    """Mirror ``graph._session_entry``'s definition of a usable Session_Label.

    A usable label is a dict that is NOT an Unavailable_Marker and carries an
    in-enum session_phase, an in-enum time_favorability, and an expiry_context
    dict with a boolean is_expiry_day.
    """
    if not isinstance(value, dict):
        return False
    if value.get("unavailable") is True:
        return False
    expiry = value.get("expiry_context")
    return (
        value.get("session_phase") in SESSION_PHASES
        and value.get("time_favorability") in TIME_FAVORABILITY
        and isinstance(expiry, dict)
        and isinstance(expiry.get("is_expiry_day"), bool)
    )


# ── Strategies ─────────────────────────────────────────────────────────────--
_reason_text = st.text(min_size=0, max_size=40)
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])

# A value under get_session_context that is NOT a dict at all.
_non_dict_value = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=20),
    st.lists(st.integers(), max_size=4),
    st.tuples(st.integers(), st.integers()),
)

# An honest Unavailable_Marker ({"unavailable": true, "reason": ...}), optionally
# carrying symbol/timeframe context.
@st.composite
def _unavailable_marker(draw):
    marker = {"unavailable": True, "reason": draw(_reason_text)}
    if draw(st.booleans()):
        marker["symbol"] = draw(_symbol)
    if draw(st.booleans()):
        marker["timeframe"] = draw(_timeframe)
    return marker


# A "label-shaped" dict that is NOT a usable label: at least one of session_phase,
# time_favorability, or expiry_context is missing or malformed. Never carries
# unavailable=True (so it exercises the no-usable-label rejection path, not the
# marker path).
_bad_phase = st.one_of(
    st.none(),
    st.sampled_from(["", "noon", "OPENING", "lunch", "pre-open", "open"]),
    st.integers(),
)
_bad_favorability = st.one_of(
    st.none(),
    st.sampled_from(["", "great", "bad", "FAVORABLE", "ok"]),
    st.integers(),
)
_bad_expiry = st.one_of(
    st.none(),
    st.just("expiry"),
    st.integers(),
    st.just({}),  # missing is_expiry_day
    st.just({"is_expiry_day": "yes"}),  # non-boolean
    st.just({"is_expiry_day": 1}),  # non-boolean (int, not bool)
    st.just({"days_until_expiry": 0}),  # missing is_expiry_day
    st.lists(st.integers(), max_size=2),
)
_maybe_minutes = st.one_of(st.none(), st.floats(min_value=0, max_value=400, allow_nan=False))


@st.composite
def _broken_label(draw):
    label = {}
    # Each field is independently maybe-present and maybe-valid; we then force the
    # whole thing to be non-usable via ``assume``.
    if draw(st.booleans()):
        label["session_phase"] = draw(
            st.one_of(st.sampled_from(sorted(SESSION_PHASES)), _bad_phase)
        )
    if draw(st.booleans()):
        label["time_favorability"] = draw(
            st.one_of(st.sampled_from(sorted(TIME_FAVORABILITY)), _bad_favorability)
        )
    if draw(st.booleans()):
        label["expiry_context"] = draw(
            st.one_of(
                st.just({"is_expiry_day": True, "days_until_expiry": 0}), _bad_expiry
            )
        )
    if draw(st.booleans()):
        label["minutes_since_open"] = draw(_maybe_minutes)
    if draw(st.booleans()):
        label["minutes_until_close"] = draw(_maybe_minutes)
    if draw(st.booleans()):
        label["symbol"] = draw(_symbol)
    if draw(st.booleans()):
        label["timeframe"] = draw(_timeframe)
    # Never an honest marker here, and never a usable label.
    label.pop("unavailable", None)
    assume(not _is_usable_label(label))
    return label


# The value to place under the get_session_context slot, or a sentinel meaning the
# key is absent entirely.
_ABSENT = object()
_session_slot = st.one_of(
    st.just(_ABSENT),
    _non_dict_value,
    _unavailable_marker(),
    _broken_label(),
)


@st.composite
def _results_without_usable_session(draw):
    """A ``_latest_tool_results``-shaped map lacking a usable session label."""
    results = {}
    # Harmless noise from other tools so we prove the entry reads only the
    # get_session_context slot.
    for name in draw(st.lists(st.sampled_from(_OTHER_TOOL_NAMES), max_size=3, unique=True)):
        results[name] = draw(
            st.one_of(
                st.just({"unavailable": True, "reason": "noise"}),
                st.dictionaries(st.text(max_size=5), st.integers(), max_size=3),
                st.none(),
            )
        )
    slot = draw(_session_slot)
    if slot is not _ABSENT:
        results[SESSION_TOOL] = slot
    return results


# ── Property 18 ────────────────────────────────────────────────────────────--
@settings(max_examples=300)
@given(results=_results_without_usable_session())
def test_absent_session_context_recorded_as_unavailable(results):
    """No usable get_session_context result ⇒ entry unavailable, no substitutes."""
    # Guard: the generated map must genuinely lack a usable label.
    assert not _is_usable_label(results.get(SESSION_TOOL))

    entry = graph._session_entry(results)

    # The entry is a dict explicitly marked unavailable.
    assert isinstance(entry, dict)
    assert entry.get("available") is False

    # No fabricated Session_Phase / minutes / Expiry_Context / Time_Favorability.
    for key in SUBSTITUTE_KEYS:
        assert key not in entry, f"unavailable entry must not carry {key!r}"

    # An unavailable entry carries an honest, non-empty reason string.
    assert isinstance(entry.get("reason"), str)
    assert entry["reason"] != ""


@settings(max_examples=120)
@given(reason=_reason_text)
def test_unavailable_marker_reason_is_carried_through(reason):
    """An Unavailable_Marker's own reason is surfaced verbatim when present."""
    marker = {"unavailable": True, "reason": reason, "symbol": "RELIANCE", "timeframe": "15m"}
    entry = graph._session_entry({SESSION_TOOL: marker})

    assert entry.get("available") is False
    for key in SUBSTITUTE_KEYS:
        assert key not in entry
    # Non-empty marker reason flows through; empty/missing falls back to a default.
    expected = reason or "session context unavailable"
    assert entry.get("reason") == expected


def test_missing_session_key_entirely_is_unavailable():
    """A results map with no get_session_context key at all ⇒ unavailable."""
    entry = graph._session_entry({"get_forecast": {"unavailable": True}})
    assert entry.get("available") is False
    for key in SUBSTITUTE_KEYS:
        assert key not in entry
    assert isinstance(entry.get("reason"), str) and entry["reason"] != ""
