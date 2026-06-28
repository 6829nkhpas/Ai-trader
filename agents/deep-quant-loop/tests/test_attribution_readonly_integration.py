# Feature: feature-attribution-pruning (task 9.2): read-only / scope integration test
"""Read-only / scope integration test for ``attribution_report_from_journal``.

Feature: feature-attribution-pruning

Validates: Requirements 9.1, 9.2.

This integration test proves the Feature Attribution pass is byte-for-byte
read-only with respect to the Trade_Journal (Requirement 9.1) and that it of
itself emits / mutates nothing (Requirement 9.2):

  * A throwaway journal DB is seeded with several already-scored backtest trades
    via the REAL ``journal.record_backtest_trade`` writer (no mocks), so the on-
    disk schema and rows are exactly what production code produces.
  * The full ``trades`` table (row count AND every column of every row, ordered
    deterministically by ``id``) and the raw DB file bytes + mtime are snapshotted
    BEFORE the attribution read.
  * ``attribution_report_from_journal()`` and ``weight_map_from_journal()`` are
    run against that journal.
  * The table contents are asserted row-for-row unchanged and the DB file bytes
    are asserted byte-for-byte unchanged afterwards — the attribution pass issues
    only a single read-only ``SELECT`` and never scores open trades, writes, or
    alters the schema.
  * The produced report is asserted well-formed (the documented Attribution_Report
    shape) over the seeded scored rows.

The sys.path / import and temp-DB isolation patterns mirror the sibling
calibration / journal smoke tests in this directory.
"""

import os
import sqlite3
import sys

import pytest

# Make the service package importable (attribution.py / journal.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)


def _decision(action: str, *, macro: str, regime_trend: str, regime_fav: str) -> dict:
    """Build a minimal committed-decision dict that derives a groupable setup_key.

    Only the fields ``record_backtest_trade`` / ``derive_setup_tags`` read are
    populated: the action, entry/stop/target geometry, and a defensibility record
    carrying a macro-trend-conflict phrase and a regime so the fingerprint spans
    more than one dimension value.
    """
    return {
        "action": action,
        "entry": 100.0,
        "stop_loss": 98.0,
        "take_profit": 104.0,
        "atr_14": 1.5,
        "conviction_score": 7,
        "defensibility": {
            "risk_reward": 2.0,
            "macro_trend_conflict": macro,
            "defensibility": {},
            "regime": {
                "available": True,
                "trend_state": regime_trend,
                "favorability": regime_fav,
            },
        },
    }


# A small, deterministic seed set spanning wins and losses across two regimes /
# macro stances so the report has real Scored_Trades in more than one dimension
# value. (action, status, r_multiple, macro phrase, regime trend, favorability).
_SEED = [
    ("BUY",  "win",  2.0, "aligned with the 1d trend", "trending", "favorable"),
    ("BUY",  "loss", -1.0, "aligned with the 1d trend", "trending", "favorable"),
    ("BUY",  "win",  2.0, "aligned with the 1d trend", "trending", "favorable"),
    ("SELL", "loss", -1.0, "macro conflict", "ranging", "unfavorable"),
    ("SELL", "win",  1.5, "macro conflict", "ranging", "unfavorable"),
    ("BUY",  "loss", -1.0, "macro conflict", "ranging", "unfavorable"),
    ("SELL", "win",  3.0, "aligned with the 1d trend", "trending", "favorable"),
]


def _snapshot_rows(db_path: str) -> list:
    """Full, deterministically-ordered snapshot of the ``trades`` table.

    Returns every column of every row (``SELECT *`` ordered by ``id``) as a list
    of plain tuples so it can be compared verbatim before / after the read.
    """
    conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        return conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
    finally:
        conn.close()


def _row_count(db_path: str) -> int:
    conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        return conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    finally:
        conn.close()


