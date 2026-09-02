"""Session_Store tests — ownership, ordering, idempotency, and honest completeness.

Four guarantees carry the design, and each has tests whose failure means a real
product defect rather than a style regression:

1. **Ownership.** No read returns another user's data, and a missing session is
   indistinguishable from someone else's session (otherwise ids are enumerable).
2. **Ordering.** ``messages.seq`` is dense and gap-free per session, and pagination
   never skips or repeats a row while sessions are being reordered by activity.
3. **Idempotency.** A retried send, a re-delivered frame, and a duplicate terminal
   event all become no-ops. Each of these is producible by the reattach path.
4. **Honest completeness.** A ``streaming`` message can never be turned into a
   ``complete`` one by a late or duplicate event, and a run left live by a crashed
   process becomes ``truncated`` rather than silently presenting as an answer.

Plus one boundary that is not about this store at all: a hard delete must leave
``compliance.db`` byte-identical.

Every test gets its own database file. A shared store would make the ordering and
pagination properties depend on test order.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import session_store as ss


@pytest.fixture
def db(tmp_path):
    """A fresh store, and the SESSIONS_DB_PATH pointing at it."""
    path = str(tmp_path / "sessions.db")
    ss.ensure_store(path)
    return path


def _session(db, user="u1", symbol="RELIANCE", profile="INTRADAY", timeframe="10m", **kw):
    return ss.create_session(
        user_id=user, symbol=symbol, profile=profile, timeframe=timeframe, path=db, **kw
    )


def _run(db, sess, user="u1", kind="find", **kw):
    return ss.create_run(
        session_id=sess["session_id"],
        user_id=user,
        kind=kind,
        symbol=kw.pop("symbol", sess["symbol"]),
        timeframe=kw.pop("timeframe", sess["timeframe"]),
        profile=kw.pop("profile", sess["profile"]),
        path=db,
        **kw,
    )


# ── Identifiers ───────────────────────────────────────────────────────────────


def test_ids_are_prefixed_and_opaque():
    sid = ss.new_id("sess")
    assert sid.startswith("sess_")
    assert len(sid) == len("sess_") + 26
    # Crockford base32 without I/L/O/U: an id cannot be mis-transcribed into another
    # valid id, and cannot accidentally spell a word.
    body = sid.split("_", 1)[1]
    assert set(body) <= set(ss._B32)


def test_ids_are_unique_at_volume():
    ids = {ss.new_id("run") for _ in range(20_000)}
    assert len(ids) == 20_000


def test_ids_sort_by_creation_time():
    """Time-sortable is what makes the pagination tiebreaker stable."""
    first = ss.new_id("sess")
    import time as _t

    _t.sleep(0.005)
    second = ss.new_id("sess")
    assert first < second


def test_ids_carry_no_symbol_or_guessable_time_component():
    """The old identity was `thread_${symbol}_${Date.now()}` — guessable to the second.

    Ownership is enforced independently now, but an unguessable id means one missed
    check is not immediately exploitable.
    """
    ids = [ss.new_id("thread") for _ in range(50)]
    for value in ids:
        assert "RELIANCE" not in value
    # 80 random bits means the low half of two ids minted in the same millisecond differ.
    assert len({v[-16:] for v in ids}) > 40


def test_sql_in_renders_a_single_value_without_a_trailing_comma():
    """`f"{('x',)}"` yields `('x',)`, which is a SQLite syntax error.

    The vocabulary tuples all happen to have >= 2 members today, so a direct
    interpolation would work right up until someone narrowed one.
    """
    assert ss._sql_in(["x"]) == "('x')"
    assert ss._sql_in(["a", "b"]) == "('a', 'b')"
    assert ss._sql_in(["O'Neill"]) == "('O''Neill')"


# ── Schema ────────────────────────────────────────────────────────────────────


def test_ensure_store_is_idempotent(tmp_path):
    path = str(tmp_path / "s.db")
    ss.ensure_store(path)
    ss.ensure_store(path)
    conn = ss.connect(path)
    try:
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
        assert len(rows) == 1, "the version row must not be duplicated"
        assert rows[0]["version"] == ss.SCHEMA_VERSION
    finally:
        conn.close()


def test_foreign_keys_are_on_for_every_connection(db):
    """Per-connection in SQLite and OFF by default.

    Without this the ON DELETE CASCADEs silently do nothing — this pragma IS the
    cascade, so it is asserted rather than assumed.
    """
    conn = ss.connect(db)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_parent_directory_is_created(tmp_path):
    """A path under a fresh volume mount must not fail with 'unable to open'.

    Measured while wiring this up: exactly that error, for a missing directory.
    """
    nested = tmp_path / "data" / "deep" / "sessions.db"
    ss.ensure_store(str(nested))
    assert nested.exists()


def test_a_run_cannot_reference_a_missing_session(db):
    """The FK is real, not decorative."""
    conn = ss.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO runs (run_id, session_id, user_id, thread_id, kind, symbol, "
                "timeframe, profile, status, started_at, last_event_at) "
                "VALUES ('r', 'nope', 'u', 't', 'find', 'X', '10m', 'INTRADAY', 'running', 1, 1)"
            )
    finally:
        conn.close()


def test_one_thread_id_per_run(db):
    """1 run <-> 1 LangGraph thread, enforced by a unique index.

    Two runs sharing a thread would make `/stream` ownership ambiguous and would
    collide with reco_store's UNIQUE(thread_id) on the compliance side.
    """
    s = _session(db)
    r = _run(db, s)
    conn = ss.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO runs (run_id, session_id, user_id, thread_id, kind, symbol, "
                "timeframe, profile, status, started_at, last_event_at) "
                "VALUES (?, ?, 'u1', ?, 'find', 'X', '10m', 'INTRADAY', 'running', 1, 1)",
                (ss.new_id("run"), s["session_id"], r["thread_id"]),
            )
    finally:
        conn.close()


def test_a_user_message_cannot_be_streaming(db):
    """Schema-level: the user's own text is never half-written."""
    s = _session(db)
    conn = ss.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO messages (message_id, session_id, seq, role, kind, content, "
                "status, created_at, updated_at) VALUES ('m', ?, 1, 'user', 'qa_question', "
                "'hi', 'streaming', 1, 1)",
                (s["session_id"],),
            )
    finally:
        conn.close()


