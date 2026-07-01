"""Store-separation integration test for the Telemetry_Store (telemetry.py, task 10.3).

Feature: session-telemetry

Example-based integration test proving that Session Telemetry is structurally
read-only with respect to the Trade_Journal — it persists to its OWN dedicated
SQLite store and never opens, creates, reads, or writes the Trade_Journal database
or its ``trades`` table (Requirements 6.4, 7.1, 7.2):

  1. The resolved Telemetry_Store path differs from ``journal.JOURNAL_DB_PATH``
     (distinct files, distinct default basenames ``telemetry.db`` vs
     ``trade_journal.db``).
  2. The Telemetry_Store DDL targets ONLY the dedicated ``sessions`` /
     ``funnel_events`` tables — a fresh store (initialized, then written to) never
     contains a ``trades`` table.
  3. Telemetry never opens the Trade_Journal DB: every ``sqlite3.connect`` issued
     during ``_init_db`` / ``save`` / ``load_sessions`` targets the telemetry
     ``db_path`` and never ``journal.JOURNAL_DB_PATH`` (nor any ``trade_journal``
     path), and a Trade_Journal file that did not exist beforehand still does not
     exist afterwards.

The sys.path / import pattern mirrors the other telemetry tests
(``tests/test_telemetry_store_migration_unit.py``).
"""

import os
import sys

# Make the service package importable (telemetry.py / journal.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import journal  # noqa: E402
import telemetry  # noqa: E402
from telemetry import (  # noqa: E402
    FUNNEL_DECISION,
    FUNNEL_REASONING_TURN,
    FUNNEL_RESUMED,
    FUNNEL_SESSION_STARTED,
    FUNNEL_WATCH_REGISTERED,
    OUTCOME_TRADE_BUY,
    TRIGGER_TARGET,
    WATCH_TOOL_NAME,
    FunnelEvent,
    SessionRecord,
    TelemetryConfig,
    _connect,
    _init_db,
    load_sessions,
    save,
)


def _sample_record():
    """Build a representative, fully-populated SessionRecord for persistence.

    Models a converted BUY hunt: session opens, reasons, registers a watch cycle,
    resumes on a target, then decides — an ordered funnel with contiguous seq
    numbers, non-zero counters, and cost proxies, so the write path exercises both
    the ``sessions`` and ``funnel_events`` tables.
    """
    funnel = [
        FunnelEvent(seq=0, kind=FUNNEL_SESSION_STARTED, ts=1000.0),
        FunnelEvent(seq=1, kind=FUNNEL_REASONING_TURN, ts=1001.0),
        FunnelEvent(
            seq=2, kind=FUNNEL_WATCH_REGISTERED, ts=1002.0, tool_name=WATCH_TOOL_NAME
        ),
        FunnelEvent(seq=3, kind=FUNNEL_RESUMED, ts=1060.0, trigger_kind=TRIGGER_TARGET),
        FunnelEvent(seq=4, kind=FUNNEL_DECISION, ts=1120.5),
    ]
    return SessionRecord(
        session_id="thread-sep:1000.0",
        thread_id="thread-sep",
        symbol="RELIANCE",
        timeframe="15m",
        mode="FIND",
        started_at=1000.0,
        ended_at=1120.5,
        outcome=OUTCOME_TRADE_BUY,
        hold_reason=None,
        watch_cycles=1,
        target_events=1,
        invalidation_events=0,
        resume_count=1,
        reasoning_turns=1,
        tool_calls_total=1,
        tool_calls_by_name={WATCH_TOOL_NAME: 1},
        model_turns=1,
        tokens=None,
        time_to_decision_s=120.5,
        suspended_s=58.0,
        funnel=funnel,
    )


def _table_names(conn):
    """Return the set of user table names in a store (excluding SQLite internals).

    Internal ``sqlite_*`` tables (e.g. ``sqlite_sequence``, created because
    ``funnel_events`` has an AUTOINCREMENT primary key) are filtered out — they are
    SQLite bookkeeping, not part of the telemetry schema.
    """
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row[0] for row in rows if not str(row[0]).startswith("sqlite_")}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Resolved telemetry DB path differs from the Trade_Journal path
# ─────────────────────────────────────────────────────────────────────────────

