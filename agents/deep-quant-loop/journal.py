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

# Trade_Manager (trade-management) — the SINGLE source of truth for the
# exit-simulation math (AD-2). The journal NEVER reimplements the multi-leg
# fill / breakeven / trail logic: a managed trade is scored by reconstructing its
# persisted Management_Plan and calling ``trade_manager.simulate_plan`` against
# the subsequent candles (R6.1, R6.5). Pure module, no I/O, no circular import
# (it imports only ``regime``), so a top-level import is safe.
import trade_manager

# Adaptive Opportunity Engine (adaptive-opportunity-engine). The Trade_Journal
# appends an opportunity-tier fingerprint dimension via ``opportunity.tier_tag`` so
# per-tier win-rate and expectancy become measurable (R9.2). Pure module.
import opportunity

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
            scored_at     REAL,
            forecast_up_probability REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
    # Additive, backward-compatible migrations for journals created before a
    # column existed. Each entry is applied via a guarded ALTER that only runs
    # when the column is absent, so existing journals upgrade in place and the
    # operation never raises into the agent loop (R11.4).
    _ensure_column(conn, "forecast_up_probability", "REAL")
    # Trade-management migrations (R6.3, R6.1): persist the serialized
    # Management_Plan so a managed trade can be re-scored reproducibly on later
    # candles (``management_plan`` is NULL for a single-target trade), and persist
    # a representation of the simulated Exit_Breakdown alongside the Realized_R
    # written to the existing ``r_multiple`` column. Both are additive, nullable,
    # and applied via the same idempotent guarded ALTER, so existing journals
    # upgrade in place without touching legacy single-target rows.
    _ensure_column(conn, "management_plan", "TEXT")
    _ensure_column(conn, "exit_breakdown", "TEXT")
    # Idempotent-commit migration (Bug 5): persist the LangGraph ``thread_id`` that
    # committed each decision so a re-entered finalize for the same thread cannot
    # write a second journal row. Additive and nullable — legacy rows (written
    # before this column existed) keep NULL and are unaffected. The partial UNIQUE
    # index enforces at most one row per non-NULL ``thread_id`` at the storage
    # layer (defense in depth behind the pre-insert existence check in
    # ``record_decision``) while the ``WHERE thread_id IS NOT NULL`` predicate lets
    # any number of legacy NULL rows coexist.
    _ensure_column(conn, "thread_id", "TEXT")
    _ensure_thread_id_index(conn)
    conn.commit()


def _ensure_thread_id_index(conn: sqlite3.Connection) -> None:
    """Create the partial UNIQUE index on ``thread_id`` (idempotent). Never raises.

    ``CREATE UNIQUE INDEX IF NOT EXISTS ... WHERE thread_id IS NOT NULL`` is a
    partial index: it enforces uniqueness only across rows with a non-NULL
    ``thread_id`` so multiple legacy NULL rows are permitted, and re-running on an
    already-migrated journal is a no-op. Guarded so a failure degrades the run to
    the pre-insert existence check rather than aborting the agent loop.
    """
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_thread "
            "ON trades(thread_id) WHERE thread_id IS NOT NULL"
        )
    except Exception as e:
        print(f"[Trade_Journal] WARN: could not ensure thread_id index: {e}")


def _ensure_column(conn: sqlite3.Connection, column: str, decl_type: str) -> None:
    """Add ``column`` to the ``trades`` table when it is missing (idempotent).

    Inspects the live schema via ``PRAGMA table_info(trades)`` and only issues an
    ``ALTER TABLE ... ADD COLUMN`` when the column is absent, so re-running on an
    already-migrated journal is a no-op. Additive only (new columns are nullable
    with no default), preserving existing rows. Never raises.
    """
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {column} {decl_type}")
    except Exception as e:
        print(f"[Trade_Journal] WARN: could not ensure column '{column}': {e}")


# ── Setup fingerprinting ──────────────────────────────────────────────────────

# Fixed, low-cardinality regime enumeration for the setup fingerprint (R9.3).
# The journal collapses the (Trend_State x Favorability) space into this small
# set so the regime-extended ``setup_key`` stays groupable and individual setups
# can accumulate enough scored trades to clear LOW_SAMPLE_THRESHOLD. At most 8
# distinct values including ``unknown``.
REGIME_TAG_VALUES = {
    "trend-favorable", "trend-unfavorable", "trend-neutral",
    "range-favorable", "range-unfavorable", "range-neutral",
    "unknown",
}

# Trend_State -> tag family. ``trending``/``transitional`` collapse to the
# ``trend`` family; ``ranging`` collapses to the ``range`` family (design table).
_REGIME_TREND_FAMILY = {
    "trending": "trend",
    "transitional": "trend",
    "ranging": "range",
}
_REGIME_FAVORABILITY = {"favorable", "unfavorable", "neutral"}


def _regime_tag(decision: dict) -> str:
    """Collapse the decision's regime into exactly one fixed enumeration value.

    Reads the regime recorded in the defensibility record
    (``decision['defensibility']['regime']``) and maps (Trend_State x
    Favorability) to one of ``REGIME_TAG_VALUES``:
      * Trend_State ``trending``/``transitional`` -> ``trend-*`` family
      * Trend_State ``ranging``                   -> ``range-*`` family
      * the Favorability is carried as the suffix (favorable/unfavorable/neutral)

    Any missing/unavailable regime, empty value, or unrecognized combination
    collapses to ``unknown`` (R9.2). Returns the bare value (caller prefixes
    ``regime:``). Never raises.
    """
    try:
        d = decision or {}
        deff = d.get("defensibility") or {}
        regime = deff.get("regime")
        if not isinstance(regime, dict):
            return "unknown"
        # An explicitly unavailable regime entry carries no fabricated states.
        if regime.get("available") is False:
            return "unknown"
        trend_state = str(regime.get("trend_state") or "").strip().lower()
        favorability = str(regime.get("favorability") or "").strip().lower()
        family = _REGIME_TREND_FAMILY.get(trend_state)
        if family is None or favorability not in _REGIME_FAVORABILITY:
            return "unknown"
        value = f"{family}-{favorability}"
        return value if value in REGIME_TAG_VALUES else "unknown"
    except Exception:
        return "unknown"


