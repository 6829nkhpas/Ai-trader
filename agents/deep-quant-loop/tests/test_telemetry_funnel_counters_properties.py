"""Property-based test for telemetry funnel counters (telemetry.py, task 3.3).

Feature: session-telemetry

This module implements design **Property 5: Funnel counters equal the observed
events they count**:

    For any observed event stream and its ``/run`` + ``/resume`` entries, the
    finalized Session_Record's counters equal the number of corresponding
    Funnel_Events: ``watch_cycles`` equals the number of ``watch_price_condition``
    tool registrations, ``resume_count`` equals the number of resume events,
    ``target_events + invalidation_events`` equals ``resume_count`` (each resume is
    tagged exactly one ``trigger_kind``), and ``reasoning_turns`` equals the number
    of reasoning events.

Validates: Requirements 2.1, 2.2, 2.3.

The counters the Session_Record exposes are, by design, plain counts over the
ordered ``FunnelEvent`` list that ``interpret_events`` derives for a Session's
``/run`` fragment plus each of its ``/resume`` fragments (the session model folds
those fragments into one Session keyed by ``thread_id``). Since the counts are
additive over fragments and independent of re-sequencing, this property is
validated directly over the funnel that ``interpret_events`` — the pure function
that is the sole source of those counters — produces for the run + resume
entries. The sys.path / import pattern mirrors
``tests/test_telemetry_config_robustness_properties.py``.
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
    EVENT_DECISION,
    EVENT_ERROR,
    EVENT_REASONING,
    EVENT_TOOL_CALL_START,
    FUNNEL_REASONING_TURN,
    FUNNEL_RESUMED,
    FUNNEL_WATCH_REGISTERED,
    RunEntry,
    TRIGGER_INVALIDATION,
    TRIGGER_TARGET,
    WATCH_TOOL_NAME,
    interpret_events,
)

# ── Independent reference oracles (do NOT call the module internals) ──────────
# These replicate the DOCUMENTED matching semantics of the pure core without
# reusing its helpers, so the property is a genuine check rather than a tautology.


def _oracle_is_watch(payload):
    """A TOOL_CALL_START registers a Watch_Cycle iff its ``tool`` is the watch tool.

    Mirrors the design: the tool name is whitespace-stripped and compared to
    ``watch_price_condition`` (R2.1). A missing / non-string tool never counts.
    """
    if isinstance(payload, dict):
        name = payload.get("tool")
        if isinstance(name, str) and name.strip() == WATCH_TOOL_NAME:
            return True
    return False


def _oracle_trigger(trigger_kind):
    """A resume is an Invalidation_Event iff its trigger is exactly ``invalidation``.

    Mirrors the design's normalization (R2.2): an explicit, case-insensitive,
    whitespace-tolerant ``invalidation`` maps to the invalidation kind; anything
    else (``target``, unknown, missing) maps to the neutral ``target`` kind — so
    every resume carries exactly one trigger_kind.
    """
    if isinstance(trigger_kind, str) and trigger_kind.strip().lower() == "invalidation":
        return TRIGGER_INVALIDATION
    return TRIGGER_TARGET


# ── Hypothesis strategies over arbitrary observed event streams + entries ─────

# Tool names: the watch tool (exact + whitespace variant), several non-watch
# tools, and ``None`` (a TOOL_CALL_START with no usable tool -> a plain tool_call).
_tool_names = st.sampled_from(
    [
        WATCH_TOOL_NAME,
        "  " + WATCH_TOOL_NAME + "  ",   # whitespace variant still registers a watch
        "get_candles",
        "options_snapshot",
        "order_flow",
        "regime_snapshot",
        None,
    ]
)

_ts = st.one_of(st.none(), st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False))


def _payload_with_ts(base):
    """Attach an optional ``ts`` to a base payload dict (funnel ts is optional)."""
    return st.builds(
        lambda b, ts: ({**b, "ts": ts} if ts is not None else dict(b)),
        st.just(base),
        _ts,
    )


_tool_payload = st.builds(
    lambda tool, ts: ({"tool": tool} if tool is not None else {}) | ({"ts": ts} if ts is not None else {}),
    _tool_names,
    _ts,
)

# One observed (event_name, payload) tuple. Mixes the funnel-bearing events
# (REASONING, TOOL_CALL_START, DECISION, ERROR) with lifecycle noise that carries
# no funnel semantics (RUN_STARTED / RUN_FINISHED / TOOL_CALL_RESULT / ...).
_observed_event = st.one_of(
    st.tuples(st.just(EVENT_REASONING), _payload_with_ts({})),
    st.tuples(st.just(EVENT_TOOL_CALL_START), _tool_payload),
    st.tuples(st.just(EVENT_DECISION), _payload_with_ts({"action": "HOLD"})),
    st.tuples(st.just(EVENT_ERROR), _payload_with_ts({})),
    st.tuples(
        st.sampled_from(["RUN_STARTED", "RUN_FINISHED", "TOOL_CALL_RESULT", "TOOL_CALL_END", "VERIFICATION_STEP"]),
        _payload_with_ts({}),
    ),
)

_event_stream = st.lists(_observed_event, max_size=12)

_symbol = st.one_of(st.none(), st.sampled_from(["RELIANCE", "INFY", "TCS"]))
_timeframe = st.one_of(st.none(), st.sampled_from(["5m", "15m", "1h"]))
_mode = st.one_of(st.none(), st.sampled_from(["FIND", "MANAGE"]))

# The trigger tag a ``/resume`` arrives with (some valid, some odd/missing).
_trigger_kinds = st.sampled_from(
    ["target", "invalidation", "INVALIDATION", "  invalidation  ", "Target", "unknown", "", None]
)

# A ``/run`` entry paired with its observed event stream.
_run_pair = st.tuples(
    st.builds(
        lambda symbol, timeframe, mode: RunEntry(
            kind="run", symbol=symbol, timeframe=timeframe, mode=mode
        ),
        _symbol,
        _timeframe,
        _mode,
    ),
    _event_stream,
)

# A ``/resume`` entry (tagged with a trigger_kind) paired with its event stream.
_resume_pair = st.tuples(
    st.builds(lambda tk: RunEntry(kind="resume", trigger_kind=tk), _trigger_kinds),
    _event_stream,
)

# A whole Session: one ``/run`` and any (possibly empty) sequence of ``/resume``s.
_session = st.tuples(_run_pair, st.lists(_resume_pair, max_size=6))


# ─────────────────────────────────────────────────────────────────────────────
# Property 5 (task 3.3): Funnel counters equal the observed events they count
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 5: Funnel counters equal the observed events they count
@settings(max_examples=100, deadline=None)
@given(session=_session)
def test_property_5_funnel_counters_equal_observed_events(session):
    """Feature: session-telemetry, Property 5: Funnel counters equal the observed
    events they count — for any observed event stream and its ``/run`` + ``/resume``
    entries, the Session's funnel counters equal the number of corresponding
    Funnel_Events: ``watch_cycles`` == watch_price_condition registrations,
    ``resume_count`` == resume events, ``target_events + invalidation_events`` ==
    ``resume_count`` (each resume tagged exactly one trigger_kind), and
    ``reasoning_turns`` == reasoning events.

    Validates: Requirements 2.1, 2.2, 2.3
    """
    (run_entry, run_events), resumes = session
    entries_with_events = [(run_entry, run_events)] + list(resumes)

    # Build the Session's combined funnel from the real pure API: the run fragment
    # followed by each resume fragment (the session model folds them by thread_id;
    # the counters are additive over fragments and order-independent).
    combined = []
    for entry, events in entries_with_events:
        combined.extend(interpret_events(entry, events))

    # ── Counters as counts over the derived funnel (what the Session_Record holds) ─
    watch_cycles = sum(1 for f in combined if f.kind == FUNNEL_WATCH_REGISTERED)
    reasoning_turns = sum(1 for f in combined if f.kind == FUNNEL_REASONING_TURN)
    resumed = [f for f in combined if f.kind == FUNNEL_RESUMED]
    resume_count = len(resumed)
    target_events = sum(1 for f in resumed if f.trigger_kind == TRIGGER_TARGET)
    invalidation_events = sum(1 for f in resumed if f.trigger_kind == TRIGGER_INVALIDATION)

    # ── Independent oracle counted straight from the raw observed inputs ──────
    expected_watch = sum(
        1
        for _entry, events in entries_with_events
        for (name, payload) in events
        if name == EVENT_TOOL_CALL_START and _oracle_is_watch(payload)
    )
    expected_reasoning = sum(
        1
        for _entry, events in entries_with_events
        for (name, _payload) in events
        if name == EVENT_REASONING
    )
    expected_resume_count = len(resumes)
    expected_target = sum(
        1 for entry, _events in resumes if _oracle_trigger(entry.trigger_kind) == TRIGGER_TARGET
    )
    expected_invalidation = sum(
        1 for entry, _events in resumes if _oracle_trigger(entry.trigger_kind) == TRIGGER_INVALIDATION
    )

    # R2.1: watch_cycles == number of watch_price_condition registrations.
    assert watch_cycles == expected_watch

    # R2.2 / R2.3: resume_count == number of resume events (one per /resume entry).
    assert resume_count == expected_resume_count

    # R2.2: each resume is tagged exactly one trigger_kind, so the target and
    # invalidation counts partition the resumes.
    assert target_events + invalidation_events == resume_count
    assert target_events == expected_target
    assert invalidation_events == expected_invalidation

    # R2.3: reasoning_turns == number of reasoning events.
    assert reasoning_turns == expected_reasoning
