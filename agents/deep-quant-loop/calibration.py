"""Conviction calibration — does the Judge's conviction predict outcomes?

Validation for the Multi-Agent Bull/Bear Debate is explicitly different from the
other deep-quant features: a multi-agent LLM debate CANNOT be replayed on the
historical candle archive, so there is no historical backtest comparison. Its
validity is instead proven by **live calibration** (R10.5): the Judge's
Conviction and the Debate_Consensus are persisted in the Trade_Journal, and this
module measures, over the journaled live results, whether

  * higher-conviction debates realize a higher win-rate (R10.1), and whether
    that win-rate is non-decreasing across increasing conviction bins — a
    reliability indication (R10.2), and
  * ``contested`` debates underperform ``strong_agree`` debates, by reporting a
    per-consensus-class win-rate AND expectancy (R10.3).

Design constraints honored here:
  * ``conviction_calibration`` is a PURE measurement over the journal rows passed
    to it. It performs NO database reads, NO candle fetches, and NEVER triggers a
    backtest (R10.5). The thin ``conviction_calibration_from_journal`` entry point
    reads already-recorded rows from the journal store and hands them off — it,
    too, never runs a backtest.
  * Empty conviction bins and empty Debate_Consensus classes are reported as
    not-applicable; the measurement NEVER divides by zero (R10.4).
  * Every function is TOTAL and defensive: a malformed row never raises — it is
    simply excluded from the measurement.
"""

from __future__ import annotations

import json
import math
from typing import Optional

# The three categorical Debate_Consensus classes whose realized win-rate and
# expectancy are reported (R10.3). A row tagged ``db:unknown`` (every non-DEBATE
# decision, or a missing/unrecognized consensus) belongs to none of these.
CONSENSUS_CLASSES = ("strong_agree", "lean", "contested")

# Default conviction binning over the [0, 100] Conviction scale (R10.1). Each
# bin is the half-open interval ``[lower, upper)`` so the bins partition the
# scale with no overlap; the FINAL bin is closed at its upper bound (``[lower,
# upper]``) so a maximal conviction of 100 still lands in a bin. Documented,
# deterministic, and ordered by increasing conviction so the reliability check
# (R10.2) reads them in order.
DEFAULT_CONVICTION_BINS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]


