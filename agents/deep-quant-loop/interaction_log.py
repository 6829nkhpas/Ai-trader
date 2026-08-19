"""P5 — the interaction log: what was published, to whom, and when.

`docs/business/PLAN_OF_ACTION.md` §4.2 blocker **P5**. A SEBI Research Analyst must
be able to show the distribution side of a recommendation, not only the
recommendation itself: which client received it, at what time, what they asked,
and what they were told. `reco_store.py` (P2) answers *what was recommended*; this
module answers *what was communicated*. Together they reconstruct a client
interaction years later.

Built on the same ``hashchain`` primitive, for the same reason: an interaction log
that can be edited after a complaint is not evidence. Append-only triggers plus a
hash chain, one row per event, verified by ``verify_chain()``.

**Content is stored verbatim, not hashed.** A digest would prove a message was not
altered while being useless for the question an inspection actually asks — "what
did you tell this client?" So the question text and the answer text are the record.
That has two consequences, both deliberate:

  * The store contains user-submitted text and user ids. It is gitignored, and the
    rotation runbook's off-machine backup requirement covers it.
  * There is no ``purge()`` and no delete path. SEBI's retention floor is five
    years; a DPDP Act 2023 erasure request cannot reach these rows while that floor
    applies, which is the carve-out `docs/business/SEBI_COMPLIANCE_BLUEPRINT.md`
    flags. Erasure requests must be answered from the *product* stores, not here,
    and the legal basis for retaining these rows is the statutory obligation.

Two event rows per interaction, not one:

  * ``request`` — written BEFORE any work starts, so an interaction that crashes,
    is refused, or times out still leaves a trace. A log that only records
    successes cannot show that a gate refused someone.
  * ``outcome`` — written when the stream terminates, carrying the status and (for
    a Q&A turn) the answer text that was actually sent.

Refusals are first-class outcomes: ``status="refused_entitlement"`` records the
P1 SKU gate turning an unlicensed caller away, and ``refusal_category`` records
which personalisation category the P8a guardrail matched. Those rows are the
written evidence that the boundary held — the thing Gate 0→1 asks to see.

Failure posture matches ``reco_store``: this module raises on a failed write, and
the caller in ``main.py`` swallows it with a WARN. An HTTP endpoint that 500s
because the audit log is unwritable trades a compliance gap for an outage; the WARN
is the operator's signal that the gap happened.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, Optional

import hashchain

TABLE = "interactions"

# The columns the chain hashes, in INSERT order. See `reco_store.PAYLOAD_COLUMNS`
# for why appending is safe and what `_payload_of` guarantees for old rows.
PAYLOAD_COLUMNS = (
    "created_at",
    "event",
    "kind",
    "thread_id",
    "user_id",
    "mode",
    "symbol",
    "timeframe",
    "profile",
    "model",
    "content",
    "status",
    "detail",
    "refusal_category",
)

# ``event`` values. A row is either the inbound request or the terminal outcome.
EVENT_REQUEST = "request"
EVENT_OUTCOME = "outcome"

# ``kind`` values — the surface that was used. These mirror the FastAPI endpoints
# so a row can be traced back to the exact route that produced it.
KIND_RUN = "run"
KIND_RESUME = "resume"
KIND_QA = "qa"
KIND_CANCEL = "cancel"

# A single message is capped so one pathological payload cannot make the store
# unreadable. Truncation is RECORDED in the text rather than done silently: a row
# that says it was truncated is honest, a row that quietly lost its tail is not.
MAX_CONTENT_CHARS = 100_000


def _init(conn: sqlite3.Connection) -> None:
    """Create the table, its indexes and the append-only triggers. Idempotent."""
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at       REAL NOT NULL,
            event            TEXT NOT NULL,
            kind             TEXT NOT NULL,
            thread_id        TEXT,
            user_id          TEXT,
            mode             TEXT,
            symbol           TEXT,
            timeframe        TEXT,
            profile          TEXT,
            model            TEXT,
            content          TEXT,
            status           TEXT,
            detail           TEXT,
            refusal_category TEXT,
            prev_hash        TEXT NOT NULL,
            row_hash         TEXT NOT NULL
        )
        """
    )
    # The two lookups an inspection actually performs: everything for one client,
    # and everything for one interaction.
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_user ON {TABLE}(user_id)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_thread ON {TABLE}(thread_id)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_created ON {TABLE}(created_at)")
    # NOTE: deliberately NO uniqueness constraint. Unlike a recommendation (one per
    # thread), a thread has many interactions — several Q&A turns, a resume, a
    # cancel — and collapsing them would destroy the sequence that makes the log
    # a log.
    hashchain.enforce_append_only(conn, TABLE)
    conn.commit()


def _payload_of(row: Any) -> Dict[str, Any]:
    """Rebuild the hashed payload from a stored row. See ``reco_store._payload_of``."""
    return {column: hashchain._row_get(row, column) for column in PAYLOAD_COLUMNS}


