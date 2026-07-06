"""Unit tests for journal multi-leg delegation and aggregation (journal.py, task 9.4).

Feature: trade-management

These example-based unit tests exercise the journal's trade-management wiring
added in tasks 9.1 / 9.2 against a TEMP sqlite DB (so the real
``trade_journal.db`` is never touched). They cover, per the task:

  1. A MANAGED trade round-trips through the journal: ``record_decision`` persists
     the serialized ``Management_Plan`` (the ``management_plan`` column is
     non-NULL), and after scoring — with ``journal._fetch_candles`` monkeypatched
     to return a synthetic candle window that resolves the plan — the
     ``r_multiple`` (Realized_R) and ``exit_breakdown`` columns are populated and
     the status is ``win`` / ``loss`` per a positive / non-positive Realized_R
     (Requirements 6.1, 6.2).

  2. A SINGLE_TARGET trade (no ``management_plan``) scored over the same candles
     yields the SAME r_multiple / status the legacy single-target scorer produced:
     a target-first fill -> ``win`` at ``+risk_reward``; a stop-first fill ->
     ``loss`` at ``-1.0`` (Requirement 6.2, the non-breaking parity guarantee).

  3. Per-``tm:`` grouping and the low-sample / weak-prior flag: recording several
     managed trades whose defensibility management style differs makes
     ``derive_setup_tags`` append distinct ``tm:<value>`` tags, so ``get_stats``
     ``by_setup`` groups them by the management-extended ``setup_key`` and flags
     each thin group (< ``LOW_SAMPLE_THRESHOLD``) as a weak prior (Requirements
     6.4, 11.4).

Scoring candles are mocked at ``journal._fetch_candles`` (monkeypatch) so no Rust
Tool_Server is needed, and every candle window is stamped strictly AFTER the
trade's ``created_at`` (the journal only scores candles with
``timestamp_ms > created_at * 1000``). Each test points ``journal.JOURNAL_DB_PATH``
at a per-test ``tmp_path`` file via monkeypatch, so the temp DB is isolated and
torn down automatically and the real journal is never read or written.

The sys.path / import pattern mirrors the sibling journal tests
(``test_forecast_probability_persistence_properties.py``).
"""

import os
import sqlite3
import sys
import time

import pytest

# Make the service package importable (journal.py / trade_manager.py live one level up).
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import journal  # noqa: E402
import trade_manager  # noqa: E402


# ── Temp-DB isolation ─────────────────────────────────────────────────────────
@pytest.fixture
def temp_journal(tmp_path, monkeypatch):
    """Point the journal at a per-test throwaway sqlite file.

    ``journal.JOURNAL_DB_PATH`` is read inside ``_connect()`` on every call, so a
    plain ``setattr`` is enough to redirect all reads / writes to the temp file;
    ``tmp_path`` is removed by pytest after the test, so the real
    ``trade_journal.db`` is never touched.
    """
    db_path = str(tmp_path / "j.db")
    monkeypatch.setattr(journal, "JOURNAL_DB_PATH", db_path)
    return db_path


# ── Helpers ───────────────────────────────────────────────────────────────────
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


def _candle(o, h, lo, c, ts):
    return {"open": o, "high": h, "low": lo, "close": c, "volume": 1000.0, "timestamp_ms": ts}


def _stamp_after_now(ohlc_list):
    """Stamp an OHLC list with timestamps strictly after the current wall clock.

    Every recorded trade's ``created_at`` is set to ``time.time()`` AT record
    time, which is before this fetch runs, so a base of ``(now + 1) * 1000``
    guarantees ``timestamp_ms > created_at * 1000`` for every candle — the
    journal's entry-time gate (and the legacy ``ts <= created_ms`` exclusion).
    """
    base = int((time.time() + 1.0) * 1000.0)
    return [
        _candle(o, h, lo, c, base + (i + 1) * 60_000)
        for i, (o, h, lo, c) in enumerate(ohlc_list)
    ]