@pytest.fixture()
def seeded_journal(tmp_path, monkeypatch):
    """Point the journal at a temp DB and seed it with the real writer.

    Sets ``JOURNAL_DB_PATH`` (env) and the live ``journal.JOURNAL_DB_PATH``
    module attribute BEFORE any read so both a fresh import and an already-loaded
    journal module resolve the temp file. Returns ``(journal, db_path)``.
    """
    db_path = str(tmp_path / "attribution_readonly_journal.db")
    monkeypatch.setenv("JOURNAL_DB_PATH", db_path)

    # Redirect the journal store whether or not journal was already imported by
    # an earlier test in the session (the module-level path is cached at import).
    journal = sys.modules.get("journal")
    if journal is None:
        import journal  # noqa: F811
    monkeypatch.setattr(journal, "JOURNAL_DB_PATH", db_path, raising=False)

    for action, status, r_mult, macro, trend, fav in _SEED:
        rowid = journal.record_backtest_trade(
            decision=_decision(action, macro=macro, regime_trend=trend, regime_fav=fav),
            symbol="NIFTY",
            timeframe="15m",
            status=status,
            outcome_price=104.0 if status == "win" else 98.0,
            outcome_at=1_700_000_500.0,
            r_multiple=r_mult,
        )
        assert rowid is not None, "seed write should succeed"

    assert _row_count(db_path) == len(_SEED), "all seed rows should be persisted"
    return journal, db_path


def test_attribution_report_is_well_formed_over_seeded_journal(seeded_journal):
    """The report reads the seeded rows and carries the documented shape."""
    import attribution  # noqa: E402

    report = attribution.attribution_report_from_journal(symbol="NIFTY")

    # Documented Attribution_Report shape (design "Data Models").
    assert isinstance(report, dict)
    for key in ("dimensions", "total_scored", "backtest_scored",
                "config", "weak_prior", "insufficient_data"):
        assert key in report, f"report missing key {key!r}"

    # All seven seeded rows are win/loss outcomes -> all are Scored_Trades, and
    # every row is source='backtest'.
    assert report["total_scored"] == len(_SEED)
    assert report["backtest_scored"] == len(_SEED)
    assert report["insufficient_data"] is False
    assert isinstance(report["dimensions"], list) and report["dimensions"]

    # Each Dimension_Report names a dimension and ranks its values.
    for dim in report["dimensions"]:
        assert "dimension" in dim


def test_attribution_read_does_not_mutate_journal_rows(seeded_journal):
    """Row count AND full row contents are unchanged after the attribution read (R9.1)."""
    import attribution  # noqa: E402
    _, db_path = seeded_journal

    before_count = _row_count(db_path)
    before_rows = _snapshot_rows(db_path)

    # Exercise BOTH journal read entry points.
    attribution.attribution_report_from_journal(symbol="NIFTY")
    attribution.weight_map_from_journal(symbol="NIFTY")

    after_count = _row_count(db_path)
    after_rows = _snapshot_rows(db_path)

    assert after_count == before_count, "attribution must not add/remove rows"
    assert after_rows == before_rows, "attribution must not modify any row contents"


def test_attribution_read_does_not_mutate_db_file_bytes(seeded_journal):
    """The journal DB file is byte-for-byte unchanged after the read (R9.1, R9.2)."""
    import attribution  # noqa: E402
    _, db_path = seeded_journal

    before_bytes = open(db_path, "rb").read()
    before_mtime = os.path.getmtime(db_path)

    attribution.attribution_report_from_journal(symbol="NIFTY")
    attribution.weight_map_from_journal(symbol="NIFTY")

    after_bytes = open(db_path, "rb").read()
    after_mtime = os.path.getmtime(db_path)

    assert after_bytes == before_bytes, "attribution read must not alter the DB file bytes"
    assert after_mtime == before_mtime, "attribution read must not rewrite the DB file"

    # A read-only pass must not spawn WAL / rollback side files alongside the DB.
    assert not os.path.exists(db_path + "-wal"), "read-only pass must not create a WAL file"
    assert not os.path.exists(db_path + "-journal"), "read-only pass must not create a rollback journal"


def test_source_scope_filters_select_only_backtest_rows(seeded_journal):
    """Scope by source narrows rows without mutating the journal (R9.1, R9.2)."""
    import attribution  # noqa: E402
    _, db_path = seeded_journal

    before_rows = _snapshot_rows(db_path)

    # All seeded rows are source='backtest': the backtest scope sees them all and
    # the complementary live scope sees none.
    backtest_report = attribution.attribution_report_from_journal(symbol="NIFTY", source="backtest")
    live_report = attribution.attribution_report_from_journal(symbol="NIFTY", source="live")

    assert backtest_report["total_scored"] == len(_SEED)
    assert backtest_report["backtest_scored"] == len(_SEED)
    assert live_report["total_scored"] == 0
    assert live_report["insufficient_data"] is True

    assert _snapshot_rows(db_path) == before_rows, "scoped reads must not mutate the journal"
