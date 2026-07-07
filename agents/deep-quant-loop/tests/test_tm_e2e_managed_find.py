"""End-to-end example test for a managed FIND-mode run (task 15.1).

Feature: trade-management

This is a deterministic, offline, EXAMPLE-based end-to-end test. It stitches the
trade-management feature through the consumer layers it touches on a committed
MANAGED decision WITHOUT a live LLM or the Rust Tool_Server:

  1. ``graph.build_defensibility_record`` (via ``_management_entry``) reads the
     committed ``management_plan`` off the decision and writes the defensibility
     ``management`` entry, citing the declared scale-out legs / breakeven trigger
     / trailing rule VERBATIM and the single fixed-enumeration management style
     (Req 9.1).
  2. ``stream_events.decision_events`` (via ``build_verification_steps`` /
     ``_trade_management_step``) surfaces exactly one trade-management
     ``VERIFICATION_STEP`` (check id ``trade-management``) whose outcome is
     ``pass`` for a valid multi-leg plan (Req 10.2), ordered BEFORE the
     ``DECISION`` event of the run (Req 10.5).
  3. ``journal.derive_setup_tags`` appends exactly one fixed-position,
     low-cardinality management tag ``tm:<value>`` whose value is the declared
     plan's NON-``single`` style (Req 11.1).
  4. ``journal.record_decision`` persists the serialized ``Management_Plan`` in
     the nullable ``management_plan`` column (Req 6.3), and the persisted plan is
     re-scorable: ``journal.score_open_trades`` invokes the ``Trade_Manager``
     against a synthetic candle window (``journal._fetch_candles`` monkeypatched)
     and populates the multi-leg ``Realized_R`` (``r_multiple``) and the
     ``exit_breakdown`` (Req 4.3, the committed plan validated & scored on the
     same simulator).

Validates: Requirements 4.3, 9.1, 10.2, 10.5, 11.1, 6.3

The real LLM / Rust server is never invoked. ``build_defensibility_record`` reads
the committed plan straight off ``decision["management_plan"]`` (no tool-message
plumbing needed for the plan itself), so no get_candles message is supplied and
the defensibility entry is recorded PLAN-ONLY (legs / breakeven / trailing /
style) — never a fabricated exit. Scoring candles are mocked at
``journal._fetch_candles`` (monkeypatch) and ``journal.JOURNAL_DB_PATH`` is
pointed at a per-test ``tmp_path`` file via monkeypatch, so the temp DB is
isolated, torn down automatically, and the real ``trade_journal.db`` is never
touched. The sys.path / import pattern (service directory one level up prepended
to ``sys.path``) matches the sibling e2e tests
(``test_forecast_aligned_find_mode_e2e.py`` / ``test_journal_management.py``).
"""

import os
import sqlite3
import sys
import time

import pytest

# Make the service package importable (graph.py / stream_events.py / journal.py /
# trade_manager.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import journal  # noqa: E402
import stream_events  # noqa: E402
import trade_manager  # noqa: E402
from graph import build_defensibility_record, _coerce_management_plan  # noqa: E402
from stream_events import DECISION, VERIFICATION_STEP  # noqa: E402

TM_CHECK_ID = "trade-management"

# A managed BUY plan: entry 100, initial stop 90 (risk = 10), two scale-out legs
# (110 @ 0.5 = +1R, 120 @ 0.5 = +2R), breakeven after +1R. No trailing, so the
# resolution is fully determined by the candle window and the style collapses to
# the NON-single ``scale-be`` enumeration value.
_ENTRY = 100.0
_STOP = 90.0
_TAKE = 120.0
_PLAN_DICT = {
    "legs": [
        {"target": 110.0, "fraction": 0.5},
        {"target": 120.0, "fraction": 0.5},
    ],
    "breakeven": {"r_multiple": 1.0},
}

# Candle window that resolves the managed plan to a WIN:
#   c1: high 112 fills leg0 (+1R) and triggers breakeven (stop -> entry 100);
#   c2: high 122 fills leg1 (+2R) -> fully scaled out.
#   Realized_R = 0.5*1 + 0.5*2 = 1.5 (> 0 -> win).
_WIN_CANDLES = [
    (100.0, 112.0, 100.0, 111.0),
    (111.0, 122.0, 105.0, 120.0),
]
_EXPECTED_REALIZED_R = 1.5


