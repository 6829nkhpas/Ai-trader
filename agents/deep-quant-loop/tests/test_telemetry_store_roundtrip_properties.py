"""Property-based test for the Telemetry_Store round-trip (telemetry.py, task 7.3).

Feature: session-telemetry

This module implements design **Property 15: Persisting then loading
Session_Records round-trips**:

    For any list of Session_Records, writing them to a fresh Telemetry_Store and
    then loading them back yields an equal list of Session_Records (identity,
    counters, timings, outcome, and ordered funnel preserved).

Validates: Requirements 7.1.

The sys.path / import pattern mirrors
``tests/test_telemetry_outcome_rates_properties.py``.

Design notes that pin the generators to the store's exact reconstruction
semantics (see ``telemetry.save`` / ``telemetry.load_sessions`` /
``telemetry._decoded_tool_calls_by_name`` / ``telemetry._load_funnel``):

* ``tool_calls_by_name`` is JSON-encoded; JSON object keys are always strings,
  so the generated dict uses ``str`` keys and ``int`` counts (which round-trip
  exactly). The loader always yields a ``dict`` (never ``None``), so every
  generated record carries a dict.
* ``tokens`` is persisted only when it is a finite number and is otherwise stored
  as SQL ``NULL``; the generator therefore draws ``None`` or a non-negative
  ``int`` (both round-trip).
* ``started_at`` is ``REAL NOT NULL``; ``ended_at`` / ``time_to_decision_s`` /
  ``suspended_s`` are nullable ``REAL``. All timings are finite Python floats,
  which round-trip exactly through SQLite's 8-byte REAL storage. (NaN is excluded
  because ``NaN != NaN`` would break equality, not the store.)
* The funnel is loaded ``ORDER BY seq`` and reconstructed element-by-element, so
  every generated funnel carries contiguous ``seq`` values ``0..n-1`` in order and
  each event's ``extra`` bag is JSON-round-trippable (``str`` keys, scalar values).
* ``session_id`` is the ``sessions`` PRIMARY KEY and ``save`` is an
  ``INSERT OR REPLACE`` UPSERT, so the generated list carries UNIQUE
  ``session_id`` values (assigned from the record index) — otherwise a later
  record would silently overwrite an earlier one.

Each Hypothesis example uses its OWN fresh temporary database file (a unique
``uuid``-named path) so no state leaks between examples; the file (and any SQLite
side files) is removed in a ``finally`` block.
"""

import os
import sys
import tempfile
import uuid

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import telemetry  # noqa: E402
from telemetry import (  # noqa: E402
    FUNNEL_DECISION,
    FUNNEL_ERROR,
    FUNNEL_REASONING_TURN,
    FUNNEL_RESUMED,
    FUNNEL_SESSION_STARTED,
    FUNNEL_TOOL_CALL,
    FUNNEL_WATCH_REGISTERED,
    SESSION_OUTCOMES,
    FunnelEvent,
    SessionRecord,
    TelemetryConfig,
    load_sessions,
    save,
)

# ── Building-block strategies pinned to the store's reconstruction semantics ──

_opt_text = st.none() | st.text(min_size=1, max_size=12)
_finite_float = st.floats(allow_nan=False, allow_infinity=False, min_value=-1e9, max_value=1e9)
_opt_finite_float = st.none() | _finite_float
_count = st.integers(min_value=0, max_value=1000)
_opt_tokens = st.none() | st.integers(min_value=0, max_value=2 ** 31)
_opt_outcome = st.none() | st.sampled_from(list(SESSION_OUTCOMES))

# JSON object keys are always strings; counts round-trip as ints.
_tool_calls_by_name = st.dictionaries(
    keys=st.text(min_size=1, max_size=8),
    values=st.integers(min_value=0, max_value=100),
    max_size=5,
)

# A forward-compat JSON bag: str keys, JSON-round-trippable scalar values.
_extra = st.none() | st.dictionaries(
    keys=st.text(min_size=1, max_size=6),
    values=st.one_of(
        st.integers(min_value=-100, max_value=100),
        st.text(max_size=6),
        st.booleans(),
        st.none(),
    ),
    max_size=3,
)

_FUNNEL_KINDS = [
    FUNNEL_SESSION_STARTED,
    FUNNEL_WATCH_REGISTERED,
    FUNNEL_RESUMED,
    FUNNEL_REASONING_TURN,
    FUNNEL_TOOL_CALL,
    FUNNEL_DECISION,
    FUNNEL_ERROR,
]

# One funnel event's fields (seq is assigned from position when the list is built).
_funnel_event_spec = st.fixed_dictionaries(
    {
        "kind": st.sampled_from(_FUNNEL_KINDS),
        "ts": _opt_finite_float,
        "trigger_kind": _opt_text,
        "tool_name": _opt_text,
        "extra": _extra,
    }
)

