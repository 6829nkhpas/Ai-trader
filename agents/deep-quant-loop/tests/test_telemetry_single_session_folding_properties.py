"""Property-based test for single-session folding (telemetry.py, task 3.2).

Feature: session-telemetry

This module implements design **Property 1: Continuations fold into one Session
keyed by thread_id**:

    For any one ``/run`` entry followed by any (possibly empty) sequence of
    ``/resume`` entries sharing its ``thread_id``, interpreting and finalizing the
    observed lifecycle produces exactly ONE Session_Record carrying the run's
    ``symbol``, ``timeframe``, ``mode``, and ``thread_id``, whose ``resume_count``
    equals the number of ``/resume`` continuations and whose ``started_at`` is set
    from the opening run.

Validates: Requirements 1.1, 1.2.

``finalize_session`` (task 4.3) is not part of the module yet, so — as the design's
observation model prescribes — this test reconstructs the single Session by folding
the per-entry ``interpret_events`` fragments into one ``SessionState`` keyed by
``thread_id`` (the same fold the background writer / ``finalize_session`` will
perform). The fold groups every observation sharing a ``thread_id`` into one
accumulator, so a run and all of its resumes collapse into a single session while
a distinct thread stays separate.

The sys.path / import pattern mirrors
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
    ENTRY_KIND_RUN,
    ENTRY_KIND_RESUME,
    FUNNEL_ERROR,
    FUNNEL_DECISION,
    FUNNEL_REASONING_TURN,
    FUNNEL_RESUMED,
    FUNNEL_SESSION_STARTED,
    FUNNEL_TOOL_CALL,
    FUNNEL_WATCH_REGISTERED,
    FunnelEvent,
    RunEntry,
    SessionState,
    TRIGGER_INVALIDATION,
    TRIGGER_TARGET,
    interpret_events,
)


# ── The fold under test: interpret fragments -> one SessionState per thread_id ─
# This mirrors the design's observation model (the background writer /
# ``finalize_session`` fold): each observed ``/run`` or ``/resume`` produces an
# ``interpret_events`` fragment, and fragments are accumulated into a single
# ``SessionState`` keyed by ``thread_id`` — so a run and all its resumes collapse
# into ONE session (Requirement 1.2). The run's ``session_started`` marker seeds
# the session identity (thread_id, symbol, timeframe, mode) and ``started_at``
# (Requirement 1.1); each ``resumed`` marker increments ``resume_count``.


def _fold_observations(observations):
    """Fold ordered ``(thread_id, RunEntry, events)`` observations into sessions.

    Returns a ``dict`` mapping ``thread_id`` -> ``SessionState``. Every observation
    sharing a ``thread_id`` folds into the same accumulator; funnel events are
    re-sequenced contiguously (0..n-1) per session in observation order.
    """
    sessions = {}

    for thread_id, entry, events in observations:
        fragment = interpret_events(entry, events)

        state = sessions.get(thread_id)
        if state is None:
            state = SessionState(thread_id=thread_id)
            sessions[thread_id] = state

        for fe in fragment:
            reseq = FunnelEvent(
                seq=len(state.funnel),
                kind=fe.kind,
                ts=fe.ts,
                trigger_kind=fe.trigger_kind,
                tool_name=fe.tool_name,
                extra=fe.extra,
            )
            state.funnel.append(reseq)

            if fe.kind == FUNNEL_SESSION_STARTED:
                # The opening run seeds identity + started_at (only once).
                state.symbol = entry.symbol
                state.timeframe = entry.timeframe
                state.mode = entry.mode
                if state.started_at is None:
                    state.started_at = fe.ts
            elif fe.kind == FUNNEL_RESUMED:
                state.resume_count += 1
                if fe.trigger_kind == TRIGGER_INVALIDATION:
                    state.invalidation_events += 1
                else:
                    state.target_events += 1
            elif fe.kind == FUNNEL_WATCH_REGISTERED:
                state.watch_cycles += 1
            elif fe.kind == FUNNEL_REASONING_TURN:
                state.reasoning_turns += 1

    return sessions


# ── Generators ────────────────────────────────────────────────────────────────
# The identity dimensions telemetry captures off the run entry (Requirement 1.1);
# ``None`` models a missing dimension so the fold must faithfully carry it through.
_symbols = st.sampled_from(["RELIANCE", "TCS", "INFY", "NIFTY", "BANKNIFTY", None])
_timeframes = st.sampled_from(["1m", "5m", "15m", "1h", "1d", None])
_modes = st.sampled_from(["FIND", "MANAGE", "FORECAST", None])
_thread_ids = st.text(min_size=1, max_size=12)

# Any casing / unknown / missing trigger_kind normalizes to exactly one tripwire
# kind on the resumed marker (target unless explicitly "invalidation").
_trigger_kinds = st.sampled_from(
    ["target", "invalidation", "TARGET", "Invalidation", "INVALIDATION", None, "unknown"]
)

# Interior funnel-bearing event kinds an entry's stream may contain.
_interior_kind = st.sampled_from(
    ["REASONING", "watch", "tool", "DECISION", "ERROR", "noise"]
)


def _make_events(kinds, ts_counter):
    """Build a ``[(event_name, payload), ...]`` stream, stamping monotonic ts.

    ``ts_counter`` is a one-element list acting as a shared, ever-increasing clock
    across the whole scenario so the run's opening timestamp is strictly the
    earliest observed time (letting us assert ``started_at`` comes from the run and
    not from any later resume).
    """
    events = []
    for k in kinds:
        t = ts_counter[0]
        ts_counter[0] += 1.0
        if k == "REASONING":
            events.append(("REASONING", {"ts": t}))
        elif k == "watch":
            events.append(("TOOL_CALL_START", {"tool": "watch_price_condition", "ts": t}))
        elif k == "tool":
            events.append(("TOOL_CALL_START", {"tool": "get_candles", "ts": t}))
        elif k == "DECISION":
            events.append(("DECISION", {"ts": t, "action": "HOLD"}))
        elif k == "ERROR":
            events.append(("ERROR", {"ts": t, "message": "boom"}))
        else:  # "noise": events with no funnel semantics
            events.append(("RUN_FINISHED", {"ts": t, "status": "paused"}))
    return events


@st.composite
def _single_session_scenario(draw):
    """One run + a (possibly empty) sequence of resumes sharing its thread_id.

    Optionally interleaves a distinct decoy thread so the fold's ``thread_id``
    keying is exercised genuinely (the decoy must never merge into the target
    session). Returns everything the property needs to check.
    """
    thread_id = draw(_thread_ids)
    symbol = draw(_symbols)
    timeframe = draw(_timeframes)
    mode = draw(_modes)

    # Start the shared clock at an arbitrary non-zero base.
    ts_counter = [draw(st.floats(min_value=0.0, max_value=1000.0))]

    # The run's opening timestamp: guarantee an observable ts by leading with a
    # timestamped RUN_STARTED, so the session's started_at is well-defined and is
    # strictly the earliest time in the scenario.
    run_started_ts = ts_counter[0]
    ts_counter[0] += 1.0
    run_events = [("RUN_STARTED", {"ts": run_started_ts})]
    run_events += _make_events(draw(st.lists(_interior_kind, max_size=6)), ts_counter)
    run_entry = RunEntry(ENTRY_KIND_RUN, symbol=symbol, timeframe=timeframe, mode=mode)

    # Zero or more resume continuations, each sharing the same thread_id.
    resume_specs = draw(
        st.lists(
            st.tuples(_trigger_kinds, st.lists(_interior_kind, max_size=4)),
            max_size=6,
        )
    )

    # Build the ordered observation stream for the target thread.
    observations = [(thread_id, run_entry, run_events)]
    expected_invalidations = 0
    for trigger_kind, kinds in resume_specs:
        resume_events = _make_events(kinds, ts_counter)
        resume_entry = RunEntry(
            ENTRY_KIND_RESUME,
            symbol=draw(_symbols),          # resume identity is intentionally arbitrary
            timeframe=draw(_timeframes),    # (the run's identity must win the fold)
            mode=draw(_modes),
            trigger_kind=trigger_kind,
        )
        observations.append((thread_id, resume_entry, resume_events))
        if isinstance(trigger_kind, str) and trigger_kind.strip().lower() == TRIGGER_INVALIDATION:
            expected_invalidations += 1

    # Optionally interleave a distinct decoy thread to prove thread_id keying.
    add_decoy = draw(st.booleans())
    decoy_thread_id = None
    if add_decoy:
        decoy_thread_id = draw(_thread_ids.filter(lambda t: t != thread_id))
        decoy_run = (
            decoy_thread_id,
            RunEntry(ENTRY_KIND_RUN, symbol="DECOY", timeframe="1m", mode="FIND"),
            [("RUN_STARTED", {"ts": ts_counter[0]})],
        )
        ts_counter[0] += 1.0
        # Insert the decoy run somewhere in the middle so it interleaves.
        insert_at = draw(st.integers(min_value=0, max_value=len(observations)))
        observations.insert(insert_at, decoy_run)

    return {
        "thread_id": thread_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "mode": mode,
        "run_started_ts": run_started_ts,
        "n_resumes": len(resume_specs),
        "expected_invalidations": expected_invalidations,
        "observations": observations,
        "decoy_thread_id": decoy_thread_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Property 1 (task 3.2): Continuations fold into one Session keyed by thread_id
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 1: Continuations fold into one Session keyed by thread_id
@settings(max_examples=100, deadline=None)
@given(scenario=_single_session_scenario())
def test_property_1_continuations_fold_into_one_session(scenario):
    """Feature: session-telemetry, Property 1: Continuations fold into one Session
    keyed by thread_id — a ``/run`` and all of its ``/resume`` continuations
    sharing a ``thread_id`` fold into exactly ONE Session carrying the run's
    identity (thread_id/symbol/timeframe/mode), with ``resume_count`` equal to the
    number of resumes and ``started_at`` taken from the opening run.

    Validates: Requirements 1.1, 1.2
    """
    thread_id = scenario["thread_id"]

    sessions = _fold_observations(scenario["observations"])

    # ── Exactly one Session per thread_id (Requirement 1.2) ───────────────────
    # The target thread_id resolves to exactly one accumulated session; any
    # interleaved decoy stays a separate session and never merges in.
    assert thread_id in sessions
    expected_session_count = 2 if scenario["decoy_thread_id"] is not None else 1
    assert len(sessions) == expected_session_count
    if scenario["decoy_thread_id"] is not None:
        assert scenario["decoy_thread_id"] in sessions
        assert scenario["decoy_thread_id"] != thread_id

    session = sessions[thread_id]

    # ── Exactly one session was OPENED (the run); resumes did not open new ones ─
    # Every resume folds into the run's session: precisely one session_started
    # marker exists, and the resumed markers equal the resume count.
    session_started = [fe for fe in session.funnel if fe.kind == FUNNEL_SESSION_STARTED]
    resumed = [fe for fe in session.funnel if fe.kind == FUNNEL_RESUMED]
    assert len(session_started) == 1
    assert len(resumed) == scenario["n_resumes"]

    # ── Session carries the run's identity (Requirement 1.1) ──────────────────
    assert session.thread_id == thread_id
    assert session.symbol == scenario["symbol"]
    assert session.timeframe == scenario["timeframe"]
    assert session.mode == scenario["mode"]

    # ── resume_count equals the number of /resume continuations (R1.2) ────────
    assert session.resume_count == scenario["n_resumes"]
    # Each resume is tagged exactly one trigger_kind, so the two split counters
    # partition the resume count.
    assert session.target_events + session.invalidation_events == session.resume_count
    assert session.invalidation_events == scenario["expected_invalidations"]

    # ── started_at is set from the opening run (Requirement 1.1) ──────────────
    # It equals the run's opening timestamp and is the earliest time observed, so
    # no later resume could have supplied it.
    assert session.started_at == scenario["run_started_ts"]
    resume_ts = [fe.ts for fe in resumed if fe.ts is not None]
    for t in resume_ts:
        assert session.started_at <= t

    # ── The funnel is contiguously sequenced within the single session ────────
    assert [fe.seq for fe in session.funnel] == list(range(len(session.funnel)))
