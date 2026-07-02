"""End-to-end example test for a PRESERVED single-target run (task 15.2).

Feature: trade-management

This is a deterministic, offline, EXAMPLE-based end-to-end test proving the
NON-BREAKING guarantee (R14.5): a committed ``Single_Target_Trade`` (entry /
stop / one take-profit, NO ``management_plan``) still threads through every
trade-management consumer layer exactly as before this feature, producing today's
behavior end-to-end. The real LLM and the Rust Tool_Server are never invoked.

It stitches the four layers a committed single-target decision touches:

  1. **Validation (R14.5)** — ``validator.validate_trade`` with NO plan passes a
     sound bracket (entry 100, stop 90, target 120, ``atr_14=None``) and reports
     the single-target Risk_Reward of ``2.0``, exactly as before.

  2. **Journal LEGACY scoring (R14.5)** — ``journal.record_decision`` with no
     ``management_plan`` persists a NULL ``management_plan`` column; scoring over
     a synthetic candle window (``journal._fetch_candles`` monkeypatched) resolves
     via the unchanged legacy single-target path: a target-first window wins at
     ``+risk_reward`` (= +2.0), a stop-first window loses at ``-1.0``, and the
     ``exit_breakdown`` column stays NULL (the legacy path writes none).

  3. **Defensibility + stream verification step (R10.3)** —
     ``graph.build_defensibility_record`` on a committed BUY with levels and no
     ``management_plan`` yields a management entry of style ``single`` (the
     degenerate single-target plan, no fabricated scale-out legs); the
     ``stream_events`` decision events surface exactly one ``trade-management``
     ``VERIFICATION_STEP`` whose outcome is ``informational`` (no active
     management), ordered before the ``DECISION`` event.

  4. **Journal fingerprint tag (R9.3)** — ``journal.derive_setup_tags`` appends a
     single fixed-position ``tm:single`` tag for the single-target management
     entry.

A TEMP ``JOURNAL_DB_PATH`` is used (removed on teardown) so the real
``trade_journal.db`` is never touched, and ``journal._fetch_candles`` is
monkeypatched so no Rust server is needed. The sys.path / import pattern mirrors
the sibling ``test_journal_management.py`` and ``test_forecast_aligned_find_mode_e2e.py``.

Validates: Requirements 9.3, 10.3, 14.5
"""

import os
import sqlite3
import sys
import time

import pytest

# Make the service package importable (validator/journal/graph/stream_events
# live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import journal  # noqa: E402
import stream_events  # noqa: E402
import validator  # noqa: E402
from graph import build_defensibility_record  # noqa: E402
from stream_events import DECISION, VERIFICATION_STEP  # noqa: E402
from validator import Action, ExecutionLevels, validate_trade  # noqa: E402

TM_CHECK_ID = "trade-management"

# The preserved single-target bracket: entry 100, initial stop 90 (risk = 10),
# one take-profit at 120 => single-target Risk_Reward = (120-100)/10 = 2.0.
_ENTRY = 100.0
_STOP = 90.0
_TARGET = 120.0
_EXPECTED_RR = 2.0


# ── Temp-DB isolation ─────────────────────────────────────────────────────────
@pytest.fixture
def temp_journal(tmp_path, monkeypatch):
    """Point the journal at a per-test throwaway sqlite file.

    ``journal.JOURNAL_DB_PATH`` is read inside ``_connect()`` on every call, so a
    plain ``setattr`` redirects all reads / writes to the temp file; ``tmp_path``
    is removed by pytest after the test, so the real ``trade_journal.db`` is never
    touched.
    """
    db_path = str(tmp_path / "j.db")
    monkeypatch.setattr(journal, "JOURNAL_DB_PATH", db_path)
    return db_path


# ── Helpers ───────────────────────────────────────────────────────────────────
def _read_row(db_path, row_id):
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT * FROM trades WHERE id=?", (row_id,))
        row = cur.fetchone()
        assert row is not None, "the recorded row must exist"
        return {k: row[k] for k in row.keys()}
    finally:
        conn.close()


