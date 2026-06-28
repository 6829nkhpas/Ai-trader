# Feature: feature-attribution-pruning (task 10.2): CLI smoke integration test
"""CLI smoke integration test for ``attribution.py``'s ``main()`` entry point.

Feature: feature-attribution-pruning

Validates: Requirements 4.2.

The attribution CLI is a thin, READ-ONLY front door over the journal that
mirrors ``backtest.py``: it resolves config, reads the journal, prints the
Attribution_Report as JSON, and ALWAYS exits ``0`` — even on an empty /
insufficient journal, because emptiness is carried by the report's
``insufficient_data`` flag rather than signalled through an error code
(Requirement 4.2).

This test invokes the CLI as a real SUBPROCESS (``python attribution.py``) with
``JOURNAL_DB_PATH`` pointed at a throwaway DB, exactly the way a user would run
it, and asserts:

  * an EMPTY / missing journal run with ``--json`` exits ``0`` and prints a
    single valid JSON document whose ``insufficient_data`` is ``True``;
  * the same run with ``--weight-map --json`` exits ``0`` and prints TWO valid
    JSON documents (the report followed by the Weight_Map);
  * a POPULATED journal — seeded with the REAL ``journal.record_backtest_trade``
    writer (no mocks) into the same temp DB — run with ``--json`` exits ``0``,
    prints valid JSON, and reports ``insufficient_data == False`` with a positive
    ``total_scored``.

The CLI is run with ``cwd`` set to the service directory (one level up) so the
script's ``import journal`` resolves, and with ``sys.executable`` as the Python
binary so the subprocess matches the test interpreter. The sys.path / temp-DB
isolation patterns mirror the sibling read-only and degradation integration
tests in this directory.
"""

import json
import os
import subprocess
import sys

import pytest

# The service directory (attribution.py / journal.py live one level up from tests).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

_ATTRIBUTION_SCRIPT = "attribution.py"