def test_resolved_telemetry_path_differs_from_journal_path():
    """The telemetry store and the Trade_Journal are distinct files.

    Validates: Requirements 7.1.
    """
    cfg = telemetry.resolve_telemetry_config()

    # Distinct files (never the same path).
    assert cfg.db_path != journal.JOURNAL_DB_PATH

    # Distinct documented default basenames.
    assert os.path.basename(telemetry._DEFAULT_DB) == "telemetry.db"
    assert os.path.basename(journal._DEFAULT_DB) == "trade_journal.db"
    # And the resolved default carries the telemetry basename (not the journal's).
    assert os.path.basename(cfg.db_path) == "telemetry.db"
    assert "trade_journal" not in os.path.basename(cfg.db_path)


# ─────────────────────────────────────────────────────────────────────────────
# 2. DDL targets only ``sessions`` / ``funnel_events`` (never ``trades``)
# ─────────────────────────────────────────────────────────────────────────────

def test_ddl_creates_only_sessions_and_funnel_events(tmp_path):
    """A fresh, then written-to, store contains only the two telemetry tables.

    Validates: Requirements 7.1, 7.2.
    """
    db_path = str(tmp_path / "telemetry_sep.db")
    cfg = TelemetryConfig(
        db_path=db_path,
        weak_prior_min_sessions=20,
        incomplete_horizon_seconds=float(24 * 3600),
    )

    # Initialize the schema on a brand-new store.
    conn = _connect(cfg)
    try:
        _init_db(conn)
        tables_after_init = _table_names(conn)
    finally:
        conn.close()

    assert tables_after_init == {"sessions", "funnel_events"}
    assert "trades" not in tables_after_init

    # Writing a real record must not introduce any further table (no ``trades``).
    save(cfg, _sample_record())

    conn = _connect(cfg)
    try:
        tables_after_save = _table_names(conn)
    finally:
        conn.close()

    assert tables_after_save == {"sessions", "funnel_events"}
    assert "trades" not in tables_after_save


# ─────────────────────────────────────────────────────────────────────────────
# 3. Telemetry never opens the Trade_Journal DB / trades table
# ─────────────────────────────────────────────────────────────────────────────

def test_telemetry_never_opens_the_journal_database(tmp_path, monkeypatch):
    """Every connection telemetry opens targets its own store, never the journal.

    Wraps ``telemetry.sqlite3.connect`` with a recorder and drives the full store
    I/O surface (``_init_db`` via ``_connect``, ``save``, ``load_sessions``)
    against a dedicated telemetry store. Asserts none of the recorded connect
    paths is the Trade_Journal path (nor mentions ``trade_journal``), and that a
    non-existent Trade_Journal file is never created by telemetry.

    Validates: Requirements 6.4, 7.2.
    """
    db_path = str(tmp_path / "telemetry_isolated.db")
    cfg = TelemetryConfig(
        db_path=db_path,
        weak_prior_min_sessions=20,
        incomplete_horizon_seconds=float(24 * 3600),
    )

    # Record whether the Trade_Journal file exists before any telemetry op so we
    # can assert telemetry neither creates nor removes it.
    journal_existed_before = os.path.exists(journal.JOURNAL_DB_PATH)

    connected_paths = []
    real_connect = telemetry.sqlite3.connect

    def _recording_connect(path, *args, **kwargs):
        connected_paths.append(path)
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(telemetry.sqlite3, "connect", _recording_connect)

    # Exercise the whole store I/O surface.
    conn = _connect(cfg)
    try:
        _init_db(conn)
    finally:
        conn.close()
    save(cfg, _sample_record())
    loaded = load_sessions(cfg)

    # Sanity: the record round-tripped through the telemetry store.
    assert len(loaded) == 1
    assert loaded[0].session_id == "thread-sep:1000.0"

    # At least one connection was opened (the recorder is actually wired in).
    assert connected_paths

    # Every recorded connection targeted the telemetry store, never the journal.
    for path in connected_paths:
        assert path == db_path
        assert path != journal.JOURNAL_DB_PATH
        assert "trade_journal" not in str(path)

    # Telemetry never created (or removed) the Trade_Journal file.
    assert os.path.exists(journal.JOURNAL_DB_PATH) == journal_existed_before
