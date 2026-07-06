# Feature: multi-agent-debate (task 15.4): conviction persistence
"""Integration test for DEBATE conviction persistence (journal.py, task 15.4).

Feature: multi-agent-debate

Validates: Requirements 9.4 — ``record_decision`` persists the Judge's conviction.

This is an INTEGRATION test (not property-based): it drives the real
``journal.record_decision`` against a real (throwaway) SQLite database and reads
the persisted row back through ``journal._connect()``, asserting that:

  * the ``conviction`` column equals the decision's ``conviction_score`` (72),
  * the ``mode`` column equals ``"DEBATE"``, and
  * the ``setup_key`` carries the debate ``db:<consensus>`` dimension contributed
    by the recorded debate entry.

DB ISOLATION: ``JOURNAL_DB_PATH`` is pointed at a throwaway temp file BEFORE
``journal`` is imported (and the module global is overridden defensively after
import), so the real ``trade_journal.db`` is never touched. The env var is
restored in a ``finally`` block. The sys.path / import pattern mirrors the
sibling ``test_*_aggregation_properties.py`` / ``test_session_*`` modules.
"""

import os
import sys
import tempfile

# Make the service package importable (journal.py lives one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

# Point the journal at a throwaway DB BEFORE importing it, so the module-level
# JOURNAL_DB_PATH global picks up the temp path and the real journal DB is
# untouched.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="debate_conv_journal_"), "trade_journal.db")
_ORIG_ENV = os.environ.get("JOURNAL_DB_PATH")
os.environ["JOURNAL_DB_PATH"] = _TMP_DB

import journal  # noqa: E402

# Defensive: ensure the module global points at the temp DB regardless of any
# import-time env caching.
_ORIG_DB_PATH = journal.JOURNAL_DB_PATH
journal.JOURNAL_DB_PATH = _TMP_DB


def _make_debate_decision():
    """A DEBATE-mode decision carrying a Judge conviction and a debate entry.

    A directional BUY with finite entry/stop/target so the row is scoreable, and
    a defensibility record carrying a ``debate`` entry (consensus + conviction)
    so the derived ``setup_key`` includes the ``db:<consensus>`` dimension.
    """
    consensus = "strong_agree"
    decision = {
        "action": "BUY",
        "entry": 100.0,
        "stop_loss": 99.0,
        "take_profit": 103.0,
        "conviction_score": 72,
        "source": "declare_trade",
        "defensibility": {
            "debate": {
                "bull_stance": "Strong long case from the shared evidence.",
                "bear_stance": "Weak short case; little opposition.",
                "consensus": consensus,
                "conviction": 72,
                "conviction_basis": "One-sided debate -> high conviction.",
            },
        },
    }
    return decision, consensus


def test_debate_conviction_persists_and_reads_back():
    """Validates: Requirements 9.4

    Record a DEBATE decision with conviction 72, then read the row back from the
    journal DB and assert the conviction, mode, and debate-extended setup_key
    were persisted faithfully.
    """
    try:
        decision, consensus = _make_debate_decision()

        row_id = journal.record_decision(
            decision, symbol="TESTSYM", timeframe="15m", mode="DEBATE"
        )

        # record_decision returns the inserted row id (never None on success).
        assert row_id is not None, "record_decision should return a row id"

        conn = journal._connect()
        try:
            row = conn.execute(
                "SELECT conviction, mode, setup_key, symbol, timeframe, action "
                "FROM trades WHERE id = ?",
                (row_id,),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None, "the recorded row must be readable back by id"

        # The Judge's conviction_score is persisted verbatim into the conviction column.
        assert row["conviction"] == 72

        # The DEBATE mode is persisted.
        assert row["mode"] == "DEBATE"

        # Sanity: the other identifying columns round-trip.
        assert row["symbol"] == "TESTSYM"
        assert row["timeframe"] == "15m"
        assert row["action"] == "BUY"

        # The setup_key carries the debate db: dimension contributed by the
        # recorded debate entry's consensus.
        assert f"db:{consensus}" in row["setup_key"]
    finally:
        # Restore the journal DB env / module global so no other test is affected.
        journal.JOURNAL_DB_PATH = _ORIG_DB_PATH
        if _ORIG_ENV is None:
            os.environ.pop("JOURNAL_DB_PATH", None)
        else:
            os.environ["JOURNAL_DB_PATH"] = _ORIG_ENV
