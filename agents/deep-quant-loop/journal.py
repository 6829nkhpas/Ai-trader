"""Trade_Journal — measurement & feedback loop for the Deep Quant agent.

Phase 2 of the trading-edge roadmap: the agent had NO memory of its own past
trades and never found out whether it was right. Without that feedback every
"improvement" is a guess. This module closes the loop:

  1. RECORD   — every committed decision (BUY/SELL/HOLD) is persisted with the
                setup context that produced it (a coarse "setup fingerprint").
  2. SCORE    — open BUY/SELL trades are scored lazily against subsequent candle
                data: did price reach the take-profit or the stop-loss first?
  3. AGGREGATE— realized win-rate and expectancy (in R multiples) are computed
                overall AND per setup type, so the agent can see which kinds of
                setups actually make money and calibrate its conviction.

Design choices (kept consistent with the rest of the deep-quant-loop):
  * Pure-stdlib persistence (sqlite3) so no new dependency / infra is required.
  * Candles for scoring are fetched from the SAME authoritative Rust Tool_Server
    endpoint every tool uses, so scoring prices match the system's price source.
  * Every public function is defensive: it NEVER raises into the agent loop. A
    journal/scoring failure degrades to "no stats" rather than aborting a run.

The store path is configurable via ``JOURNAL_DB_PATH`` (defaults to
``trade_journal.db`` beside this file).
"""

from __future__ import annotations

import os
import json
import math
import sqlite3
import time
from typing import Optional

import httpx

RUST_SERVER_URL = "http://localhost:8084"

# ── Configuration ─────────────────────────────────────────────────────────────
_DEFAULT_DB = os.path.join(os.path.abspath(os.path.dirname(__file__)), "trade_journal.db")
JOURNAL_DB_PATH = os.getenv("JOURNAL_DB_PATH", _DEFAULT_DB)

# An open BUY/SELL that has neither hit its target nor its stop after this many
# seconds of available candle data is marked ``expired`` (excluded from win-rate
# / expectancy). Default 7 days.
JOURNAL_EXPIRY_SECONDS = float(os.getenv("JOURNAL_EXPIRY_SECONDS", str(7 * 24 * 3600)))

# How many recent candles to pull when scoring a symbol/timeframe.
JOURNAL_SCORING_CANDLE_LIMIT = int(os.getenv("JOURNAL_SCORING_CANDLE_LIMIT", "1000"))

# Below this many scored (win+loss) trades, stats are flagged as a weak prior so
# the agent does not over-fit to a tiny sample.
LOW_SAMPLE_THRESHOLD = int(os.getenv("JOURNAL_LOW_SAMPLE_THRESHOLD", "10"))


