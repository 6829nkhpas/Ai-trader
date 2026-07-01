"""Unit test for the additive Telemetry_Store migration (telemetry.py, task 7.4).

Feature: session-telemetry

Example-based (non-property) unit test that exercises the idempotent, additive
forward-compat migration in ``_init_db`` / ``_ensure_column``:

    Open an OLD-SHAPE ``sessions`` table — one created before the forward-compat
    ``opportunity_tier`` / ``session_budget`` / ``extra`` columns existed, holding
    pre-existing rows — run ``_init_db``, and assert the three forward-compat
    columns are added while every existing row is preserved untouched (the new
    columns read back as NULL for them).

Validates: Requirements 7.4.

The sys.path / import pattern mirrors the other telemetry tests
(``tests/test_telemetry_config_robustness_properties.py``).
"""

import os
import sqlite3
import sys

# Make the service package importable (telemetry.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import telemetry  # noqa: E402
from telemetry import (  # noqa: E402
    DEFAULT_INCOMPLETE_HORIZON_SECONDS,
    DEFAULT_WEAK_PRIOR_MIN_SESSIONS,
    TABLE_SESSIONS,
    TelemetryConfig,
    _connect,
    _init_db,
)

# The forward-compat columns the migration must add to an old-shape store.
_FORWARD_COMPAT_COLUMNS = ("opportunity_tier", "session_budget", "extra")

# An OLD-SHAPE ``sessions`` table: the realistic pre-migration schema, i.e. the
# current ``sessions`` schema MINUS the three forward-compat columns. It keeps the
# core identity / timing / outcome / counter / cost columns so the fixture models
# a genuinely old store rather than a toy table.
_OLD_SHAPE_SESSIONS_DDL = """
    CREATE TABLE sessions (
        session_id     TEXT PRIMARY KEY,
        thread_id      TEXT NOT NULL,
        symbol         TEXT,
        timeframe      TEXT,
        mode           TEXT,
        started_at     REAL NOT NULL,
        ended_at       REAL,
        outcome        TEXT,
        hold_reason    TEXT,
        watch_cycles         INTEGER NOT NULL DEFAULT 0,
        target_events        INTEGER NOT NULL DEFAULT 0,
        invalidation_events  INTEGER NOT NULL DEFAULT 0,
        resume_count         INTEGER NOT NULL DEFAULT 0,
        reasoning_turns      INTEGER NOT NULL DEFAULT 0,
        tool_calls_total     INTEGER NOT NULL DEFAULT 0,
        tool_calls_by_name   TEXT,
        model_turns          INTEGER NOT NULL DEFAULT 0,
        tokens               INTEGER,
        time_to_decision_s   REAL,
        suspended_s          REAL
    )
"""

# Two representative pre-existing rows (a converted BUY session and a forced HOLD).
_OLD_ROWS = [
    {
        "session_id": "thread-alpha:1000.0",
        "thread_id": "thread-alpha",
        "symbol": "RELIANCE",
        "timeframe": "15m",
        "mode": "FIND",
        "started_at": 1000.0,
        "ended_at": 1120.5,
        "outcome": "trade_buy",
        "hold_reason": None,
        "watch_cycles": 2,
        "target_events": 1,
        "invalidation_events": 1,
        "resume_count": 2,
        "reasoning_turns": 4,
        "tool_calls_total": 7,
        "tool_calls_by_name": '{"watch_price_condition": 2, "get_candles": 5}',
        "model_turns": 4,
        "tokens": 3200,
        "time_to_decision_s": 120.5,
        "suspended_s": 60.0,
    },
    {
        "session_id": "thread-beta:2000.0",
        "thread_id": "thread-beta",
        "symbol": "TCS",
        "timeframe": "1h",
        "mode": "MANAGE",
        "started_at": 2000.0,
        "ended_at": 2050.0,
        "outcome": "hold",
        "hold_reason": "forced",
        "watch_cycles": 0,
        "target_events": 0,
        "invalidation_events": 0,
        "resume_count": 0,
        "reasoning_turns": 3,
        "tool_calls_total": 1,
        "tool_calls_by_name": '{"get_candles": 1}',
        "model_turns": 3,
        "tokens": None,
        "time_to_decision_s": 50.0,
        "suspended_s": None,
    },
]