def ensure_store(path: Optional[str] = None) -> None:
    """Create the table and triggers without writing a row.

    Called at service startup so the append-only guarantee is in place before the
    first interaction rather than after it.
    """
    conn = hashchain.connect(path)
    try:
        _init(conn)
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _text(value: Any) -> Optional[str]:
    """Coerce to stored text: None stays None, everything else is str()-ed.

    Whitespace-only becomes None so an empty field reads as absent rather than as
    a message that consisted of a space.
    """
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if not text.strip():
        return None
    if len(text) > MAX_CONTENT_CHARS:
        dropped = len(text) - MAX_CONTENT_CHARS
        return f"{text[:MAX_CONTENT_CHARS]}\n[truncated {dropped} characters]"
    return text


def _append(payload: Dict[str, Any], path: Optional[str]) -> int:
    """Append one chained row. Raises on failure — see the module docstring."""
    conn = hashchain.connect(path)
    try:
        _init(conn)
        prev_hash = hashchain.chain_tip(conn, TABLE)
        row_hash = hashchain.hash_payload(prev_hash, payload)
        columns = ", ".join((*PAYLOAD_COLUMNS, "prev_hash", "row_hash"))
        placeholders = ", ".join("?" for _ in range(len(PAYLOAD_COLUMNS) + 2))
        values = [payload[column] for column in PAYLOAD_COLUMNS] + [prev_hash, row_hash]
        cursor = conn.execute(
            f"INSERT INTO {TABLE} ({columns}) VALUES ({placeholders})", values
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def record_request(
    *,
    kind: str,
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
    content: Any = None,
    mode: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    profile: Optional[str] = None,
    model: Optional[str] = None,
    path: Optional[str] = None,
) -> int:
    """Log an inbound request, before any analysis runs.

    Written first on purpose. If this only ran after a successful response, the log
    would silently omit every refused, failed and abandoned interaction — the exact
    population a complaint is drawn from.
    """
    return _append(
        {
            "created_at": hashchain.now(),
            "event": EVENT_REQUEST,
            "kind": _text(kind) or "unknown",
            "thread_id": _text(thread_id),
            "user_id": _text(user_id),
            "mode": _text(mode),
            "symbol": _text(symbol),
            "timeframe": _text(timeframe),
            "profile": _text(profile),
            "model": _text(model),
            "content": _text(content),
            "status": None,
            "detail": None,
            "refusal_category": None,
        },
        path,
    )


def record_outcome(
    *,
    kind: str,
    status: str,
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
    content: Any = None,
    detail: Any = None,
    refusal_category: Optional[str] = None,
    mode: Optional[str] = None,
    model: Optional[str] = None,
    path: Optional[str] = None,
) -> int:
    """Log how an interaction ended, and what was sent back.

    ``content`` carries the answer text for a Q&A turn — the part of the record
    that answers "what did you tell this client?". ``status`` distinguishes
    completed / paused / cancelled / error / refused_entitlement, and
    ``refusal_category`` names the personalisation category when the P8a guardrail
    refused the turn (compliance blocker P8a), so the log shows the boundary
    holding rather than merely not being crossed.
    """
    return _append(
        {
            "created_at": hashchain.now(),
            "event": EVENT_OUTCOME,
            "kind": _text(kind) or "unknown",
            "thread_id": _text(thread_id),
            "user_id": _text(user_id),
            "mode": _text(mode),
            "symbol": None,
            "timeframe": None,
            "profile": None,
            "model": _text(model),
            "content": _text(content),
            "status": _text(status) or "unknown",
            "detail": _text(detail),
            "refusal_category": _text(refusal_category),
        },
        path,
    )


def verify_chain(path: Optional[str] = None) -> hashchain.ChainVerification:
    """Verify the interaction chain end to end. Never raises."""
    return hashchain.verify_table(TABLE, _payload_of, path=path)


def count(path: Optional[str] = None) -> int:
    """Number of logged events. Never raises; returns 0 on any failure."""
    try:
        conn = hashchain.connect(path)
    except sqlite3.Error:
        return 0
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {TABLE}").fetchone()
        return int(row["n"]) if row is not None else 0
    except sqlite3.Error:
        return 0
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def for_thread(thread_id: str, path: Optional[str] = None) -> list:
    """Every event for one interaction, oldest first. Read-only audit helper."""
    try:
        conn = hashchain.connect(path)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            f"SELECT * FROM {TABLE} WHERE thread_id=? ORDER BY id ASC", (str(thread_id),)
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def for_user(user_id: str, limit: int = 500, path: Optional[str] = None) -> list:
    """Every event for one client, newest first. The subject-access / audit query."""
    try:
        conn = hashchain.connect(path)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            f"SELECT * FROM {TABLE} WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (str(user_id), max(0, int(limit))),
        ).fetchall()
        return [dict(row) for row in rows]
    except (sqlite3.Error, TypeError, ValueError):
        return []
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def latest(limit: int = 50, path: Optional[str] = None) -> list:
    """Most recent events, newest first. Read-only helper for audit tooling."""
    try:
        conn = hashchain.connect(path)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            f"SELECT * FROM {TABLE} ORDER BY id DESC LIMIT ?", (max(0, int(limit)),)
        ).fetchall()
        return [dict(row) for row in rows]
    except (sqlite3.Error, TypeError, ValueError):
        return []
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
