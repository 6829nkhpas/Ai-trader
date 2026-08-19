"""P2 — the immutable recommendation record.

`docs/business/PLAN_OF_ACTION.md` §4.2 lists blocker **P2**, and
`docs/business/SEBI_COMPLIANCE_BLUEPRINT.md` explains why the existing journal
does not satisfy it: a SEBI Research Analyst must be able to produce, years after
publication, the recommendation as issued, the rationale and risk factors behind
it, and the identity of the analyst responsible — and must be able to show the
record has not been altered since.

``journal.py`` records the same decisions, but it is a *measurement* store: rows
are updated when a trade is scored, and it has a ``purge()``. Both are correct for
its purpose and disqualifying for this one. So this module stores a second,
parallel, write-once copy. The duplication is the point — the journal can be
rebuilt or pruned without touching the regulatory record.

What each row must answer, and the column that answers it:

  * *What was recommended?* — ``action``, ``entry``, ``stop_loss``,
    ``take_profit``, ``horizon``, ``conviction``, ``risk_reward``
  * *On what basis?* — ``rationale_json`` (the defensibility record: volatility
    basis, risk-reward derivation, multi-timeframe bias, named patterns, and the
    mandated risk factors) and ``tool_inputs_json`` (the raw tool results the
    reasoning was drawn from)
  * *Who is responsible?* — ``analyst_of_record``
  * *By what process?* — ``model_id``, ``prompt_hash``, ``prompt_set_hash``
  * *To whom, and when?* — ``user_id``, ``created_at`` (distribution detail lives
    in the P5 interaction log, which shares this store's chain primitive)
  * *Has it been altered?* — ``prev_hash`` / ``row_hash`` (see ``hashchain.py``)

**``analyst_of_record`` is NULL until a certified person signs off.** It is read
from the ``ANALYST_OF_RECORD`` env var, which is unset today because blocker P8b
(the NISM-certified sign-off workflow) has not been built. A record that names
nobody is honest; one that names a placeholder is a false statement in a
regulatory record, so the column stays null and the gap stays visible.

There is no ``update``, no ``delete``, and no ``purge`` in this module, and the
table carries triggers that abort both statements. The retention floor is five
years from the recommendation date (SEBI), which is why the DPDP-erasure carve-out
matters: a data-subject deletion request cannot reach these rows while that floor
applies.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, Optional

import hashchain

TABLE = "recommendations"

# The payload columns, in the order the chain hashes them. This tuple IS the
# hashed payload definition: appending a column changes the hash of new rows only
# (old rows keep verifying, because each row's payload is rebuilt from the columns
# that existed when it was written — see `_payload_of`), but REORDERING it would
# not change any hash, since the payload is a dict keyed by name. Order here is
# for legibility and for the INSERT statement.
PAYLOAD_COLUMNS = (
    "created_at",
    "thread_id",
    "user_id",
    "symbol",
    "timeframe",
    "mode",
    "profile",
    "action",
    "entry",
    "stop_loss",
    "take_profit",
    "horizon",
    "conviction",
    "risk_reward",
    "rationale_json",
    "tool_inputs_json",
    "model_id",
    "prompt_hash",
    "prompt_set_hash",
    "analyst_of_record",
)


def _init(conn: sqlite3.Connection) -> None:
    """Create the table, its indexes and the append-only triggers. Idempotent.

    Every column except ``created_at``, ``action``, ``prev_hash`` and ``row_hash``
    is nullable on purpose: a HOLD has no levels, a forced HOLD may have no
    conviction, and ``analyst_of_record`` is null by design. Storing NULL is
    honest; storing 0 would fabricate a number in a regulatory record.
    """
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at        REAL NOT NULL,
            thread_id         TEXT,
            user_id           TEXT,
            symbol            TEXT,
            timeframe         TEXT,
            mode              TEXT,
            profile           TEXT,
            action            TEXT NOT NULL,
            entry             REAL,
            stop_loss         REAL,
            take_profit       REAL,
            horizon           TEXT,
            conviction        INTEGER,
            risk_reward       REAL,
            rationale_json    TEXT,
            tool_inputs_json  TEXT,
            model_id          TEXT,
            prompt_hash       TEXT,
            prompt_set_hash   TEXT,
            analyst_of_record TEXT,
            prev_hash         TEXT NOT NULL,
            row_hash          TEXT NOT NULL
        )
        """
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_symbol ON {TABLE}(symbol)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_created ON {TABLE}(created_at)")
    # One recommendation per LangGraph thread. Partial, so the (unexpected) case
    # of a thread-less commit does not collapse every such row into one.
    conn.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_thread "
        f"ON {TABLE}(thread_id) WHERE thread_id IS NOT NULL"
    )
    hashchain.enforce_append_only(conn, TABLE)
    conn.commit()


def _payload_of(row: Any) -> Dict[str, Any]:
    """Rebuild the hashed payload from a stored row.

    Only ``PAYLOAD_COLUMNS`` participate — ``id``, ``prev_hash`` and ``row_hash``
    are chain metadata, not content. A column absent from an older row reads as
    ``None``, which is exactly what was hashed when it did not exist, so adding a
    column never invalidates history.
    """
    return {column: hashchain._row_get(row, column) for column in PAYLOAD_COLUMNS}


