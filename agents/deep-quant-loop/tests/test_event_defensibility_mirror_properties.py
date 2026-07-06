# Feature: earnings-event-risk-gate, Property 19: The defensibility event entry mirrors the tool result without fabrication
"""Property-based test for the defensibility event entry (graph.py, task 5.3).

Feature: earnings-event-risk-gate

This module implements design **Property 19: The defensibility event entry
mirrors the tool result without fabrication**:

    When the most recent ``get_event_risk`` result in message history is a
    usable Event_Assessment, ``_event_entry`` builds an ``available: True``
    entry that copies the four assessment fields — ``event_risk``,
    ``days_until_event``, ``event_date``, and ``event_recommendation`` —
    VERBATIM from that result. It never infers, substitutes, or fabricates any
    value not present in the tool output.

Validates: Requirements 8.1, 8.2.

The implementation under test lives in ``graph.py``:
  - ``_event_entry(results)`` — reads ``results['get_event_risk']`` (the
    ``_latest_tool_results`` map entry, already parsed to a dict) and mirrors a
    usable Event_Assessment into the defensibility record.

The real LLM / Rust server is never invoked: ``_event_entry`` operates purely
on an in-memory results map, so the property runs fully in-memory.

The sys.path / import pattern mirrors
``tests/test_session_defensibility_mirror_properties.py``: the service
directory (one level up) is prepended to ``sys.path`` so ``graph`` is
importable when pytest is run from anywhere.
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
from graph import EVENT_RISK_STATES, EVENT_RECOMMENDATIONS, _event_entry  # noqa: E402

EVENT_TOOL = "get_event_risk"

# ── Strategies ───────────────────────────────────────────────────────────────
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_holding_horizon = st.sampled_from(["intraday", "swing", "1d", "3d", "1w", "2w", "1m"])
_event_risk = st.sampled_from(sorted(EVENT_RISK_STATES))
_event_recommendation = st.sampled_from(sorted(EVENT_RECOMMENDATIONS))

# days_until_event: a finite non-negative number or null per the tool contract.
_days_value = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=120),
    st.floats(min_value=0.0, max_value=120.0, allow_nan=False, allow_infinity=False),
)

# event_date: an ISO-like reference date string identifying the Scheduled_Event.
_event_date = st.dates(
    min_value=__import__("datetime").date(2000, 1, 1),
    max_value=__import__("datetime").date(2100, 12, 31),
).map(lambda d: d.isoformat())


@st.composite
def _usable_event_assessment(draw):
    """A full, usable Event_Assessment dict as produced by the event tool.

    A usable assessment must carry an ``event_risk`` and an
    ``event_recommendation`` from their fixed enums plus an ``event_date``
    string — exactly the recognition predicate ``_event_entry`` applies.
    """
    assessment = {
        "event_risk": draw(_event_risk),
        "days_until_event": draw(_days_value),
        "event_date": draw(_event_date),
        "event_recommendation": draw(_event_recommendation),
        "symbol": draw(_symbol),
        "holding_horizon": draw(_holding_horizon),
    }
    return assessment


# ─────────────────────────────────────────────────────────────────────────────
# Property 19: defensibility event entry mirrors the tool result
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 19: The defensibility event entry mirrors the tool result without fabrication
@settings(max_examples=200, deadline=None)
@given(assessment=_usable_event_assessment())
def test_property_19_defensibility_event_entry_mirrors_tool_result(assessment):
    """Validates: Requirements 8.1, 8.2

    (8.1) The defensibility record includes an event entry carrying the
          Event_Risk_State, the days-until-event, the reference event_date, and
          the Event_Recommendation taken from the most recent ``get_event_risk``
          result.
    (8.2) The entry is populated using ONLY values returned by the event tool;
          no value is inferred or substituted — every mirrored field equals the
          source verbatim.
    """
    # The _latest_tool_results map: the most recent event result is a usable
    # Event_Assessment.
    results = {EVENT_TOOL: assessment}

    entry = _event_entry(results)

    # The entry must be marked available for a usable Event_Assessment.
    assert entry.get("available") is True

    # ── R8.1 / R8.2: every assessment field is copied VERBATIM from the source ──
    assert entry["event_risk"] == assessment["event_risk"]
    assert entry["days_until_event"] == assessment["days_until_event"]
    assert entry["event_date"] == assessment["event_date"]
    assert entry["event_recommendation"] == assessment["event_recommendation"]

    # ── No fabrication: each mirrored value originates from the source result ──
    # The mirrored risk/recommendation are exactly the source's (drawn from the
    # fixed enums), never a default or substitute.
    assert entry["event_risk"] in EVENT_RISK_STATES
    assert entry["event_recommendation"] in EVENT_RECOMMENDATIONS

    # Symbol/holding_horizon context, when carried, is also verbatim (never invented).
    assert entry.get("symbol") == assessment["symbol"]
    assert entry.get("holding_horizon") == assessment["holding_horizon"]

    # No extra fabricated risk field is introduced beyond the mirrored/context
    # fields: the entry's keys are a subset of {available + mirrored + context}.
    allowed_keys = {
        "available",
        "event_risk",
        "days_until_event",
        "event_date",
        "event_recommendation",
        "symbol",
        "holding_horizon",
    }
    assert set(entry.keys()) <= allowed_keys

    # Determinism: a second build over the identical source yields an identical entry.
    assert _event_entry({EVENT_TOOL: dict(assessment)}) == entry