# ── Temp-DB isolation ─────────────────────────────────────────────────────────
@pytest.fixture
def temp_journal(tmp_path, monkeypatch):
    """Point the journal at a per-test throwaway sqlite file.

    ``journal.JOURNAL_DB_PATH`` is read inside ``_connect()`` on every call, so a
    plain monkeypatch is enough to redirect all reads / writes to the temp file;
    ``tmp_path`` is removed by pytest after the test so the real
    ``trade_journal.db`` is never touched.
    """
    db_path = str(tmp_path / "tm_e2e.db")
    monkeypatch.setattr(journal, "JOURNAL_DB_PATH", db_path)
    return db_path


# ── Helpers ───────────────────────────────────────────────────────────────────
def _candle(o, h, lo, c, ts):
    return {"open": o, "high": h, "low": lo, "close": c, "volume": 1000.0, "timestamp_ms": ts}


def _stamp_after_now(ohlc_list):
    """Stamp an OHLC list with timestamps strictly after the current wall clock.

    The recorded trade's ``created_at`` is set at record time (before this fetch
    runs), so a base of ``(now + 1) * 1000`` guarantees ``timestamp_ms >
    created_at * 1000`` for every candle — the journal's entry-time gate.
    """
    base = int((time.time() + 1.0) * 1000.0)
    return [
        _candle(o, h, lo, c, base + (i + 1) * 60_000)
        for i, (o, h, lo, c) in enumerate(ohlc_list)
    ]


def _patch_candles(monkeypatch, ohlc_list):
    """Monkeypatch ``journal._fetch_candles`` to return a synthetic window."""

    def _fake_fetch(symbol, timeframe, limit):
        return _stamp_after_now(ohlc_list)

    monkeypatch.setattr(journal, "_fetch_candles", _fake_fetch)


def _read_row(db_path, row_id):
    """Read a recorded trade row straight from sqlite as a dict."""
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT * FROM trades WHERE id=?", (row_id,))
        row = cur.fetchone()
        assert row is not None, "the recorded row must exist"
        return {k: row[k] for k in row.keys()}
    finally:
        conn.close()


def _committed_managed_buy_decision():
    """A committed directional (BUY) MANAGED decision carrying its plan.

    The structured execution levels (entry/stop_loss/take_profit) and the
    declared ``management_plan`` are exactly what ``declare_trade`` forwards once
    the plan validates (R4.3); the defensibility builder reads both straight off
    the decision.
    """
    return {
        "action": "BUY",
        "conviction_score": 7,
        "source": "declare_trade",
        "entry": _ENTRY,
        "stop_loss": _STOP,
        "take_profit": _TAKE,
        "atr_14": 5.0,
        "management_plan": _PLAN_DICT,
    }