def test_status_and_archived_at_cannot_disagree(db):
    """An 'active' row with an archive timestamp is a state the read model cannot show."""
    s = _session(db)
    conn = ss.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE sessions SET archived_at = 123 WHERE session_id = ?",
                (s["session_id"],),
            )
    finally:
        conn.close()


# ── Sessions ──────────────────────────────────────────────────────────────────


def test_create_and_read_back(db):
    s = _session(db, symbol="reliance", profile="intraday")
    assert s["symbol"] == "RELIANCE", "symbol is normalised so the tab label is stable"
    assert s["profile"] == "INTRADAY"
    assert s["status"] == ss.SESSION_ACTIVE
    assert s["archived_at"] is None
    assert s["title"] is None, "no title means the client renders the derived label"
    assert s["active_run_id"] is None
    got = ss.get_session_for_user(s["session_id"], "u1", path=db)
    assert got == s


def test_a_blank_owner_is_refused(db):
    """An unowned session is unreachable by design; creating one is a bug, not a state."""
    for bad in ("", "   ", None):
        with pytest.raises(ValueError):
            ss.create_session(
                user_id=bad, symbol="X", profile="INTRADAY", timeframe="10m", path=db
            )


@pytest.mark.parametrize("field", ["symbol", "profile", "timeframe"])
@pytest.mark.parametrize("blank", ["  ", "", "\x00", "\x00\x00"])
def test_required_fields_are_required(db, field, blank):
    kwargs = dict(user_id="u1", symbol="X", profile="INTRADAY", timeframe="10m", path=db)
    kwargs[field] = blank
    with pytest.raises(ValueError):
        ss.create_session(**kwargs)


@pytest.mark.parametrize("bad", ["\x00", "\x00\x00", " \x00 "])
def test_a_nul_user_id_is_a_clean_value_error_not_an_integrity_error(db, bad):
    """Python and SQLite must agree on what 'empty' means.

    `'\\x00'.strip()` is truthy in Python, but SQLite's `length('\\x00')` is 0 — so this
    used to pass the Python guard and then trip `CHECK (length(user_id) > 0)`, turning a
    422 into a 500. Found by the ownership property test.
    """
    with pytest.raises(ValueError):
        ss.create_session(
            user_id=bad, symbol="X", profile="INTRADAY", timeframe="10m", path=db
        )


def test_a_nul_inside_message_content_is_stripped_rather_than_refused(db):
    """A NUL is never meaningful in a chat message; dropping it beats losing the turn."""
    s = _session(db)
    m = ss.create_message(
        session_id=s["session_id"], role="user", kind="qa_question",
        status="complete", content="why\x00 that stop?", path=db,
    )
    assert m["content"] == "why that stop?"


def test_a_nul_in_a_streamed_delta_does_not_truncate_the_rest(db):
    """SQLite's `substr()` stops at an embedded NUL.

    So a delta containing one silently discarded everything after it — the rest of the
    assistant's answer, gone, with no error and nothing in the row to say so. `create_message`
    sanitised its content; this path bypassed that. Found by a property test with `'\\x000'`:
    expected `'0'`, stored `''`.
    """
    s = _session(db)
    m = ss.create_message(
        session_id=s["session_id"], role="assistant", kind="qa_answer",
        status="streaming", path=db,
    )
    ss.append_message_delta(m["message_id"], "before\x00after", path=db)
    ss.append_message_delta(m["message_id"], "\x00tail", path=db)
    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert msgs[0]["content"] == "beforeaftertail"


def test_a_nul_in_a_replacement_body_does_not_truncate_it(db):
    """finalize_message passes content through the same `substr`."""
    s = _session(db)
    m = ss.create_message(
        session_id=s["session_id"], role="assistant", kind="qa_answer",
        status="streaming", content="streamed", path=db,
    )
    ss.finalize_message(m["message_id"], ss.MSG_COMPLETE, content="head\x00tail", path=db)
    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert msgs[0]["content"] == "headtail"


def test_finalize_without_content_still_keeps_what_streamed(db):
    """The None-vs-sanitised distinction must survive: None means "keep it"."""
    s = _session(db)
    m = ss.create_message(
        session_id=s["session_id"], role="assistant", kind="qa_answer",
        status="streaming", content="partial answer", path=db,
    )
    ss.finalize_message(m["message_id"], ss.MSG_TRUNCATED, path=db)
    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert msgs[0]["content"] == "partial answer"


def test_the_same_symbol_can_have_many_sessions(db):
    """The headline fix.

    The old key was `${SYMBOL}::${PROFILE}`, so a second FIND on RELIANCE/INTRADAY
    overwrote the first and two timeframes could not coexist at all.
    """
    a = _session(db, timeframe="10m")
    b = _session(db, timeframe="5m")
    c = _session(db, timeframe="10m")  # identical attributes, still distinct
    assert len({a["session_id"], b["session_id"], c["session_id"]}) == 3
    items, _ = ss.list_sessions("u1", path=db)
    assert len(items) == 3


# ── Ownership ─────────────────────────────────────────────────────────────────


def test_another_users_session_is_indistinguishable_from_a_missing_one(db):
    """Both are None.

    A caller who could tell them apart could enumerate which ids exist, which is why
    the API answers 404 for both and never 403.
    """
    s = _session(db, user="owner")
    assert ss.get_session_for_user(s["session_id"], "intruder", path=db) is None
    assert ss.get_session_for_user("sess_DOESNOTEXIST", "intruder", path=db) is None


def test_there_is_no_ownership_free_session_read():
    """An unowned reader would eventually be called from a path that forgot to check."""
    assert not hasattr(ss, "get_session")


def test_another_user_cannot_list_read_rename_or_archive(db):
    s = _session(db, user="owner")
    ss.create_message(
        session_id=s["session_id"], role="user", kind="qa_question",
        status="complete", content="secret question", path=db,
    )
    _run(db, s, user="owner")

    items, _ = ss.list_sessions("intruder", path=db)
    assert items == []
    assert ss.update_session(s["session_id"], "intruder", patch={"title": "pwned"}, path=db) is None
    assert ss.archive_session(s["session_id"], "intruder", path=db) is None
    assert ss.delete_session(s["session_id"], "intruder", hard=True, path=db) is None
    msgs, last = ss.list_messages(s["session_id"], "intruder", path=db)
    assert msgs == [] and last == 0
    assert ss.list_runs(s["session_id"], "intruder", path=db) == []

    # And nothing was actually mutated by any of those attempts.
    owned = ss.get_session_for_user(s["session_id"], "owner", path=db)
    assert owned["title"] is None and owned["status"] == ss.SESSION_ACTIVE
    msgs, _ = ss.list_messages(s["session_id"], "owner", path=db)
    assert len(msgs) == 1