# Fixed, low-cardinality relative-strength enumeration for the setup fingerprint
# (R10.3). The journal collapses the (Relative_Strength_State x Alignment) space
# into this small set so the relative-strength-extended ``setup_key`` stays
# groupable and individual setups can accumulate enough scored trades to clear
# LOW_SAMPLE_THRESHOLD. At most 8 distinct values including ``unknown``.
RS_TAG_VALUES = {
    "leader-aligned", "leader-misaligned",
    "laggard-aligned", "laggard-misaligned",
    "inline-neutral", "aligned", "misaligned",
    "unknown",
}

_RS_STATES = {"leader", "inline", "laggard"}
_RS_ALIGNMENTS = {"aligned", "misaligned", "neutral"}


def _relative_strength_tag(decision: dict) -> str:
    """Collapse the decision's relative strength into one fixed enumeration value.

    Reads the relative-strength entry recorded in the defensibility record
    (``decision['defensibility']['relative_strength']``) and maps
    (Relative_Strength_State x Alignment) to one of ``RS_TAG_VALUES``:
      * the directional pairings ``leader``/``laggard`` x ``aligned``/``misaligned``
        collapse to ``<state>-<alignment>``
      * an ``inline`` state with a ``neutral`` Alignment collapses to ``inline-neutral``
      * the residual combinations (``inline`` x directional) collapse to the bare
        Alignment value (``aligned``/``misaligned``)

    Any missing/unavailable relative-strength entry, empty value, or unrecognized
    combination collapses to ``unknown`` (R10.2). Returns the bare value (caller
    prefixes ``rs:``). Never raises.
    """
    try:
        d = decision or {}
        deff = d.get("defensibility") or {}
        rs = deff.get("relative_strength")
        if not isinstance(rs, dict):
            return "unknown"
        # An explicitly unavailable relative-strength entry carries no states.
        if rs.get("available") is False:
            return "unknown"
        state = str(rs.get("relative_strength_state") or "").strip().lower()
        alignment = str(rs.get("alignment") or "").strip().lower()
        if state not in _RS_STATES or alignment not in _RS_ALIGNMENTS:
            return "unknown"
        # Directional pairings (leader/laggard x aligned/misaligned) and the
        # inline-neutral pairing collapse to ``<state>-<alignment>`` directly.
        value = f"{state}-{alignment}"
        if value in RS_TAG_VALUES:
            return value
        # Residual combinations collapse to the bare Alignment (aligned/misaligned);
        # anything else (e.g. a leader/laggard neutral) collapses to ``unknown``.
        if alignment in RS_TAG_VALUES:
            return alignment
        return "unknown"
    except Exception:
        return "unknown"


# Fixed, low-cardinality forecast enumeration for the setup fingerprint (R11.3).
# The journal collapses the (Forecast_Alignment x Up_Probability confidence band)
# space into this small set so the forecast-extended ``setup_key`` stays
# groupable and individual setups can accumulate enough scored trades to clear
# LOW_SAMPLE_THRESHOLD. At most 8 distinct values including ``unknown`` (here 7):
# each Alignment (aligned/misaligned/neutral) paired with a strong/weak band.
FC_TAG_VALUES = {
    "aligned-strong", "aligned-weak",
    "misaligned-strong", "misaligned-weak",
    "neutral-strong", "neutral-weak",
    "unknown",
}

# Fixed split for the Up_Probability confidence band: the forecast is tagged
# ``strong`` when its probability is at least this far from a 50/50 coin flip
# (``abs(up_probability - 0.5) >= FC_STRONG_PROB_SPLIT``), otherwise ``weak``.
# Documented constant (not a magic number): 0.15 means a >=65% / <=35% directional
# probability is "strong", anything closer to 0.5 is "weak".
FC_STRONG_PROB_SPLIT = 0.15

_FC_ALIGNMENTS = {"aligned", "misaligned", "neutral"}


def _forecast_tag(decision: dict) -> str:
    """Collapse the decision's forecast into exactly one fixed enumeration value.

    Reads the forecast entry recorded in the defensibility record
    (``decision['defensibility']['forecast']``) and maps (Forecast_Alignment x
    Up_Probability confidence band) to one of ``FC_TAG_VALUES``:
      * the Alignment (``aligned``/``misaligned``/``neutral``) is carried as the prefix
      * the Up_Probability confidence band is the suffix: ``strong`` when
        ``abs(up_probability - 0.5) >= FC_STRONG_PROB_SPLIT``, else ``weak``

    Any missing/unavailable forecast entry, empty value, non-numeric
    Up_Probability, or unrecognized Alignment collapses to ``unknown`` (R11.2).
    Returns the bare value (caller prefixes ``fc:``). Never raises.
    """
    try:
        d = decision or {}
        deff = d.get("defensibility") or {}
        fc = deff.get("forecast")
        if not isinstance(fc, dict):
            return "unknown"
        # An explicitly unavailable forecast entry carries no fabricated fields.
        if fc.get("available") is False:
            return "unknown"
        alignment = str(fc.get("forecast_alignment") or "").strip().lower()
        up_probability = fc.get("up_probability")
        if alignment not in _FC_ALIGNMENTS or not _is_num(up_probability):
            return "unknown"
        band = "strong" if abs(up_probability - 0.5) >= FC_STRONG_PROB_SPLIT else "weak"
        value = f"{alignment}-{band}"
        return value if value in FC_TAG_VALUES else "unknown"
    except Exception:
        return "unknown"