# ─────────────────────────────────────────────────────────────────────────────
# End-to-end: a managed FIND-mode run threads the plan through every layer.
# ─────────────────────────────────────────────────────────────────────────────
def test_managed_find_mode_run_threads_plan_through_all_layers(temp_journal, monkeypatch):
    """Validates: Requirements 4.3, 9.1, 10.2, 10.5, 11.1, 6.3

    A mocked committed MANAGED trade produces, end-to-end:
      * a defensibility ``management`` entry citing the declared legs / breakeven
        / trailing and the single fixed-enumeration style (R9.1),
      * exactly one ``pass`` trade-management VERIFICATION_STEP (check
        ``trade-management``) ordered BEFORE the ``DECISION`` event (R10.2, R10.5),
      * a ``tm:<value>`` journal setup tag whose value is the plan's non-``single``
        style (R11.1),
      * a persisted, re-scorable ``Management_Plan`` whose multi-leg ``Realized_R``
        and ``exit_breakdown`` populate when scored against a synthetic candle
        window (R6.3, R4.3).
    """
    decision = _committed_managed_buy_decision()

    # The declared plan collapses to a known NON-single management style. Derive
    # it the SAME way the defensibility builder does (coerce the declared dict via
    # the shared ``_coerce_management_plan`` merge, then map to the fixed style
    # enumeration) so the expectation is exact, not hard-coded. Scale-out legs +
    # a breakeven trigger with no trailing -> ``scale-be``.
    expected_plan = _coerce_management_plan(_PLAN_DICT, "BUY", _ENTRY, _STOP, 5.0)
    expected_style = trade_manager.management_style_tag(expected_plan)
    assert expected_style == "scale-be"
    assert expected_style != "single"

    # ── Layer 1: defensibility record cites the declared plan (R9.1) ─────────
    # No get_candles message is supplied, so the management entry is recorded
    # PLAN-ONLY (legs / breakeven / trailing / style) — never a fabricated exit.
    record = build_defensibility_record([], decision, mode="FIND")
    management = record["management"]
    assert management["available"] is True
    assert management["action"] == "BUY"
    assert management["entry"] == _ENTRY
    assert management["initial_stop"] == _STOP
    # The declared scale-out legs are cited VERBATIM (same count, target, fraction).
    assert management["legs"] == _PLAN_DICT["legs"]
    # The declared breakeven trigger is cited (r_multiple set, price None).
    assert management["breakeven"]["r_multiple"] == 1.0
    assert management["breakeven"]["price"] is None
    # No trailing rule was declared -> none recorded (no fabrication).
    assert management["trailing"] is None
    # The single fixed-enumeration management style for the committed plan.
    assert management["style"] == expected_style
    # Plan-only: with no candles in scope the helper records no simulated exit.
    assert "exit_breakdown" not in management
    assert "realized_r" not in management

    # Attach the record to the committed decision (as the live loop does).
    decision["defensibility"] = record

    # ── Layer 2: decision_events emits a pass step before DECISION (R10.2, R10.5) ─
    events = list(stream_events.decision_events(decision))
    event_names = [name for name, _ in events]

    tm_steps = [
        (i, payload)
        for i, (name, payload) in enumerate(events)
        if name == VERIFICATION_STEP and payload.get("check") == TM_CHECK_ID
    ]
    assert len(tm_steps) == 1, f"expected exactly one trade-management step, got {len(tm_steps)}"
    tm_step_index, tm_payload = tm_steps[0]
    # A valid multi-leg plan reports a ``pass`` outcome (R10.2).
    assert tm_payload["outcome"].split()[0] == "pass"
    assert expected_style in tm_payload["outcome"]

    # The trade-management step precedes the DECISION event (R10.5).
    assert DECISION in event_names, "the run must emit a DECISION event"
    decision_index = event_names.index(DECISION)
    assert tm_step_index < decision_index

    # The committed decision is surfaced unchanged (BUY) — management is a
    # defensibility / verification surface, never a gate.
    assert events[decision_index][1]["action"] == "BUY"

    # ── Layer 3: journal setup fingerprint carries the tm tag (R11.1) ────────
    tags = journal.derive_setup_tags(decision)
    tm_tags = [t for t in tags if t.startswith("tm:")]
    assert len(tm_tags) == 1, "exactly one management-style tag"
    assert tm_tags[0] == f"tm:{expected_style}"
    # The tm tag sits at its fixed position immediately after the ``fc:`` tag and
    # is NON-single. The session dimension and the multi-agent-debate ``db:`` tag
    # are appended after it, so the ``db:`` tag is the final tag.
    tm_index = tags.index(tm_tags[0])
    assert tags[tm_index - 1].startswith("fc:")
    assert tags[-1].startswith("evt:")  # evt: is the final dimension (earnings-event-risk-gate R10.1)
    assert tags[-2].startswith("tier:")  # tier: is now second-to-last (opportunity engine R9.2)
    assert tm_tags[0] != "tm:single"

    # ── Layer 4: the plan persists and is re-scorable (R6.3, R4.3) ───────────
    row_id = journal.record_decision(
        decision, symbol="TEST", timeframe="1d", mode="FIND",
        management_plan=_PLAN_DICT,
    )
    assert row_id is not None, "recording the managed decision must succeed"

    recorded = _read_row(temp_journal, row_id)
    # The serialized Management_Plan persists (non-NULL) so re-scoring is
    # reproducible (R6.3); the trade is open and unscored before scoring runs.
    assert recorded["management_plan"] is not None
    assert recorded["status"] == "open"
    assert recorded["r_multiple"] is None
    assert recorded["exit_breakdown"] is None
    # The persisted fingerprint carries the same management-style tag.
    assert f"tm:{expected_style}" in (recorded["setup_key"] or "")

    # Score the persisted plan against a synthetic window via the Trade_Manager
    # (R4.3 — the committed plan validated & scored on the same simulator).
    _patch_candles(monkeypatch, _WIN_CANDLES)
    resolved = journal.score_open_trades(symbol="TEST")
    assert resolved == 1, "the managed trade must resolve"

    scored = _read_row(temp_journal, row_id)
    # The multi-leg Realized_R and the Exit_Breakdown both populate.
    assert scored["status"] == "win"
    assert scored["r_multiple"] is not None
    assert abs(scored["r_multiple"] - _EXPECTED_REALIZED_R) < 1e-6
    assert scored["exit_breakdown"] is not None
    assert "fills" in scored["exit_breakdown"]