def test_another_user_cannot_read_a_run_by_id_or_thread(db):
    s = _session(db, user="owner")
    r = _run(db, s, user="owner")
    assert ss.get_run_for_user(r["run_id"], "intruder", path=db) is None
    assert ss.get_run_by_thread_for_user(r["thread_id"], "intruder", path=db) is None
    # The unchecked variant exists for the SERVICE-authenticated /resume only.
    assert ss.get_run_by_thread(r["thread_id"], path=db)["user_id"] == "owner"


# A user id the STORE considers valid — NULs stripped, then non-blank.
#
# Defined once because a plain `.filter(lambda s: s.strip())` is subtly wrong here and bit
# two separate property tests: `'\x00'.strip()` is truthy in Python, so a NUL-only string
# passes that filter while the store correctly rejects it as empty. The property under test
# is about ids that ARE valid; ids that reduce to nothing raise ValueError and are covered
# by their own tests.
VALID_USER_IDS = st.text(min_size=1, max_size=30).filter(
    lambda s: s.replace("\x00", "").strip()
)


@given(other=VALID_USER_IDS)
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_no_user_id_but_the_owner_can_read_a_session(db, other):
    """Property: only the exact owner reads the session."""
    s = _session(db, user="owner")
    if other.replace("\x00", "").strip() == "owner":
        return
    assert ss.get_session_for_user(s["session_id"], other, path=db) is None


# ── Mutability ────────────────────────────────────────────────────────────────


def test_title_and_timeframe_are_mutable(db):
    s = _session(db)
    updated = ss.update_session(
        s["session_id"], "u1", patch={"title": "NIFTY breakout idea", "timeframe": "5m"}, path=db
    )
    assert updated["title"] == "NIFTY breakout idea"
    assert updated["timeframe"] == "5m"
    assert updated["updated_at"] >= s["updated_at"]


@pytest.mark.parametrize("field", ["symbol", "profile"])
def test_symbol_and_profile_are_immutable(db, field):
    """A tab is an instrument.

    A session whose symbol changed would hold a conversation about two instruments with
    a history that no longer describes what was analysed.
    """
    s = _session(db)
    with pytest.raises(ss.ImmutableFieldError):
        ss.update_session(s["session_id"], "u1", patch={field: "TCS"}, path=db)
    assert ss.get_session_for_user(s["session_id"], "u1", path=db)[field] == s[field]


def test_an_unknown_field_is_an_error_not_a_silent_no_op(db):
    """A typo'd key must not look like a successful rename."""
    s = _session(db)
    with pytest.raises(ValueError):
        ss.update_session(s["session_id"], "u1", patch={"titel": "typo"}, path=db)


def test_an_empty_patch_returns_the_session_unchanged(db):
    s = _session(db)
    assert ss.update_session(s["session_id"], "u1", patch={}, path=db)["session_id"] == s["session_id"]


# ── Lifecycle ─────────────────────────────────────────────────────────────────


def test_archive_then_reopen(db):
    s = _session(db)
    archived = ss.archive_session(s["session_id"], "u1", path=db)
    assert archived["status"] == ss.SESSION_ARCHIVED
    assert archived["archived_at"] is not None

    active, _ = ss.list_sessions("u1", status=ss.SESSION_ACTIVE, path=db)
    assert active == [], "an archived session leaves the tab bar"
    in_history, _ = ss.list_sessions("u1", status=ss.SESSION_ARCHIVED, path=db)
    assert len(in_history) == 1, "but stays in history"

    reopened = ss.reopen_session(s["session_id"], "u1", path=db)
    assert reopened["status"] == ss.SESSION_ACTIVE
    assert reopened["archived_at"] is None, "archived_at is derived from status"


def test_soft_delete_keeps_the_content(db):
    s = _session(db)
    ss.create_message(
        session_id=s["session_id"], role="user", kind="qa_question",
        status="complete", content="keep me", path=db,
    )
    ss.delete_session(s["session_id"], "u1", path=db)
    assert ss.get_session_for_user(s["session_id"], "u1", path=db)["status"] == ss.SESSION_DELETED
    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert len(msgs) == 1


def test_hard_delete_scrubs_children_but_keeps_a_tombstone(db):
    s = _session(db)
    r = _run(db, s)
    ss.create_message(
        session_id=s["session_id"], role="user", kind="analysis_request",
        status="complete", content="gone", path=db,
    )
    ss.append_run_events(r["run_id"], [("REASONING", {"content": "gone"})], path=db)

    deleted = ss.delete_session(s["session_id"], "u1", hard=True, path=db)
    assert deleted["status"] == ss.SESSION_DELETED
    assert deleted["title"] is None
    # The tombstone keeps UNIQUE(thread_id) meaningful and lets a stale client be told
    # "gone" rather than "never existed".
    assert ss.get_session_for_user(s["session_id"], "u1", path=db) is not None

    counts = ss.stats(path=db)
    assert counts["messages"] == 0
    assert counts["runs"] == 0
    assert counts["run_events"] == 0
    assert counts["sessions"] == 1


def test_a_deleted_session_is_not_listable(db):
    s = _session(db)
    ss.delete_session(s["session_id"], "u1", hard=True, path=db)
    for status in (ss.SESSION_ACTIVE, ss.SESSION_ARCHIVED, None):
        items, _ = ss.list_sessions("u1", status=status, path=db)
        assert all(i["session_id"] != s["session_id"] for i in items), status