def _now() -> float:
    return time.time()


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ── Schema ────────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(JOURNAL_DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at    REAL NOT NULL,
            mode          TEXT,
            symbol        TEXT,
            timeframe     TEXT,
            action        TEXT NOT NULL,
            entry         REAL,
            stop_loss     REAL,
            take_profit   REAL,
            atr_14        REAL,
            conviction    INTEGER,
            risk_reward   REAL,
            setup_key     TEXT,
            setup_tags    TEXT,
            source        TEXT,
            status        TEXT NOT NULL,       -- open | win | loss | expired | hold
            outcome_price REAL,
            outcome_at    REAL,
            r_multiple    REAL,
            scored_at     REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
    conn.commit()


# ── Setup fingerprinting ──────────────────────────────────────────────────────

def derive_setup_tags(decision: dict) -> list:
    """Derive a coarse, groupable setup fingerprint from a committed decision.

    The tags are intentionally low-cardinality so that meaningfully many trades
    share a ``setup_key`` and per-setup statistics become significant. They are
    read from the defensibility record assembled by the graph (which cites only
    real tool results), so the fingerprint reflects the actual evidence:
      * direction   (BUY / SELL / HOLD)
      * macro       (aligned / against / neutral / unknown) vs the 1D trend
      * predictive  (aligned / conflict / na) vs the forward projection
      * value-area  (above / inside / below / unknown) from the volume profile
    """
    d = decision or {}
    deff = d.get("defensibility") or {}
    action = str(d.get("action") or "HOLD").upper()
    tags = [f"dir:{action}"]

    mc = str(deff.get("macro_trend_conflict") or "").lower()
    if "macro conflict" in mc:
        tags.append("macro:against")
    elif "aligned with the 1d" in mc:
        tags.append("macro:aligned")
    elif "unavailable" in mc:
        tags.append("macro:unknown")
    else:
        tags.append("macro:neutral")

    pc = str(deff.get("predictive_conflict") or "").lower()
    # Check the aligned phrase first: the conflict message starts with
    # "CONFLICT:" while the aligned message is "No predictive conflict: ...
    # aligns with trade bias" — which also contains "conflict:", so order matters.
    if "no predictive conflict" in pc or "aligns with trade bias" in pc:
        tags.append("pred:aligned")
    elif pc.startswith("conflict") or "conflict:" in pc:
        tags.append("pred:conflict")
    else:
        tags.append("pred:na")

    vp = deff.get("volume_profile") or {}
    loc = vp.get("price_vs_value_area")
    if loc in ("above_value_area", "inside_value_area", "below_value_area"):
        tags.append("va:" + loc.split("_")[0])
    else:
        tags.append("va:unknown")

    return tags


def setup_key_from_tags(tags) -> str:
    return "|".join(tags) if tags else "unknown"


# ── Recording ─────────────────────────────────────────────────────────────────

def record_decision(
    decision: dict,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    mode: Optional[str] = None,
) -> Optional[int]:
    """Persist a committed decision to the journal. Never raises into the loop.

    BUY/SELL with finite entry/stop/target are stored as ``open`` (scoreable);
    everything else (HOLD, or a directional trade missing levels) is stored as
    ``hold`` and excluded from win-rate/expectancy. Returns the row id, or None
    on failure.
    """
    try:
        d = decision or {}
        action = str(d.get("action") or "HOLD").upper()
        deff = d.get("defensibility") or {}

        entry = d.get("entry")
        stop_loss = d.get("stop_loss")
        take_profit = d.get("take_profit")
        # Fall back to levels parsed into the defensibility record when the
        # structured args were not all present on declare_trade.
        if not (_is_num(entry) and _is_num(stop_loss) and _is_num(take_profit)):
            lv = deff.get("levels") or {}
            entry = lv.get("entry") if _is_num(lv.get("entry")) else entry
            stop_loss = lv.get("stop_loss") if _is_num(lv.get("stop_loss")) else stop_loss
            take_profit = lv.get("take_profit") if _is_num(lv.get("take_profit")) else take_profit

        scoreable = action in ("BUY", "SELL") and _is_num(entry) and _is_num(stop_loss) and _is_num(take_profit)
        status = "open" if scoreable else "hold"

        tags = derive_setup_tags(decision)
        key = setup_key_from_tags(tags)

        rr = deff.get("risk_reward")
        if not _is_num(rr) and scoreable:
            risk = abs(entry - stop_loss)
            rr = abs(take_profit - entry) / risk if risk > 0 else None

        conv = d.get("conviction_score")
        try:
            conv = int(conv) if conv is not None else None
        except (TypeError, ValueError):
            conv = None

        conn = _connect()
        try:
            _init_db(conn)
            cur = conn.execute(
                """
                INSERT INTO trades (
                    created_at, mode, symbol, timeframe, action, entry, stop_loss,
                    take_profit, atr_14, conviction, risk_reward, setup_key,
                    setup_tags, source, status, outcome_price, outcome_at,
                    r_multiple, scored_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    _now(), (mode or "FIND"), symbol, timeframe, action,
                    entry if _is_num(entry) else None,
                    stop_loss if _is_num(stop_loss) else None,
                    take_profit if _is_num(take_profit) else None,
                    d.get("atr_14") if _is_num(d.get("atr_14")) else None,
                    conv,
                    rr if _is_num(rr) else None,
                    key, json.dumps(tags), d.get("source"),
                    status, None, None, None, None,
                ),
            )
            conn.commit()
            row_id = cur.lastrowid
            print(f"[Trade_Journal] Recorded {action} {symbol}/{timeframe} as '{status}' (setup={key}, id={row_id}).")
            return row_id
        finally:
            conn.close()
    except Exception as e:
        print(f"[Trade_Journal] WARN: failed to record decision: {e}")
        return None


# ── Scoring ───────────────────────────────────────────────────────────────────

def _fetch_candles(symbol: str, timeframe: str, limit: int) -> list:
    try:
        r = httpx.post(
            f"{RUST_SERVER_URL}/tools/get_candles",
            json={"symbol": symbol, "timeframe": timeframe, "limit": limit},
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[Trade_Journal] WARN: candle fetch for scoring failed ({symbol}/{timeframe}): {e}")
        return []


def _score_one(trade: sqlite3.Row, candles: list) -> Optional[dict]:
    """Score a single open trade against candles. Returns an update dict or None.

    Conservative fill model: the position is assumed entered at the declared
    ``entry`` at ``created_at``; only candles strictly after that timestamp are
    considered. The first candle whose range touches a level decides the outcome;
    if a single candle touches BOTH the stop and the target, the loss is assumed
    (worst-case) so the journal never flatters itself.
    """
    action = str(trade["action"]).upper()
    entry = trade["entry"]
    sl = trade["stop_loss"]
    tp = trade["take_profit"]
    created_at = trade["created_at"]
    if not (_is_num(entry) and _is_num(sl) and _is_num(tp) and _is_num(created_at)):
        return None

    created_ms = created_at * 1000.0
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    rr = abs(tp - entry) / risk

    last_ts_ms = None
    for c in candles:
        if not isinstance(c, dict):
            continue
        ts = c.get("timestamp_ms")
        hi = c.get("high")
        lo = c.get("low")
        if not (_is_num(ts) and _is_num(hi) and _is_num(lo)):
            continue
        if ts <= created_ms:
            continue
        last_ts_ms = ts
        hit_tp = hi >= tp if action == "BUY" else lo <= tp
        hit_sl = lo <= sl if action == "BUY" else hi >= sl
        if hit_sl and hit_tp:
            return {"status": "loss", "outcome_price": sl, "outcome_at": ts / 1000.0, "r_multiple": -1.0}
        if hit_tp:
            return {"status": "win", "outcome_price": tp, "outcome_at": ts / 1000.0, "r_multiple": round(rr, 4)}
        if hit_sl:
            return {"status": "loss", "outcome_price": sl, "outcome_at": ts / 1000.0, "r_multiple": -1.0}

    # Neither level reached within available candles. Expire only if enough real
    # time has elapsed since entry (so a still-developing trade stays "open").
    if last_ts_ms is not None and (_now() - created_at) > JOURNAL_EXPIRY_SECONDS:
        return {"status": "expired", "outcome_price": None, "outcome_at": last_ts_ms / 1000.0, "r_multiple": None}
    return None


def score_open_trades(symbol: Optional[str] = None) -> int:
    """Lazily score open BUY/SELL trades. Returns the number newly resolved.

    Groups open trades by (symbol, timeframe), fetches candles once per group,
    and resolves each trade it can. Never raises into the agent loop.
    """
    resolved = 0
    try:
        conn = _connect()
        try:
            _init_db(conn)
            if symbol:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE status='open' AND symbol=?", (symbol,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM trades WHERE status='open'").fetchall()

            # Group by (symbol, timeframe) to fetch candles once per group.
            groups: dict = {}
            for r in rows:
                groups.setdefault((r["symbol"], r["timeframe"]), []).append(r)

            for (sym, tf), trades in groups.items():
                if not sym or not tf:
                    continue
                candles = _fetch_candles(sym, tf, JOURNAL_SCORING_CANDLE_LIMIT)
                if not candles:
                    continue
                for tr in trades:
                    upd = _score_one(tr, candles)
                    if upd is None:
                        continue
                    conn.execute(
                        "UPDATE trades SET status=?, outcome_price=?, outcome_at=?, r_multiple=?, scored_at=? WHERE id=?",
                        (upd["status"], upd["outcome_price"], upd["outcome_at"], upd["r_multiple"], _now(), tr["id"]),
                    )
                    resolved += 1
            conn.commit()
            if resolved:
                print(f"[Trade_Journal] Scored {resolved} open trade(s).")
        finally:
            conn.close()
    except Exception as e:
        print(f"[Trade_Journal] WARN: scoring failed: {e}")
    return resolved


# ── Aggregation ───────────────────────────────────────────────────────────────

def _aggregate(rows) -> dict:
    """Aggregate a set of trade rows into win-rate / expectancy stats."""
    wins = sum(1 for r in rows if r["status"] == "win")
    losses = sum(1 for r in rows if r["status"] == "loss")
    open_n = sum(1 for r in rows if r["status"] == "open")
    expired = sum(1 for r in rows if r["status"] == "expired")
    scored = wins + losses
    win_rate = round(wins / scored, 4) if scored else None
    r_vals = [r["r_multiple"] for r in rows if r["status"] in ("win", "loss") and _is_num(r["r_multiple"])]
    expectancy_r = round(sum(r_vals) / len(r_vals), 4) if r_vals else None
    return {
        "trades_scored": scored,
        "wins": wins,
        "losses": losses,
        "open": open_n,
        "expired": expired,
        "win_rate": win_rate,
        "expectancy_r": expectancy_r,
    }


def get_stats(symbol: Optional[str] = None, setup_key: Optional[str] = None, source: Optional[str] = None) -> dict:
    """Return realized performance stats overall and broken down by setup type.

    Scores any pending open trades first so the numbers are current. Optionally
    filter by ``symbol`` and/or ``source`` ("live" decisions vs "backtest" seed).
    Never raises — on any failure it returns a stats object flagged unavailable.
    """
    try:
        score_open_trades(symbol)
        conn = _connect()
        try:
            _init_db(conn)
            clauses = []
            params: list = []
            if symbol:
                clauses.append("symbol=?")
                params.append(symbol)
            if source:
                clauses.append("source=?")
                params.append(source)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(f"SELECT * FROM trades{where}", tuple(params)).fetchall()

            overall = _aggregate(rows)
            overall["symbol"] = symbol or "ALL"

            # Per-setup breakdown (directional setups only — HOLDs carry no edge).
            by_setup_map: dict = {}
            for r in rows:
                if str(r["action"]).upper() not in ("BUY", "SELL"):
                    continue
                by_setup_map.setdefault(r["setup_key"] or "unknown", []).append(r)

            by_setup = []
            for key, grp in by_setup_map.items():
                agg = _aggregate(grp)
                agg["setup_key"] = key
                by_setup.append(agg)
            # Most-traded setups first.
            by_setup.sort(key=lambda a: (a["trades_scored"], a["wins"] + a["losses"] + a["open"]), reverse=True)

            result = {
                "overall": overall,
                "by_setup": by_setup,
                "low_sample": (overall["trades_scored"] < LOW_SAMPLE_THRESHOLD),
                "low_sample_threshold": LOW_SAMPLE_THRESHOLD,
            }
            if setup_key is not None:
                match = next((b for b in by_setup if b["setup_key"] == setup_key), None)
                result["setup_match"] = match
            return result
        finally:
            conn.close()
    except Exception as e:
        print(f"[Trade_Journal] WARN: get_stats failed: {e}")
        return {
            "overall": {"trades_scored": 0, "win_rate": None, "expectancy_r": None},
            "by_setup": [],
            "low_sample": True,
            "unavailable": True,
            "error": str(e),
        }


# ── Backtest seeding (Phase 2.5) ──────────────────────────────────────────────
# The backtest seeder (backtest.py) replays historical candles through
# deterministic rule-based setups and inserts the ALREADY-RESOLVED outcomes here
# with source='backtest', so the agent has a per-setup-type prior on day one
# instead of waiting for live trades to accumulate. These rows are tagged with
# the SAME setup fingerprint as live decisions, so get_stats groups them
# identically — but the distinct source lets us separate / purge them later.

def record_backtest_trade(
    decision: dict,
    symbol: str,
    timeframe: str,
    status: str,
    outcome_price: Optional[float],
    outcome_at: Optional[float],
    r_multiple: Optional[float],
) -> Optional[int]:
    """Insert an already-scored backtest trade (source='backtest'). Never raises."""
    try:
        d = decision or {}
        action = str(d.get("action") or "HOLD").upper()
        deff = d.get("defensibility") or {}
        entry = d.get("entry")
        stop_loss = d.get("stop_loss")
        take_profit = d.get("take_profit")
        tags = derive_setup_tags(decision)
        key = setup_key_from_tags(tags)
        rr = deff.get("risk_reward")
        if not _is_num(rr) and _is_num(entry) and _is_num(stop_loss) and _is_num(take_profit):
            risk = abs(entry - stop_loss)
            rr = abs(take_profit - entry) / risk if risk > 0 else None

        conn = _connect()
        try:
            _init_db(conn)
            cur = conn.execute(
                """
                INSERT INTO trades (
                    created_at, mode, symbol, timeframe, action, entry, stop_loss,
                    take_profit, atr_14, conviction, risk_reward, setup_key,
                    setup_tags, source, status, outcome_price, outcome_at,
                    r_multiple, scored_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    d.get("created_at") or _now(), "BACKTEST", symbol, timeframe, action,
                    entry if _is_num(entry) else None,
                    stop_loss if _is_num(stop_loss) else None,
                    take_profit if _is_num(take_profit) else None,
                    d.get("atr_14") if _is_num(d.get("atr_14")) else None,
                    d.get("conviction_score"),
                    rr if _is_num(rr) else None,
                    key, json.dumps(tags), "backtest",
                    status, outcome_price, outcome_at, r_multiple, _now(),
                ),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()
    except Exception as e:
        print(f"[Trade_Journal] WARN: failed to record backtest trade: {e}")
        return None


def purge(source: Optional[str] = None, symbol: Optional[str] = None) -> int:
    """Delete trades matching ``source`` and/or ``symbol``. Returns rows deleted.

    Used by the seeder to make re-seeding idempotent (purge source='backtest'
    before re-inserting). Never raises into the caller.
    """
    try:
        conn = _connect()
        try:
            _init_db(conn)
            clauses = []
            params: list = []
            if source:
                clauses.append("source=?")
                params.append(source)
            if symbol:
                clauses.append("symbol=?")
                params.append(symbol)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            cur = conn.execute(f"DELETE FROM trades{where}", tuple(params))
            conn.commit()
            return cur.rowcount or 0
        finally:
            conn.close()
    except Exception as e:
        print(f"[Trade_Journal] WARN: purge failed: {e}")
        return 0