def _candle(o, h, lo, c, ts):
    return {"open": o, "high": h, "low": lo, "close": c, "volume": 1000.0, "timestamp_ms": ts}


def _stamp_after_now(ohlc_list):
    """Stamp an OHLC list with timestamps strictly after the current wall clock,
    so every candle clears the journal's ``timestamp_ms > created_at * 1000`` gate.
    """
    base = int((time.time() + 1.0) * 1000.0)
    return [
        _candle(o, h, lo, c, base + (i + 1) * 60_000)
        for i, (o, h, lo, c) in enumerate(ohlc_list)
    ]


def _patch_candles(monkeypatch, ohlc_list):
    def _fake_fetch(symbol, timeframe, limit):
        return _stamp_after_now(ohlc_list)

    monkeypatch.setattr(journal, "_fetch_candles", _fake_fetch)


def _committed_single_target_decision():
    """A committed single-target BUY with execution levels and NO management plan."""
    return {
        "action": "BUY",
        "conviction_score": 7,
        "source": "declare_trade",
        "entry": _ENTRY,
        "stop_loss": _STOP,
        "take_profit": _TARGET,
    }


def _decision_with_defensibility():
    """A committed single-target decision carrying its FIND-mode defensibility
    record (which holds the single-target management entry, style ``single``).
    """
    decision = _committed_single_target_decision()
    # No analysis-tool messages in scope: the management entry is built from the
    # committed bracket alone (the degenerate single-target plan), and with no
    # candles in scope it records the plan only (no fabricated exit).
    record = build_defensibility_record([], decision, mode="FIND")
    decision["defensibility"] = record
    return decision, record


# ─────────────────────────────────────────────────────────────────────────────
# 1. Validation: a sound single-target bracket passes with no plan (R14.5)
# ─────────────────────────────────────────────────────────────────────────────
def test_single_target_validates_with_no_plan():
    """Validates: Requirements 14.5

    ``validate_trade`` with NO management plan passes the sound bracket and
    reports the single-target Risk_Reward of 2.0 — exactly as before this feature.
    """
    outcome = validate_trade(
        Action.BUY,
        ExecutionLevels(entry=_ENTRY, stop_loss=_STOP, take_profit=_TARGET),
        None,  # atr_14 unknown -> no 1.5x ATR floor applies
    )
    assert outcome.is_pass(), "a sound single-target bracket must validate"
    assert outcome.reason is None
    assert outcome.risk_reward is not None
    assert abs(outcome.risk_reward - _EXPECTED_RR) < 1e-9


# ─────────────────────────────────────────────────────────────────────────────
# 2. Defensibility + stream: single-target entry -> informational step,
#    ordered before the DECISION (R10.3); and the tm:single tag (R9.3)
# ─────────────────────────────────────────────────────────────────────────────
def test_single_target_management_entry_informational_step_and_tag():
    """Validates: Requirements 9.3, 10.3

    The committed single-target trade yields a management entry of style
    ``single`` (no fabricated scale-out legs), an ``informational``
    trade-management verification step ordered before the ``DECISION`` event, and
    a single fixed-position ``tm:single`` journal fingerprint tag.
    """
    decision, record = _decision_with_defensibility()

    # ── Defensibility: single-target management entry (R9.3) ─────────────────
    management = record["management"]
    assert management["available"] is True
    assert management["style"] == "single"
    # No fabricated scale-out legs: exactly one leg at fraction 1.0 at the target.
    assert len(management["legs"]) == 1
    assert management["legs"][0]["fraction"] == 1.0
    assert management["legs"][0]["target"] == _TARGET
    assert management["breakeven"] is None
    assert management["trailing"] is None
    # No candles in scope -> the plan is recorded but never simulated (no fabrication).
    assert "exit_breakdown" not in management

    # ── Stream: exactly one informational trade-management step, before DECISION ─
    events = list(stream_events.decision_events(decision))
    event_names = [name for name, _ in events]

    tm_steps = [
        (i, payload)
        for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP and payload.get("check") == TM_CHECK_ID
    ]
    assert len(tm_steps) == 1, f"expected exactly one trade-management step, got {len(tm_steps)}"
    tm_index, tm_payload = tm_steps[0]
    assert str(tm_payload["outcome"]).startswith("informational"), (
        "a single-target trade surfaces an informational trade-management step (R10.3)"
    )

    assert DECISION in event_names, "the run must emit a DECISION event"
    decision_index = event_names.index(DECISION)
    assert tm_index < decision_index, "the trade-management step must precede the DECISION (R10.5)"

    # The committed decision surfaces unchanged (BUY) — management is informational.
    assert events[decision_index][1]["action"] == "BUY"

    # ── Journal fingerprint: a single fixed-position tm:single tag (R9.3) ────
    tags = journal.derive_setup_tags(decision)
    tm_tags = [t for t in tags if t.startswith("tm:")]
    assert tm_tags == ["tm:single"], "a single-target management entry yields exactly tm:single"
    # The tm tag sits at its fixed position immediately after the ``fc:`` tag;
    # the session and multi-agent-debate dimensions follow, so ``db:`` is last.
    tm_index = tags.index("tm:single")
    assert tags[tm_index - 1].startswith("fc:")
    assert tags[-1].startswith("tier:"), "tier: is the fixed final dimension (opportunity engine R9.2)"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Journal LEGACY single-target scoring: target-first WIN at +risk_reward,