def test_a_hard_delete_does_not_touch_the_compliance_database(db, tmp_path, monkeypatch):
    """THE compliance boundary.

    A user deleting a conversation must not erase the five-year SEBI record. Asserted
    by hashing compliance.db before and after, because "we only opened the other file"
    is a claim and a hash is evidence.
    """
    import hashchain
    import interaction_log

    compliance = tmp_path / "compliance.db"
    monkeypatch.setenv("COMPLIANCE_DB_PATH", str(compliance))
    interaction_log.ensure_store()
    interaction_log.record_request(kind="run", thread_id="t1", user_id="u1", content="analyse")

    def digest():
        return hashlib.sha256(compliance.read_bytes()).hexdigest()

    before = digest()

    s = _session(db)
    r = _run(db, s)
    ss.append_run_events(r["run_id"], [("REASONING", {"content": "x"})], path=db)
    ss.delete_session(s["session_id"], "u1", hard=True, path=db)
    ss.prune_run_events(days=1, path=db)

    assert digest() == before, "the compliance chain must be byte-identical"
    # verify_table opens its own connection and never raises — it reports.
    result = hashchain.verify_table(interaction_log.TABLE, interaction_log._payload_of)
    assert result.ok, f"the compliance chain must still verify: {result}"


# ── Pagination ────────────────────────────────────────────────────────────────


def test_sessions_are_newest_activity_first(db):
    a = _session(db, timeframe="1m")
    b = _session(db, timeframe="5m")
    c = _session(db, timeframe="10m")
    # Activity on `a` must move it to the front — this ordering is what the tab bar
    # and the history list both use.
    ss.create_message(
        session_id=a["session_id"], role="user", kind="qa_question",
        status="complete", content="ping", path=db,
    )
    items, _ = ss.list_sessions("u1", path=db)
    assert items[0]["session_id"] == a["session_id"]
    assert {i["session_id"] for i in items} == {a["session_id"], b["session_id"], c["session_id"]}


def test_cursor_pagination_covers_every_row_exactly_once(db):
    made = [_session(db, timeframe=f"{i}m")["session_id"] for i in range(1, 24)]
    seen = []
    cursor = None
    for _ in range(20):  # bounded so a broken cursor cannot loop forever
        page, cursor = ss.list_sessions("u1", limit=5, cursor=cursor, path=db)
        seen.extend(p["session_id"] for p in page)
        if cursor is None:
            break
    assert cursor is None, "pagination must terminate"
    assert len(seen) == len(set(seen)) == len(made)
    assert set(seen) == set(made)


def test_pagination_is_stable_when_rows_are_reordered_mid_scroll(db):
    """Keyset, not OFFSET.

    Sessions are constantly reordered by activity — a streaming run bumps updated_at on
    every flush — and under OFFSET that means a row can be skipped or repeated between
    pages. Here: read page 1, bump a row that was already on it, read page 2.
    """
    ids = [_session(db, timeframe=f"{i}m")["session_id"] for i in range(1, 13)]
    page1, cursor = ss.list_sessions("u1", limit=4, path=db)
    assert cursor
    ss.create_message(
        session_id=page1[0]["session_id"], role="user", kind="qa_question",
        status="complete", content="bump", path=db,
    )
    page2, _ = ss.list_sessions("u1", limit=4, cursor=cursor, path=db)
    first_ids = {p["session_id"] for p in page1}
    assert not (first_ids & {p["session_id"] for p in page2}), "no row may repeat"
    assert len(page2) == 4, "and none may be skipped"
    assert set(ids) >= first_ids


def test_a_malformed_cursor_yields_the_first_page(db):
    """A stale bookmark should not be a broken screen; the query is scoped regardless."""
    for _ in range(3):
        _session(db)
    for bad in ("garbage", "", "|", "abc|", "notafloat|sess_x"):
        page, _ = ss.list_sessions("u1", limit=2, cursor=bad, path=db)
        assert len(page) == 2


def test_limit_is_clamped(db):
    for _ in range(3):
        _session(db)
    assert len(ss.list_sessions("u1", limit=0, path=db)[0]) == 1
    assert len(ss.list_sessions("u1", limit=10_000, path=db)[0]) == 3


def test_search_matches_symbol_and_title_only(db):
    a = _session(db, symbol="RELIANCE")
    b = _session(db, symbol="TCS")
    ss.update_session(b["session_id"], "u1", patch={"title": "Momentum idea"}, path=db)
    ss.create_message(
        session_id=a["session_id"], role="user", kind="qa_question",
        status="complete", content="momentum question", path=db,
    )

    assert [i["session_id"] for i in ss.list_sessions("u1", query="relian", path=db)[0]] == [a["session_id"]]
    assert [i["session_id"] for i in ss.list_sessions("u1", query="momentum", path=db)[0]] == [b["session_id"]]


def test_an_unknown_status_filter_is_an_error(db):
    with pytest.raises(ValueError):
        ss.list_sessions("u1", status="bogus", path=db)


# ── Runs ──────────────────────────────────────────────────────────────────────


def test_create_run_mints_a_thread_and_becomes_the_active_run(db):
    s = _session(db)
    r = _run(db, s)
    assert r["thread_id"].startswith("thread_")
    assert r["status"] == ss.RUN_RUNNING
    assert r["terminal_status"] is None
    assert ss.get_session_for_user(s["session_id"], "u1", path=db)["active_run_id"] == r["run_id"]


def test_a_run_snapshots_its_context_immutably(db):
    """A session's timeframe is a mutable default; a run's is history.

    Without the snapshot, changing the session timeframe would silently rewrite what an
    earlier run claims to have analysed.
    """
    s = _session(db, timeframe="10m")
    r = _run(db, s)
    ss.update_session(s["session_id"], "u1", patch={"timeframe": "5m"}, path=db)
    assert ss.get_run_for_user(r["run_id"], "u1", path=db)["timeframe"] == "10m"


def test_two_finds_in_one_session_get_separate_runs_and_threads(db):
    """Required, not preferred.

    reco_store enforces UNIQUE(thread_id), so reusing a thread for a second committed
    decision would collide with the append-only compliance record.
    """
    s = _session(db)
    a, b = _run(db, s), _run(db, s)
    assert a["run_id"] != b["run_id"]
    assert a["thread_id"] != b["thread_id"]
    assert len(ss.list_runs(s["session_id"], "u1", path=db)) == 2
    assert ss.get_session_for_user(s["session_id"], "u1", path=db)["active_run_id"] == b["run_id"]


def test_a_run_cannot_be_created_in_an_unowned_or_deleted_session(db):
    s = _session(db, user="owner")
    assert _run(db, s, user="intruder") is None
    ss.delete_session(s["session_id"], "owner", hard=True, path=db)
    assert _run(db, s, user="owner") is None