def _forecast_up_probability(deff: dict) -> Optional[float]:
    """Extract the forecast ``up_probability`` for persistence, or ``None``.

    Reads ``deff['forecast']['up_probability']`` from a decision's defensibility
    record and returns it only when the forecast entry is present, not explicitly
    unavailable, and the probability is a finite number (validated via
    ``_is_num``). Any missing/unavailable forecast or non-finite probability maps
    to ``None`` so the column is written as ``NULL`` (R11.4). Never raises.
    """
    try:
        d = deff or {}
        fc = d.get("forecast")
        if not isinstance(fc, dict):
            return None
        if fc.get("available") is False:
            return None
        up_probability = fc.get("up_probability")
        return up_probability if _is_num(up_probability) else None
    except Exception:
        return None


# Fixed, low-cardinality management-style enumeration for the setup fingerprint
# (R11.2). The journal collapses every committed plan into this small set so the
# management-extended ``setup_key`` stays groupable and per-management-style
# win-rate / expectancy can accumulate enough scored trades to clear
# LOW_SAMPLE_THRESHOLD. The allowed values are owned by the Trade_Manager — the
# SINGLE source of truth for the style mapping (AD-2/AD-8) — and re-exported here
# only so this module's intent reads locally; at most 8 values including
# ``unknown`` (``single``/``scale``/``scale-be``/``scale-trail``/``scale-be-trail``/
# ``be``/``trail``/``unknown``).
TM_TAG_VALUES = set(trade_manager.MANAGEMENT_STYLE_TAGS)


def _management_style_tag(decision: dict) -> str:
    """Collapse the decision's management style into one fixed enumeration value.

    Reads the management entry recorded in the defensibility record
    (``decision['defensibility']['management']``) — written by
    ``graph._management_entry`` / ``backtest._management_defensibility_entry`` as
    ``{available, style, ...}`` where ``style`` is the bare value produced by
    ``trade_manager.management_style_tag`` (the single source of truth for the
    style mapping, AD-2/AD-8). When the entry is present, available, and carries a
    ``style`` in the fixed ``trade_manager.MANAGEMENT_STYLE_TAGS`` enumeration that
    value is used verbatim; a missing/unavailable management entry, empty value,
    or unrecognized style collapses to ``unknown`` (R11.2). Returns the bare value
    (caller prefixes ``tm:``). Never raises.
    """
    try:
        d = decision or {}
        deff = d.get("defensibility") or {}
        mgmt = deff.get("management")
        if not isinstance(mgmt, dict):
            return "unknown"
        # An explicitly unavailable management entry carries no committed style.
        if mgmt.get("available") is False:
            return "unknown"
        style = str(mgmt.get("style") or "").strip().lower()
        return style if style in TM_TAG_VALUES else "unknown"
    except Exception:
        return "unknown"


# Fixed, low-cardinality session enumeration for the setup fingerprint (R10.3).
# The journal collapses the (Session_Phase x expiry-day flag) space into this
# small set so the session-extended ``setup_key`` stays groupable and individual
# setups can accumulate enough scored trades to clear LOW_SAMPLE_THRESHOLD.
# Exactly 8 distinct values including ``unknown`` (AD-8):
#   * each in-session phase keeps its own bucket: ``opening``, ``morning``,
#     ``midday``, ``afternoon``, ``closing``;
#   * the two out-of-session phases (``pre_open``/``post_close``) collapse to
#     ``offhours``;
#   * an expiry-day candle in the ``afternoon``/``closing`` chop window collapses
#     to ``expiry`` (overriding the bare phase bucket);
#   * anything missing/unavailable/unrecognized collapses to ``unknown``.
SESS_TAG_VALUES = {
    "opening", "morning", "midday", "afternoon", "closing",
    "offhours", "expiry", "unknown",
}

# The two phases that, on a weekly-expiry day, collapse to the ``expiry`` chop
# bucket rather than their own phase bucket (R2.3 / design table).
_SESS_EXPIRY_PHASES = {"afternoon", "closing"}
# The out-of-session phases that collapse to the ``offhours`` bucket.
_SESS_OFFHOURS_PHASES = {"pre_open", "post_close"}
# The in-session phases that keep their own bucket (non-expiry).
_SESS_OWN_BUCKET_PHASES = {"opening", "morning", "midday", "afternoon", "closing"}


def _session_tag(decision: dict) -> str:
    """Collapse the decision's session context into one fixed enumeration value.

    Reads the session entry recorded in the defensibility record
    (``decision['defensibility']['session']``) and maps (Session_Phase x
    expiry-day flag) to one of ``SESS_TAG_VALUES``:
      * an ``is_expiry_day`` candle in the ``afternoon``/``closing`` phase (the
        key chop window) collapses to ``expiry``;
      * otherwise ``pre_open``/``post_close`` collapse to ``offhours``;
      * each remaining phase (``opening``/``morning``/``midday`` and a non-expiry
        ``afternoon``/``closing``) keeps its own bucket.

    Any missing/unavailable session entry, empty value, or unrecognized phase
    collapses to ``unknown`` (R10.2). Returns the bare value (caller prefixes
    ``sess:``). Never raises.
    """
    try:
        d = decision or {}
        deff = d.get("defensibility") or {}
        sess = deff.get("session")
        if not isinstance(sess, dict):
            return "unknown"
        # An explicitly unavailable session entry carries no fabricated phase.
        if sess.get("available") is False:
            return "unknown"
        phase = str(sess.get("session_phase") or "").strip().lower()
        if phase not in _SESS_OWN_BUCKET_PHASES and phase not in _SESS_OFFHOURS_PHASES:
            return "unknown"
        # Expiry-day afternoon/closing chop overrides the bare phase bucket.
        expiry_context = sess.get("expiry_context")
        is_expiry_day = bool(expiry_context.get("is_expiry_day")) if isinstance(expiry_context, dict) else False
        if is_expiry_day and phase in _SESS_EXPIRY_PHASES:
            return "expiry"
        if phase in _SESS_OFFHOURS_PHASES:
            return "offhours"
        # Remaining in-session phases keep their own bucket.
        return phase if phase in SESS_TAG_VALUES else "unknown"
    except Exception:
        return "unknown"