#    NULL management_plan, NULL exit_breakdown (R14.5)
# ─────────────────────────────────────────────────────────────────────────────
def test_single_target_legacy_scoring_target_first_win(temp_journal, monkeypatch):
    """Validates: Requirements 14.5

    A committed single-target trade (no ``management_plan``) records a NULL plan
    column, carries the ``tm:single`` fingerprint, and scores via the unchanged
    legacy single-target path to a WIN at ``+risk_reward`` (= +2.0) with a NULL
    ``exit_breakdown``.
    """
    decision, _ = _decision_with_defensibility()
    row_id = journal.record_decision(decision, symbol="TEST", timeframe="1d", mode="FIND")
    assert row_id is not None, "recording the single-target decision must succeed"

    # The single-target trade persists NO management plan and is tagged tm:single.
    recorded = _read_row(temp_journal, row_id)
    assert recorded["management_plan"] is None, "a single-target trade persists a NULL plan (R14.5)"
    assert recorded["status"] == "open"
    assert recorded["r_multiple"] is None
    assert recorded["exit_breakdown"] is None
    assert "tm:single" in (recorded["setup_key"] or "")

    # Target-first window: high 121 reaches the target (120) while low 99 never
    # reaches the stop (90). Legacy outcome: win at +risk_reward = 2.0.
    _patch_candles(monkeypatch, [(100.0, 121.0, 99.0, 120.0)])
    resolved = journal.score_open_trades(symbol="TEST")
    assert resolved == 1, "the single-target trade must resolve"

    scored = _read_row(temp_journal, row_id)
    assert scored["status"] == "win"
    assert scored["r_multiple"] is not None
    assert abs(scored["r_multiple"] - _EXPECTED_RR) < 1e-9
    # The legacy single-target path writes NO exit_breakdown (R14.5).
    assert scored["exit_breakdown"] is None


def test_single_target_legacy_scoring_stop_first_loss(temp_journal, monkeypatch):
    """Validates: Requirements 14.5

    A committed single-target trade whose stop is reached before the target scores
    via the unchanged legacy single-target path to a LOSS at ``-1.0`` with a NULL
    ``exit_breakdown``.
    """
    decision, _ = _decision_with_defensibility()
    row_id = journal.record_decision(decision, symbol="TEST", timeframe="1d", mode="FIND")
    assert row_id is not None

    # Stop-first window: low 85 reaches the stop (90) before the target (120).
    _patch_candles(monkeypatch, [(100.0, 101.0, 85.0, 88.0)])
    resolved = journal.score_open_trades(symbol="TEST")
    assert resolved == 1

    scored = _read_row(temp_journal, row_id)
    assert scored["status"] == "loss"
    assert scored["r_multiple"] is not None
    assert abs(scored["r_multiple"] - (-1.0)) < 1e-9
    assert scored["exit_breakdown"] is None