def test_verify_run_keeps_the_inputs_that_were_verified(db):
    """So a reopened VERIFY session can show WHAT was verified, not just the verdict."""
    s = _session(db)
    manual = {"side": "BUY", "entry": 2470.0, "stop_loss": 2435.0, "take_profit": 2550.0}
    r = _run(db, s, kind="verify", manual_trade=manual)
    assert ss.get_run_for_user(r["run_id"], "u1", path=db)["manual_trade"] == manual


def test_an_unknown_run_kind_is_refused(db):
    s = _session(db)
    with pytest.raises(ValueError):
        _run(db, s, kind="guess")


def test_finalize_run_is_set_once(db):
    """A duplicate RUN_FINISHED — which the reattach path really produces — is a no-op."""
    s = _session(db)
    r = _run(db, s)
    assert ss.finalize_run(r["run_id"], ss.RUN_COMPLETE, path=db) is True
    assert ss.finalize_run(r["run_id"], ss.RUN_ERROR, path=db) is False
    got = ss.get_run_for_user(r["run_id"], "u1", path=db)
    assert got["status"] == ss.RUN_COMPLETE
    assert got["terminal_status"] == ss.RUN_COMPLETE
    assert got["ended_at"] is not None


def test_a_terminal_status_cannot_go_through_update_run_status(db):
    """Splitting the two is what makes idempotency a store property, not a caller duty."""
    s = _session(db)
    r = _run(db, s)
    with pytest.raises(ValueError):
        ss.update_run_status(r["run_id"], ss.RUN_COMPLETE, path=db)
    assert ss.update_run_status(r["run_id"], ss.RUN_WATCHING, path=db) is True
    assert ss.get_run_for_user(r["run_id"], "u1", path=db)["status"] == ss.RUN_WATCHING


def test_a_finalized_run_cannot_be_reopened_as_live(db):
    s = _session(db)
    r = _run(db, s)
    ss.finalize_run(r["run_id"], ss.RUN_COMPLETE, path=db)
    assert ss.update_run_status(r["run_id"], ss.RUN_RUNNING, path=db) is False


def test_finalize_run_rejects_a_non_terminal_status(db):
    s = _session(db)
    r = _run(db, s)
    for bad in (ss.RUN_RUNNING, ss.RUN_WATCHING, "bogus"):
        with pytest.raises(ValueError):
            ss.finalize_run(r["run_id"], bad, path=db)


# ── Run events ────────────────────────────────────────────────────────────────


def test_events_keep_their_order_and_structure(db):
    """Structure survives: DECISION.execution_levels must not become prose."""
    s = _session(db)
    r = _run(db, s)
    levels = {"entry": 2470.0, "stop_loss": 2435.0, "take_profit": 2550.0}
    ss.append_run_events(
        r["run_id"],
        [
            ("RUN_STARTED", {"thread_id": r["thread_id"]}),
            ("TOOL_CALL_START", {"tool": "get_candles", "args": {"symbol": "RELIANCE"}}),
            ("DECISION", {"action": "BUY", "execution_levels": levels}),
        ],
        path=db,
    )
    events, last = ss.list_run_events(r["run_id"], path=db)
    assert [e["event"] for e in events] == ["RUN_STARTED", "TOOL_CALL_START", "DECISION"]
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert last == 3
    assert events[1]["data"]["args"] == {"symbol": "RELIANCE"}
    assert events[2]["data"]["execution_levels"] == levels


def test_seq_continues_across_batches(db):
    s = _session(db)
    r = _run(db, s)
    assert ss.append_run_events(r["run_id"], [("A", {}), ("B", {})], path=db) == 2
    assert ss.append_run_events(r["run_id"], [("C", {})], path=db) == 3
    events, _ = ss.append_run_events(r["run_id"], [], path=db), None
    assert ss.append_run_events(r["run_id"], [], path=db) == 3, "an empty batch is a no-op"
    seqs = [e["seq"] for e in ss.list_run_events(r["run_id"], path=db)[0]]
    assert seqs == [1, 2, 3]


def test_after_seq_replays_only_the_gap(db):
    """This is what makes reattach gap-free.

    Today `_publish_frame` returns early when nobody is attached, so frames emitted
    between the /run stream ending and the hub GET landing are lost with no recovery.
    """
    s = _session(db)
    r = _run(db, s)
    ss.append_run_events(r["run_id"], [(f"E{i}", {"i": i}) for i in range(1, 6)], path=db)

    events, last = ss.list_run_events(r["run_id"], after_seq=2, path=db)
    assert [e["event"] for e in events] == ["E3", "E4", "E5"]
    assert last == 5
    assert ss.list_run_events(r["run_id"], after_seq=5, path=db)[0] == []
    assert ss.list_run_events(r["run_id"], after_seq=99, path=db)[0] == []


def test_a_redelivered_frame_cannot_duplicate_the_transcript(db):
    """PRIMARY KEY (run_id, seq) + INSERT OR IGNORE: reconnect cannot double the log."""
    s = _session(db)
    r = _run(db, s)
    conn = ss.connect(db)
    try:
        conn.execute(
            "INSERT INTO run_events (run_id, seq, event, payload_json, created_at) "
            "VALUES (?, 1, 'REASONING', '{}', 1)",
            (r["run_id"],),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO run_events (run_id, seq, event, payload_json, created_at) "
                "VALUES (?, 1, 'REASONING', '{}', 1)",
                (r["run_id"],),
            )
    finally:
        conn.close()


def test_events_for_an_unknown_run_are_a_no_op(db):
    assert ss.append_run_events("run_NOPE", [("A", {})], path=db) == 0
    assert ss.list_run_events("run_NOPE", path=db) == ([], 0)


def test_an_unserialisable_payload_does_not_break_the_write(db):
    """A stream must not die because one frame held an odd object."""
    s = _session(db)
    r = _run(db, s)

    class Odd:
        def __repr__(self):
            return "<Odd>"

    assert ss.append_run_events(r["run_id"], [("REASONING", {"o": Odd()})], path=db) == 1
    events, _ = ss.list_run_events(r["run_id"], path=db)
    assert events[0]["data"] is not None


# ── Messages ──────────────────────────────────────────────────────────────────