# Fixed, low-cardinality debate enumeration for the setup fingerprint (R9.3).
# The journal collapses the Multi-Agent Debate's classified consensus into this
# small set so the debate-extended ``setup_key`` stays groupable and individual
# setups can accumulate enough scored trades to clear LOW_SAMPLE_THRESHOLD.
# Exactly 4 distinct values (<= 8 per R9.3): the three categorical
# Debate_Consensus values (``strong_agree``/``lean``/``contested``) plus
# ``unknown`` for every non-DEBATE decision (which carries no debate entry) or
# any missing/empty/unrecognized consensus.
DB_TAG_VALUES = {"strong_agree", "lean", "contested", "unknown"}

_DB_CONSENSUS_VALUES = {"strong_agree", "lean", "contested"}


def _debate_tag(decision: dict) -> str:
    """Collapse the decision's debate consensus into one fixed enumeration value.

    Reads the debate entry recorded in the defensibility record
    (``decision['defensibility']['debate']`` — written by ``graph._debate_entry``
    on a DEBATE-mode run as ``{bull_stance, bear_stance, consensus, conviction,
    conviction_basis, ...}``) and returns the classified ``consensus`` verbatim
    when it is one of the three categorical Debate_Consensus values
    (``strong_agree``/``lean``/``contested``).

    Any non-DEBATE decision (no ``debate`` entry), a missing/non-dict entry, an
    empty or ``unknown`` consensus, or an unrecognized value collapses to
    ``unknown`` (R9.2). Returns the bare value (caller prefixes ``db:``). Never
    raises.
    """
    try:
        d = decision or {}
        deff = d.get("defensibility") or {}
        debate = deff.get("debate")
        if not isinstance(debate, dict):
            return "unknown"
        consensus = str(debate.get("consensus") or "").strip().lower()
        return consensus if consensus in _DB_CONSENSUS_VALUES else "unknown"
    except Exception:
        return "unknown"


# Fixed, low-cardinality options enumeration for the setup fingerprint (R8.3).
# The journal collapses the (Options_Bias_State x Alignment) space into this
# small set so the options-extended ``setup_key`` stays groupable and individual
# setups can accumulate enough scored trades to clear LOW_SAMPLE_THRESHOLD.
# Exactly 6 distinct values (<= 8 per R8.3) including ``unknown``:
#   * a directional bias (``bullish``/``bearish``) pairs with its ``aligned``/
#     ``misaligned`` Alignment -> ``<bias>-<alignment>``;
#   * a ``neutral`` bias collapses to ``neutral`` regardless of Alignment;
#   * any missing/unavailable options entry, empty value, or unrecognized
#     combination (e.g. a directional bias with a ``neutral`` Alignment)
#     collapses to ``unknown`` (R8.2).
OPT_TAG_VALUES = {
    "bullish-aligned", "bullish-misaligned",
    "bearish-aligned", "bearish-misaligned",
    "neutral", "unknown",
}

_OPT_BIAS_STATES = {"bullish", "bearish", "neutral"}
_OPT_ALIGNMENTS = {"aligned", "misaligned", "neutral"}


def _options_tag(decision: dict) -> str:
    """Collapse the decision's options bias into one fixed enumeration value.

    Reads the options entry recorded in the defensibility record
    (``decision['defensibility']['options']`` — written by ``graph._options_entry``)
    and maps (Options_Bias_State x Alignment) to one of ``OPT_TAG_VALUES``:
      * a directional bias (``bullish``/``bearish``) pairs with its
        ``aligned``/``misaligned`` Alignment to ``<bias>-<alignment>``;
      * a ``neutral`` bias collapses to ``neutral`` regardless of Alignment.

    Any missing/non-dict/unavailable options entry, empty value, or unrecognized
    combination (including a directional bias with a ``neutral`` Alignment)
    collapses to ``unknown`` (R8.2). Returns the bare value (caller prefixes
    ``opt:``). Never raises.
    """
    try:
        d = decision or {}
        deff = d.get("defensibility") or {}
        opts = deff.get("options")
        if not isinstance(opts, dict):
            return "unknown"
        # An explicitly unavailable options entry carries no fabricated bias.
        if opts.get("available") is False:
            return "unknown"
        bias = str(opts.get("options_bias_state") or "").strip().lower()
        alignment = str(opts.get("alignment") or "").strip().lower()
        if bias not in _OPT_BIAS_STATES or alignment not in _OPT_ALIGNMENTS:
            return "unknown"
        # A neutral bias collapses to ``neutral`` regardless of Alignment.
        if bias == "neutral":
            return "neutral"
        # A directional bias pairs with its aligned/misaligned Alignment; a
        # directional bias with a neutral Alignment is not in the enumeration and
        # collapses to ``unknown``.
        value = f"{bias}-{alignment}"
        return value if value in OPT_TAG_VALUES else "unknown"
    except Exception:
        return "unknown"


# Exactly 4 distinct values (<= 8 per R10.3) including ``unknown``: the three
# categorical Event_Risk classifications (``clear``/``imminent``/``through_event``)
# plus ``unknown`` for a decision carrying no event entry, an unavailable entry,
# or any missing/empty/unrecognized Event_Risk value.
EVT_TAG_VALUES = {"clear", "imminent", "through_event", "unknown"}

_EVT_RISK_VALUES = {"clear", "imminent", "through_event"}