def _run_cli(args, db_path):
    """Invoke ``python attribution.py <args>`` as a subprocess against ``db_path``.

    Runs from the service directory (so the script's ``import journal`` resolves)
    with ``JOURNAL_DB_PATH`` overridden to the throwaway DB. Uses ``sys.executable``
    so the subprocess interpreter matches the one running the tests. Captures text
    stdout/stderr. Never raises on a non-zero exit — the caller asserts on the
    returned ``CompletedProcess``.
    """
    env = dict(os.environ)
    env["JOURNAL_DB_PATH"] = db_path
    return subprocess.run(
        [sys.executable, _ATTRIBUTION_SCRIPT, *args],
        cwd=_SVC_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _parse_json_documents(text):
    """Decode one or more concatenated JSON documents from ``text``.

    Under ``--json`` the CLI prints the report and (with ``--weight-map``) the
    Weight_Map as back-to-back ``json.dumps(..., indent=2)`` blocks with no human
    header. ``json.JSONDecoder().raw_decode`` consumes one value at a time so we
    can split the stream into its constituent documents, tolerating the
    interleaving whitespace/newlines between them.
    """
    decoder = json.JSONDecoder()
    docs = []
    idx = 0
    n = len(text)
    while idx < n:
        # Skip any whitespace between documents.
        while idx < n and text[idx].isspace():
            idx += 1
        if idx >= n:
            break
        value, end = decoder.raw_decode(text, idx)
        docs.append(value)
        idx = end
    return docs


# ── Seed data for the populated-journal case ──────────────────────────────────
# A minimal committed-decision dict whose only populated fields are those that
# ``record_backtest_trade`` / ``derive_setup_tags`` read to build a groupable
# setup_key (mirrors the sibling read-only integration test).
def _decision(action, *, macro, regime_trend, regime_fav):
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


_SEED = [
    ("BUY",  "win",  2.0, "aligned with the 1d trend", "trending", "favorable"),
    ("BUY",  "loss", -1.0, "aligned with the 1d trend", "trending", "favorable"),
    ("BUY",  "win",  2.0, "aligned with the 1d trend", "trending", "favorable"),
    ("SELL", "loss", -1.0, "macro conflict", "ranging", "unfavorable"),
    ("SELL", "win",  1.5, "macro conflict", "ranging", "unfavorable"),
    ("BUY",  "loss", -1.0, "macro conflict", "ranging", "unfavorable"),
    ("SELL", "win",  3.0, "aligned with the 1d trend", "trending", "favorable"),
]


@pytest.fixture()
def empty_db_path(tmp_path, monkeypatch):
    """A VALID but EMPTY journal DB (schema present, zero rows).

    Creates the real ``trades`` schema via ``journal._init_db`` so the CLI's
    read-only ``SELECT`` succeeds and returns no rows — yielding a clean
    ``insufficient_data`` report with NO degradation warning on stdout. (The
    missing/locked-DB degradation path is exercised separately by the degradation
    integration test, task 9.3.)
    """
    db_path = str(tmp_path / "attribution_cli_empty.db")
    monkeypatch.setenv("JOURNAL_DB_PATH", db_path)

    journal = sys.modules.get("journal")
    if journal is None:
        import journal  # noqa: F811
    monkeypatch.setattr(journal, "JOURNAL_DB_PATH", db_path, raising=False)

    conn = journal._connect()
    try:
        journal._init_db(conn)
    finally:
        conn.close()

    assert os.path.exists(db_path), "an empty-but-valid journal DB should exist"
    return db_path


@pytest.fixture()
def seeded_db_path(tmp_path, monkeypatch):
    """Seed a throwaway journal with the real writer and return its path.

    Points the journal store (both ``JOURNAL_DB_PATH`` env and the cached
    ``journal.JOURNAL_DB_PATH`` module attribute) at a temp file, writes the seed
    rows via ``journal.record_backtest_trade``, and returns the on-disk path so
    the CLI subprocess can read the SAME file via its env override.
    """
    db_path = str(tmp_path / "attribution_cli_seeded.db")
    monkeypatch.setenv("JOURNAL_DB_PATH", db_path)

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

    assert os.path.exists(db_path), "seeding should have created the journal DB"
    return db_path


def test_cli_json_empty_journal_exits_zero_with_insufficient_data(empty_db_path):
    """Validates: Requirements 4.2

    ``python attribution.py --json`` against an empty/missing journal exits ``0``
    and prints a single valid JSON report flagged ``insufficient_data``.
    """
    result = _run_cli(["--json"], empty_db_path)

    assert result.returncode == 0, (
        f"CLI must exit 0 on an empty journal; got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )

    docs = _parse_json_documents(result.stdout)
    assert len(docs) == 1, f"expected exactly one JSON document, got {len(docs)}"

    report = docs[0]
    assert isinstance(report, dict)
    assert report["insufficient_data"] is True
    assert report["dimensions"] == []
    assert report["total_scored"] == 0


def test_cli_json_weight_map_empty_journal_emits_two_valid_json_docs(empty_db_path):
    """Validates: Requirements 4.2

    ``--weight-map --json`` against an empty journal exits ``0`` and prints two
    valid JSON documents: the insufficient_data report and the (empty) Weight_Map.
    """
    result = _run_cli(["--weight-map", "--json"], empty_db_path)

    assert result.returncode == 0, (
        f"CLI must exit 0 on an empty journal; got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )

    docs = _parse_json_documents(result.stdout)
    assert len(docs) == 2, (
        f"expected report + weight map (two JSON documents), got {len(docs)}"
    )

    report, weight_map = docs
    assert isinstance(report, dict) and report["insufficient_data"] is True
    # An insufficient_data report has no dimensions, so the derived map is empty.
    assert weight_map == {}


def test_cli_json_populated_journal_exits_zero_with_valid_json(seeded_db_path):
    """Validates: Requirements 4.2

    ``python attribution.py --json`` against a seeded journal exits ``0`` and
    prints a single valid JSON report carrying real Scored_Trades.
    """
    result = _run_cli(["--json", "--symbol", "NIFTY"], seeded_db_path)

    assert result.returncode == 0, (
        f"CLI must exit 0 on a populated journal; got {result.returncode}. "
        f"stderr={result.stderr!r}"
    )

    docs = _parse_json_documents(result.stdout)
    assert len(docs) == 1, f"expected exactly one JSON document, got {len(docs)}"

    report = docs[0]
    assert isinstance(report, dict)
    # All seven seeded rows are win/loss outcomes -> all are Scored_Trades.
    assert report["insufficient_data"] is False
    assert report["total_scored"] == len(_SEED)
    assert isinstance(report["dimensions"], list) and report["dimensions"]