def test_seq_is_dense_and_per_session(db):
    a, b = _session(db), _session(db)
    for i in range(4):
        ss.create_message(
            session_id=a["session_id"], role="user", kind="qa_question",
            status="complete", content=f"a{i}", path=db,
        )
        ss.create_message(
            session_id=b["session_id"], role="user", kind="qa_question",
            status="complete", content=f"b{i}", path=db,
        )
    for sess in (a, b):
        msgs, last = ss.list_messages(sess["session_id"], "u1", path=db)
        assert [m["seq"] for m in msgs] == [1, 2, 3, 4]
        assert last == 4


def test_client_msg_id_makes_a_retried_send_idempotent(db):
    """Without it, a flaky submit shows the user's question twice, unfixably."""
    s = _session(db)
    first = ss.create_message(
        session_id=s["session_id"], role="user", kind="qa_question", status="complete",
        content="why that stop?", client_msg_id="composer-1", path=db,
    )
    again = ss.create_message(
        session_id=s["session_id"], role="user", kind="qa_question", status="complete",
        content="why that stop?", client_msg_id="composer-1", path=db,
    )
    assert again["message_id"] == first["message_id"]
    assert again["seq"] == first["seq"]
    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert len(msgs) == 1


def test_the_same_client_msg_id_in_a_different_session_is_a_different_message(db):
    a, b = _session(db), _session(db)
    m1 = ss.create_message(
        session_id=a["session_id"], role="user", kind="qa_question", status="complete",
        content="q", client_msg_id="c1", path=db,
    )
    m2 = ss.create_message(
        session_id=b["session_id"], role="user", kind="qa_question", status="complete",
        content="q", client_msg_id="c1", path=db,
    )
    assert m1["message_id"] != m2["message_id"]


def test_streaming_deltas_accumulate(db):
    s = _session(db)
    r = _run(db, s)
    m = ss.create_message(
        session_id=s["session_id"], role="assistant", kind="analysis_answer",
        status="streaming", run_id=r["run_id"], path=db,
    )
    for chunk in ("The ", "setup ", "is "):
        assert ss.append_message_delta(m["message_id"], chunk, path=db) is True
    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert msgs[0]["content"] == "The setup is "
    assert msgs[0]["status"] == ss.MSG_STREAMING


def test_a_late_delta_cannot_reopen_a_finalized_message(db):
    """The reattach path really does deliver frames after the terminal event."""
    s = _session(db)
    m = ss.create_message(
        session_id=s["session_id"], role="assistant", kind="qa_answer",
        status="streaming", content="done", path=db,
    )
    ss.finalize_message(m["message_id"], ss.MSG_COMPLETE, path=db)
    assert ss.append_message_delta(m["message_id"], " EXTRA", path=db) is False
    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert msgs[0]["content"] == "done"


def test_finalize_message_is_set_once(db):
    """The specific way a truncated answer would become a 'complete' one."""
    s = _session(db)
    m = ss.create_message(
        session_id=s["session_id"], role="assistant", kind="qa_answer",
        status="streaming", content="half", path=db,
    )
    assert ss.finalize_message(m["message_id"], ss.MSG_TRUNCATED, path=db) is True
    assert ss.finalize_message(m["message_id"], ss.MSG_COMPLETE, path=db) is False
    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert msgs[0]["status"] == ss.MSG_TRUNCATED
    assert msgs[0]["content"] == "half", "partial content is kept, not discarded"


def test_finalize_can_replace_the_content_or_keep_it(db):
    s = _session(db)
    keep = ss.create_message(
        session_id=s["session_id"], role="assistant", kind="qa_answer",
        status="streaming", content="streamed", path=db,
    )
    ss.finalize_message(keep["message_id"], ss.MSG_COMPLETE, path=db)

    replace = ss.create_message(
        session_id=s["session_id"], role="assistant", kind="qa_answer",
        status="streaming", content="streamed", path=db,
    )
    ss.finalize_message(replace["message_id"], ss.MSG_COMPLETE, content="assembled", path=db)

    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert msgs[0]["content"] == "streamed"
    assert msgs[1]["content"] == "assembled"


def test_an_error_message_keeps_its_detail(db):
    s = _session(db)
    m = ss.create_message(
        session_id=s["session_id"], role="assistant", kind="qa_answer",
        status="streaming", path=db,
    )
    ss.finalize_message(m["message_id"], ss.MSG_ERROR, error_detail="LLM unavailable", path=db)
    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert msgs[0]["status"] == ss.MSG_ERROR
    assert msgs[0]["error_detail"] == "LLM unavailable"


def test_qa_activity_is_stored_structurally(db):
    s = _session(db)
    m = ss.create_message(
        session_id=s["session_id"], role="assistant", kind="qa_answer",
        status="streaming", activity=["> get_candles"], path=db,
    )
    ss.finalize_message(
        m["message_id"], ss.MSG_COMPLETE, activity=["> get_candles", "get_candles"], path=db
    )
    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert msgs[0]["activity"] == ["> get_candles", "get_candles"]


@pytest.mark.parametrize("role,kind,status", [
    ("bogus", "qa_answer", "complete"),
    ("assistant", "bogus", "complete"),
    ("assistant", "qa_answer", "bogus"),
    ("user", "qa_question", "streaming"),
])
def test_invalid_message_vocabulary_is_refused_in_python(db, role, kind, status):
    """Refused before SQL, so the API answers 422 rather than surfacing IntegrityError as 500."""
    s = _session(db)
    with pytest.raises(ValueError):
        ss.create_message(
            session_id=s["session_id"], role=role, kind=kind, status=status, path=db
        )


def test_a_message_in_an_unknown_session_is_none(db):
    assert ss.create_message(
        session_id="sess_NOPE", role="user", kind="qa_question", status="complete",
        content="x", path=db,
    ) is None


def test_oversize_content_records_its_own_truncation(db):
    """A row that says it was truncated is honest; one that quietly lost its tail is not."""
    s = _session(db)
    m = ss.create_message(
        session_id=s["session_id"], role="user", kind="qa_question", status="complete",
        content="x" * (ss.MAX_CONTENT_CHARS + 500), path=db,
    )
    assert "[truncated 500 characters]" in m["content"]