def _event_tag(decision: dict) -> str:
    """Collapse the decision's scheduled-event risk into one fixed enumeration value.

    Reads the event entry recorded in the defensibility record
    (``decision['defensibility']['event']`` — written by ``graph._event_entry``)
    and returns the recorded ``event_risk`` verbatim when it is one of the three
    categorical Event_Risk classifications (``clear``/``imminent``/``through_event``).

    A missing/non-dict event entry, an explicitly unavailable entry
    (``available`` is False), an empty value, or an unrecognized Event_Risk
    collapses to ``unknown`` (R10.2). Returns the bare value (caller prefixes
    ``evt:``). Never raises.
    """
    try:
        d = decision or {}
        deff = d.get("defensibility") or {}
        event = deff.get("event")
        if not isinstance(event, dict):
            return "unknown"
        # An explicitly unavailable event entry carries no fabricated risk.
        if event.get("available") is False:
            return "unknown"
        event_risk = str(event.get("event_risk") or "").strip().lower()
        return event_risk if event_risk in _EVT_RISK_VALUES else "unknown"
    except Exception:
        return "unknown"


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
      * regime      (trend/range x favorable/unfavorable/neutral, or unknown)
                    collapsed from the regime recorded in the defensibility
                    record; appended last at a FIXED position so ``setup_key``
                    stays deterministic and low-cardinality.
      * relative-strength (leader/laggard x aligned/misaligned, inline-neutral,
                    bare aligned/misaligned, or unknown) collapsed from the
                    relative-strength entry recorded in the defensibility
                    record; appended last at a FIXED position (after the
                    ``regime:`` tag) so ``setup_key`` stays deterministic and
                    low-cardinality.
      * forecast    (alignment x probability band: aligned/misaligned/neutral
                    paired with strong/weak, or unknown) collapsed from the
                    forecast entry recorded in the defensibility record;
                    appended last at a FIXED position (after the ``rs:`` tag) so
                    ``setup_key`` stays deterministic and low-cardinality.
      * management  (single/scale/scale-be/scale-trail/scale-be-trail/be/trail,
                    or unknown) — the committed plan's management style recorded
                    in the defensibility management entry; appended last at a
                    FIXED position (after the ``fc:`` tag) so ``setup_key`` stays
                    deterministic and low-cardinality.
      * session     (opening/morning/midday/afternoon/closing/offhours/expiry,
                    or unknown) — collapsed from the (Session_Phase x expiry-day
                    flag) recorded in the defensibility session entry; appended
                    last at a FIXED position (after the ``tm:`` tag) so
                    ``setup_key`` stays deterministic and low-cardinality.
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

    # Regime dimension — appended last at a FIXED position (after ``va:``) so the
    # resulting ``setup_key`` is deterministic for identical inputs (R9.1).
    tags.append("regime:" + _regime_tag(decision))

    # Relative-strength dimension — appended at a FIXED position immediately
    # after the ``regime:`` tag so the resulting ``setup_key`` is deterministic
    # for identical inputs and stays low-cardinality (R10.1, R10.3).
    tags.append("rs:" + _relative_strength_tag(decision))

    # Forecast dimension — appended at a FIXED position last (after the
    # ``rs:`` tag, and after the order-flow ``of:`` tag once that feature lands)
    # so the resulting ``setup_key`` is deterministic for identical inputs and
    # stays low-cardinality. Collapses (Forecast_Alignment x Up_Probability
    # confidence band) into one fixed value (R11.1, R11.2, R11.3).
    tags.append("fc:" + _forecast_tag(decision))

    # Management-style dimension — appended at a FIXED position last (after the
    # ``fc:`` tag) so the resulting ``setup_key`` is deterministic for identical
    # inputs and stays low-cardinality. Collapses the committed plan's style
    # (recorded in the defensibility management entry by the graph / backtest via
    # ``trade_manager.management_style_tag``) into one fixed ``tm:`` value; a
    # missing/unavailable management entry defaults to ``tm:unknown`` (R11.1,
    # R11.2, R11.3).
    tags.append("tm:" + _management_style_tag(decision))

    # Session dimension — appended at a FIXED position last (after the ``tm:``
    # tag) so the resulting ``setup_key`` is deterministic for identical inputs
    # and stays low-cardinality. Collapses (Session_Phase x expiry-day flag) into
    # one fixed ``sess:`` value; a missing/unavailable session entry defaults to
    # ``sess:unknown`` (R10.1, R10.2, R10.3).
    tags.append("sess:" + _session_tag(decision))

    # Debate dimension — appended at the FINAL fixed position (after the
    # ``sess:`` tag) so the resulting ``setup_key`` is deterministic for
    # identical inputs and stays low-cardinality. Collapses the Multi-Agent
    # Debate's classified consensus into one fixed ``db:`` value; a non-DEBATE
    # decision (no debate entry) or any missing/empty/unrecognized consensus
    # defaults to ``db:unknown`` (R9.1, R9.2, R9.3).
    tags.append("db:" + _debate_tag(decision))

    # Options dimension — appended at a FIXED position last (after the ``db:``
    # tag) so the resulting ``setup_key`` is deterministic for identical inputs
    # and stays low-cardinality. Collapses (Options_Bias_State x Alignment) into
    # one fixed ``opt:`` value; a missing/non-dict/unavailable options entry, an
    # empty value, or a value outside the enumeration defaults to ``opt:unknown``
    # (R8.1, R8.2, R8.3).
    tags.append("opt:" + _options_tag(decision))

    # Opportunity-tier dimension — appended at the FINAL fixed position (after the
    # ``opt:`` tag) so the resulting ``setup_key`` is deterministic for identical
    # inputs and stays low-cardinality. Collapses the committed Opportunity_Tier
    # into one fixed ``tier:`` value via ``opportunity.tier_tag``; a decision with
    # no stamped tier (or an unrecognized one) defaults to ``tier:unknown``
    # (adaptive-opportunity-engine R9.2). ``opt:`` is *options*; ``tier:`` is the
    # distinct opportunity dimension.
    tags.append("tier:" + opportunity.tier_tag(decision))

    # Event-date risk dimension — appended at the FINAL fixed position
    # (immediately after the ``tier:`` tag) so the resulting ``setup_key`` is
    # deterministic for identical inputs and stays low-cardinality. Collapses the
    # recorded Event_Risk (Earnings/Event-Date Risk Gate) into one fixed ``evt:``
    # value; a decision with no event entry, an unavailable entry, or any
    # missing/empty/unrecognized Event_Risk defaults to ``evt:unknown`` (R10.1,
    # R10.2, R10.3).
    tags.append("evt:" + _event_tag(decision))

    return tags


def setup_key_from_tags(tags) -> str:
    return "|".join(tags) if tags else "unknown"


