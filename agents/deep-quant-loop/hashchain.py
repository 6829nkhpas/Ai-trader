"""Append-only, tamper-evident storage primitive for the compliance records.

Shared by the two records `docs/business/PLAN_OF_ACTION.md` §4.2 makes blocking:

  * **P2** — ``reco_store.py``, the immutable recommendation record. Every
    published recommendation must be reproducible years later: the levels, the
    evidence behind them, the model and the prompt that produced it.
  * **P5** — ``interaction_log.py``, the record of what was sent to whom and when.

Both need the same three properties, which is why they live here rather than
being written twice:

1. **Append-only.** No UPDATE and no DELETE path exists in either module, and
   SQLite ``BEFORE UPDATE`` / ``BEFORE DELETE`` triggers abort the statement even
   if one is issued by hand from a shell. There is deliberately no ``purge()``
   (contrast ``journal.py``, which has one — the journal is a performance
   measurement store, not a regulatory record).
2. **Tamper-evident.** Each row stores ``prev_hash`` (the previous row's
   ``row_hash``) and ``row_hash = sha256(prev_hash || canonical_json(payload))``.
   Editing, reordering, inserting or removing any row breaks the chain at that
   point and ``verify_chain`` reports the first row that fails, so an inspection
   gets a specific answer rather than "looks fine".
3. **Deterministic.** ``canonical_json`` sorts keys and normalises numbers and
   line endings, so the same payload hashes identically on any machine and in any
   Python version — otherwise re-verification years later would fail for reasons
   unrelated to tampering.

The two layers are complementary, not redundant. The triggers stop casual and
accidental mutation (an ORM, a support script, a habit of "just fixing" a row).
An operator with the database file can still drop a trigger — which is exactly
what the chain is for: it makes the edit *provable* rather than preventable.
Neither layer defends against destroying the whole file, so the runbook's
off-machine backup requirement is part of this control, not an extra.

Failure posture differs from the rest of the agent on purpose. Read helpers never
raise. Writes DO propagate their exception to the caller, because a silently
dropped compliance row is the failure this module exists to prevent — the callers
in ``graph.py`` and ``main.py`` decide whether that is fatal for their path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

# ── Location ──────────────────────────────────────────────────────────────────
# One file for both records: a single artefact to back up, snapshot and hand to
# an auditor. The two tables carry independent chains, so neither can be affected
# by the other's write volume.
_DEFAULT_DB = os.path.join(os.path.abspath(os.path.dirname(__file__)), "compliance.db")


def db_path() -> str:
    """Resolve the compliance database path at call time.

    Read per call rather than captured at import so a test (or an operator moving
    the store to a mounted volume) can point ``COMPLIANCE_DB_PATH`` somewhere else
    without reloading the module.
    """
    configured = os.getenv("COMPLIANCE_DB_PATH")
    return configured.strip() if (configured and configured.strip()) else _DEFAULT_DB


# The chain's first link. A row whose ``prev_hash`` is this is claiming to be the
# first row in its table, which ``verify_chain`` checks rather than assumes.
GENESIS_HASH = "0" * 64


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """Open the compliance store. ``check_same_thread=False`` is NOT set.

    Each writer opens its own short-lived connection (the pattern ``journal.py``
    uses), so there is no shared handle to leak across LangGraph's executor
    threads.
    """
    conn = sqlite3.connect(path or db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    # Foreign keys are irrelevant here, but WAL matters: a reader running
    # `verify_chain` during an audit must not block the agent from appending.
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        # An older SQLite or a read-only filesystem — the chain does not depend
        # on WAL, so degrade rather than fail the write.
        pass
    return conn


# ── Canonical serialisation ───────────────────────────────────────────────────


def _canonicalise(value: Any) -> Any:
    """Recursively coerce ``value`` into JSON-stable primitives.

    Three specific hazards are handled, each of which would otherwise make a
    replayed hash differ from the stored one without anything being tampered
    with:

      * ``float`` values that are NaN or ±Infinity — ``json.dumps`` emits
        ``NaN``/``Infinity``, which is not valid JSON and does not round-trip.
        They become ``None``, matching the "every numeric leaf is finite or null"
        rule the rest of this codebase follows.
      * ``bool`` before ``int`` — ``isinstance(True, int)`` is True in Python, so
        the order of these checks decides whether ``True`` is stored as ``true``
        or ``1``.
      * Non-JSON objects (datetime, Decimal, dataclass, model object) become
        their ``str()``. Lossy, but stable and never raises mid-write.
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # NaN != NaN, so a non-finite float could never be re-verified anyway.
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        # str() on keys: JSON object keys are strings, and json.dumps would
        # stringify them anyway — doing it here keeps sort order deterministic
        # for mixed-type keys instead of raising.
        return {str(k): _canonicalise(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalise(v) for v in value]
    if isinstance(value, (set, frozenset)):
        # Sets have no order; sorting their string forms makes the hash stable.
        return sorted(str(v) for v in value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    return str(value)


def canonical_json(payload: Any) -> str:
    """Serialise ``payload`` to the one form its hash is defined over.

    ``sort_keys`` removes dict-ordering as a variable, the compact separators
    remove whitespace as one, and ``ensure_ascii=False`` keeps ₹ and other
    non-ASCII text readable in the stored row (the hash is taken over UTF-8 bytes,
    so this does not affect stability).
    """
    return json.dumps(
        _canonicalise(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def hash_payload(prev_hash: Optional[str], payload: Any) -> str:
    """The chain link: ``sha256(prev_hash || "\\n" || canonical_json(payload))``.

    The separator matters. Without it, a chain could be forged by moving
    characters between the two fields (the classic length-extension-style
    concatenation ambiguity), and ``prev_hash`` is fixed-width only as long as
    every caller passes a real hash — which a corrupted row does not.
    """
    prev = prev_hash if isinstance(prev_hash, str) and prev_hash else GENESIS_HASH
    material = f"{prev}\n{canonical_json(payload)}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def now() -> float:
    """Wall-clock seconds. Named so callers read as `hashchain.now()`."""
    return time.time()


# ── Append-only schema helpers ────────────────────────────────────────────────


def enforce_append_only(conn: sqlite3.Connection, table: str) -> None:
    """Install ABORT triggers so ``table`` rejects UPDATE and DELETE.

    Idempotent (``IF NOT EXISTS``), so it runs on every open. ``table`` is
    interpolated into DDL because SQLite cannot parametrise identifiers; it is
    validated first so this can never become an injection point from a caller
    that passes a computed name.
    """
    if not table.replace("_", "").isalnum():
        raise ValueError(f"unsafe table name for trigger DDL: {table!r}")
    for verb in ("UPDATE", "DELETE"):
        conn.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS {table}_no_{verb.lower()}
            BEFORE {verb} ON {table}
            BEGIN
                SELECT RAISE(ABORT, '{table} is append-only: {verb} is not permitted');
            END
            """
        )


def chain_tip(conn: sqlite3.Connection, table: str) -> str:
    """The ``row_hash`` of the last row in ``table``, or ``GENESIS_HASH`` if empty.

    Ordered by ``id`` (the AUTOINCREMENT insertion order), not by ``created_at``:
    a clock that steps backwards must not be able to reorder the chain.
    """
    if not table.replace("_", "").isalnum():
        raise ValueError(f"unsafe table name: {table!r}")
    row = conn.execute(
        f"SELECT row_hash FROM {table} ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return GENESIS_HASH
    tip = row["row_hash"] if isinstance(row, sqlite3.Row) else row[0]
    return tip if isinstance(tip, str) and tip else GENESIS_HASH


# ── Verification ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChainVerification:
    """The outcome of verifying one table's chain.

    ``ok`` is the only field a caller needs for a pass/fail gate; the rest exist
    so a failure is actionable. ``broken_at_id`` names the first row whose hash or
    linkage does not reconcile — everything before it is intact, which is what
    makes partial tampering detectable rather than just "the chain is broken".
    """

    ok: bool
    rows: int
    broken_at_id: Optional[int] = None
    reason: Optional[str] = None

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.ok


def verify_rows(rows: Iterable[Any], payload_of) -> ChainVerification:
    """Verify an in-order sequence of chained rows. Pure — no I/O.

    ``payload_of(row)`` must rebuild the exact payload that was hashed at write
    time. Keeping that projection in the caller is deliberate: the payload
    definition belongs to the record (``reco_store`` / ``interaction_log``), and a
    single shared "hash everything in the row" rule would silently change meaning
    the first time either table gained a column.
    """
    prev = GENESIS_HASH
    count = 0
    for row in rows:
        count += 1
        row_id = _row_get(row, "id")
        stored_prev = _row_get(row, "prev_hash")
        stored_hash = _row_get(row, "row_hash")

        if stored_prev != prev:
            return ChainVerification(
                ok=False,
                rows=count,
                broken_at_id=row_id,
                reason=(
                    "prev_hash does not match the previous row's row_hash — a row "
                    "was inserted, removed or reordered"
                ),
            )
        expected = hash_payload(prev, payload_of(row))
        if stored_hash != expected:
            return ChainVerification(
                ok=False,
                rows=count,
                broken_at_id=row_id,
                reason="row_hash does not match the row's contents — the row was edited",
            )
        prev = stored_hash
    return ChainVerification(ok=True, rows=count)


def verify_table(
    table: str,
    payload_of,
    path: Optional[str] = None,
    columns: Sequence[str] = ("*",),
) -> ChainVerification:
    """Verify ``table``'s chain end to end. Never raises.

    A missing table or unreadable file is reported as a failed verification with
    the reason attached, not as an exception: an audit tool asking "is the chain
    intact?" needs an answer in every case, including "there is no chain".
    """
    if not table.replace("_", "").isalnum():
        return ChainVerification(ok=False, rows=0, reason=f"unsafe table name: {table!r}")
    try:
        conn = connect(path)
    except sqlite3.Error as exc:
        return ChainVerification(ok=False, rows=0, reason=f"cannot open store: {exc}")
    try:
        select = ", ".join(columns)
        rows = conn.execute(f"SELECT {select} FROM {table} ORDER BY id ASC").fetchall()
    except sqlite3.Error as exc:
        return ChainVerification(ok=False, rows=0, reason=f"cannot read {table}: {exc}")
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    return verify_rows(rows, payload_of)


def _row_get(row: Any, key: str) -> Any:
    """Read ``key`` from a sqlite3.Row, a dict, or anything else with a mapping.

    Tests build plain dicts; production passes ``sqlite3.Row``. Supporting both
    keeps ``verify_rows`` pure and directly unit-testable without a database.
    """
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return getattr(row, key, None)