def ensure_store(path: Optional[str] = None) -> None:
    """Create the table, indexes and append-only triggers without writing a row.

    ``record`` does this on every call, so this exists for the two cases that must
    not have to publish a recommendation first: an audit tool verifying a store
    that has not been written to yet, and a deployment that wants the triggers in
    place before the first run rather than after it.

    The distinction matters to ``verify_chain``: an EXISTING empty table verifies
    (``ok``, 0 rows — no recommendations yet), while a MISSING table is a
    verification *failure*, because the honest reading of "the file has no
    recommendations table" is that something happened to the file.
    """
    conn = hashchain.connect(path)
    try:
        _init(conn)
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def analyst_of_record() -> Optional[str]:
    """The certified analyst who signed off, or ``None`` until P8b exists.

    Read per call so the value appears the moment it is configured, without a
    restart. Whitespace-only is treated as unset: a record must not be signed by
    ``" "``.
    """
    value = os.getenv("ANALYST_OF_RECORD")
    return value.strip() if (value and value.strip()) else None


def record(
    *,
    action: str,
    thread_id: Optional[str] = None,
    user_id: Optional[str] = None,
    symbol: Optional[str] = None,
    timeframe: Optional[str] = None,
    mode: Optional[str] = None,
    profile: Optional[str] = None,
    entry: Optional[float] = None,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    horizon: Optional[str] = None,
    conviction: Optional[int] = None,
    risk_reward: Optional[float] = None,
    rationale: Any = None,
    tool_inputs: Any = None,
    model_id: Optional[str] = None,
    prompt_hash: Optional[str] = None,
    prompt_set_hash: Optional[str] = None,
    path: Optional[str] = None,
) -> Optional[int]:
    """Append one recommendation. Returns its row id, or the existing id.

    Idempotent per ``thread_id``, mirroring ``journal.record_decision``: a
    re-entered finalize for a thread that already has a row returns that row's id
    and writes nothing. Without this, LangGraph's checkpoint replay would append a
    second, differently-hashed copy of the same recommendation — which reads as
    two recommendations to anyone auditing the chain.

    Raises on write failure. Unlike the journal (best-effort by design), a dropped
    row here is the exact defect this store exists to prevent; the caller in
    ``graph.py`` decides how loud to be about it.
    """
    conn = hashchain.connect(path)
    try:
        _init(conn)
        tid = thread_id.strip() if isinstance(thread_id, str) and thread_id.strip() else None
        if tid is not None:
            existing = conn.execute(
                f"SELECT id FROM {TABLE} WHERE thread_id=? ORDER BY id ASC LIMIT 1", (tid,)
            ).fetchone()
            if existing is not None:
                return int(existing["id"])

        payload: Dict[str, Any] = {
            "created_at": hashchain.now(),
            "thread_id": tid,
            "user_id": user_id if isinstance(user_id, str) and user_id.strip() else None,
            "symbol": symbol,
            "timeframe": timeframe,
            "mode": mode,
            "profile": profile,
            "action": str(action or "HOLD").upper(),
            "entry": _finite(entry),
            "stop_loss": _finite(stop_loss),
            "take_profit": _finite(take_profit),
            "horizon": horizon,
            "conviction": _int_or_none(conviction),
            "risk_reward": _finite(risk_reward),
            # Serialised with the SAME canonicaliser the hash uses, so the stored
            # text and the hashed text can never disagree.
            "rationale_json": hashchain.canonical_json(rationale) if rationale is not None else None,
            "tool_inputs_json": (
                hashchain.canonical_json(tool_inputs) if tool_inputs is not None else None
            ),
            "model_id": model_id,
            "prompt_hash": prompt_hash,
            "prompt_set_hash": prompt_set_hash,
            "analyst_of_record": analyst_of_record(),
        }

        prev_hash = hashchain.chain_tip(conn, TABLE)
        row_hash = hashchain.hash_payload(prev_hash, payload)

        columns = ", ".join((*PAYLOAD_COLUMNS, "prev_hash", "row_hash"))
        placeholders = ", ".join("?" for _ in range(len(PAYLOAD_COLUMNS) + 2))
        values = [payload[column] for column in PAYLOAD_COLUMNS] + [prev_hash, row_hash]
        try:
            cursor = conn.execute(
                f"INSERT INTO {TABLE} ({columns}) VALUES ({placeholders})", values
            )
        except sqlite3.IntegrityError:
            # Lost the race on the partial UNIQUE index — another writer committed
            # this thread's row first. Its row is the record; return it.
            conn.rollback()
            if tid is None:
                raise
            existing = conn.execute(
                f"SELECT id FROM {TABLE} WHERE thread_id=? ORDER BY id ASC LIMIT 1", (tid,)
            ).fetchone()
            if existing is None:
                raise
            return int(existing["id"])
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def verify_chain(path: Optional[str] = None) -> hashchain.ChainVerification:
    """Verify the recommendation chain end to end. Never raises.

    This is the function an inspection runs. A ``False`` result names the first
    row that does not reconcile, so "the record was altered" becomes "row 412 was
    altered" — and everything before row 412 is still provably intact.
    """
    return hashchain.verify_table(TABLE, _payload_of, path=path)


def count(path: Optional[str] = None) -> int:
    """Number of stored recommendations. Never raises; returns 0 on any failure."""
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


def latest(limit: int = 20, path: Optional[str] = None) -> list:
    """Most recent rows, newest first. Read-only helper for audit tooling."""
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


# ── Coercion helpers ──────────────────────────────────────────────────────────
# Both return None rather than a substitute value. A price of 0.0 or a conviction
# of 0 in a recommendation record would be a claim about what was recommended.


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