def _is_num(v) -> bool:
    """True for a finite real number (bool excluded), mirroring journal._is_num."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _tags_of(row: dict) -> list:
    """Return a row's setup tags as a list of strings, defensively.

    The journal persists ``setup_tags`` as a JSON-encoded list string and
    ``setup_key`` as the ``|``-joined tag string. A row dict read back may carry
    either (or a Python list already), so this normalizes all shapes into a flat
    list of tag tokens. Never raises — an unparseable value yields ``[]``.
    """
    try:
        raw = row.get("setup_tags")
        if isinstance(raw, list):
            return [str(t) for t in raw]
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(t) for t in parsed]
            except Exception:
                pass
        key = row.get("setup_key")
        if isinstance(key, str) and key:
            return key.split("|")
    except Exception:
        pass
    return []


def consensus_of(row: dict) -> Optional[str]:
    """Extract the Debate_Consensus from a row's ``db:<value>`` setup tag.

    Parses the fixed-position debate dimension tag appended by
    ``journal.derive_setup_tags`` (``db:<value>``) from the row's ``setup_tags``
    or ``setup_key``. Returns the bare consensus value only when it is one of the
    three categorical classes (``strong_agree``/``lean``/``contested``); a
    ``db:unknown`` tag, a missing tag, or any unrecognized value yields ``None``
    (the row belongs to no consensus class). Never raises.
    """
    try:
        for tag in _tags_of(row):
            t = str(tag).strip()
            if t.startswith("db:"):
                value = t[len("db:"):].strip().lower()
                return value if value in CONSENSUS_CLASSES else None
    except Exception:
        pass
    return None


def _is_scored_debate_row(row: dict) -> bool:
    """True when a row is a scored DEBATE-mode trade usable for calibration.

    A row qualifies when it has a realized win/loss outcome (``status`` in
    ``win``/``loss``), carries a numeric ``conviction``, and is a debate row —
    either ``mode == "DEBATE"`` or it carries a real ``db:`` consensus tag
    (``strong_agree``/``lean``/``contested``). Open/expired/hold rows, rows with
    no recorded conviction, and non-debate rows are excluded. Never raises.
    """
    try:
        if str(row.get("status") or "").strip().lower() not in ("win", "loss"):
            return False
        if not _is_num(row.get("conviction")):
            return False
        mode = str(row.get("mode") or "").strip().upper()
        return mode == "DEBATE" or consensus_of(row) is not None
    except Exception:
        return False


def _bin_index(conviction: float, bins: list) -> Optional[int]:
    """Index of the bin containing ``conviction``, or ``None`` if it fits none.

    Bins are half-open ``[lower, upper)`` intervals; the LAST bin is closed at
    its upper bound so a maximal conviction equal to the final upper bound is
    still placed. A conviction outside every bin's range is unplaced (``None``)
    rather than forced into an edge bucket. Never raises.
    """
    try:
        last = len(bins) - 1
        for i, (lo, hi) in enumerate(bins):
            if i == last:
                if lo <= conviction <= hi:
                    return i
            elif lo <= conviction < hi:
                return i
    except Exception:
        pass
    return None


def _win_rate(wins: int, losses: int) -> Optional[float]:
    """wins / (wins + losses), rounded; ``None`` when there are no scored trades.

    The single guard against divide-by-zero (R10.4): an empty group returns
    ``None`` (reported not-applicable) rather than raising.
    """
    total = wins + losses
    return round(wins / total, 4) if total else None


def _expectancy(r_values: list) -> Optional[float]:
    """Mean R-multiple over the supplied finite values, or ``None`` when empty."""
    return round(sum(r_values) / len(r_values), 4) if r_values else None


def conviction_calibration(rows: list, bins: Optional[list] = None) -> dict:
    """Measure conviction calibration over journaled live DEBATE-mode trades.

    PURE over ``rows`` (R10.5): no DB reads, no candle fetches, no backtest. The
    input is a list of journal row dicts; this filters to scored DEBATE rows
    (a realized win/loss outcome, a numeric recorded conviction, and either
    ``mode == "DEBATE"`` or a real ``db:`` consensus tag) and reports:

      * ``bins`` — for each conviction bin, the mean conviction of its members
        and the realized win-rate (wins / (wins + losses)) (R10.1).
      * ``reliability`` — whether realized win-rate is non-decreasing across the
        non-empty bins in increasing conviction order; empty bins are skipped,
        not treated as violations (R10.2).
      * ``by_consensus`` — for each Debate_Consensus class
        (``strong_agree``/``lean``/``contested``), the realized win-rate and
        expectancy (mean R-multiple), so ``contested`` can be compared against
        ``strong_agree`` (R10.3).

    Empty bins and empty consensus classes are reported as not-applicable
    (``applicable=False`` with ``win_rate``/``mean_conviction``/``expectancy_r``
    of ``None``); the measurement NEVER divides by zero (R10.4). TOTAL: malformed
    rows are skipped, and any unexpected failure degrades to an
    ``applicable=False`` result rather than raising.
    """
    try:
        bin_defs = bins if isinstance(bins, list) and bins else DEFAULT_CONVICTION_BINS
        rows = rows if isinstance(rows, list) else []

        scored = [r for r in rows if isinstance(r, dict) and _is_scored_debate_row(r)]

        # ── Per-bin accumulation (R10.1) ──────────────────────────────────────
        bin_rows: list = [[] for _ in bin_defs]
        for r in scored:
            idx = _bin_index(float(r.get("conviction")), bin_defs)
            if idx is not None:
                bin_rows[idx].append(r)

        bin_reports = []
        for (lo, hi), members in zip(bin_defs, bin_rows):
            wins = sum(1 for r in members if str(r.get("status")).lower() == "win")
            losses = sum(1 for r in members if str(r.get("status")).lower() == "loss")
            convs = [float(r.get("conviction")) for r in members if _is_num(r.get("conviction"))]
            applicable = len(members) > 0
            bin_reports.append({
                "lower": lo,
                "upper": hi,
                "label": f"{lo}-{hi}",
                "applicable": applicable,
                "count": len(members),
                "wins": wins,
                "losses": losses,
                "mean_conviction": round(sum(convs) / len(convs), 4) if convs else None,
                "win_rate": _win_rate(wins, losses),
            })

        # ── Reliability: non-decreasing win-rate across non-empty bins (R10.2) ─
        non_empty_rates = [
            b["win_rate"] for b in bin_reports
            if b["applicable"] and b["win_rate"] is not None
        ]
        non_decreasing = all(
            non_empty_rates[i] <= non_empty_rates[i + 1]
            for i in range(len(non_empty_rates) - 1)
        )
        reliability = {
            # Comparable only when at least two non-empty bins exist; with fewer
            # the monotonic check is vacuously True but flagged not-applicable so
            # callers do not over-read a single data point (R10.4).
            "applicable": len(non_empty_rates) >= 2,
            "non_decreasing": non_decreasing,
            "bins_compared": len(non_empty_rates),
        }

        # ── Per-consensus-class win-rate and expectancy (R10.3) ───────────────
        by_consensus: dict = {}
        for cls in CONSENSUS_CLASSES:
            members = [r for r in scored if consensus_of(r) == cls]
            wins = sum(1 for r in members if str(r.get("status")).lower() == "win")
            losses = sum(1 for r in members if str(r.get("status")).lower() == "loss")
            r_values = [
                float(r.get("r_multiple")) for r in members
                if str(r.get("status")).lower() in ("win", "loss") and _is_num(r.get("r_multiple"))
            ]
            by_consensus[cls] = {
                "applicable": len(members) > 0,
                "count": len(members),
                "wins": wins,
                "losses": losses,
                "win_rate": _win_rate(wins, losses),
                "expectancy_r": _expectancy(r_values),
            }

        return {
            "applicable": len(scored) > 0,
            "trades_scored": len(scored),
            "bins": bin_reports,
            "reliability": reliability,
            "by_consensus": by_consensus,
        }
    except Exception as e:
        # TOTAL: never raise into a caller. Degrade to a not-applicable result.
        print(f"[Conviction_Calibration] WARN: conviction_calibration failed: {e}")
        return {
            "applicable": False,
            "trades_scored": 0,
            "bins": [],
            "reliability": {"applicable": False, "non_decreasing": True, "bins_compared": 0},
            "by_consensus": {c: {
                "applicable": False, "count": 0, "wins": 0, "losses": 0,
                "win_rate": None, "expectancy_r": None,
            } for c in CONSENSUS_CLASSES},
            "unavailable": True,
            "error": str(e),
        }


def conviction_calibration_from_journal(bins: Optional[list] = None) -> dict:
    """Thin offline entry point: read recorded DEBATE rows and calibrate them.

    Reads ALREADY-RECORDED journal rows from the journal store and hands them to
    the pure ``conviction_calibration`` measurement. It reads only persisted rows
    and has NO backtest dependency — it never replays candles or seeds backtest
    trades (R10.5). The journal import is performed lazily so importing this
    module stays cheap and side-effect free. Never raises.
    """
    try:
        import journal  # lazy: keeps module import free of journal's I/O deps

        conn = journal._connect()
        try:
            journal._init_db(conn)
            # Pure read of recorded debate rows: DEBATE-mode rows or any row
            # carrying a real db: consensus tag. No scoring / candle fetch / backtest.
            db_rows = conn.execute(
                "SELECT * FROM trades WHERE mode='DEBATE' OR setup_key LIKE '%db:%'"
            ).fetchall()
            rows = [dict(r) for r in db_rows]
        finally:
            conn.close()
        return conviction_calibration(rows, bins=bins)
    except Exception as e:
        print(f"[Conviction_Calibration] WARN: read from journal failed: {e}")
        return conviction_calibration([], bins=bins)
