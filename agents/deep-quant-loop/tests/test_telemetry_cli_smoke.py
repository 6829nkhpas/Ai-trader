"""CLI smoke test for ``telemetry.py``'s ``main()`` entry point (task 11.3).

Feature: session-telemetry

Validates: Requirements 5.1.

The telemetry CLI is a thin, READ-ONLY front door over the Telemetry_Store that
mirrors ``attribution.py`` / ``backtest.py``: it resolves config from the
environment, loads the recorded Session_Records read-only, aggregates them into a
Telemetry_Report, prints that report as ``json.dumps(report, indent=2)``, and
ALWAYS exits ``0`` — even over an EMPTY store, because emptiness is carried by the
report's own ``session_count`` / ``weak_prior`` signal rather than an error code
(Requirement 5.1).

This test drives ``telemetry.main(argv)`` IN-PROCESS (capturing stdout via
``capsys``) with ``TELEMETRY_DB_PATH`` pointed at a throwaway DB via
``monkeypatch.setenv`` so ``main()``'s ``resolve_telemetry_config()`` resolves to
the temp store. The two cases asserted are:

  * EMPTY store — a fresh temp DB with no rows: ``main([])`` exits ``0`` and
    prints a single valid JSON report whose ``session_count`` is ``0``.
  * POPULATED store — the SAME temp DB seeded with a few terminal-outcome
    Session_Records through the REAL ``telemetry.save`` writer (no mocks): both
    ``main([])`` and a filtered ``main(["--symbol", "RELIANCE"])`` exit ``0`` and
    print valid JSON whose ``session_count`` matches the classified records in
    scope and whose ``filters`` block reflects the CLI arguments.

The sys.path / temp-DB isolation patterns mirror the sibling telemetry
integration and round-trip tests in this directory.
"""

import json
import os
import sys
import time

import pytest

# Make the service package importable (telemetry.py lives one level up from tests).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import telemetry  # noqa: E402
from telemetry import (  # noqa: E402
    OUTCOME_HOLD,
    OUTCOME_TRADE_BUY,
    OUTCOME_TRADE_SELL,
    HOLD_VOLUNTARY,
    FunnelEvent,
    FUNNEL_SESSION_STARTED,
    FUNNEL_DECISION,
    SessionRecord,
    TelemetryConfig,
    save,
)


def _extract_report(captured_out):
    """Decode the Telemetry_Report JSON from captured CLI stdout.

    ``main()`` may print a human-readable ``[Telemetry] Weak prior: ...`` note line
    ahead of the report when the report is flagged ``weak_prior`` and ``--json`` is
    not passed. That note carries no ``{`` so the JSON document is exactly the
    substring from the first brace onward — decode that so the smoke test is robust
    whether or not the note is present.
    """
    brace = captured_out.find("{")
    assert brace != -1, f"CLI stdout carried no JSON document: {captured_out!r}"
    return json.loads(captured_out[brace:])


def _record(session_id, *, thread_id, symbol, timeframe, mode, started_at, outcome,
            hold_reason=None):
    """Build a well-formed, TERMINAL Session_Record for seeding the store.

    The record carries a recognized ``outcome`` (and an ``ended_at`` /
    ``time_to_decision_s``), so ``aggregate`` classifies and counts it
    DETERMINISTICALLY regardless of the CLI's ``now_ref = time.time()`` — it never
    depends on the ``incomplete`` horizon (Requirement 5.1 smoke: the report is
    well-formed and countable).
    """
    ended_at = started_at + 12.0
    return SessionRecord(
        session_id=session_id,
        thread_id=thread_id,
        symbol=symbol,
        timeframe=timeframe,
        mode=mode,
        started_at=started_at,
        ended_at=ended_at,
        outcome=outcome,
        hold_reason=hold_reason,
        watch_cycles=1,
        target_events=1,
        invalidation_events=0,
        resume_count=1,
        reasoning_turns=2,
        tool_calls_total=3,
        tool_calls_by_name={"watch_price_condition": 1, "get_candles": 2},
        model_turns=2,
        tokens=None,
        time_to_decision_s=ended_at - started_at,
        suspended_s=None,
        funnel=[
            FunnelEvent(seq=0, kind=FUNNEL_SESSION_STARTED, ts=started_at),
            FunnelEvent(seq=1, kind=FUNNEL_DECISION, ts=ended_at),
        ],
    )