def test_message_pagination_by_seq(db):
    s = _session(db)
    for i in range(10):
        ss.create_message(
            session_id=s["session_id"], role="user", kind="qa_question",
            status="complete", content=str(i), path=db,
        )
    page, last = ss.list_messages(s["session_id"], "u1", after_seq=4, limit=3, path=db)
    assert [m["seq"] for m in page] == [5, 6, 7]
    assert last == 10, "last_seq is the session's max, not the page's"


# ── Activity ordering ─────────────────────────────────────────────────────────


def test_every_child_write_bumps_the_session(db):
    """updated_at orders the tab bar; a session whose newest message did not move it
    would sort as stale, which is the one thing that list must get right."""
    s = _session(db)
    r = _run(db, s)
    stamps = [ss.get_session_for_user(s["session_id"], "u1", path=db)["updated_at"]]

    for action in (
        lambda: ss.create_message(
            session_id=s["session_id"], role="user", kind="qa_question",
            status="complete", content="q", path=db,
        ),
        lambda: ss.append_run_events(r["run_id"], [("REASONING", {})], path=db),
        lambda: ss.finalize_run(r["run_id"], ss.RUN_COMPLETE, path=db),
    ):
        action()
        stamps.append(ss.get_session_for_user(s["session_id"], "u1", path=db)["updated_at"])

    assert stamps == sorted(stamps), stamps


# ── Reconciliation: the anti-fabrication pass ────────────────────────────────


class _PendingGraph:
    """A checkpointer reporting every thread as still having a pending step."""

    def get_state(self, _config):
        class S:
            next = ("watch_price_condition",)

        return S()


class _FinishedGraph:
    def get_state(self, _config):
        class S:
            next = ()

        return S()


class _BrokenGraph:
    def get_state(self, _config):
        raise RuntimeError("checkpoint unreadable")


def test_a_crashed_run_becomes_truncated_not_complete(db):
    """THE honesty test.

    A process that died mid-stream leaves status='running' and a 'streaming' message.
    Rendered unchanged, that shows a half-written answer as though it were still
    arriving — and then as though it had succeeded.
    """
    s = _session(db)
    r = _run(db, s)
    m = ss.create_message(
        session_id=s["session_id"], role="assistant", kind="analysis_answer",
        status="streaming", content="I was analysing the", run_id=r["run_id"], path=db,
    )

    assert ss.reconcile_stale_runs(_FinishedGraph(), path=db) == 1

    got = ss.get_run_for_user(r["run_id"], "u1", path=db)
    assert got["status"] == ss.RUN_TRUNCATED
    assert got["terminal_status"] == ss.RUN_TRUNCATED
    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert msgs[0]["status"] == ss.MSG_TRUNCATED
    assert msgs[0]["content"] == "I was analysing the", "partial text is preserved"


def test_a_genuinely_paused_run_stays_resumable(db):
    """What MemorySaver could never produce.

    Truncating a resumable run would break the watcher; leaving a dead one 'watching'
    is the WATCHING-forever bug. The checkpoint is what makes this decidable.
    """
    s = _session(db)
    r = _run(db, s)
    ss.update_run_status(r["run_id"], ss.RUN_WATCHING, path=db)

    assert ss.reconcile_stale_runs(_PendingGraph(), path=db) == 0
    got = ss.get_run_for_user(r["run_id"], "u1", path=db)
    assert got["status"] == ss.RUN_WATCHING
    assert got["terminal_status"] is None


def test_no_checkpointer_means_nothing_is_resumable(db):
    s = _session(db)
    r = _run(db, s)
    assert ss.reconcile_stale_runs(None, path=db) == 1
    assert ss.get_run_for_user(r["run_id"], "u1", path=db)["status"] == ss.RUN_TRUNCATED


def test_an_unreadable_checkpoint_truncates_rather_than_guessing_resumable(db):
    """Guessing resumable on an error leaves a run permanently unwakeable.

    Guessing truncated is honest and the user can re-run.
    """
    s = _session(db)
    r = _run(db, s)
    assert ss.reconcile_stale_runs(_BrokenGraph(), path=db) == 1
    assert ss.get_run_for_user(r["run_id"], "u1", path=db)["status"] == ss.RUN_TRUNCATED


def test_reconciliation_is_idempotent(db):
    s = _session(db)
    _run(db, s)
    assert ss.reconcile_stale_runs(_FinishedGraph(), path=db) == 1
    assert ss.reconcile_stale_runs(_FinishedGraph(), path=db) == 0


def test_reconciliation_does_not_disturb_finished_runs(db):
    s = _session(db)
    r = _run(db, s)
    ss.finalize_run(r["run_id"], ss.RUN_COMPLETE, path=db)
    assert ss.reconcile_stale_runs(_FinishedGraph(), path=db) == 0
    assert ss.get_run_for_user(r["run_id"], "u1", path=db)["status"] == ss.RUN_COMPLETE


def test_an_orphaned_streaming_message_is_swept(db):
    """Reachable if a process died between finalizing the run and the message.

    Same lie about completeness, different window.
    """
    s = _session(db)
    r = _run(db, s)
    m = ss.create_message(
        session_id=s["session_id"], role="assistant", kind="analysis_answer",
        status="streaming", content="partial", run_id=r["run_id"], path=db,
    )
    ss.finalize_run(r["run_id"], ss.RUN_COMPLETE, path=db)  # run done, message left open

    assert ss.reconcile_stale_runs(_FinishedGraph(), path=db) == 1
    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert msgs[0]["status"] == ss.MSG_TRUNCATED


def test_reconciliation_on_an_empty_store_is_a_no_op(db):
    assert ss.reconcile_stale_runs(_FinishedGraph(), path=db) == 0


# ── Retention ─────────────────────────────────────────────────────────────────


