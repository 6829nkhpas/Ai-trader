"""Session_Store — the durable, user-owned record of Find Quant Trade conversations.

What this replaces
------------------
Nothing, which is the problem. The conversation lived entirely in the browser's
Zustand store, keyed by ``${SYMBOL}::${PROFILE}``. Consequences, all verified in the
code before this was written:

* a page reload lost the whole transcript;
* a second FIND on the same symbol+profile OVERWROTE the first session, because the
  key collided;
* two timeframes for one symbol could not coexist at all;
* Q&A chunks were routed by a React closure into whatever session was on screen, so
  switching tabs mid-answer silently dropped the rest of it.

Four entities, deliberately not one
-----------------------------------
::

    SESSION            the conversation. Opaque id. Owns message ordering.
      +-- RUN          one analysis execution. 1 run <-> 1 LangGraph thread_id.
      |     +-- run_events   the ordered, durable SSE transcript (the glass box)
      +-- MESSAGE      one user-visible chat turn, optionally attributed to a run

A second FIND in one session creates a NEW run with a NEW thread_id. It must not
reuse the thread, and that is not a preference: ``reco_store`` enforces
``UNIQUE(thread_id) WHERE thread_id IS NOT NULL``, so a second committed decision on
the same thread would collide with the append-only compliance record.

Why ``run_events`` stores raw frames rather than rendered text
-------------------------------------------------------------
Three things follow from keeping the frames, none of which a flattened blob allows:

1. **Rehydration replays the existing reducer.** Reopening a session feeds stored
   frames through the frontend's property-tested ``applyStreamEvent``, so a restored
   transcript is identical to a live one and there is no second rendering path to
   drift.
2. **Gap-free reattach.** ``GET /stream/{thread_id}?after_seq=N`` can replay from
   here, which closes the existing hole where frames published while no subscriber is
   attached are lost forever (``_publish_frame`` returns early on an empty subscriber
   set).
3. **Structure survives.** ``TOOL_CALL_START.args``, ``DECISION.execution_levels``,
   ``BEST_CURRENT_READ.levels`` keep their shape instead of becoming prose.

What this store is NOT
----------------------
It is **not** the compliance record. ``interaction_log`` (P5) and ``reco_store`` (P2)
remain hash-chained, append-only and trigger-protected in ``compliance.db``; this is a
separate file precisely so a mutable store never shares a database with tables whose
integrity depends on being immutable. ``delete_session(hard=True)`` scrubs rows here
and provably does not touch that file — there is a test asserting its hash is
unchanged.

Concurrency posture, stated rather than implied
-----------------------------------------------
SQLite in WAL mode, one writer process. deep-quant is already single-replica for three
independent reasons (``_CANCELLED``, ``_SUBSCRIBERS`` and the LangGraph checkpointer
are all process-local), so this adds no new constraint — but it does make the existing
one load-bearing for user data, which is why it is written down here and in
``docs/DEPLOYMENT.md`` section 7.2 rather than left to be discovered.

Every function that touches user-owned data takes ``user_id`` and puts it in the
WHERE clause. There is deliberately no ``get_session(session_id)``.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ── Location ──────────────────────────────────────────────────────────────────

_DEFAULT_DB = os.path.join(os.path.abspath(os.path.dirname(__file__)), "sessions.db")

ENV_DB_PATH = "SESSIONS_DB_PATH"
ENV_RETENTION_DAYS = "RUN_EVENTS_RETENTION_DAYS"

SCHEMA_VERSION = 1


def _sql_in(values: Iterable[str]) -> str:
    """Render a value list for a SQL ``IN`` clause.

    Not just ``f"{tuple(values)}"``. Python renders a one-element tuple as ``('x',)``,
    and that trailing comma is a syntax error in SQLite — so interpolating the
    vocabulary tuples directly works only for as long as every one of them happens to
    have two or more members. This makes that a non-issue rather than a trap for
    whoever later narrows a status set to one value.

    Single-quotes are doubled, so a vocabulary constant containing an apostrophe cannot
    break out of the literal. These are module constants rather than user input, but a
    schema builder that is only safe because of who calls it is one refactor away from
    not being.
    """
    rendered = ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)
    return f"({rendered})"

# ── Vocabularies ──────────────────────────────────────────────────────────────
# Mirrored by CHECK constraints. Both exist on purpose: the constraint is the
# guarantee, and these are what the API layer validates against so a bad request is a
# 422 rather than an IntegrityError surfacing as a 500.

SESSION_ACTIVE = "active"
SESSION_ARCHIVED = "archived"
SESSION_DELETED = "deleted"
SESSION_STATUSES = (SESSION_ACTIVE, SESSION_ARCHIVED, SESSION_DELETED)

RUN_FIND = "find"
RUN_VERIFY = "verify"
RUN_KINDS = (RUN_FIND, RUN_VERIFY)

# ``truncated`` is the one that earns its place: it is what a run becomes when the
# process died mid-stream. Without it, a half-written answer is indistinguishable
# from a complete one, which is the single failure mode this store must not have.
RUN_RUNNING = "running"
RUN_WATCHING = "watching"
RUN_COMPLETE = "complete"
RUN_CANCELLED = "cancelled"
RUN_ERROR = "error"
RUN_TRUNCATED = "truncated"
RUN_STATUSES = (RUN_RUNNING, RUN_WATCHING, RUN_COMPLETE, RUN_CANCELLED, RUN_ERROR, RUN_TRUNCATED)
RUN_LIVE_STATUSES = (RUN_RUNNING, RUN_WATCHING)

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_SYSTEM = "system"
ROLES = (ROLE_USER, ROLE_ASSISTANT, ROLE_SYSTEM)

KIND_ANALYSIS_REQUEST = "analysis_request"
KIND_ANALYSIS_ANSWER = "analysis_answer"
KIND_QA_QUESTION = "qa_question"
KIND_QA_ANSWER = "qa_answer"
KIND_NOTICE = "notice"
MESSAGE_KINDS = (
    KIND_ANALYSIS_REQUEST,
    KIND_ANALYSIS_ANSWER,
    KIND_QA_QUESTION,
    KIND_QA_ANSWER,
    KIND_NOTICE,
)

MSG_STREAMING = "streaming"
MSG_COMPLETE = "complete"
MSG_TRUNCATED = "truncated"
MSG_ERROR = "error"
MSG_CANCELLED = "cancelled"
MESSAGE_STATUSES = (MSG_STREAMING, MSG_COMPLETE, MSG_TRUNCATED, MSG_ERROR, MSG_CANCELLED)

# A single message body is capped so one pathological stream cannot make the store
# unreadable. Mirrors interaction_log.MAX_CONTENT_CHARS, and truncation is RECORDED in
# the text rather than done silently.
MAX_CONTENT_CHARS = 200_000


def db_path() -> str:
    """Resolve the store path at call time.

    Per call, not captured at import, matching ``hashchain.db_path()`` — a test or an
    operator moving the store onto a volume must not have to reload the module.
    (``journal.py`` captures its path at import and that has been a recurring
    annoyance in its tests, hence the deliberate difference.)
    """
    configured = os.getenv(ENV_DB_PATH)
    return configured.strip() if (configured and configured.strip()) else _DEFAULT_DB


def now() -> float:
    """Epoch seconds as a float, matching ``hashchain.now()`` and the other stores."""
    return time.time()


# ── Opaque identifiers ────────────────────────────────────────────────────────

# Crockford base32 minus I, L, O and U — so an id cannot be transcribed into a
# different valid id, and cannot accidentally spell a word.
_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_id(prefix: str) -> str:
    """A time-sortable, opaque identifier: ``<prefix>_<26 chars>``.

    ULID layout: 48 bits of millisecond timestamp then 80 bits of randomness.

    Time-sortable matters for pagination — a cursor over ``(updated_at, session_id)``
    needs a tiebreaker that does not shuffle, and a random UUID gives none.

    Opaque matters more. The old identity was ``thread_${symbol}_${Date.now()}``, which
    is guessable to within a second, and ``GET /stream/{thread_id}`` had no ownership
    check. Ownership is enforced independently now, but an unguessable id means a
    single missed check is not immediately exploitable.

    ``secrets`` rather than ``random``: 80 bits from a PRNG seeded predictably is not
    unguessable, and this is used as a capability-adjacent identifier.
    """
    stamp = int(now() * 1000) & ((1 << 48) - 1)
    value = (stamp << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(_B32[value & 0x1F])
        value >>= 5
    return f"{prefix}_{''.join(reversed(chars))}"


# ── Connection ────────────────────────────────────────────────────────────────


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """Open the store with the pragmas this schema depends on.

    ``foreign_keys`` is per-CONNECTION in SQLite and defaults to OFF. Without it here,
    ``ON DELETE CASCADE`` on messages/runs/run_events silently does nothing and a
    deleted session leaves orphans behind — so this line is the cascade, not a
    nicety.

    ``busy_timeout`` rather than failing fast: a stream flush and a read for the
    session list can legitimately collide, and waiting briefly is correct where
    dropping a transcript batch is not.
    """
    target = path or db_path()
    # A path under a volume mount whose directory does not exist yet would otherwise
    # fail with a bare "unable to open database file", which reads like a permissions
    # fault. Shared with the checkpointer via state_paths so there is one copy.
    try:
        import state_paths

        state_paths.ensure_parent_dir(target)
    except Exception:  # noqa: BLE001 - a missing helper must not stop the store opening
        pass
    conn = sqlite3.connect(target, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        # Older SQLite or a read-only mount. Correctness does not depend on WAL;
        # concurrency does, so degrade rather than refuse.
        pass
    return conn


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA_V1 = f"""
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    title         TEXT,
    symbol        TEXT NOT NULL,
    profile       TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT '{SESSION_ACTIVE}',
    active_run_id TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    archived_at   REAL,
    metadata_json TEXT,
    CHECK (status IN {_sql_in(SESSION_STATUSES)}),
    CHECK (length(user_id) > 0),
    CHECK (length(symbol) > 0),
    CHECK (length(profile) > 0),
    CHECK (length(timeframe) > 0),
    -- An 'active' row with an archive timestamp, or an archived row without one, is a
    -- state the read model cannot represent. Made unrepresentable instead of trusted.
    CHECK ((status = '{SESSION_ACTIVE}') = (archived_at IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_sessions_owner_activity
    ON sessions(user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_owner_symbol
    ON sessions(user_id, symbol, status);

CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    user_id           TEXT NOT NULL,
    thread_id         TEXT NOT NULL,
    kind              TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    timeframe         TEXT NOT NULL,
    profile           TEXT NOT NULL,
    model             TEXT,
    manual_trade_json TEXT,
    status            TEXT NOT NULL,
    terminal_status   TEXT,
    started_at        REAL NOT NULL,
    ended_at          REAL,
    last_event_at     REAL NOT NULL,
    last_seq          INTEGER NOT NULL DEFAULT 0,
    CHECK (kind IN {_sql_in(RUN_KINDS)}),
    CHECK (status IN {_sql_in(RUN_STATUSES)}),
    CHECK (last_seq >= 0)
);
-- 1 run <-> 1 LangGraph thread. This is the ONLY join between this store and the
-- checkpoint, and it is what makes /stream and /cancel ownership a single indexed read.
CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_thread ON runs(thread_id);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, started_at);
CREATE INDEX IF NOT EXISTS idx_runs_owner ON runs(user_id);
-- Exactly what the startup reconciliation pass scans.
CREATE INDEX IF NOT EXISTS idx_runs_live ON runs(status, last_event_at)
    WHERE status IN {_sql_in(RUN_LIVE_STATUSES)};

CREATE TABLE IF NOT EXISTS messages (
    message_id    TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    run_id        TEXT REFERENCES runs(run_id) ON DELETE SET NULL,
    seq           INTEGER NOT NULL,
    role          TEXT NOT NULL,
    kind          TEXT NOT NULL,
    content       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    error_detail  TEXT,
    activity_json TEXT,
    client_msg_id TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    CHECK (role IN {_sql_in(ROLES)}),
    CHECK (kind IN {_sql_in(MESSAGE_KINDS)}),
    CHECK (status IN {_sql_in(MESSAGE_STATUSES)}),
    CHECK (seq > 0),
    -- A user's own message can never be half-written. Schema fact, not convention.
    CHECK (role <> '{ROLE_USER}' OR status = '{MSG_COMPLETE}')
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_seq ON messages(session_id, seq);
-- Client idempotency: a retried send cannot produce a duplicate turn.
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_client
    ON messages(session_id, client_msg_id) WHERE client_msg_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_messages_run ON messages(run_id);

CREATE TABLE IF NOT EXISTS run_events (
    run_id       TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    event        TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   REAL NOT NULL,
    -- Replay idempotency by construction: a duplicated or re-delivered frame cannot
    -- be stored twice, so a reconnect cannot double the transcript.
    PRIMARY KEY (run_id, seq),
    CHECK (seq > 0)
);

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring the schema to ``SCHEMA_VERSION``. Idempotent.

    A hand-rolled stepper rather than alembic: one new dependency plus a migrations
    directory to carry a single ``CREATE TABLE`` is not a trade worth making, and this
    service has no other schema-migration machinery to reuse. When there is a v2 the
    shape is obvious — an ``if current < 2:`` block below.
    """
    conn.executescript(_SCHEMA_V1)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif int(row["version"]) < SCHEMA_VERSION:
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
    conn.commit()


def ensure_store(path: Optional[str] = None) -> None:
    """Create the schema without writing a row. Call at startup.

    So the first request meets a ready database rather than paying for the DDL, and so
    a broken path is discovered at boot instead of mid-stream.
    """
    conn = connect(path)
    try:
        _migrate(conn)
    finally:
        _close(conn)


def _close(conn: sqlite3.Connection) -> None:
    try:
        conn.close()
    except sqlite3.Error:
        pass


class _Tx:
    """Short-lived connection + transaction.

    Every public function opens its own connection, as ``journal.py`` and
    ``hashchain.py`` do. That is not laziness: LangGraph runs sync tools in an executor,
    so a module-level shared handle would be used from several threads, and
    ``sqlite3``'s ``check_same_thread`` guard exists for good reason. Short connections
    under WAL are cheap and have no cross-thread hazard.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> sqlite3.Connection:
        self.conn = connect(self.path)
        _migrate(self.conn)
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        assert self.conn is not None
        try:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            _close(self.conn)
        return False


# ── Coercion helpers ──────────────────────────────────────────────────────────


def _text(value: Any, *, cap: int = MAX_CONTENT_CHARS) -> Optional[str]:
    """Coerce to stored text. Empty becomes NULL; oversize is capped.

    Truncation is recorded IN the text. A row that says it was truncated is honest; a
    row that quietly lost its tail is a lie the reader cannot detect. Same rule as
    ``interaction_log._text``.

    NUL characters are stripped, and that is not tidiness — it closes a real
    Python/SQLite disagreement found by
    ``test_property_a_session_is_only_ever_visible_to_its_owner``. ``'\\x00'.strip()``
    is TRUTHY in Python (NUL is not whitespace), so this function used to accept it as a
    valid ``user_id``; but SQLite's ``length('\\x00')`` returns **0**, because ``length``
    stops at the C-string terminator. The ``CHECK (length(user_id) > 0)`` constraint
    therefore fired, turning what should be a clean ``ValueError`` (a 422) into an
    ``sqlite3.IntegrityError`` surfacing as a 500 — the same shape of bug as the
    non-ASCII crash in the identity verifier.

    Stripping rather than rejecting outright, because a NUL is never meaningful in a
    chat message and silently dropping it from *content* is kinder than refusing the
    turn. Identifier fields go through ``_required``, which then sees an empty string
    and raises properly.
    """
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    if "\x00" in text:
        text = text.replace("\x00", "")
    if not text.strip():
        return None
    if len(text) > cap:
        dropped = len(text) - cap
        return f"{text[:cap]}\n[truncated {dropped} characters]"
    return text


def _required(value: Any, field: str) -> str:
    text = _text(value)
    if text is None:
        raise ValueError(f"{field} is required")
    return text


def _json_or_none(value: Any) -> Optional[str]:
    """Serialise flexible metadata, or ``None``.

    Never raises into a caller: an unserialisable object becomes its ``repr`` under a
    marker key rather than failing a write whose real payload was fine. JSON columns
    here hold genuinely-flexible metadata only — no normalised entity is smuggled into
    one.
    """
    if value is None:
        return None
    try:
        return json.dumps(value, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return json.dumps({"_unserialisable": repr(value)[:2000]})


def _loads(raw: Optional[str]) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _row_to_session(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "session_id": row["session_id"],
        "user_id": row["user_id"],
        "title": row["title"],
        "symbol": row["symbol"],
        "profile": row["profile"],
        "timeframe": row["timeframe"],
        "status": row["status"],
        "active_run_id": row["active_run_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "archived_at": row["archived_at"],
        "metadata": _loads(row["metadata_json"]),
    }


def _row_to_run(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "session_id": row["session_id"],
        "user_id": row["user_id"],
        "thread_id": row["thread_id"],
        "kind": row["kind"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "profile": row["profile"],
        "model": row["model"],
        "manual_trade": _loads(row["manual_trade_json"]),
        "status": row["status"],
        "terminal_status": row["terminal_status"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "last_event_at": row["last_event_at"],
        "last_seq": row["last_seq"],
    }


def _row_to_message(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "message_id": row["message_id"],
        "session_id": row["session_id"],
        "run_id": row["run_id"],
        "seq": row["seq"],
        "role": row["role"],
        "kind": row["kind"],
        "content": row["content"],
        "status": row["status"],
        "error_detail": row["error_detail"],
        "activity": _loads(row["activity_json"]),
        "client_msg_id": row["client_msg_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _touch(conn: sqlite3.Connection, session_id: str, stamp: Optional[float] = None) -> None:
    """Bump a session's ``updated_at``.

    Called on every write to a child row, because ``updated_at`` is what orders the tab
    bar and the history list. A session whose newest message did not move it would sort
    as stale, which is the one thing that list has to get right.
    """
    conn.execute(
        "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
        (stamp if stamp is not None else now(), session_id),
    )


# ── Sessions ──────────────────────────────────────────────────────────────────


def create_session(
    *,
    user_id: str,
    symbol: str,
    profile: str,
    timeframe: str,
    title: Optional[str] = None,
    metadata: Any = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a session owned by ``user_id``.

    ``symbol`` and ``profile`` are IMMUTABLE after this point (see
    ``update_session``): a tab is an instrument, and a session whose symbol changed
    would hold a conversation about two of them with a message history that no longer
    describes what was analysed. ``timeframe`` is a mutable default — re-running the
    same instrument at 5m after 10m within one conversation is ordinary analyst
    behaviour — and each run snapshots its own, so changing it never rewrites history.

    ``title`` NULL means "no user rename yet"; the client renders the derived
    ``SYMBOL - TF - time`` label. Storing a derived label would make a later change to
    the label format a data migration.
    """
    uid = _required(user_id, "user_id")
    stamp = now()
    session_id = new_id("sess")
    with _Tx(path) as conn:
        conn.execute(
            """
            INSERT INTO sessions (
                session_id, user_id, title, symbol, profile, timeframe, status,
                active_run_id, created_at, updated_at, archived_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, ?)
            """,
            (
                session_id,
                uid,
                _text(title, cap=200),
                _required(symbol, "symbol").upper(),
                _required(profile, "profile").upper(),
                _required(timeframe, "timeframe"),
                SESSION_ACTIVE,
                stamp,
                stamp,
                _json_or_none(metadata),
            ),
        )
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return _row_to_session(row)


def get_session_for_user(
    session_id: str, user_id: str, *, path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """The session, or ``None`` if it does not exist OR is not owned by ``user_id``.

    The two cases are deliberately indistinguishable. A caller that could tell them
    apart could enumerate which session ids exist, so the API layer returns 404 for
    both — never 403, which would confirm the id.

    There is intentionally no ``get_session(session_id)`` in this module. An
    ownership-free read would eventually get called from a path that forgot to check.
    """
    with _Tx(path) as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ? AND user_id = ?",
            (session_id, _required(user_id, "user_id")),
        ).fetchone()
        return _row_to_session(row) if row else None


def list_sessions(
    user_id: str,
    *,
    status: Optional[str] = SESSION_ACTIVE,
    cursor: Optional[str] = None,
    limit: int = 25,
    query: Optional[str] = None,
    path: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """One page of the caller's sessions, newest activity first.

    Returns ``(items, next_cursor)``; ``next_cursor`` is ``None`` on the last page.

    Keyset pagination on ``(updated_at DESC, session_id DESC)``, not OFFSET. Sessions
    are reordered by activity constantly — a streaming run bumps ``updated_at`` on
    every flush — and under OFFSET that means a row can be skipped or repeated between
    pages while the user scrolls. ``session_id`` is the tiebreaker, which works only
    because the ids are time-sortable.

    ``status=None`` returns every status including ``deleted``; that is for
    administrative/diagnostic use, and the API layer does not expose it.
    """
    uid = _required(user_id, "user_id")
    limit = max(1, min(int(limit), 100))

    where = ["user_id = ?"]
    params: List[Any] = [uid]
    if status is not None:
        if status not in SESSION_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        where.append("status = ?")
        params.append(status)
    else:
        # Even with no filter, a hard-deleted session is not a listable thing.
        where.append("status <> ?")
        params.append(SESSION_DELETED)

    if query and query.strip():
        # Search covers the symbol and the user's own title. Deliberately not the
        # message bodies: that is a full-text index this store does not have, and
        # LIKE over every transcript would be a table scan per keystroke.
        like = f"%{query.strip().upper()}%"
        where.append("(symbol LIKE ? OR UPPER(COALESCE(title, '')) LIKE ?)")
        params.extend([like, like])

    if cursor:
        parsed = _decode_cursor(cursor)
        if parsed is not None:
            where.append("(updated_at < ? OR (updated_at = ? AND session_id < ?))")
            params.extend([parsed[0], parsed[0], parsed[1]])

    sql = (
        f"SELECT * FROM sessions WHERE {' AND '.join(where)} "
        f"ORDER BY updated_at DESC, session_id DESC LIMIT ?"
    )
    params.append(limit + 1)  # one extra row tells us whether another page exists

    with _Tx(path) as conn:
        rows = conn.execute(sql, params).fetchall()

    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_row_to_session(r) for r in rows]
    next_cursor = (
        _encode_cursor(rows[-1]["updated_at"], rows[-1]["session_id"]) if has_more and rows else None
    )
    return items, next_cursor


def _encode_cursor(updated_at: float, session_id: str) -> str:
    """Opaque page cursor.

    Opaque so the pagination key can change without breaking a client that stored one,
    and so nobody is tempted to construct one by hand. Not signed: it encodes only a
    timestamp and an id the caller was just shown, and every query it feeds is already
    scoped to the caller's ``user_id``.

    ``repr`` and NOT ``f"{updated_at:.6f}"``. Python guarantees ``float(repr(x)) == x``,
    whereas ``.6f`` ROUNDS — and a timestamp rounded UP compares as strictly greater
    than the row it came from, so the keyset predicate ``updated_at < cursor`` matches
    that row again and it is served on two consecutive pages. Found by
    ``test_cursor_pagination_covers_every_row_exactly_once``: 23 sessions, 27 rows
    returned. Lossy is not an option for a value used as an exact boundary.
    """
    return f"{updated_at!r}|{session_id}"


def _decode_cursor(cursor: str) -> Optional[Tuple[float, str]]:
    """Parse a cursor, or ``None`` if it is malformed.

    A bad cursor yields the FIRST page rather than an error. The alternative — a 400 —
    turns a stale bookmark into a broken screen, and the query is ownership-scoped
    regardless, so there is nothing to protect by being strict.
    """
    try:
        stamp, _, sid = cursor.partition("|")
        if not sid:
            return None
        return float(stamp), sid
    except (TypeError, ValueError):
        return None


# Fields a caller may change. ``symbol`` and ``profile`` are absent BY DESIGN.
_MUTABLE_SESSION_FIELDS = ("title", "timeframe", "status", "active_run_id", "metadata")
_IMMUTABLE_SESSION_FIELDS = ("symbol", "profile", "session_id", "user_id", "created_at")


class ImmutableFieldError(ValueError):
    """Raised on an attempt to change ``symbol`` or ``profile``.

    A distinct type so the API layer can answer 409 rather than a generic 422 — the
    request was well-formed, the operation is simply not allowed on this entity.
    """


def update_session(
    session_id: str,
    user_id: str,
    *,
    patch: Dict[str, Any],
    path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Apply ``patch``; return the updated session, or ``None`` if not owned/found.

    Raises ``ImmutableFieldError`` for ``symbol``/``profile``, and ``ValueError`` for an
    unknown field — silently ignoring an unknown key would let a typo look like a
    successful rename.
    """
    uid = _required(user_id, "user_id")

    for key in patch:
        if key in _IMMUTABLE_SESSION_FIELDS:
            raise ImmutableFieldError(
                f"{key} is immutable on a session. Symbol and profile define the "
                f"session's identity; create a new session instead."
            )
        if key not in _MUTABLE_SESSION_FIELDS:
            raise ValueError(f"unknown session field {key!r}")

    assignments: List[str] = []
    params: List[Any] = []
    stamp = now()

    if "title" in patch:
        assignments.append("title = ?")
        params.append(_text(patch["title"], cap=200))
    if "timeframe" in patch:
        assignments.append("timeframe = ?")
        params.append(_required(patch["timeframe"], "timeframe"))
    if "active_run_id" in patch:
        assignments.append("active_run_id = ?")
        params.append(_text(patch["active_run_id"]))
    if "metadata" in patch:
        assignments.append("metadata_json = ?")
        params.append(_json_or_none(patch["metadata"]))
    if "status" in patch:
        status = patch["status"]
        if status not in SESSION_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        assignments.append("status = ?")
        params.append(status)
        # archived_at is derived from status, never set independently — the CHECK
        # constraint requires them to agree, so deriving it is what keeps a reopen
        # from failing on a stale timestamp.
        assignments.append("archived_at = ?")
        params.append(None if status == SESSION_ACTIVE else stamp)

    if not assignments:
        return get_session_for_user(session_id, uid, path=path)

    assignments.append("updated_at = ?")
    params.append(stamp)
    params.extend([session_id, uid])

    with _Tx(path) as conn:
        cur = conn.execute(
            f"UPDATE sessions SET {', '.join(assignments)} WHERE session_id = ? AND user_id = ?",
            params,
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return _row_to_session(row) if row else None


def archive_session(
    session_id: str, user_id: str, *, path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Soft-close: drops out of the tab bar, stays in history, fully reopenable."""
    return update_session(session_id, user_id, patch={"status": SESSION_ARCHIVED}, path=path)


def reopen_session(
    session_id: str, user_id: str, *, path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Return an archived session to active."""
    return update_session(session_id, user_id, patch={"status": SESSION_ACTIVE}, path=path)


def delete_session(
    session_id: str,
    user_id: str,
    *,
    hard: bool = False,
    path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Delete a session. Soft by default.

    ``hard=True`` scrubs the user-visible content — messages and run_events go via
    ``ON DELETE CASCADE``, the title is blanked — while KEEPING the session row as a
    ``deleted`` tombstone. The tombstone is what makes the ``UNIQUE(thread_id)``
    constraint on ``runs`` continue to mean something and keeps a stale client's
    request answerable as "gone" rather than "never existed".

    It touches ONLY this database. ``interaction_log`` and ``recommendations`` live in
    ``compliance.db``, are append-only and trigger-protected, and are the five-year
    SEBI record — a user deleting a conversation does not and must not erase the audit
    trail. There is a test asserting that file's hash is unchanged across a hard delete.
    """
    uid = _required(user_id, "user_id")
    if not hard:
        return update_session(session_id, uid, patch={"status": SESSION_DELETED}, path=path)

    stamp = now()
    with _Tx(path) as conn:
        owned = conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ? AND user_id = ?", (session_id, uid)
        ).fetchone()
        if owned is None:
            return None
        # Explicit deletes as well as relying on the cascade: run_events cascades from
        # runs, not from sessions, so a session-level cascade alone would leave the
        # transcript behind if the FK pragma were ever off. Belt and braces on data the
        # user asked to be gone.
        conn.execute(
            "DELETE FROM run_events WHERE run_id IN (SELECT run_id FROM runs WHERE session_id = ?)",
            (session_id,),
        )
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM runs WHERE session_id = ?", (session_id,))
        conn.execute(
            """
            UPDATE sessions
               SET status = ?, title = NULL, active_run_id = NULL, metadata_json = NULL,
                   archived_at = ?, updated_at = ?
             WHERE session_id = ? AND user_id = ?
            """,
            (SESSION_DELETED, stamp, stamp, session_id, uid),
        )
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return _row_to_session(row) if row else None


# ── Runs ──────────────────────────────────────────────────────────────────────


def create_run(
    *,
    session_id: str,
    user_id: str,
    kind: str,
    symbol: str,
    timeframe: str,
    profile: str,
    model: Optional[str] = None,
    manual_trade: Any = None,
    thread_id: Optional[str] = None,
    path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Create a run under an owned session, minting its ``thread_id``.

    Returns ``None`` when the session does not exist or is not owned — the caller then
    answers 404 without ever learning which it was.

    The server mints ``thread_id``. It used to be ``thread_${symbol}_${Date.now()}``
    from the browser: guessable within a second, and ``GET /stream/{thread_id}`` had no
    ownership check, so knowing the symbol and roughly the time was enough to read
    someone else's research stream.

    ``symbol``/``timeframe``/``profile`` are snapshotted immutably. The session's
    ``timeframe`` is a mutable default, so without the snapshot a later change would
    silently rewrite what an earlier run claims to have analysed.

    Sets ``sessions.active_run_id``, which is the default Q&A grounding target.
    """
    uid = _required(user_id, "user_id")
    if kind not in RUN_KINDS:
        raise ValueError(f"unknown run kind {kind!r}")

    stamp = now()
    run_id = new_id("run")
    tid = _text(thread_id) or new_id("thread")

    with _Tx(path) as conn:
        owned = conn.execute(
            "SELECT status FROM sessions WHERE session_id = ? AND user_id = ?",
            (session_id, uid),
        ).fetchone()
        if owned is None or owned["status"] == SESSION_DELETED:
            return None
        conn.execute(
            """
            INSERT INTO runs (
                run_id, session_id, user_id, thread_id, kind, symbol, timeframe, profile,
                model, manual_trade_json, status, terminal_status, started_at, ended_at,
                last_event_at, last_seq
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, 0)
            """,
            (
                run_id,
                session_id,
                uid,
                tid,
                kind,
                _required(symbol, "symbol").upper(),
                _required(timeframe, "timeframe"),
                _required(profile, "profile").upper(),
                _text(model),
                _json_or_none(manual_trade),
                RUN_RUNNING,
                stamp,
                stamp,
            ),
        )
        conn.execute(
            "UPDATE sessions SET active_run_id = ?, updated_at = ? WHERE session_id = ?",
            (run_id, stamp, session_id),
        )
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return _row_to_run(row)


def get_run_for_user(
    run_id: str, user_id: str, *, path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """The run, or ``None`` if not owned. Backs ``/cancel`` ownership."""
    with _Tx(path) as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ? AND user_id = ?",
            (run_id, _required(user_id, "user_id")),
        ).fetchone()
        return _row_to_run(row) if row else None


def get_run_by_thread_for_user(
    thread_id: str, user_id: str, *, path: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """The run for a ``thread_id``, or ``None`` if not owned.

    Backs ``GET /stream/{thread_id}`` ownership. A single indexed read with no join,
    which is why ``user_id`` is denormalised onto ``runs`` — this check sits on the hot
    path of a long-lived SSE attach.
    """
    with _Tx(path) as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE thread_id = ? AND user_id = ?",
            (thread_id, _required(user_id, "user_id")),
        ).fetchone()
        return _row_to_run(row) if row else None


def get_run_by_thread(thread_id: str, *, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The run for a ``thread_id``, with NO ownership check.

    Exists for exactly one caller: ``POST /resume``, where the requester is the
    headless Rust watcher authenticated as a SERVICE. It has no user session, so the
    owning user has to be READ from this row in order to resolve that user's LLM key.

    Not to be used on any user-facing path. The user-facing equivalent is
    ``get_run_by_thread_for_user``.
    """
    with _Tx(path) as conn:
        row = conn.execute("SELECT * FROM runs WHERE thread_id = ?", (thread_id,)).fetchone()
        return _row_to_run(row) if row else None


def list_runs(
    session_id: str, user_id: str, *, path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every run in an owned session, oldest first. Empty list when not owned."""
    with _Tx(path) as conn:
        rows = conn.execute(
            """
            SELECT r.* FROM runs r
              JOIN sessions s ON s.session_id = r.session_id
             WHERE r.session_id = ? AND s.user_id = ?
             ORDER BY r.started_at ASC, r.run_id ASC
            """,
            (session_id, _required(user_id, "user_id")),
        ).fetchall()
        return [_row_to_run(r) for r in rows]


def update_run_status(
    run_id: str,
    status: str,
    *,
    path: Optional[str] = None,
) -> bool:
    """Move a run to a NON-terminal status (``running`` / ``watching``).

    Terminal transitions go through ``finalize_run``, which is set-once. Splitting them
    is what makes "duplicate RUN_FINISHED is idempotent" a property of the store rather
    than of every caller.
    """
    if status not in RUN_LIVE_STATUSES:
        raise ValueError(
            f"{status!r} is terminal; use finalize_run so the transition stays set-once"
        )
    with _Tx(path) as conn:
        cur = conn.execute(
            "UPDATE runs SET status = ?, last_event_at = ? WHERE run_id = ? AND terminal_status IS NULL",
            (status, now(), run_id),
        )
        return cur.rowcount > 0


def finalize_run(
    run_id: str,
    terminal_status: str,
    *,
    path: Optional[str] = None,
) -> bool:
    """Record a run's terminal state, exactly once. Returns whether this call did it.

    Set-once via ``WHERE terminal_status IS NULL``, so a duplicated or replayed
    ``RUN_FINISHED`` — which the reattach path can genuinely produce — cannot rewrite
    an outcome. The frontend reducer already guards this with
    ``_runFinishedProcessed``; making it a property of the store too means the
    guarantee does not depend on which client is talking.
    """
    if terminal_status not in RUN_STATUSES or terminal_status in RUN_LIVE_STATUSES:
        raise ValueError(f"{terminal_status!r} is not a terminal run status")
    stamp = now()
    with _Tx(path) as conn:
        cur = conn.execute(
            """
            UPDATE runs
               SET status = ?, terminal_status = ?, ended_at = ?, last_event_at = ?
             WHERE run_id = ? AND terminal_status IS NULL
            """,
            (terminal_status, terminal_status, stamp, stamp, run_id),
        )
        if cur.rowcount:
            sid = conn.execute(
                "SELECT session_id FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if sid:
                _touch(conn, sid["session_id"], stamp)
        return cur.rowcount > 0


# ── Run events (the durable glass-box transcript) ─────────────────────────────


def append_run_events(
    run_id: str,
    events: Sequence[Tuple[str, Any]],
    *,
    path: Optional[str] = None,
) -> int:
    """Append a BATCH of ``(event_name, payload)`` frames. Returns the new ``last_seq``.

    Batched deliberately. A REASONING stream is many small frames, and one transaction
    per frame is the only thing that would make SQLite the wrong choice here; the
    caller flushes on size, on time, and unconditionally before any terminal frame, so
    the terminal state can never be ahead of the transcript.

    ``seq`` is allocated from ``runs.last_seq`` inside this transaction, so concurrent
    batches cannot interleave into a gap or a duplicate.

    ``INSERT OR IGNORE`` against ``PRIMARY KEY (run_id, seq)`` makes a re-delivered
    frame a no-op rather than a duplicate transcript entry.
    """
    if not events:
        with _Tx(path) as conn:
            row = conn.execute("SELECT last_seq FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            return int(row["last_seq"]) if row else 0

    stamp = now()
    with _Tx(path) as conn:
        row = conn.execute(
            "SELECT session_id, last_seq FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return 0
        seq = int(row["last_seq"])
        rows = []
        for event, payload in events:
            seq += 1
            rows.append((run_id, seq, _required(event, "event"), _json_or_none(payload) or "{}", stamp))
        conn.executemany(
            "INSERT OR IGNORE INTO run_events (run_id, seq, event, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute(
            "UPDATE runs SET last_seq = ?, last_event_at = ? WHERE run_id = ?",
            (seq, stamp, run_id),
        )
        _touch(conn, row["session_id"], stamp)
        return seq


def list_run_events(
    run_id: str,
    *,
    after_seq: int = 0,
    limit: int = 1000,
    path: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Frames after ``after_seq``, in order, plus the run's ``last_seq``.

    This is what makes reattach gap-free. Today ``_publish_frame`` returns early when
    no subscriber is attached, so everything emitted between the ``/run`` stream ending
    and the hub GET landing is lost with no way to recover it. With this, a client
    reattaches at the sequence it last saw.
    """
    with _Tx(path) as conn:
        run = conn.execute("SELECT last_seq FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            return [], 0
        rows = conn.execute(
            "SELECT seq, event, payload_json FROM run_events "
            "WHERE run_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
            (run_id, max(0, int(after_seq)), max(1, min(int(limit), 5000))),
        ).fetchall()
        items = [
            {"seq": r["seq"], "event": r["event"], "data": _loads(r["payload_json"]) or {}}
            for r in rows
        ]
        return items, int(run["last_seq"])


# ── Messages ──────────────────────────────────────────────────────────────────


def create_message(
    *,
    session_id: str,
    role: str,
    kind: str,
    status: str,
    content: str = "",
    run_id: Optional[str] = None,
    client_msg_id: Optional[str] = None,
    activity: Any = None,
    error_detail: Optional[str] = None,
    path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Append a message, allocating its ``seq`` inside the transaction.

    Idempotent on ``client_msg_id``: a retried send returns the EXISTING row rather
    than creating a second turn. Without it, a flaky connection on the composer's
    submit produces the user's question twice, which is both visible and unfixable
    afterwards.

    ``seq`` is ``MAX(seq)+1`` scoped to the session, computed here rather than by the
    caller so two concurrent writers cannot pick the same number — the
    ``UNIQUE(session_id, seq)`` index would reject the second, and this avoids
    depending on that as flow control.

    Returns ``None`` when the session does not exist. No ownership check: callers reach
    this only after resolving the session through ``get_session_for_user``, and adding
    a second lookup per streamed message would put a redundant query on the hot path.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    if kind not in MESSAGE_KINDS:
        raise ValueError(f"unknown message kind {kind!r}")
    if status not in MESSAGE_STATUSES:
        raise ValueError(f"unknown message status {status!r}")
    if role == ROLE_USER and status != MSG_COMPLETE:
        raise ValueError("a user message is never partial; status must be 'complete'")

    stamp = now()
    cid = _text(client_msg_id, cap=200)

    with _Tx(path) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if exists is None:
            return None

        if cid is not None:
            prior = conn.execute(
                "SELECT * FROM messages WHERE session_id = ? AND client_msg_id = ?",
                (session_id, cid),
            ).fetchone()
            if prior is not None:
                return _row_to_message(prior)

        seq_row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        message_id = new_id("msg")
        conn.execute(
            """
            INSERT INTO messages (
                message_id, session_id, run_id, seq, role, kind, content, status,
                error_detail, activity_json, client_msg_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                session_id,
                _text(run_id),
                int(seq_row["next"]),
                role,
                kind,
                _text(content) or "",
                status,
                _text(error_detail),
                _json_or_none(activity),
                cid,
                stamp,
                stamp,
            ),
        )
        _touch(conn, session_id, stamp)
        row = conn.execute(
            "SELECT * FROM messages WHERE message_id = ?", (message_id,)
        ).fetchone()
        return _row_to_message(row)


def append_message_delta(
    message_id: str,
    delta: str,
    *,
    activity: Any = None,
    path: Optional[str] = None,
) -> bool:
    """Append streamed text to a message that is still ``streaming``.

    Refuses to touch a finalized message (``WHERE status = 'streaming'``). A late frame
    arriving after the terminal event — which the reattach path can produce — must not
    reopen a completed answer or append to a truncated one.

    Concatenation happens in SQL so two flushes cannot read-modify-write over each
    other and lose a chunk.
    """
    # NULs are stripped BEFORE the concatenation, and this is data loss prevention rather
    # than hygiene. SQLite's `substr()` stops at an embedded NUL, so a delta containing one
    # silently discarded everything after it — the rest of the assistant's answer, gone,
    # with no error and nothing in the row to say so. Found by
    # `test_property_streamed_text_is_never_lost_or_reordered` with the chunk `'\x000'`:
    # expected `'0'`, stored `''`.
    #
    # `create_message` already sanitised its content via `_text`; this path bypassed it,
    # which is exactly how the two diverged.
    clean_delta = (delta or "").replace("\x00", "")
    stamp = now()
    with _Tx(path) as conn:
        cur = conn.execute(
            """
            UPDATE messages
               SET content = substr(content || ?, 1, ?),
                   activity_json = COALESCE(?, activity_json),
                   updated_at = ?
             WHERE message_id = ? AND status = ?
            """,
            (clean_delta, MAX_CONTENT_CHARS, _json_or_none(activity), stamp, message_id, MSG_STREAMING),
        )
        if cur.rowcount:
            sid = conn.execute(
                "SELECT session_id FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if sid:
                _touch(conn, sid["session_id"], stamp)
        return cur.rowcount > 0


def finalize_message(
    message_id: str,
    status: str,
    *,
    content: Optional[str] = None,
    error_detail: Optional[str] = None,
    activity: Any = None,
    path: Optional[str] = None,
) -> bool:
    """Move a message out of ``streaming``, exactly once.

    ``WHERE status = 'streaming'`` makes it set-once, so a duplicate terminal event
    cannot turn a ``truncated`` answer into a ``complete`` one — which is the specific
    way an incomplete response would end up presented as a successful one.

    ``content`` replaces the accumulated text when supplied (the server may hold a
    better-assembled version than the concatenated deltas); omitting it keeps whatever
    streamed, which is what preserves a partial answer on the truncated path.
    """
    if status not in MESSAGE_STATUSES or status == MSG_STREAMING:
        raise ValueError(f"{status!r} is not a terminal message status")
    # Same NUL hazard as append_message_delta: this also passes through `substr`, which
    # would truncate a replacement body at the first NUL. `None` is preserved as None so
    # the CASE below can still distinguish "keep what streamed" from "replace it".
    if content is not None:
        content = content.replace("\x00", "")
    stamp = now()
    with _Tx(path) as conn:
        cur = conn.execute(
            """
            UPDATE messages
               SET status = ?,
                   content = CASE WHEN ? IS NULL THEN content ELSE substr(?, 1, ?) END,
                   error_detail = COALESCE(?, error_detail),
                   activity_json = COALESCE(?, activity_json),
                   updated_at = ?
             WHERE message_id = ? AND status = ?
            """,
            (
                status,
                content,
                content,
                MAX_CONTENT_CHARS,
                _text(error_detail),
                _json_or_none(activity),
                stamp,
                message_id,
                MSG_STREAMING,
            ),
        )
        if cur.rowcount:
            sid = conn.execute(
                "SELECT session_id FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if sid:
                _touch(conn, sid["session_id"], stamp)
        return cur.rowcount > 0


def list_messages(
    session_id: str,
    user_id: str,
    *,
    after_seq: int = 0,
    limit: int = 200,
    path: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """One page of an owned session's messages, in order. ``(items, last_seq)``.

    Ownership is enforced by joining ``sessions``, so a caller who guessed a session id
    gets an empty page rather than someone else's conversation.

    Paginated on ``seq``, which is dense and per-session, so "everything after what I
    have" is exact — no timestamp ties and no OFFSET drift while the session is live.
    """
    uid = _required(user_id, "user_id")
    with _Tx(path) as conn:
        owned = conn.execute(
            "SELECT 1 FROM sessions WHERE session_id = ? AND user_id = ?", (session_id, uid)
        ).fetchone()
        if owned is None:
            return [], 0
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?",
            (session_id, max(0, int(after_seq)), max(1, min(int(limit), 1000))),
        ).fetchall()
        last = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS last FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return [_row_to_message(r) for r in rows], int(last["last"])


# ── Startup reconciliation — the anti-fabrication pass ───────────────────────


def reconcile_stale_runs(graph: Any = None, *, path: Optional[str] = None) -> int:
    """Resolve runs that claim to be live but cannot be. Returns how many changed.

    A process that dies mid-stream leaves a run row saying ``running`` and an assistant
    message saying ``streaming``. After a restart there is no producer for either, so
    rendering them unchanged shows a half-written answer as though it were still
    arriving — and, worse, as though it had succeeded once the spinner is gone. That is
    the "an incomplete streamed response must never appear as a complete one" rule, and
    it is enforced HERE rather than hoped for on the write path, because the write path
    is exactly what did not get to run.

    The durable checkpointer is what makes this decidable. For each live run, ask
    LangGraph whether the thread still has a pending step:

      * pending  -> genuinely resumable; left ``watching`` so the watcher can wake it.
        This is the case ``MemorySaver`` could never produce.
      * not pending (or unknown) -> ``truncated``, and its ``streaming`` assistant
        message with it. Partial content is KEPT; only its status changes, so the user
        can see how far it got.

    ``graph=None`` means no checkpointer is available, in which case nothing can be
    resumable and every live run is truncated.

    Idempotent: a second call finds no live runs.
    """
    adjusted = 0
    with _Tx(path) as conn:
        rows = conn.execute(
            f"SELECT run_id, thread_id FROM runs WHERE status IN {_sql_in(RUN_LIVE_STATUSES)}"
        ).fetchall()
        stamp = now()
        for row in rows:
            if _thread_has_pending_step(graph, row["thread_id"]):
                # Resumable. Normalise to `watching`, which is what the UI renders as
                # "the agent is waiting for your price trigger".
                conn.execute(
                    "UPDATE runs SET status = ?, last_event_at = ? WHERE run_id = ?",
                    (RUN_WATCHING, stamp, row["run_id"]),
                )
                continue
            conn.execute(
                """
                UPDATE runs
                   SET status = ?, terminal_status = COALESCE(terminal_status, ?),
                       ended_at = COALESCE(ended_at, ?), last_event_at = ?
                 WHERE run_id = ?
                """,
                (RUN_TRUNCATED, RUN_TRUNCATED, stamp, stamp, row["run_id"]),
            )
            conn.execute(
                "UPDATE messages SET status = ?, updated_at = ? WHERE run_id = ? AND status = ?",
                (MSG_TRUNCATED, stamp, row["run_id"], MSG_STREAMING),
            )
            adjusted += 1

        # An orphan sweep, separate from the loop above: a `streaming` message whose run
        # is already terminal. Reachable if a process died between finalizing the run
        # and finalizing the message, and it is the same lie about completeness.
        cur = conn.execute(
            f"""
            UPDATE messages SET status = ?, updated_at = ?
             WHERE status = ?
               AND run_id IS NOT NULL
               AND run_id NOT IN (SELECT run_id FROM runs WHERE status IN {_sql_in(RUN_LIVE_STATUSES)})
            """,
            (MSG_TRUNCATED, stamp, MSG_STREAMING),
        )
        adjusted += cur.rowcount
    return adjusted


def _thread_has_pending_step(graph: Any, thread_id: str) -> bool:
    """Whether LangGraph reports a pending next step for ``thread_id``.

    Mirrors the exact check ``POST /resume`` performs (``graph.get_state(config).next``),
    so reconciliation and the resume endpoint can never disagree about what is
    resumable.

    Any failure means "not pending". Guessing resumable on an error would leave a run
    permanently ``watching`` and unwakeable — the WATCHING-forever bug — whereas
    guessing truncated is honest and recoverable by re-running.
    """
    if graph is None or not thread_id:
        return False
    try:
        state = graph.get_state({"configurable": {"thread_id": thread_id}})
        return bool(getattr(state, "next", None))
    except Exception:  # noqa: BLE001 - an unreadable thread is not a resumable one
        return False


# ── Retention ─────────────────────────────────────────────────────────────────


def retention_days() -> float:
    """Days of ``run_events`` to keep. ``0`` disables pruning."""
    raw = (os.getenv(ENV_RETENTION_DAYS) or "").strip()
    if not raw:
        return 90.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 90.0


def prune_run_events(*, days: Optional[float] = None, path: Optional[str] = None) -> int:
    """Delete transcript frames for finished runs older than the retention window.

    ``run_events`` is the only unbounded table here — hundreds to thousands of rows per
    run. Sessions, runs and messages are small and are NOT pruned: losing the
    conversation would defeat the point, while losing an old glass-box transcript
    costs only the ability to replay a months-old run frame by frame.

    Opens ``SESSIONS_DB_PATH`` and nothing else. This is application data; the
    compliance chain in ``compliance.db`` has a five-year retention duty and no delete
    path at all (its triggers ABORT on DELETE), and no pruner may ever reach across.

    Live runs are never touched, however old ``last_event_at`` claims to be.
    """
    window = retention_days() if days is None else max(0.0, float(days))
    if window <= 0:
        return 0
    cutoff = now() - window * 86400.0
    with _Tx(path) as conn:
        cur = conn.execute(
            f"""
            DELETE FROM run_events
             WHERE run_id IN (
                   SELECT run_id FROM runs
                    WHERE status NOT IN {_sql_in(RUN_LIVE_STATUSES)}
                      AND COALESCE(ended_at, last_event_at) < ?
             )
            """,
            (cutoff,),
        )
        return cur.rowcount


# ── Diagnostics ───────────────────────────────────────────────────────────────


def stats(*, path: Optional[str] = None) -> Dict[str, int]:
    """Row counts, for the startup report and ops. Never raises."""
    out: Dict[str, int] = {}
    try:
        with _Tx(path) as conn:
            for table in ("sessions", "runs", "messages", "run_events"):
                row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
                out[table] = int(row["n"]) if row else 0
    except sqlite3.Error:
        pass
    return out
