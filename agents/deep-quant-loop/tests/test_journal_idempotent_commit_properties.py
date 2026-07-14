"""Idempotent-commit property tests for the Trade_Journal (journal.py, Bug 5, task 7).

Feature: deep-quant-decision-reliability

This module implements the design's Bug 5 correctness properties for the
idempotent decision commit:

  * **Property 9 (Bug Condition) — Idempotent decision commit.** When a thread has
    already committed a decision, a re-entered commit for the SAME non-NULL
    ``thread_id`` is a journaling no-op: N >= 1 calls to ``record_decision`` with
    one ``thread_id`` (and a HOLD decision) produce EXACTLY one row, and every
    call returns the SAME row id. A Hypothesis property over random
    ``(thread_id, N)`` asserts ``rows(thread) == 1`` for every committed thread.

  * **Property 10 (Preservation) — first-commit / distinct-thread / legacy /
    guarded cleanup.** The first commit per thread still writes exactly one row;
    M distinct ``thread_id`` values each get exactly one row; legacy callers that
    omit ``thread_id`` (``thread_id=None``) keep the pre-existing behavior — every
    call inserts a fresh row. A seeded, polluted journal is collapsed to the
    earliest row per duplicate group ONLY when ``dedupe_thread_rows()`` is
    explicitly invoked; it never runs implicitly from ``record_decision`` and a
    second invocation is a no-op (deletes 0).

Validates: Requirements 2.10, 2.11, 2.12, 3.9, 3.10, 3.11.

The implementation under test lives in ``journal.py``:
  - ``record_decision(decision, symbol, timeframe, mode, management_plan, thread_id)``
    — idempotent per non-NULL ``thread_id`` (pre-insert existence check + partial
    UNIQUE index); legacy ``thread_id=None`` inserts a fresh row each time.
  - ``dedupe_thread_rows()`` — the explicit, one-time cleanup collapsing duplicate
    rows to the earliest per group (keeping ``MIN(id)``), never called from a run
    path.

No live LLM / Rust server is involved: decisions are persisted through the real
public ``record_decision`` path into a TEMP sqlite DB (so the real
``trade_journal.db`` is never touched) and the rows are read straight back via
sqlite. HOLD decisions are stored as non-scoreable ``hold`` rows, so no ``open``
rows exist and no candle fetch/scoring occurs. The temp DB is removed on
teardown.

The sys.path / module-level temp-DB harness mirrors
``test_forecast_probability_persistence_properties.py`` (a module-scoped temp DB
so Hypothesis is not fighting a function-scoped fixture); example tests
``purge()`` the table to start from a clean slate.
"""

