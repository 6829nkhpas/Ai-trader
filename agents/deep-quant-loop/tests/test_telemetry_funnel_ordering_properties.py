"""Property-based test for ordered funnel reconstruction (telemetry.py, task 3.4).

Feature: session-telemetry

This module implements design **Property 6: The Funnel is an ordered,
contiguously sequenced reconstruction**:

    For any observed event stream, the derived Funnel_Events appear in the same
    relative order as their source events and carry contiguous sequence numbers
    0, 1, ..., n-1, so the run's path (analyze -> watch -> invalidate ->
    re-watch -> ...) can be reconstructed exactly.

Validates: Requirements 2.4.

The sys.path / import pattern mirrors
``tests/test_telemetry_config_robustness_properties.py`` and the other
deep-quant-loop property tests. The test drives ``interpret_events`` directly with
arbitrary ``(event_name, payload)`` streams that mix recognized lifecycle events
(REASONING / TOOL_CALL_START / DECISION / ERROR) with unrecognized / skipped ones,
and checks the derived funnel against an INDEPENDENT reference oracle so the
property is a genuine check rather than a tautology.
"""

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

from telemetry import (  # noqa: E402
    ENTRY_KIND_RESUME,
    ENTRY_KIND_RUN,
    EVENT_DECISION,
    EVENT_ERROR,
    EVENT_REASONING,
    EVENT_RUN_FINISHED,
    EVENT_RUN_STARTED,
    EVENT_TOOL_CALL_START,
    FUNNEL_DECISION,
    FUNNEL_ERROR,
    FUNNEL_REASONING_TURN,
    FUNNEL_RESUMED,
    FUNNEL_SESSION_STARTED,
    FUNNEL_TOOL_CALL,
    FUNNEL_WATCH_REGISTERED,
    TRIGGER_INVALIDATION,
    TRIGGER_TARGET,
    WATCH_TOOL_NAME,
    FunnelEvent,
    RunEntry,
    interpret_events,
)

# ── Event-name pools ──────────────────────────────────────────────────────────
# Recognized names carry funnel semantics; unrecognized ones (RUN_STARTED,
# RUN_FINISHED, tool-lifecycle noise, arbitrary garbage) are skipped by the
# interpreter and MUST NOT appear as derived funnel events (Requirement 2.4).
_RECOGNIZED_NAMES = [
    EVENT_REASONING,
    EVENT_TOOL_CALL_START,
    EVENT_DECISION,
    EVENT_ERROR,
]
_UNRECOGNIZED_NAMES = [
    EVENT_RUN_STARTED,
    EVENT_RUN_FINISHED,
    "TOOL_CALL_END",
    "TOOL_CALL_RESULT",
    "VERIFICATION_STEP",
]

# Tool names for TOOL_CALL_START payloads: the watch tool (a Watch_Cycle), a
# couple of ordinary tools, a whitespace-padded watch tool (the interpreter strips
# before matching), and the watch tool as a substring that must NOT match.
_TOOL_NAMES = [
    WATCH_TOOL_NAME,
    "get_ohlcv",
    "compute_indicator",
    "search_news",
    f"  {WATCH_TOOL_NAME}  ",
    "not_watch_price_condition_x",
]


@st.composite
def _raw_event(draw):
    """One arbitrary ``(event_name, base_payload)`` observation (ts added later).

    Mixes recognized events, unrecognized/skipped events, and arbitrary garbage
    names. TOOL_CALL_START events sometimes carry a ``tool`` field (watch tool,
    ordinary tool, padded, or a non-matching name) and sometimes omit it entirely.
    """
    name = draw(
        st.one_of(
            st.sampled_from(_RECOGNIZED_NAMES),
            st.sampled_from(_UNRECOGNIZED_NAMES),
            st.text(max_size=6),  # arbitrary unrecognized garbage
        )
    )
    payload = {}
    if name == EVENT_TOOL_CALL_START and draw(st.booleans()):
        payload["tool"] = draw(st.sampled_from(_TOOL_NAMES))
    return (name, payload)


_raw_event_stream = st.lists(_raw_event(), max_size=25)

