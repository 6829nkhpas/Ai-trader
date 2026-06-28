# Feature: feature-attribution-pruning (task 9.3): degradation integration test
"""Degradation integration test for the attribution read-only I/O layer.

Feature: feature-attribution-pruning

Validates: Requirements 5.3, 9.5.

The journal entry points ``attribution_report_from_journal`` and
``weight_map_from_journal`` own a DEFENSIVE degradation contract: on ANY SQLite
failure — a missing/never-created database, a locked file, or a dropped/renamed
``trades`` table / schema drift (here simulated with a non-SQLite file of
garbage bytes) — they must log a single ``[Attribution]`` warning and return a
well-formed ``insufficient_data`` report (``build_attribution_report([],
config)``) rather than raising into the CLI / agent caller (Requirements 5.3,
9.5).

This test points ``JOURNAL_DB_PATH`` at:

  * a MISSING file path (no DB ever created), and
  * a CORRUPT file path (garbage bytes — not a valid SQLite database, standing
    in for schema drift),

and asserts both entry points degrade gracefully:

  * ``attribution_report_from_journal()`` returns a dict with
    ``insufficient_data == True``, ``dimensions == []``, ``total_scored == 0``,
    and does NOT raise; and
  * ``weight_map_from_journal()`` returns an empty map ``{}`` without raising.

The sys.path / import and temp-DB isolation patterns (env var + the cached
``journal.JOURNAL_DB_PATH`` module attribute) mirror the sibling read-only
integration test in this directory.
"""

import os
import sys

import pytest

# Make the service package importable (attribution.py / journal.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)


def _point_journal_at(monkeypatch, db_path: str):
    """Redirect the journal store to ``db_path`` for both env and module attr.

    Sets ``JOURNAL_DB_PATH`` (env) AND the live ``journal.JOURNAL_DB_PATH``
    module attribute, because the journal module caches its resolved path at
    import time — so an already-imported journal (from an earlier test in the
    session) would otherwise keep pointing at the old store. Returns the journal
    module.
    """
    monkeypatch.setenv("JOURNAL_DB_PATH", db_path)
    journal = sys.modules.get("journal")
    if journal is None:
        import journal  # noqa: F811
    monkeypatch.setattr(journal, "JOURNAL_DB_PATH", db_path, raising=False)
    return journal


def _assert_insufficient_data_report(report):
    """Assert ``report`` is the documented empty/insufficient_data report shape."""
    assert isinstance(report, dict)
    assert report["insufficient_data"] is True
    assert report["dimensions"] == []
    assert report["total_scored"] == 0


def test_report_from_missing_journal_degrades_to_insufficient_data(tmp_path, monkeypatch):
    """A missing DB file yields an insufficient_data report and never raises (R5.3, R9.5)."""
    import attribution  # noqa: E402

    missing_path = str(tmp_path / "does_not_exist_journal.db")
    assert not os.path.exists(missing_path), "precondition: the DB must not exist"
    _point_journal_at(monkeypatch, missing_path)

    # Must NOT raise even though the underlying SELECT cannot open the DB.
    report = attribution.attribution_report_from_journal(symbol="NIFTY")
    _assert_insufficient_data_report(report)


def test_weight_map_from_missing_journal_degrades_to_empty_map(tmp_path, monkeypatch):
    """A missing DB file yields an empty Weight_Map and never raises (R5.3, R9.5)."""
    import attribution  # noqa: E402

    missing_path = str(tmp_path / "does_not_exist_journal.db")
    _point_journal_at(monkeypatch, missing_path)

    weight_map = attribution.weight_map_from_journal(symbol="NIFTY")
    assert weight_map == {}


def test_report_from_corrupt_journal_degrades_to_insufficient_data(tmp_path, monkeypatch):
    """A non-SQLite (garbage) file simulates schema drift and degrades safely (R5.3, R9.5)."""
    import attribution  # noqa: E402

    corrupt_path = tmp_path / "corrupt_journal.db"
    # Garbage bytes: a real file that exists but is NOT a valid SQLite database,
    # so opening read-only succeeds but the SELECT raises sqlite3.DatabaseError —
    # standing in for a dropped/renamed trades table / schema drift.
    corrupt_path.write_bytes(b"this is definitely not a sqlite database \x00\x01\x02 garbage")
    _point_journal_at(monkeypatch, str(corrupt_path))

    report = attribution.attribution_report_from_journal(symbol="NIFTY")
    _assert_insufficient_data_report(report)


def test_weight_map_from_corrupt_journal_degrades_to_empty_map(tmp_path, monkeypatch):
    """A corrupt DB file yields an empty Weight_Map and never raises (R5.3, R9.5)."""
    import attribution  # noqa: E402

    corrupt_path = tmp_path / "corrupt_journal.db"
    corrupt_path.write_bytes(b"\x00\x01\x02\x03 not-a-db garbage bytes \xff\xfe")
    _point_journal_at(monkeypatch, str(corrupt_path))

    weight_map = attribution.weight_map_from_journal(symbol="NIFTY")
    assert weight_map == {}


def test_degraded_report_has_well_formed_config_echo(tmp_path, monkeypatch):
    """Even when degraded, the report carries the documented config echo (R5.3)."""
    import attribution  # noqa: E402

    missing_path = str(tmp_path / "missing_for_config_echo.db")
    _point_journal_at(monkeypatch, missing_path)

    report = attribution.attribution_report_from_journal()
    _assert_insufficient_data_report(report)

    # The degradation path returns build_attribution_report([], config), so the
    # full documented shape (including the resolved config echo) is preserved.
    for key in ("dimensions", "total_scored", "backtest_scored",
                "live_scored", "config", "weak_prior", "insufficient_data"):
        assert key in report, f"degraded report missing key {key!r}"
    assert isinstance(report["config"], dict)