def _backdate_run(db, run_id, seconds_ago=86_400 * 400):
    """Age a finished run so the retention window can be exercised.

    Backdating beats passing an absurdly small `days`: `days=1e-7` is 8.6 ms, and a run
    finalized microseconds earlier is correctly INSIDE that window, so the assertion
    would be testing the clock rather than the pruner.
    """
    conn = ss.connect(db)
    try:
        stamp = ss.now() - seconds_ago
        conn.execute(
            "UPDATE runs SET ended_at = ?, last_event_at = ? WHERE run_id = ?",
            (stamp, stamp, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_pruning_removes_old_transcripts_but_keeps_the_conversation(db):
    """run_events is the only unbounded table.

    Losing an old glass-box transcript costs frame-by-frame replay; losing the
    conversation would defeat the point.
    """
    s = _session(db)
    r = _run(db, s)
    ss.append_run_events(r["run_id"], [("REASONING", {"content": "old"})], path=db)
    ss.create_message(
        session_id=s["session_id"], role="user", kind="analysis_request",
        status="complete", content="keep me", path=db,
    )
    ss.finalize_run(r["run_id"], ss.RUN_COMPLETE, path=db)
    _backdate_run(db, r["run_id"])

    assert ss.prune_run_events(days=90, path=db) == 1
    counts = ss.stats(path=db)
    assert counts["run_events"] == 0
    assert counts["messages"] == 1
    assert counts["runs"] == 1
    assert counts["sessions"] == 1


def test_pruning_never_touches_a_live_run(db):
    s = _session(db)
    r = _run(db, s)
    ss.append_run_events(r["run_id"], [("REASONING", {})], path=db)
    # Backdate it so only the live-status guard can protect it.
    conn = ss.connect(db)
    try:
        conn.execute("UPDATE runs SET last_event_at = 0 WHERE run_id = ?", (r["run_id"],))
        conn.commit()
    finally:
        conn.close()
    assert ss.prune_run_events(days=1, path=db) == 0
    assert ss.stats(path=db)["run_events"] == 1


def test_recent_transcripts_are_kept(db):
    s = _session(db)
    r = _run(db, s)
    ss.append_run_events(r["run_id"], [("REASONING", {})], path=db)
    ss.finalize_run(r["run_id"], ss.RUN_COMPLETE, path=db)
    assert ss.prune_run_events(days=90, path=db) == 0
    assert ss.stats(path=db)["run_events"] == 1


def test_zero_retention_disables_pruning(db):
    """0 means 'never prune', not 'prune everything'.

    The distinction matters: an operator setting 0 to turn the feature off must not
    thereby delete every transcript in the store.
    """
    s = _session(db)
    r = _run(db, s)
    ss.append_run_events(r["run_id"], [("REASONING", {})], path=db)
    ss.finalize_run(r["run_id"], ss.RUN_COMPLETE, path=db)
    _backdate_run(db, r["run_id"])
    assert ss.prune_run_events(days=0, path=db) == 0
    assert ss.stats(path=db)["run_events"] == 1


@pytest.mark.parametrize("raw,expected", [
    ("", 90.0), ("30", 30.0), ("0", 0.0), ("nonsense", 90.0), ("-5", 0.0),
])
def test_retention_env_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv(ss.ENV_RETENTION_DAYS, raw)
    assert ss.retention_days() == expected


# ── Properties ────────────────────────────────────────────────────────────────


@given(
    interleaving=st.lists(st.booleans(), min_size=1, max_size=40),
)
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property_seq_is_always_dense_under_any_interleaving(db, interleaving):
    """Property: for any interleaving of writes across two sessions, each session's
    seq is exactly 1..n with no gaps and no duplicates.

    Gap-free seq is what makes `?after_seq=` an exact "everything I have not seen".
    """
    a = _session(db, timeframe="1m")
    b = _session(db, timeframe="2m")
    counts = {a["session_id"]: 0, b["session_id"]: 0}
    for pick_a in interleaving:
        target = a if pick_a else b
        ss.create_message(
            session_id=target["session_id"], role="user", kind="qa_question",
            status="complete", content="x", path=db,
        )
        counts[target["session_id"]] += 1
    for sid, expected in counts.items():
        msgs, last = ss.list_messages(sid, "u1", after_seq=0, limit=1000, path=db)
        assert [m["seq"] for m in msgs] == list(range(1, expected + 1))
        assert last == expected


@given(batches=st.lists(st.integers(min_value=0, max_value=5), min_size=1, max_size=15))
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property_replaying_stored_events_reproduces_the_input_order(db, batches):
    """Property: however the frames were batched, reading them back in seq order
    yields the exact input sequence.

    Rehydration feeds these through the frontend's applyStreamEvent; if the order were
    not faithful, a restored transcript would differ from a live one.
    """
    s = _session(db)
    r = _run(db, s)
    expected = []
    counter = 0
    for size in batches:
        batch = []
        for _ in range(size):
            counter += 1
            batch.append((f"E{counter}", {"i": counter}))
        expected.extend(name for name, _ in batch)
        ss.append_run_events(r["run_id"], batch, path=db)

    events, last = ss.list_run_events(r["run_id"], limit=5000, path=db)
    assert [e["event"] for e in events] == expected
    assert last == len(expected)
    assert [e["seq"] for e in events] == list(range(1, len(expected) + 1))


@given(user=VALID_USER_IDS)
@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property_a_session_is_only_ever_visible_to_its_owner(db, user):
    """Property: whoever creates a session is the only one who can list it.

    Uses the shared VALID_USER_IDS strategy, which matches the store's own notion of
    "non-empty". A user id that reduces to nothing is a ValueError and is covered
    separately; this property is about the ones that are valid.
    """
    s = ss.create_session(
        user_id=user, symbol="RELIANCE", profile="INTRADAY", timeframe="10m", path=db
    )
    mine, _ = ss.list_sessions(user, path=db)
    assert s["session_id"] in {m["session_id"] for m in mine}
    others, _ = ss.list_sessions(user.strip() + "_other", path=db)
    assert s["session_id"] not in {o["session_id"] for o in others}


@given(status=st.sampled_from(ss.MESSAGE_STATUSES))
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property_a_finalized_message_never_becomes_complete_again(db, status):
    """Property: once out of `streaming`, no call can make a message claim completeness.

    This is the "an incomplete streamed response must never appear as a complete one"
    rule, expressed as a property of the store.
    """
    if status == ss.MSG_STREAMING:
        return
    s = _session(db)
    m = ss.create_message(
        session_id=s["session_id"], role="assistant", kind="qa_answer",
        status="streaming", content="partial", path=db,
    )
    ss.finalize_message(m["message_id"], status, path=db)
    assert ss.finalize_message(m["message_id"], ss.MSG_COMPLETE, path=db) is False
    assert ss.append_message_delta(m["message_id"], "more", path=db) is False
    msgs, _ = ss.list_messages(s["session_id"], "u1", path=db)
    assert msgs[0]["status"] == status