_entries = st.one_of(
    st.builds(
        RunEntry,
        kind=st.just(ENTRY_KIND_RUN),
        symbol=st.one_of(st.none(), st.sampled_from(["RELIANCE", "TCS"])),
        timeframe=st.one_of(st.none(), st.sampled_from(["5m", "15m", "1h"])),
        mode=st.one_of(st.none(), st.sampled_from(["FIND", "MANAGE"])),
    ),
    st.builds(
        RunEntry,
        kind=st.just(ENTRY_KIND_RESUME),
        symbol=st.one_of(st.none(), st.sampled_from(["RELIANCE", "TCS"])),
        timeframe=st.one_of(st.none(), st.sampled_from(["5m", "15m"])),
        mode=st.one_of(st.none(), st.sampled_from(["FIND", "MANAGE"])),
        trigger_kind=st.one_of(
            st.none(),
            st.sampled_from([TRIGGER_TARGET, TRIGGER_INVALIDATION, "TARGET", "Invalidation", "weird"]),
        ),
    ),
)


def _expected_leading_kind(entry):
    """The leading marker kind: ``resumed`` for a /resume, else ``session_started``."""
    if getattr(entry, "kind", None) == ENTRY_KIND_RESUME:
        return FUNNEL_RESUMED
    return FUNNEL_SESSION_STARTED


def _expected_derived_kind(name, payload):
    """Independent oracle: the funnel kind a source event maps to, or None if skipped.

    Mirrors the design's observation model WITHOUT calling the interpreter:
      REASONING -> reasoning_turn; TOOL_CALL_START -> watch_registered when the
      (stripped) tool is the watch tool else tool_call; DECISION -> decision;
      ERROR -> error; everything else -> skipped (None).
    """
    if name == EVENT_REASONING:
        return FUNNEL_REASONING_TURN
    if name == EVENT_TOOL_CALL_START:
        tool = payload.get("tool") if isinstance(payload, dict) else None
        if isinstance(tool, str) and tool.strip() == WATCH_TOOL_NAME:
            return FUNNEL_WATCH_REGISTERED
        return FUNNEL_TOOL_CALL
    if name == EVENT_DECISION:
        return FUNNEL_DECISION
    if name == EVENT_ERROR:
        return FUNNEL_ERROR
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Property 6 (task 3.4): The Funnel is an ordered, contiguously sequenced reconstruction
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 6: The Funnel is an ordered, contiguously sequenced reconstruction
@settings(max_examples=100, deadline=None)
@given(entry=_entries, raw_events=_raw_event_stream)
def test_property_6_funnel_ordered_and_contiguously_sequenced(entry, raw_events):
    """Feature: session-telemetry, Property 6: The Funnel is an ordered,
    contiguously sequenced reconstruction — for any observed event stream, the
    derived Funnel_Events carry contiguous seq numbers 0..n-1 and appear in the
    same relative order as their source events (after the leading marker and once
    skipped/unrecognized events are accounted for).

    Validates: Requirements 2.4
    """
    # Stamp each source event with a strictly increasing ts (its position). This
    # lets us prove ORDER preservation independently of event kind: every derived
    # non-marker event carries its source event's ts, so those ts values must come
    # out strictly increasing iff relative order is preserved.
    events = [
        (name, {**payload, "ts": float(idx)})
        for idx, (name, payload) in enumerate(raw_events)
    ]

    funnel = interpret_events(entry, events)

    # ── The interpreter never raises and returns a list of FunnelEvents ─────────
    assert isinstance(funnel, list)
    assert all(isinstance(f, FunnelEvent) for f in funnel)

    # ── (1) Contiguous, 0-based sequence numbers 0, 1, ..., n-1 ─────────────────
    assert [f.seq for f in funnel] == list(range(len(funnel)))

    # ── Build the expected kind sequence from the independent oracle ────────────
    expected_kinds = [_expected_leading_kind(entry)]
    for name, payload in events:
        kind = _expected_derived_kind(name, payload)
        if kind is not None:
            expected_kinds.append(kind)

    actual_kinds = [f.kind for f in funnel]

    # ── (2a) Derived kinds match the source stream in the same relative order ───
    assert actual_kinds == expected_kinds

    # A leading marker is always present; length is 1 + number of recognized events.
    assert len(funnel) >= 1
    assert funnel[0].kind == _expected_leading_kind(entry)

    # ── (2b) Relative order preserved: derived non-marker ts strictly increasing ─
    # Every derived event after the leading marker carries the ts of its source
    # event (== its source position). If order were scrambled, dropped, or
    # duplicated, these would not be strictly increasing.
    derived_ts = [f.ts for f in funnel[1:]]
    assert all(t is not None for t in derived_ts)
    assert all(a < b for a, b in zip(derived_ts, derived_ts[1:]))

    # ── The derived ts are exactly the source positions of the recognized events ─
    expected_ts = [
        float(idx)
        for idx, (name, payload) in enumerate(events)
        if _expected_derived_kind(name, payload) is not None
    ]
    assert derived_ts == expected_ts