# ── Recording ─────────────────────────────────────────────────────────────────

def _serialize_management_plan(
    management_plan, decision: dict, entry, stop_loss, atr_14
) -> Optional[str]:
    """Serialize a decision's Management_Plan to JSON for persistence, or None.

    Returns the stored NULL sentinel (``None``) for a Single_Target_Trade — a
    decision carrying no management plan — so today's single-bracket rows persist
    a NULL ``management_plan`` column and re-score on the unchanged legacy path
    (R6.3, backward compatibility). When a plan is present it is normalized to an
    in-memory ``trade_manager.ManagementPlan`` and re-serialized through
    ``trade_manager.plan_to_json`` (the single round-trip boundary, AD-2):

      * an explicit ``trade_manager.ManagementPlan`` is serialized directly;
      * a JSON-serializable plan dict (as declared on ``declare_trade``) has the
        base bracket fields (``action`` / ``entry`` / ``initial_stop`` / ``atr_14``)
        defaulted from the decision when the dict omits them, then is round-tripped
        via ``trade_manager.plan_from_json`` so a malformed / out-of-shape dict
        degrades to NULL rather than persisting a half-formed plan.

    Pure and TOTAL: any unexpected shape collapses to ``None`` rather than raising,
    keeping the journal write path defensive.
    """
    try:
        raw = management_plan
        if raw is None:
            raw = (decision or {}).get("management_plan")
        if raw is None:
            return None
        if isinstance(raw, trade_manager.ManagementPlan):
            return trade_manager.plan_to_json(raw)
        if isinstance(raw, dict):
            merged = dict(raw)
            if merged.get("action") is None:
                merged["action"] = str((decision or {}).get("action") or "").upper()
            if merged.get("entry") is None:
                merged["entry"] = entry
            if merged.get("initial_stop") is None:
                merged["initial_stop"] = stop_loss
            if merged.get("atr_14") is None:
                merged["atr_14"] = atr_14
            # Round-trip through the canonical (de)serializer so a malformed dict
            # yields None (plan_from_json -> None -> plan_to_json(None) -> None).
            plan = trade_manager.plan_from_json(json.dumps(merged))
            return trade_manager.plan_to_json(plan)
        return None
    except Exception as e:
        print(f"[Trade_Journal] WARN: could not serialize management plan: {e}")
        return None


def record_decision(
    decision: dict,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    mode: Optional[str] = None,
    management_plan=None,
    thread_id: Optional[str] = None,
) -> Optional[int]:
    """Persist a committed decision to the journal. Never raises into the loop.

    BUY/SELL with finite entry/stop/target are stored as ``open`` (scoreable);
    everything else (HOLD, or a directional trade missing levels) is stored as
    ``hold`` and excluded from win-rate/expectancy. Returns the row id, or None
    on failure.

    A Management_Plan (passed explicitly via ``management_plan`` or carried on the
    decision under ``management_plan``) is serialized and persisted in the
    ``management_plan`` column so a managed trade can be re-scored reproducibly on
    later candles (R6.3); a Single_Target_Trade persists a NULL plan and is scored
    on the unchanged legacy path.

    Idempotent commit (Bug 5): when a non-NULL ``thread_id`` is supplied, the
    write is idempotent per thread — if a row for this ``thread_id`` already
    exists, the insert is skipped and the EXISTING row id is returned, so N calls
    for one thread produce exactly one row (Property 9). The ``thread_id``
    parameter is optional and defaults to ``None``: legacy callers that omit it
    keep the pre-existing behavior unchanged (every call inserts a fresh row with
    a NULL ``thread_id``), preserving the first-commit-writes-one-row contract
    (Property 10).
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

        fc_up_prob = _forecast_up_probability(deff)

        plan_json = _serialize_management_plan(management_plan, d, entry, stop_loss, d.get("atr_14"))

        # Normalize the thread id so only a non-empty string participates in the
        # idempotency guard; anything else (None, "", non-string) is treated as a
        # legacy NULL write that inserts a fresh row (preservation).
        tid = thread_id if (isinstance(thread_id, str) and thread_id.strip()) else None

        conn = _connect()
        try:
            _init_db(conn)
            # Idempotent commit: a prior row for this non-NULL thread_id means the
            # decision is already journaled — skip the insert and return the
            # existing id (N calls -> exactly 1 row). Guarded so a lookup failure
            # degrades to attempting the insert rather than aborting the loop.
            if tid is not None:
                existing = conn.execute(
                    "SELECT id FROM trades WHERE thread_id=? ORDER BY id ASC LIMIT 1",
                    (tid,),
                ).fetchone()
                if existing is not None:
                    existing_id = existing[0]
                    print(
                        f"[Trade_Journal] Idempotent commit: thread {tid} already "
                        f"journaled (id={existing_id}); skipping duplicate {action} "
                        f"{symbol}/{timeframe}."
                    )
                    return existing_id
            cur = conn.execute(
                """
                INSERT INTO trades (
                    created_at, mode, symbol, timeframe, action, entry, stop_loss,
                    take_profit, atr_14, conviction, risk_reward, setup_key,
                    setup_tags, source, status, outcome_price, outcome_at,
                    r_multiple, scored_at, forecast_up_probability, management_plan,
                    thread_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    status, None, None, None, None, fc_up_prob, plan_json,
                    tid,
                ),
            )
            conn.commit()
            row_id = cur.lastrowid
            print(f"[Trade_Journal] Recorded {action} {symbol}/{timeframe} as '{status}' (setup={key}, id={row_id}).")
            return row_id
        except sqlite3.IntegrityError as e:
            # A concurrent writer won the race and inserted the same thread_id
            # first; the partial UNIQUE index rejected this insert. Resolve
            # idempotently by returning the row that won rather than raising.
            print(f"[Trade_Journal] Idempotent commit: unique thread_id conflict for {tid}: {e}")
            try:
                if tid is not None:
                    row = conn.execute(
                        "SELECT id FROM trades WHERE thread_id=? ORDER BY id ASC LIMIT 1",
                        (tid,),
                    ).fetchone()
                    if row is not None:
                        return row[0]
            except Exception:
                pass
            return None
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