def _seed_old_shape_store(db_path):
    """Create an old-shape ``sessions`` table and insert the representative rows."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_OLD_SHAPE_SESSIONS_DDL)
        columns = list(_OLD_ROWS[0].keys())
        placeholders = ", ".join("?" for _ in columns)
        conn.executemany(
            f"INSERT INTO sessions ({', '.join(columns)}) VALUES ({placeholders})",
            [tuple(row[c] for c in columns) for row in _OLD_ROWS],
        )
        conn.commit()
    finally:
        conn.close()


def _table_columns(conn, table):
    """Return the set of column names on ``table`` via PRAGMA table_info."""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_init_db_additively_migrates_old_shape_store(tmp_path):
    """``_init_db`` adds the forward-compat columns and leaves old rows untouched.

    Validates: Requirements 7.4.
    """
    db_path = str(tmp_path / "old_telemetry.db")
    _seed_old_shape_store(db_path)

    cfg = TelemetryConfig(
        db_path=db_path,
        weak_prior_min_sessions=DEFAULT_WEAK_PRIOR_MIN_SESSIONS,
        incomplete_horizon_seconds=DEFAULT_INCOMPLETE_HORIZON_SECONDS,
    )

    # Sanity: the old-shape store genuinely lacks the forward-compat columns.
    conn = _connect(cfg)
    try:
        before = _table_columns(conn, TABLE_SESSIONS)
        for column in _FORWARD_COMPAT_COLUMNS:
            assert column not in before, f"fixture already had '{column}'"

        # Run the migration.
        _init_db(conn)

        # 1. All three forward-compat columns now exist.
        after = _table_columns(conn, TABLE_SESSIONS)
        for column in _FORWARD_COMPAT_COLUMNS:
            assert column in after, f"migration did not add '{column}'"

        # 2. Pre-existing rows are untouched: same count, same original values,
        #    and the newly added columns read back as NULL.
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY started_at ASC"
        ).fetchall()
        assert len(rows) == len(_OLD_ROWS)

        for row, expected in zip(rows, _OLD_ROWS):
            for column, value in expected.items():
                assert row[column] == value, (
                    f"row {expected['session_id']} column '{column}' changed: "
                    f"{row[column]!r} != {value!r}"
                )
            for column in _FORWARD_COMPAT_COLUMNS:
                assert row[column] is None, (
                    f"new column '{column}' should be NULL for pre-existing row "
                    f"{expected['session_id']}, got {row[column]!r}"
                )
    finally:
        conn.close()


def test_init_db_is_idempotent_on_migrated_store(tmp_path):
    """Re-running ``_init_db`` on an already-migrated store is a harmless no-op.

    Validates: Requirements 7.4.
    """
    db_path = str(tmp_path / "old_telemetry_idempotent.db")
    _seed_old_shape_store(db_path)

    cfg = TelemetryConfig(
        db_path=db_path,
        weak_prior_min_sessions=DEFAULT_WEAK_PRIOR_MIN_SESSIONS,
        incomplete_horizon_seconds=DEFAULT_INCOMPLETE_HORIZON_SECONDS,
    )

    conn = _connect(cfg)
    try:
        _init_db(conn)
        _init_db(conn)  # second run must not raise or disturb the data

        after = _table_columns(conn, TABLE_SESSIONS)
        for column in _FORWARD_COMPAT_COLUMNS:
            assert column in after

        rows = conn.execute("SELECT * FROM sessions ORDER BY started_at ASC").fetchall()
        assert len(rows) == len(_OLD_ROWS)
        for row, expected in zip(rows, _OLD_ROWS):
            assert row["session_id"] == expected["session_id"]
            for column in _FORWARD_COMPAT_COLUMNS:
                assert row[column] is None
    finally:
        conn.close()
