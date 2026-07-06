"""Property-based test for the absent-event defensibility entry.

Feature: earnings-event-risk-gate (graph.py, task 5.4)

This module implements design **Property 20: Absent event context is recorded
as unavailable**:

    When no usable ``get_event_risk`` Event_Assessment is present in message
    history — none present at all, only error results ``{"error": ...}``, only
    Unavailable_Markers ``{"unavailable": true}``, a non-dict result, or an
    assessment-shaped result missing/with-invalid categorical enum fields (an
    out-of-enum event_risk / event_recommendation, or a missing/non-string
    event_date) — the defensibility event entry is recorded as unavailable with
    NO fabricated event_risk, days_until_event, event_date, or
    event_recommendation, and the record build never raises.

Validates: Requirements 8.3.

The implementation under test lives in ``graph.py``:
  - ``build_defensibility_record(messages, decision, mode, manual_trade)`` — the
    top-level record builder, whose ``record["event"]`` entry is asserted here.
  - ``_event_entry(results)`` / ``_latest_tool_results(messages)`` — the
    underlying helpers, asserted directly as a second, lower-level check.

``_latest_tool_results`` SKIPS error results (those carrying an ``error``
marker), so an error-only history yields no ``get_event_risk`` entry at all; an
Unavailable_Marker is an honest non-fatal result that passes through and is
recognised by ``_event_entry`` via its ``unavailable: true`` flag; a non-dict
result and an assessment missing/with-invalid enum fields are both treated as
"no usable assessment". In every one of these cases the event entry must be
``{"available": False, "reason": ...}`` with the Event_Risk_State,
days_until_event, event_date, and Event_Recommendation ABSENT.

The real LLM / Rust server is never invoked. A lightweight stub ToolMessage
(``type == "tool"`` with ``.name`` and ``.content``) stands in for the LangChain
``ToolMessage`` — exactly the shape the record code reads. Results are
serialized both as JSON (``{"...": ...}``) and as Python dict/list-repr
(``{'...': ...}``) strings, since both quoting styles flow through the stack.

The sys.path / import pattern mirrors ``tests/test_rs_defensibility_absent_properties.py``:
the service directory (one level up) is prepended to ``sys.path`` so ``graph``
is importable when pytest is run from anywhere.
"""