def _row_value(row, key, default=None):
    """Read an optional column from a sqlite3.Row without raising.

    A row fetched before an additive migration may not carry a newer column;
    ``sqlite3.Row`` raises ``IndexError`` on a missing key, so this guards the
    access and degrades to ``default`` (keeping scoring defensive, R6.1).
    """
    try:
        return row[key]
    except (IndexError, KeyError):
        return default


def _exit_breakdown_json(result) -> Optional[str]:
    """Serialize a ``SimulationResult`` Exit_Breakdown to JSON, or None.

    Persists a representation of HOW the managed plan resolved (per-leg fills, the
    residual, breakeven/trail markers) alongside the Realized_R written to
    ``r_multiple`` (R6.1). Total and non-raising — any unexpected shape degrades
    to ``None`` so a scoring write is never aborted by serialization.
    """
    try:
        fills = [
            {
                "index": f.index,
                "price": f.price,
                "fraction": f.fraction,
                "leg_r": f.leg_r,
                "timestamp_ms": f.timestamp_ms,
                "kind": f.kind,
            }
            for f in (result.fills or ())
        ]
        return json.dumps(
            {
                "status": result.status,
                "realized_r": result.realized_r,
                "fills": fills,
                "residual_fraction": result.residual_fraction,
                "breakeven_moved_at": result.breakeven_moved_at,
                "trailed": result.trailed,
            }
        )
    except Exception:
        return None


def _score_managed_trade(trade: sqlite3.Row, candles: list, plan_text: str) -> Optional[dict]:
    """Score a managed trade multi-leg via the Trade_Manager. Never raises.

    Reconstructs the persisted ``Management_Plan`` and invokes
    ``trade_manager.simulate_plan`` against the candles strictly after the trade's
    ``created_at`` (the same conservative entry-time window the legacy path uses),
    resolving parameters from the same ``resolve_trade_manager_config`` used on
    every Trade_Manager path (R13.5). The simulated ``Realized_R`` is recorded in
    the existing ``r_multiple`` column and a representation of the Exit_Breakdown
    in ``exit_breakdown`` (R6.1, R6.5).

    Outcome mapping (R6.4): a resolved plan with a positive ``Realized_R`` is a
    ``win`` and a non-positive ``Realized_R`` is a ``loss``. An ``open`` or
    ``invalid`` simulation is not yet scored — it expires (like the legacy path)
    only once ``JOURNAL_EXPIRY_SECONDS`` of real time has elapsed, otherwise it
    stays ``open``. Any reconstruction / simulation degeneracy degrades to "not
    scored yet" (``None``) rather than raising into the loop.
    """
    try:
        plan = trade_manager.plan_from_json(plan_text)
        if plan is None:
            # A corrupted / legacy-shaped plan column cannot be re-scored; leave
            # the trade open rather than fabricating an outcome.
            return None

        created_at = trade["created_at"]
        if not _is_num(created_at):
            return None
        created_ms = created_at * 1000.0

        # Only candles strictly after entry are part of the simulation window
        # (mirrors the legacy ``ts <= created_ms`` exclusion). simulate_plan
        # re-sorts and excludes non-finite candles itself, so this is just the
        # entry-time gate.
        subsequent = [
            c for c in candles
            if isinstance(c, dict)
            and _is_num(c.get("timestamp_ms"))
            and c.get("timestamp_ms") > created_ms
        ]

        config = trade_manager.resolve_trade_manager_config()
        result = trade_manager.simulate_plan(plan, subsequent, config)

        if result.status == "resolved" and _is_num(result.realized_r):
            status = "win" if result.realized_r > 0 else "loss"
            last_fill = result.fills[-1] if result.fills else None
            outcome_price = last_fill.price if last_fill is not None else None
            outcome_at = (
                last_fill.timestamp_ms / 1000.0
                if (last_fill is not None and _is_num(last_fill.timestamp_ms))
                else None
            )
            return {
                "status": status,
                "outcome_price": outcome_price if _is_num(outcome_price) else None,
                "outcome_at": outcome_at,
                "r_multiple": round(result.realized_r, 4),
                "exit_breakdown": _exit_breakdown_json(result),
            }

        # Unresolved (open) or invalid: expire only when enough real time has
        # elapsed since entry, exactly like the legacy single-target path.
        last_ts_ms = None
        for c in subsequent:
            ts = c.get("timestamp_ms")
            if _is_num(ts):
                last_ts_ms = ts
        if last_ts_ms is not None and (_now() - created_at) > JOURNAL_EXPIRY_SECONDS:
            return {
                "status": "expired",
                "outcome_price": None,
                "outcome_at": last_ts_ms / 1000.0,
                "r_multiple": None,
                "exit_breakdown": _exit_breakdown_json(result),
            }
        return None
    except Exception as e:
        print(f"[Trade_Journal] WARN: managed scoring failed (trade id={_row_value(trade, 'id')}): {e}")
        return None


