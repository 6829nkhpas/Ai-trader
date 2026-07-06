# Feature: session-expiry-awareness, Property 17: The defensibility session entry mirrors the tool result without fabrication
"""Property-based test for the defensibility session entry (graph.py, task 5.3).

Feature: session-expiry-awareness

This module implements design **Property 17: The defensibility session entry
mirrors the tool result without fabrication**:

    When the most recent ``get_session_context`` result in message history is a
    usable Session_Label, ``_session_entry`` builds an ``available: True`` entry
    that copies the five session fields — ``session_phase``,
    ``minutes_since_open``, ``minutes_until_close``, ``expiry_context``, and
    ``time_favorability`` — VERBATIM from that result. It never infers,
    substitutes, or fabricates any value not present in the tool output.

Validates: Requirements 8.1, 8.2.

The implementation under test lives in ``graph.py``:
  - ``_session_entry(results)`` — reads ``results['get_session_context']`` (the
    ``_latest_tool_results`` map entry, already parsed to a dict) and mirrors a
    usable Session_Label into the defensibility record.

The real LLM / Rust server is never invoked: ``_session_entry`` operates purely
on an in-memory results map, so the property runs fully in-memory.

The sys.path / import pattern mirrors
``tests/test_session_market_data_gate_properties.py``: the service directory
(one level up) is prepended to ``sys.path`` so ``graph`` is importable when
pytest is run from anywhere.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import SESSION_PHASES, TIME_FAVORABILITY, _session_entry  # noqa: E402

SESSION_TOOL = "get_session_context"

# ── Strategies ───────────────────────────────────────────────────────────────
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_timeframe = st.sampled_from(["1m", "5m", "10m", "15m", "1h", "4h", "1d"])
_session_phase = st.sampled_from(sorted(SESSION_PHASES))
_time_favorability = st.sampled_from(sorted(TIME_FAVORABILITY))

# minutes_since_open / minutes_until_close: a finite non-negative number or null.
_minutes_value = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=400.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def _usable_session_label(draw):
    """A full, usable Session_Label dict as produced by the session tool.

    A usable label must carry a ``session_phase`` and ``time_favorability`` from
    their fixed enums plus an ``expiry_context`` object with a boolean
    ``is_expiry_day`` — exactly the recognition predicate ``_session_entry``
    applies.
    """
    label = {
        "session_phase": draw(_session_phase),
        "minutes_since_open": draw(_minutes_value),
        "minutes_until_close": draw(_minutes_value),
        "expiry_context": {
            "is_expiry_day": draw(st.booleans()),
            "days_until_expiry": draw(st.integers(min_value=0, max_value=6)),
        },
        "time_favorability": draw(_time_favorability),
        "symbol": draw(_symbol),
        "timeframe": draw(_timeframe),
    }
    return label


# ─────────────────────────────────────────────────────────────────────────────
# Property 17: defensibility session entry mirrors the tool result
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-expiry-awareness, Property 17: The defensibility session entry mirrors the tool result without fabrication
@settings(max_examples=200, deadline=None)
@given(label=_usable_session_label())
def test_property_17_defensibility_session_entry_mirrors_tool_result(label):
    """Validates: Requirements 8.1, 8.2

    (8.1) The defensibility record includes a session entry carrying the
          Session_Phase, minutes-since-open, minutes-until-close, the
          Expiry_Context, and the Time_Favorability taken from the most recent
          ``get_session_context`` result.
    (8.2) The entry is populated using ONLY values returned by the session tool;
          no value is inferred or substituted — every mirrored field equals the
          source verbatim.
    """
    # The _latest_tool_results map: the most recent session result is a usable label.
    results = {SESSION_TOOL: label}

    entry = _session_entry(results)

    # The entry must be marked available for a usable Session_Label.
    assert entry.get("available") is True

    # ── R8.1 / R8.2: every session field is copied VERBATIM from the source ──
    assert entry["session_phase"] == label["session_phase"]
    assert entry["minutes_since_open"] == label["minutes_since_open"]
    assert entry["minutes_until_close"] == label["minutes_until_close"]
    assert entry["time_favorability"] == label["time_favorability"]

    # Expiry_Context is mirrored field-for-field (no inference / substitution).
    assert entry["expiry_context"]["is_expiry_day"] == label["expiry_context"]["is_expiry_day"]
    assert (
        entry["expiry_context"]["days_until_expiry"]
        == label["expiry_context"]["days_until_expiry"]
    )

    # ── No fabrication: each mirrored value originates from the source result ──
    # The mirrored phase/favorability are exactly the source's (drawn from the
    # fixed enums), never a default or substitute.
    assert entry["session_phase"] in SESSION_PHASES
    assert entry["time_favorability"] in TIME_FAVORABILITY

    # Symbol/timeframe context, when carried, is also verbatim (never invented).
    assert entry.get("symbol") == label["symbol"]
    assert entry.get("timeframe") == label["timeframe"]

    # Determinism: a second build over the identical source yields an identical entry.
    assert _session_entry({SESSION_TOOL: dict(label)}) == entry