import json
import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (graph.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import graph  # noqa: E402
from graph import (  # noqa: E402
    build_defensibility_record,
    _latest_tool_results,
    _event_entry,
)

EVENT_TOOL = "get_event_risk"

# Assessment fields that MUST be absent from an unavailable event entry
# (no fabrication).
_FORBIDDEN_KEYS = (
    "event_risk",
    "days_until_event",
    "event_date",
    "event_recommendation",
    "trade_held_through_event",
)


# ── Lightweight stub ToolMessage ─────────────────────────────────────────────
class StubToolMessage:
    """Stand-in for a LangChain ToolMessage. ``_is_tool_message`` matches type 'tool'."""

    def __init__(self, content, name):
        self.content = content
        self.name = name
        self.type = "tool"


def _serialize(payload, style):
    """Serialize a result object as a JSON string or a Python repr string."""
    if style == "json":
        return json.dumps(payload)
    return repr(payload)  # Python repr: single quotes, True/None tokens


# ── Strategies ───────────────────────────────────────────────────────────────
# Symbol restricted to tokens that can never contain the "error" / "unavailable"
# substrings, so the classification of each event result is decided purely by
# its structure, not incidental free text.
_symbol = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", min_size=1, max_size=8)
_horizon = st.sampled_from(["intraday", "multi_session"])
_event_date = st.sampled_from(["2024-05-02", "2024-11-14", "2025-01-30", "2025-07-24"])
_style = st.sampled_from(["json", "repr"])
_action = st.sampled_from(["BUY", "SELL", "HOLD"])

# Reason strings deliberately free of the substring "error" so an Unavailable_
# Marker is never misclassified as an error result.
_unavailable_reason = st.sampled_from([
    "no event source configured",
    "no upcoming event known for the symbol",
    "event source unreachable",
    "retrieval timeout",
    "unavailable marker",
])
_error_text = st.sampled_from([
    "Failed to retrieve event calendar from Rust server: timeout",
    "connection refused",
    "contract_violation",
    "no data",
])

# Invalid categorical values — never the legitimate enum members, and never the
# substrings "error"/"unavailable" — so the result is a parsed dict that fails
# the usable-assessment check in _event_entry.
_bad_risk = st.sampled_from(["high", "low", "CLEAR", "", "earnings", "none"])
_bad_recommendation = st.sampled_from(["hold", "reduce", "PROCEED", "", "avoid", "wait"])


@st.composite
def _error_event_msg(draw):
    """A get_event_risk error result message (carries an ``error`` key)."""
    payload = {
        "symbol": draw(_symbol),
        "holding_horizon": draw(_horizon),
        "error": draw(_error_text),
    }
    return StubToolMessage(_serialize(payload, draw(_style)), EVENT_TOOL)


@st.composite
def _unavailable_event_msg(draw):
    """A get_event_risk Unavailable_Marker result message (omits
    event_risk/event_recommendation/event_date)."""
    payload = {
        "symbol": draw(_symbol),
        "holding_horizon": draw(_horizon),
        "unavailable": True,
        "reason": draw(_unavailable_reason),
    }
    return StubToolMessage(_serialize(payload, draw(_style)), EVENT_TOOL)


@st.composite
def _nondict_event_msg(draw):
    """A get_event_risk result that parses to a NON-dict object (list/scalar).

    ``_event_entry`` treats any non-dict result as "no usable assessment".
    """
    payload = draw(st.sampled_from([
        [1, 2, 3],
        [],
        ["clear", "proceed", "2024-05-02"],
        42,
    ]))
    return StubToolMessage(_serialize(payload, draw(_style)), EVENT_TOOL)


@st.composite
def _invalid_assessment_event_msg(draw):
    """An assessment-shaped get_event_risk result with a missing/invalid enum
    field (out-of-enum event_risk / event_recommendation, or a missing/non-string
    event_date). None of these is a usable Event_Assessment, so the entry must be
    recorded as unavailable WITHOUT fabricating the assessment fields."""
    payload = {
        "symbol": draw(_symbol),
        "holding_horizon": draw(_horizon),
        "event_risk": draw(_bad_risk),
        "days_until_event": draw(st.sampled_from([0, 1, 5, 14, None])),
        "event_date": draw(st.sampled_from([draw(_event_date), None, 20240502])),
        "event_recommendation": draw(_bad_recommendation),
    }
    # Randomly drop one of the assessment keys entirely to exercise the
    # "missing field" branch in addition to the "invalid value" branch.
    drop = draw(st.sampled_from([None, "event_risk", "event_recommendation", "event_date"]))
    if drop is not None:
        payload.pop(drop, None)
    return StubToolMessage(_serialize(payload, draw(_style)), EVENT_TOOL)


@st.composite
def _noise_msg(draw):
    """A non-event tool result message (never a get_event_risk assessment)."""
    name = draw(st.sampled_from(["get_multi_tf_trend", "get_consensus_report", "get_support_resistance"]))
    if name == "get_multi_tf_trend":
        payload = {"symbol": draw(_symbol), "trend_1h": "Bullish", "trend_4h": "Bullish", "trend_1d": "Neutral"}
    elif name == "get_consensus_report":
        payload = {"symbol": draw(_symbol), "current_price": 2450.5, "rsi_14": 38.2, "atr_14": 18.0}
    else:
        payload = {"pivot": 2445.0, "s1": 2440.0, "r1": 2470.0}
    return StubToolMessage(_serialize(payload, draw(_style)), name)


def _assert_unavailable(entry):
    """The event entry must be unavailable with NO fabricated fields."""
    assert isinstance(entry, dict)
    # Recorded as unavailable (available is exactly False, not truthy/missing).
    assert entry.get("available") is False
    # An honest reason is carried.
    assert isinstance(entry.get("reason"), str) and entry["reason"]
    # NONE of the assessment fields may be fabricated.
    for key in _FORBIDDEN_KEYS:
        assert key not in entry, f"unavailable event entry must not contain {key!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Property 20: Absent event context is recorded as unavailable
# ─────────────────────────────────────────────────────────────────────────────

# Feature: earnings-event-risk-gate, Property 20: Absent event context is recorded as unavailable
@settings(max_examples=200, deadline=None)
@given(
    # 0+ event results, each NON-usable: an error, an Unavailable_Marker, a
    # non-dict result, or an assessment missing/with-invalid enum fields. An
    # empty list models the "no get_event_risk result at all" case.
    event_msgs=st.lists(
        st.one_of(
            _error_event_msg(),
            _unavailable_event_msg(),
            _nondict_event_msg(),
            _invalid_assessment_event_msg(),
        ),
        min_size=0,
        max_size=4,
    ),
    noise=st.lists(_noise_msg(), min_size=0, max_size=3),
    noise_first=st.booleans(),
    action=_action,
)
def test_property_20_absent_event_context_recorded_unavailable(event_msgs, noise, noise_first, action):
    """Validates: Requirements 8.3

    For any message history containing NO usable get_event_risk Event_Assessment
    (none present, or only error / Unavailable_Marker / non-dict /
    invalid-assessment results), the defensibility event entry is recorded as
    unavailable with no fabricated event_risk/days_until_event/event_date/
    event_recommendation, and the build never raises.
    """
    messages = (noise + event_msgs) if noise_first else (event_msgs + noise)

    decision = {
        "action": action,
        "conviction_score": 60,
        "setup_validation": "Setup reviewed.",
        "execution_plan": f"{action} at market",
    }

    # ── Top-level record: never raises, event entry recorded as unavailable ───
    record = build_defensibility_record(messages, decision, mode="FIND")
    assert "event" in record
    _assert_unavailable(record["event"])

    # The summary surfaces the event as unavailable (no fabricated event risk).
    assert "Event: unavailable" in record["summary"]

    # ── Lower-level helpers: same outcome via the documented call path ────────
    results = _latest_tool_results(messages)
    # Error results are skipped entirely; only a non-error result (Unavailable_
    # Marker, non-dict, or invalid assessment) may surface here — never a usable
    # assessment.
    event_result = results.get(EVENT_TOOL)
    if isinstance(event_result, dict):
        usable = (
            event_result.get("unavailable") is not True
            and event_result.get("event_risk") in graph.EVENT_RISK_STATES
            and event_result.get("event_recommendation") in graph.EVENT_RECOMMENDATIONS
            and isinstance(event_result.get("event_date"), str)
        )
        assert not usable, "no usable Event_Assessment should surface from a non-usable history"

    entry = _event_entry(results)
    _assert_unavailable(entry)