def _score_one(trade: sqlite3.Row, candles: list) -> Optional[dict]:
    """Score a single open trade against candles. Returns an update dict or None.

    A trade carrying a persisted ``management_plan`` is scored MULTI-LEG by the
    Trade_Manager (``_score_managed_trade``) — the journal reuses
    ``trade_manager.simulate_plan`` rather than reimplementing the exit logic
    (R6.1, R6.5). A Single_Target_Trade (NULL ``management_plan``) keeps the
    EXACT legacy single-target path below, so its outcome and Realized_R are
    byte-for-byte identical to before this feature (R6.2, backward compatibility).

    Legacy conservative fill model: the position is assumed entered at the
    declared ``entry`` at ``created_at``; only candles strictly after that
    timestamp are considered. The first candle whose range touches a level
    decides the outcome; if a single candle touches BOTH the stop and the target,
    the loss is assumed (worst-case) so the journal never flatters itself.
    """
    # Managed trades delegate to the Trade_Manager; single-target trades fall
    # through to the unchanged legacy scoring below (R6.2). The plan column may be
    # absent on a row read before the migration, so access it defensively.
    plan_text = _row_value(trade, "management_plan")
    if plan_text:
        return _score_managed_trade(trade, candles, plan_text)

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
                        "UPDATE trades SET status=?, outcome_price=?, outcome_at=?, r_multiple=?, exit_breakdown=?, scored_at=? WHERE id=?",
                        (
                            upd["status"], upd["outcome_price"], upd["outcome_at"],
                            upd["r_multiple"], upd.get("exit_breakdown"), _now(), tr["id"],
                        ),
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
                # Flag a per-setup group below LOW_SAMPLE_THRESHOLD as a weak
                # prior so the agent does not over-fit to a thinly-traded
                # management-extended setup_key (R11.4). The win-rate / expectancy
                # in ``agg`` are already computed from the (multi-leg) Realized_R
                # written to ``r_multiple`` — positive -> win, non-positive ->
                # loss, mapped at scoring time — grouped by the now management-
                # extended setup_key, so no recomputation is needed here.
                agg["low_sample"] = agg["trades_scored"] < LOW_SAMPLE_THRESHOLD
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

        fc_up_prob = _forecast_up_probability(deff)

        conn = _connect()
        try:
            _init_db(conn)
            cur = conn.execute(
                """
                INSERT INTO trades (
                    created_at, mode, symbol, timeframe, action, entry, stop_loss,
                    take_profit, atr_14, conviction, risk_reward, setup_key,
                    setup_tags, source, status, outcome_price, outcome_at,
                    r_multiple, scored_at, forecast_up_probability
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    status, outcome_price, outcome_at, r_multiple, _now(), fc_up_prob,
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


def dedupe_thread_rows() -> int:
    """One-time cleanup: collapse duplicate journal rows to one per decision.

    Legacy rows written BEFORE the Bug 5 idempotency fix can contain duplicate
    decisions for a single committed run (observed: two HOLD rows for one run,
    ids 24202/24203) that pollute the track record and bias the agent toward
    HOLD. This function removes those duplicates so each committed decision is
    represented exactly once, keeping the EARLIEST row (``MIN(id)``) of every
    duplicate group.

    Two duplicate keys are used, matching the two eras of journal rows:

      1. **Non-NULL ``thread_id`` rows** (written after the fix): the duplicate
         key is the ``thread_id`` itself — the canonical per-decision identity
         (design: "remove duplicate (thread_id, decision) rows keeping the
         earliest row per thread"). At most one row per thread is retained.

      2. **Legacy NULL-``thread_id`` rows** (predate the column, so no thread
         identity exists): the duplicate key is the tuple
         ``(symbol, timeframe, action, setup_key, created_at-truncated-to-whole-
         seconds)``. ``created_at`` is a wall-clock ``time.time()`` value, so two
         rows from a re-entered finalize for the same decision share every
         dimension and land in the same one-second bucket. This key is
         deliberately conservative: two rows collapse ONLY when they agree on
         symbol, timeframe, action AND the full regime/session/etc. setup
         fingerprint AND were written within the same wall-clock second, so
         genuinely distinct decisions (different symbol/timeframe/action/setup,
         or made seconds apart) are never merged.

    Returns the total number of rows deleted (0 when the journal is already
    clean). This is a DESTRUCTIVE operation and is invoked ONLY explicitly (see
    the ``--dedupe`` CLI guard at the bottom of this module); it is NEVER called
    from ``record_decision``, ``_finalize_decision``, or any run path. Wrapped
    like ``purge`` so a failure degrades to "nothing removed" and never raises
    into a caller.
    """
    try:
        conn = _connect()
        try:
            _init_db(conn)
            deleted = 0
            # 1) Non-NULL thread_id rows: keep MIN(id) per thread_id.
            cur = conn.execute(
                """
                DELETE FROM trades
                WHERE thread_id IS NOT NULL
                  AND id NOT IN (
                      SELECT MIN(id) FROM trades
                      WHERE thread_id IS NOT NULL
                      GROUP BY thread_id
                  )
                """
            )
            deleted += cur.rowcount or 0
            # 2) Legacy NULL-thread_id rows: keep MIN(id) per conservative key
            #    (symbol, timeframe, action, setup_key, created_at truncated to a
            #    whole second). COALESCE guards NULL dimensions so they group
            #    consistently rather than being excluded by SQL NULL semantics.
            cur = conn.execute(
                """
                DELETE FROM trades
                WHERE thread_id IS NULL
                  AND id NOT IN (
                      SELECT MIN(id) FROM trades
                      WHERE thread_id IS NULL
                      GROUP BY
                          COALESCE(symbol, ''),
                          COALESCE(timeframe, ''),
                          COALESCE(action, ''),
                          COALESCE(setup_key, ''),
                          CAST(COALESCE(created_at, 0) AS INTEGER)
                  )
                """
            )
            deleted += cur.rowcount or 0
            conn.commit()
            return deleted
        finally:
            conn.close()
    except Exception as e:
        print(f"[Trade_Journal] WARN: dedupe_thread_rows failed: {e}")
        return 0


# ── Guarded CLI ─────────────────────────────────────────────────────────────
# The ONLY entry point that runs the destructive one-time cleanup. It executes
# ``dedupe_thread_rows()`` exclusively when invoked with an explicit ``--dedupe``
# flag (``python -m journal --dedupe`` or ``python journal.py --dedupe``); with
# no flag it is inert and touches no data, so importing this module or running it
# accidentally never mutates the journal (Bug 5: cleanup is explicit-only).
if __name__ == "__main__":
    import sys

    if "--dedupe" in sys.argv[1:]:
        removed = dedupe_thread_rows()
        print(f"[Trade_Journal] dedupe complete: removed {removed} duplicate row(s).")
    else:
        print(
            "[Trade_Journal] No action taken. Pass --dedupe to run the one-time "
            "duplicate-row cleanup (destructive)."
        )