def _patch_candles(monkeypatch, ohlc_list):
    """Monkeypatch ``journal._fetch_candles`` to return a synthetic window.

    Timestamps are computed at call time (strictly after the trade's
    ``created_at``) so the same fixture resolves any trade recorded just before
    scoring, regardless of how the system clock advances between record & score.
    """
    def _fake_fetch(symbol, timeframe, limit):
        return _stamp_after_now(ohlc_list)

    monkeypatch.setattr(journal, "_fetch_candles", _fake_fetch)


# A managed BUY plan: entry 100, initial stop 90 (risk = 10), two scale-out legs
# (110 @ 0.5 = +1R, 120 @ 0.5 = +2R), breakeven after +1R. No trailing, so the
# resolution is fully determined by the candle window.
_MANAGED_ENTRY = 100.0
_MANAGED_STOP = 90.0
_MANAGED_PLAN_DICT = {
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
_MANAGED_WIN_CANDLES = [
    (100.0, 112.0, 100.0, 111.0),
    (111.0, 122.0, 105.0, 120.0),
]

# Candle window that resolves the managed plan to a LOSS: the first candle's low
# (85) reaches the initial stop (90) before any target, closing the whole
# position at -1R. Realized_R = -1.0 (<= 0 -> loss).
_MANAGED_LOSS_CANDLES = [
    (100.0, 101.0, 85.0, 88.0),
]


def _managed_decision(style="scale-be"):
    """A committed managed BUY decision carrying a defensibility management entry.

    ``defensibility.management.style`` drives the ``tm:<style>`` fingerprint tag
    (``derive_setup_tags`` reads the style from the defensibility record), while
    the ``management_plan`` argument to ``record_decision`` drives the multi-leg
    scoring. Both are supplied so the recorded trade is coherent: a scoreable
    managed BUY tagged with ``style``.
    """
    return {
        "action": "BUY",
        "entry": _MANAGED_ENTRY,
        "stop_loss": _MANAGED_STOP,
        "take_profit": 120.0,
        "atr_14": 5.0,
        "defensibility": {"management": {"available": True, "style": style}},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. A managed trade persists its plan and scores multi-leg via the Trade_Manager
# ─────────────────────────────────────────────────────────────────────────────
def test_managed_trade_persists_plan_and_scores_win(temp_journal, monkeypatch):
    """A managed trade persists a non-NULL plan and scores to a WIN with a
    populated Realized_R + Exit_Breakdown.

    Validates: Requirements 6.1, 6.2
    """
    row_id = journal.record_decision(
        _managed_decision(), symbol="TEST", timeframe="1d", mode="FIND",
        management_plan=_MANAGED_PLAN_DICT,
    )
    assert row_id is not None, "recording the managed decision must succeed"

    # The serialized Management_Plan persists (non-NULL) and is open for scoring.
    recorded = _read_row(temp_journal, row_id)
    assert recorded["management_plan"] is not None, "the managed plan must persist (R6.3)"
    assert recorded["status"] == "open"
    assert recorded["r_multiple"] is None
    assert recorded["exit_breakdown"] is None
    # The fingerprint carries the management style tag.
    assert "tm:scale-be" in (recorded["setup_key"] or "")

    # Score against a synthetic window that resolves the plan to a win.
    _patch_candles(monkeypatch, _MANAGED_WIN_CANDLES)
    resolved = journal.score_open_trades(symbol="TEST")
    assert resolved == 1, "the managed trade must resolve"

    scored = _read_row(temp_journal, row_id)
    # Realized_R is populated, positive -> win (R6.1, R6.4).
    assert scored["status"] == "win"
    assert scored["r_multiple"] is not None
    assert scored["r_multiple"] > 0.0
    assert abs(scored["r_multiple"] - 1.5) < 1e-6
    # The Exit_Breakdown representation is persisted alongside Realized_R (R6.1).
    assert scored["exit_breakdown"] is not None
    assert "fills" in scored["exit_breakdown"]


def test_managed_trade_scores_loss_on_stop_first(temp_journal, monkeypatch):
    """A managed trade whose stop is reached before any target scores to a LOSS
    with a non-positive Realized_R.

    Validates: Requirements 6.1, 6.4
    """
    row_id = journal.record_decision(
        _managed_decision(), symbol="TEST", timeframe="1d", mode="FIND",
        management_plan=_MANAGED_PLAN_DICT,
    )
    assert row_id is not None

    _patch_candles(monkeypatch, _MANAGED_LOSS_CANDLES)
    resolved = journal.score_open_trades(symbol="TEST")
    assert resolved == 1

    scored = _read_row(temp_journal, row_id)
    assert scored["status"] == "loss"
    assert scored["r_multiple"] is not None
    assert scored["r_multiple"] <= 0.0
    assert abs(scored["r_multiple"] - (-1.0)) < 1e-6
    assert scored["exit_breakdown"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# 2. A single-target trade scores with EXACT legacy parity
# ─────────────────────────────────────────────────────────────────────────────
def _single_target_decision():
    """A committed single-target BUY: entry 100, stop 90 (risk 10), target 110
    (risk_reward = 1.0). No ``management_plan`` -> NULL plan column -> legacy path.
    """
    return {
        "action": "BUY",
        "entry": 100.0,
        "stop_loss": 90.0,
        "take_profit": 110.0,
        "defensibility": {},
    }


def test_single_target_parity_target_first_win(temp_journal, monkeypatch):
    """A single-target trade with a target-first fill scores exactly as the
    legacy scorer: ``win`` at ``+risk_reward`` (= +1.0 here).

    Validates: Requirements 6.2
    """
    row_id = journal.record_decision(
        _single_target_decision(), symbol="TEST", timeframe="1d", mode="FIND",
    )
    assert row_id is not None

    # A single-target trade persists NO management plan (NULL) and is tagged single.
    recorded = _read_row(temp_journal, row_id)
    assert recorded["management_plan"] is None, "a single-target trade persists a NULL plan"
    assert "tm:unknown" in (recorded["setup_key"] or "")

    # Target-first window: high 111 reaches the target (110); low 99 never reaches
    # the stop (90). Legacy outcome: win at +risk_reward = (110-100)/10 = 1.0.
    _patch_candles(monkeypatch, [(100.0, 111.0, 99.0, 110.0)])
    resolved = journal.score_open_trades(symbol="TEST")
    assert resolved == 1

    scored = _read_row(temp_journal, row_id)
    assert scored["status"] == "win"
    assert abs(scored["r_multiple"] - 1.0) < 1e-9
    # The legacy single-target path writes NO exit_breakdown.
    assert scored["exit_breakdown"] is None


def test_single_target_parity_stop_first_loss(temp_journal, monkeypatch):
    """A single-target trade with a stop-first fill scores exactly as the legacy
    scorer: ``loss`` at ``-1.0``.

    Validates: Requirements 6.2
    """
    row_id = journal.record_decision(
        _single_target_decision(), symbol="TEST", timeframe="1d", mode="FIND",
    )
    assert row_id is not None

    # Stop-first window: low 85 reaches the stop (90) before the target (110).
    # Legacy outcome: loss at -1.0.
    _patch_candles(monkeypatch, [(100.0, 101.0, 85.0, 88.0)])
    resolved = journal.score_open_trades(symbol="TEST")
    assert resolved == 1

    scored = _read_row(temp_journal, row_id)
    assert scored["status"] == "loss"
    assert abs(scored["r_multiple"] - (-1.0)) < 1e-9
    assert scored["exit_breakdown"] is None


def test_single_target_parity_matches_legacy_scorer(temp_journal):
    """The single-target path is the EXACT legacy ``_score_one`` math: a managed
    plan column being NULL routes through the unchanged single-target branch.

    Builds a synthetic single-target row and a candle window directly and asserts
    ``journal._score_one`` reproduces the legacy outcome (target-first -> +rr,
    stop-first -> -1.0), proving parity at the scorer boundary.

    Validates: Requirements 6.2
    """
    created_ms = int(time.time() * 1000.0)

    def _row(action, entry, sl, tp):
        return {
            "id": 1, "action": action, "entry": entry, "stop_loss": sl,
            "take_profit": tp, "created_at": created_ms / 1000.0,
            "management_plan": None,
        }

    win_candles = [_candle(100.0, 111.0, 99.0, 110.0, created_ms + 60_000)]
    loss_candles = [_candle(100.0, 101.0, 85.0, 88.0, created_ms + 60_000)]

    win = journal._score_one(_row("BUY", 100.0, 90.0, 110.0), win_candles)
    assert win["status"] == "win"
    assert abs(win["r_multiple"] - 1.0) < 1e-9

    loss = journal._score_one(_row("BUY", 100.0, 90.0, 110.0), loss_candles)
    assert loss["status"] == "loss"
    assert abs(loss["r_multiple"] - (-1.0)) < 1e-9


# ─────────────────────────────────────────────────────────────────────────────
# 3. Per-``tm:`` grouping and the low-sample / weak-prior flag
# ─────────────────────────────────────────────────────────────────────────────
def test_get_stats_groups_by_management_extended_setup_key_and_flags_low_sample(
    temp_journal, monkeypatch
):
    """``get_stats`` groups scored trades by the management-extended ``setup_key``
    (each carrying its ``tm:<value>`` tag) and flags every thin group as a weak
    prior (``low_sample`` True below ``LOW_SAMPLE_THRESHOLD``).

    Records 3 ``scale-be`` and 2 ``scale-be-trail`` managed trades (identical
    otherwise) so the only fingerprint difference is the management-style tag,
    scores them all to wins via the shared candle window, then asserts two
    distinct ``by_setup`` groups partitioned by ``tm:`` with the expected counts
    and the weak-prior flag set.

    Validates: Requirements 6.4, 11.4
    """
    styles = ["scale-be", "scale-be", "scale-be", "scale-be-trail", "scale-be-trail"]
    for style in styles:
        rid = journal.record_decision(
            _managed_decision(style=style), symbol="TEST", timeframe="1d", mode="FIND",
            management_plan=_MANAGED_PLAN_DICT,
        )
        assert rid is not None

    # Score every open managed trade to a win via the shared resolving window.
    _patch_candles(monkeypatch, _MANAGED_WIN_CANDLES)

    stats = journal.get_stats(symbol="TEST")

    by_setup = stats["by_setup"]
    # Exactly two groups, partitioned by the management-style tag.
    keys = {b["setup_key"] for b in by_setup}
    assert any("tm:scale-be-trail" in k for k in keys)
    assert any(("tm:scale-be" in k and "tm:scale-be-trail" not in k) for k in keys)

    by_key = {b["setup_key"]: b for b in by_setup}
    scale_be = next(b for k, b in by_key.items() if "tm:scale-be" in k and "tm:scale-be-trail" not in k)
    scale_be_trail = next(b for k, b in by_key.items() if "tm:scale-be-trail" in k)

    # Grouped by the management-extended setup_key: 3 vs 2 scored wins (R6.4).
    assert scale_be["wins"] == 3
    assert scale_be["trades_scored"] == 3
    assert scale_be_trail["wins"] == 2
    assert scale_be_trail["trades_scored"] == 2

    # Win-rate / expectancy are computed from the multi-leg Realized_R (all wins).
    assert scale_be["win_rate"] == 1.0
    assert scale_be["expectancy_r"] is not None and scale_be["expectancy_r"] > 0.0

    # Each thin group is flagged a weak prior (below LOW_SAMPLE_THRESHOLD) (R11.4).
    assert scale_be["low_sample"] is True
    assert scale_be_trail["low_sample"] is True
    assert stats["low_sample_threshold"] == journal.LOW_SAMPLE_THRESHOLD