@pytest.fixture()
def empty_db_path(tmp_path, monkeypatch):
    """A fresh temp store path with NO data, exported via TELEMETRY_DB_PATH.

    No file is pre-created: the CLI's read-only ``load_sessions`` initializes the
    schema on first open and returns zero rows, yielding a clean empty report.
    """
    db_path = str(tmp_path / "telemetry_cli_empty.db")
    monkeypatch.setenv(telemetry.ENV_TELEMETRY_DB_PATH, db_path)
    return db_path


@pytest.fixture()
def seeded_db_path(tmp_path, monkeypatch):
    """Seed a throwaway store via the REAL ``save`` writer; export its path.

    ``save`` writes through a ``TelemetryConfig`` pointed at the temp file, and the
    SAME path is exported via ``TELEMETRY_DB_PATH`` so ``main()``'s
    ``resolve_telemetry_config()`` reads back exactly what was written.
    """
    db_path = str(tmp_path / "telemetry_cli_seeded.db")
    monkeypatch.setenv(telemetry.ENV_TELEMETRY_DB_PATH, db_path)

    cfg = TelemetryConfig(
        db_path=db_path,
        weak_prior_min_sessions=20,
        incomplete_horizon_seconds=float(24 * 3600),
    )

    now = time.time()
    seed = [
        _record("s1", thread_id="t1", symbol="RELIANCE", timeframe="15m", mode="FIND",
                started_at=now - 100.0, outcome=OUTCOME_TRADE_BUY),
        _record("s2", thread_id="t2", symbol="RELIANCE", timeframe="15m", mode="FIND",
                started_at=now - 80.0, outcome=OUTCOME_HOLD, hold_reason=HOLD_VOLUNTARY),
        _record("s3", thread_id="t3", symbol="TCS", timeframe="1h", mode="FIND",
                started_at=now - 60.0, outcome=OUTCOME_TRADE_SELL),
    ]
    for record in seed:
        save(cfg, record)

    assert os.path.exists(db_path), "seeding should have created the telemetry store"
    return db_path


def test_cli_empty_store_exits_zero_with_zero_session_count(empty_db_path, capsys):
    """Validates: Requirements 5.1

    ``main([])`` over an EMPTY store exits ``0`` and prints a single valid JSON
    report whose ``session_count`` is ``0``.
    """
    rc = telemetry.main([])
    assert rc == 0, f"CLI must exit 0 on an empty store; got {rc}"

    report = _extract_report(capsys.readouterr().out)
    assert isinstance(report, dict)
    assert report["session_count"] == 0
    # No CLI filters were passed, so the filters block is all-None.
    assert report["filters"] == {
        "symbol": None,
        "timeframe": None,
        "mode": None,
        "since": None,
        "until": None,
    }


def test_cli_populated_store_exits_zero_with_matching_session_count(seeded_db_path, capsys):
    """Validates: Requirements 5.1

    ``main([])`` over a POPULATED store exits ``0`` and prints valid JSON whose
    ``session_count`` equals the three seeded terminal-outcome records.
    """
    rc = telemetry.main([])
    assert rc == 0, f"CLI must exit 0 on a populated store; got {rc}"

    report = _extract_report(capsys.readouterr().out)
    assert isinstance(report, dict)
    assert report["session_count"] == 3
    assert report["filters"]["symbol"] is None


def test_cli_populated_store_symbol_filter_exits_zero_and_reflects_filter(seeded_db_path, capsys):
    """Validates: Requirements 5.1

    ``main(["--symbol", "RELIANCE"])`` over the populated store exits ``0``, counts
    only the two RELIANCE records, and stamps the active symbol into ``filters``.
    """
    rc = telemetry.main(["--symbol", "RELIANCE"])
    assert rc == 0, f"CLI must exit 0 with a symbol filter; got {rc}"

    report = _extract_report(capsys.readouterr().out)
    assert isinstance(report, dict)
    assert report["session_count"] == 2, "only the two RELIANCE sessions are in scope"
    assert report["filters"]["symbol"] == "RELIANCE"
    assert report["filters"]["timeframe"] is None