# A record's varying fields (identity is assigned from the list index for uniqueness).
_record_spec = st.fixed_dictionaries(
    {
        "thread_id": st.text(min_size=1, max_size=12),
        "symbol": _opt_text,
        "timeframe": _opt_text,
        "mode": _opt_text,
        "started_at": _finite_float,
        "ended_at": _opt_finite_float,
        "outcome": _opt_outcome,
        "hold_reason": _opt_text,
        "watch_cycles": _count,
        "target_events": _count,
        "invalidation_events": _count,
        "resume_count": _count,
        "reasoning_turns": _count,
        "tool_calls_total": _count,
        "tool_calls_by_name": _tool_calls_by_name,
        "model_turns": _count,
        "tokens": _opt_tokens,
        "time_to_decision_s": _opt_finite_float,
        "suspended_s": _opt_finite_float,
        "funnel": st.lists(_funnel_event_spec, max_size=6),
    }
)


def _build_record(index, spec):
    """Materialize a well-formed SessionRecord with a UNIQUE identity.

    ``session_id`` is derived from the list ``index`` so every record in a
    generated list has a distinct PRIMARY KEY (otherwise ``save``'s UPSERT would
    let a later record overwrite an earlier one). The funnel is built with
    contiguous ``seq`` values ``0..n-1`` in list order, matching the loader's
    ``ORDER BY seq`` reconstruction.
    """
    funnel = [
        FunnelEvent(
            seq=j,
            kind=ev["kind"],
            ts=ev["ts"],
            trigger_kind=ev["trigger_kind"],
            tool_name=ev["tool_name"],
            extra=ev["extra"],
        )
        for j, ev in enumerate(spec["funnel"])
    ]
    return SessionRecord(
        session_id=f"session-{index}",
        thread_id=spec["thread_id"],
        symbol=spec["symbol"],
        timeframe=spec["timeframe"],
        mode=spec["mode"],
        started_at=spec["started_at"],
        ended_at=spec["ended_at"],
        outcome=spec["outcome"],
        hold_reason=spec["hold_reason"],
        watch_cycles=spec["watch_cycles"],
        target_events=spec["target_events"],
        invalidation_events=spec["invalidation_events"],
        resume_count=spec["resume_count"],
        reasoning_turns=spec["reasoning_turns"],
        tool_calls_total=spec["tool_calls_total"],
        tool_calls_by_name=spec["tool_calls_by_name"],
        model_turns=spec["model_turns"],
        tokens=spec["tokens"],
        time_to_decision_s=spec["time_to_decision_s"],
        suspended_s=spec["suspended_s"],
        funnel=funnel,
    )


# A list of well-formed Session_Records with unique session_ids. ``min_size=0`` so
# the empty-store branch is exercised too.
_records = st.lists(_record_spec, min_size=0, max_size=8).map(
    lambda specs: [_build_record(i, s) for i, s in enumerate(specs)]
)


def _cleanup(db_path):
    """Remove a temporary store file and any SQLite side files."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(db_path + suffix)
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Property 15 (task 7.3): Persisting then loading Session_Records round-trips
# ─────────────────────────────────────────────────────────────────────────────

# Feature: session-telemetry, Property 15: Persisting then loading Session_Records round-trips
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(records=_records)
def test_property_15_store_roundtrip(records):
    """Feature: session-telemetry, Property 15: Persisting then loading
    Session_Records round-trips — writing an arbitrary list of well-formed
    Session_Records to a FRESH Telemetry_Store and loading them back yields an
    equal set of records (identity, counters, timings, outcome, and the ordered
    funnel all preserved).

    Validates: Requirements 7.1
    """
    # A fresh, isolated store file per example so no state leaks between runs.
    db_path = os.path.join(tempfile.gettempdir(), f"telemetry_rt_{uuid.uuid4().hex}.db")
    config = TelemetryConfig(
        db_path=db_path,
        weak_prior_min_sessions=20,
        incomplete_horizon_seconds=float(24 * 3600),
    )

    try:
        for record in records:
            save(config, record)

        loaded = load_sessions(config)

        # Same number of records survived the round-trip (no drops, no dupes).
        assert len(loaded) == len(records)

        # Compare keyed by the unique session_id so the loader's (started_at,
        # session_id) ordering does not matter — structural equality of each
        # frozen SessionRecord (including its ordered funnel list) is asserted.
        saved_by_id = {r.session_id: r for r in records}
        loaded_by_id = {r.session_id: r for r in loaded}

        assert set(loaded_by_id) == set(saved_by_id)
        assert loaded_by_id == saved_by_id
    finally:
        _cleanup(db_path)