import atexit
import os
import sqlite3
import sys
import tempfile

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the service package importable (journal.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import journal  # noqa: E402


# ── Temp DB isolation ────────────────────────────────────────────────────────
# Point the journal at a throwaway sqlite file for the whole module so no real
# journal is touched. Each example purges the table to start from a clean slate.
_ORIG_DB_PATH = journal.JOURNAL_DB_PATH
_fd, _TMP_DB = tempfile.mkstemp(prefix="idempotent_commit_journal_", suffix=".db")
os.close(_fd)
journal.JOURNAL_DB_PATH = _TMP_DB


@atexit.register
def _cleanup():
    journal.JOURNAL_DB_PATH = _ORIG_DB_PATH
    try:
        os.remove(_TMP_DB)
    except OSError:
        pass


# ── Helpers ──────────────────────────────────────────────────────────────────
def _hold_decision():
    """A minimal committed HOLD decision (non-scoreable -> stored as ``hold``)."""
    return {"action": "HOLD", "defensibility": {}}


def _rows_for_thread(thread_id):
    """All row ids carrying ``thread_id`` (ascending)."""
    conn = sqlite3.connect(journal.JOURNAL_DB_PATH, timeout=10.0)
    try:
        cur = conn.execute(
            "SELECT id FROM trades WHERE thread_id=? ORDER BY id ASC", (thread_id,)
        )
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _all_row_ids():
    """Every row id in the journal (ascending)."""
    conn = sqlite3.connect(journal.JOURNAL_DB_PATH, timeout=10.0)
    try:
        cur = conn.execute("SELECT id FROM trades ORDER BY id ASC")
        return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _null_thread_row_count():
    """Count of legacy rows with a NULL ``thread_id``."""
    conn = sqlite3.connect(journal.JOURNAL_DB_PATH, timeout=10.0)
    try:
        cur = conn.execute("SELECT COUNT(*) FROM trades WHERE thread_id IS NULL")
        return cur.fetchone()[0]
    finally:
        conn.close()


def _seed_row(symbol, timeframe, action, setup_key, created_at, thread_id=None, status="hold"):
    """Insert a journal row directly with a controlled ``created_at`` / identity.

    Bypasses ``record_decision`` so a test can seed the EXACT duplicate shape the
    conservative legacy dedupe key targets — two rows agreeing on
    ``(symbol, timeframe, action, setup_key, created_at-truncated-to-seconds)``
    with a NULL ``thread_id``. Returns the new row id.
    """
    conn = journal._connect()
    try:
        journal._init_db(conn)
        cur = conn.execute(
            """
            INSERT INTO trades (
                created_at, mode, symbol, timeframe, action, setup_key, source,
                status, thread_id
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (created_at, "FIND", symbol, timeframe, action, setup_key, "test", status, thread_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ── Strategies ───────────────────────────────────────────────────────────────
# Non-empty alphanumeric thread ids: guaranteed to survive record_decision's
# normalization (a non-empty, non-whitespace string participates in the guard).
_thread_id = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_",
    min_size=1,
    max_size=24,
)
_repeat_count = st.integers(min_value=1, max_value=8)


# ─────────────────────────────────────────────────────────────────────────────
# Property 9 (Bug Condition): N calls with one thread_id -> exactly one row
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=200, deadline=None)
@given(thread_id=_thread_id, n=_repeat_count)
def test_property_9_idempotent_commit_single_row_per_thread(thread_id, n):
    """Validates: Requirements 2.10, 2.11, 3.9, 3.10

    Calling ``record_decision`` N >= 1 times with the SAME non-NULL ``thread_id``
    and a HOLD decision commits EXACTLY one row for that thread, and every call
    returns the SAME row id (the first-committed row) — a re-entered commit is a
    journaling no-op.
    """
    # Clean slate so the thread's row count is unambiguous.
    journal.purge()

    ids = [
        journal.record_decision(
            _hold_decision(), symbol="TEST", timeframe="1d", mode="FIND",
            thread_id=thread_id,
        )
        for _ in range(n)
    ]

    # Every call succeeds and returns the SAME id (the first committed row).
    assert all(i is not None for i in ids), "every commit must return a row id"
    assert len(set(ids)) == 1, "all N calls must return the same id"
    assert ids[0] == ids[-1]

    # Exactly one row exists for the thread, and it is the returned id.
    thread_rows = _rows_for_thread(thread_id)
    assert thread_rows == [ids[0]], "exactly one row must exist for the thread"
    # The whole journal holds exactly that one row (no stray inserts).
    assert _all_row_ids() == [ids[0]]


# ─────────────────────────────────────────────────────────────────────────────
# Property 10 (Preservation): M distinct threads -> M rows, one each
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=150, deadline=None)
@given(thread_ids=st.lists(_thread_id, min_size=1, max_size=10, unique=True))
def test_property_10_distinct_threads_get_one_row_each(thread_ids):
    """Validates: Requirements 2.11, 3.10

    Committing once for each of M DISTINCT ``thread_id`` values writes exactly M
    rows — one per thread — so the first commit per thread is preserved and
    distinct threads never collide.
    """
    journal.purge()

    ids = [
        journal.record_decision(
            _hold_decision(), symbol="TEST", timeframe="1d", mode="FIND",
            thread_id=tid,
        )
        for tid in thread_ids
    ]

    assert all(i is not None for i in ids)
    # M distinct threads -> M distinct rows.
    assert len(set(ids)) == len(thread_ids)
    assert len(_all_row_ids()) == len(thread_ids)
    # Each thread carries exactly one row.
    for tid in thread_ids:
        assert len(_rows_for_thread(tid)) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Property 10 (Preservation): legacy thread_id=None inserts a fresh row each call
# ─────────────────────────────────────────────────────────────────────────────
@settings(max_examples=100, deadline=None)
@given(n=st.integers(min_value=1, max_value=8))
def test_property_10_legacy_null_thread_inserts_fresh_row_each_call(n):
    """Validates: Requirements 2.11, 3.10

    Legacy callers that omit ``thread_id`` (``thread_id=None``) keep the
    pre-existing behavior: every call inserts a fresh row (no idempotency guard),
    so N calls produce N distinct rows all carrying a NULL ``thread_id``.
    """
    journal.purge()

    ids = [
        journal.record_decision(
            _hold_decision(), symbol="TEST", timeframe="1d", mode="FIND",
            thread_id=None,
        )
        for _ in range(n)
    ]

    assert all(i is not None for i in ids)
    assert len(set(ids)) == n, "each legacy call must insert a distinct row"
    assert _null_thread_row_count() == n
    assert len(_all_row_ids()) == n


def test_legacy_null_thread_insert_matches_omitted_arg():
    """A single legacy call and an explicit ``thread_id=None`` behave identically:
    both insert a fresh row. Two such calls yield two rows (no collapse).

    Validates: Requirements 2.11, 3.10
    """
    journal.purge()

    id1 = journal.record_decision(_hold_decision(), symbol="TEST", timeframe="1d", mode="FIND")
    id2 = journal.record_decision(
        _hold_decision(), symbol="TEST", timeframe="1d", mode="FIND", thread_id=None
    )

    assert id1 is not None and id2 is not None
    assert id1 != id2, "two legacy (NULL thread) calls must not collapse"
    assert _null_thread_row_count() == 2


# ─────────────────────────────────────────────────────────────────────────────
# Property 10 (Preservation): guarded one-time cleanup collapses duplicates
# ─────────────────────────────────────────────────────────────────────────────
def test_dedupe_collapses_legacy_duplicates_to_earliest_and_is_idempotent():
    """A seeded, polluted journal is collapsed to the earliest row per duplicate
    group by ``dedupe_thread_rows()``; distinct decisions are preserved; a second
    invocation deletes 0 (idempotent).

    Validates: Requirements 2.12, 3.11
    """
    journal.purge()

    # Two legacy duplicate HOLD rows for ONE decision: identical
    # symbol/timeframe/action/setup_key AND the same whole-second created_at, both
    # thread_id NULL — the exact shape the conservative legacy dedupe key targets.
    dup_earliest = _seed_row("RELIANCE", "10m", "HOLD", "dir:HOLD", 1000.0)
    dup_later = _seed_row("RELIANCE", "10m", "HOLD", "dir:HOLD", 1000.0)
    # A genuinely DISTINCT decision (different setup_key) must be preserved.
    distinct_setup = _seed_row("RELIANCE", "10m", "HOLD", "dir:BUY", 1000.0)
    # Another distinct decision: same fingerprint but a different second bucket.
    distinct_time = _seed_row("RELIANCE", "10m", "HOLD", "dir:HOLD", 2000.0)

    before = set(_all_row_ids())
    assert before == {dup_earliest, dup_later, distinct_setup, distinct_time}

    # Explicit invocation collapses the one duplicate group to its earliest row.
    removed = journal.dedupe_thread_rows()
    assert removed == 1, "exactly the one later duplicate must be removed"

    after = set(_all_row_ids())
    assert dup_earliest in after, "the earliest row of the group is kept"
    assert dup_later not in after, "the later duplicate is collapsed away"
    assert distinct_setup in after, "a distinct-setup decision is preserved"
    assert distinct_time in after, "a distinct-time decision is preserved"

    # Second invocation is a no-op: the journal is already clean.
    assert journal.dedupe_thread_rows() == 0
    assert set(_all_row_ids()) == after


def test_record_decision_never_dedupes_implicitly():
    """Recording alone never removes anything: pre-existing duplicate rows survive
    a subsequent ``record_decision`` call — dedupe runs ONLY on explicit invocation.

    Validates: Requirements 2.12, 3.11
    """
    journal.purge()

    # Seed a polluted pair of legacy duplicates.
    dup_a = _seed_row("INFY", "10m", "HOLD", "dir:HOLD", 5000.0)
    dup_b = _seed_row("INFY", "10m", "HOLD", "dir:HOLD", 5000.0)
    seeded = {dup_a, dup_b}
    assert set(_all_row_ids()) == seeded

    # A fresh commit (distinct thread) must NOT trigger any implicit cleanup.
    new_id = journal.record_decision(
        _hold_decision(), symbol="INFY", timeframe="10m", mode="FIND",
        thread_id="fresh-thread",
    )
    assert new_id is not None

    after = set(_all_row_ids())
    # Both seeded duplicates are still present — recording removed nothing.
    assert seeded.issubset(after), "record_decision must not remove existing rows"
    assert new_id in after
    assert len(after) == 3

    # A legacy (NULL thread) commit likewise removes nothing.
    legacy_id = journal.record_decision(
        _hold_decision(), symbol="INFY", timeframe="10m", mode="FIND",
    )
    assert legacy_id is not None
    final = set(_all_row_ids())
    assert seeded.issubset(final), "a legacy commit must not remove existing rows"
    assert len(final) == 4
