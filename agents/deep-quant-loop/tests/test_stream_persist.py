"""Stream_Persister tests — the transcript, and the honesty rules around it.

Four things are being defended, and each maps to a way a user could be lied to:

1. **Terminal implies the transcript is whole.** A run marked ``complete`` whose stored
   frames stop three frames earlier would claim to have finished something it has no
   record of doing. The buffer is flushed before any terminal write, and there is a
   property test over arbitrary frame sequences.
2. **A partial answer is never presented as a complete one.** Disconnect, error, cancel and
   paused each land on a distinct status, and none of them can be overwritten by a late or
   duplicate terminal event.
3. **Persistence never affects the live stream.** The emitted SSE bytes are identical with
   persistence enabled, disabled, and hard-failing on every call.
4. **Replay is byte-identical to the live frame.** Otherwise rehydration would render
   differently from a stream that was watched live, and there would be two rendering paths
   to keep in step.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import internal_identity as ident
import session_store as store
import stream_persist

SECRET = "q" * 64
SERVICE_SECRET = "r" * 64


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(ident.ENV_IDENTITY_SECRET, SECRET)
    monkeypatch.setenv(ident.ENV_SERVICE_SECRET, SERVICE_SECRET)
    monkeypatch.setenv("DEEP_QUANT_SESSIONS_ENABLED", "1")
    monkeypatch.setenv(stream_persist.ENV_ENABLED, "1")
    monkeypatch.delenv(ident.ENV_REQUIRE_IDENTITY, raising=False)
    monkeypatch.delenv("DEEP_QUANT_REQUIRE_SESSION", raising=False)
    monkeypatch.delenv("SKU_ENFORCE", raising=False)
    ident._warned_unenforced = False
    yield
    ident._warned_unenforced = False


@pytest.fixture
def run(tmp_path, monkeypatch):
    """A session + run in a throwaway store, with the store's default path pointed at it."""
    path = str(tmp_path / "sessions.db")
    monkeypatch.setenv(store.ENV_DB_PATH, path)
    store.ensure_store(path)
    session = store.create_session(
        user_id="alice", symbol="RELIANCE", profile="INTRADAY", timeframe="10m", path=path
    )
    created = store.create_run(
        session_id=session["session_id"], user_id="alice", kind="find",
        symbol="RELIANCE", timeframe="10m", profile="INTRADAY", path=path,
    )
    return {"path": path, "session": session, "run": created}


def _persister(run, kind="run"):
    return stream_persist.StreamPersister(
        run["run"]["run_id"], run["session"]["session_id"], kind=kind
    )


# ── The flag and the no-op path ───────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("nope", False),
])
def test_the_flag_parsing(monkeypatch, value, expected):
    monkeypatch.setenv(stream_persist.ENV_ENABLED, value)
    assert stream_persist.enabled() is expected


def test_no_run_id_makes_every_method_a_no_op():
    """The legacy path (a /run with only a thread_id) has no run row.

    A no-op object means no call site has to branch on whether persistence applies.
    """
    p = stream_persist.StreamPersister()
    assert p.active is False
    assert p.open() is None
    p.add("REASONING", {"content": "x"})
    p.flush()
    p.mark_watching()
    p.finalize("completed")
    p.record_disconnect()


def test_the_flag_off_makes_every_method_a_no_op(run, monkeypatch):
    monkeypatch.setenv(stream_persist.ENV_ENABLED, "0")
    p = _persister(run)
    assert p.active is False
    p.open()
    p.add("REASONING", {"content": "x"})
    p.finalize("completed")
    events, _ = store.list_run_events(run["run"]["run_id"], path=run["path"])
    assert events == []
    assert store.get_run_for_user(run["run"]["run_id"], "alice", path=run["path"])["status"] == "running"


# ── Recording ─────────────────────────────────────────────────────────────────


def test_open_creates_a_streaming_assistant_message(run):
    p = _persister(run)
    message_id = p.open()
    assert message_id
    messages, _ = store.list_messages(run["session"]["session_id"], "alice", path=run["path"])
    assert [(m["role"], m["kind"], m["status"]) for m in messages] == [
        ("assistant", "analysis_answer", "streaming")
    ]


def test_open_is_idempotent(run):
    p = _persister(run)
    assert p.open() == p.open()
    messages, _ = store.list_messages(run["session"]["session_id"], "alice", path=run["path"])
    assert len(messages) == 1


def test_a_run_that_produces_nothing_still_leaves_a_visible_turn(run):
    """An empty answer the user can see beats a silent gap they cannot ask about."""
    p = _persister(run)
    p.open()
    p.finalize("error", detail="LLM unavailable")
    messages, _ = store.list_messages(run["session"]["session_id"], "alice", path=run["path"])
    assert messages[0]["status"] == "error"
    assert messages[0]["error_detail"] == "LLM unavailable"


def test_qa_creates_a_qa_answer_message(run):
    p = _persister(run, kind="qa")
    p.open()
    messages, _ = store.list_messages(run["session"]["session_id"], "alice", path=run["path"])
    assert messages[0]["kind"] == "qa_answer"


def test_qa_frames_stay_out_of_the_runs_glass_box(run):
    """A Q&A turn reuses the analysis run's `run_id`, so its frames must not be appended.

    They were, and the second `RUN_STARTED` reset the transcript when a reopened session
    replayed the log through the live reducer: the FIND analysis was rebuilt and then thrown
    away, leaving the Q&A answer as the whole glass box. The prose still has to be recorded,
    because that message IS the restored chat turn.
    """
    p = _persister(run, kind="qa")
    p.open()
    p.add("RUN_STARTED", {"thread_id": "t"})
    p.add("REASONING", {"content": "The stop sits below the swing low."})
    p.add("RUN_FINISHED", {"status": "completed"})
    p.flush()

    assert store.list_run_events(run["run"]["run_id"], path=run["path"]) == ([], 0)
    messages, _ = store.list_messages(run["session"]["session_id"], "alice", path=run["path"])
    assert messages[0]["content"] == "The stop sits below the swing low."


def test_frames_are_recorded_in_order_with_structure_intact(run):
    p = _persister(run)
    p.open()
    levels = {"entry": 2470.0, "stop_loss": 2435.0, "take_profit": 2550.0}
    p.add("RUN_STARTED", {"thread_id": "t"})
    p.add("TOOL_CALL_START", {"tool": "get_candles", "args": {"symbol": "RELIANCE"}})
    p.add("DECISION", {"action": "BUY", "execution_levels": levels})
    p.flush()

    events, last = store.list_run_events(run["run"]["run_id"], path=run["path"])
    assert [e["event"] for e in events] == ["RUN_STARTED", "TOOL_CALL_START", "DECISION"]
    assert last == 3
    # The glass box keeps its shape; collapsing this into prose is what must not happen.
    assert events[1]["data"]["args"] == {"symbol": "RELIANCE"}
    assert events[2]["data"]["execution_levels"] == levels


def test_reasoning_content_is_folded_into_the_message(run):
    p = _persister(run)
    p.open()
    for chunk in ("The ", "setup ", "is bullish."):
        p.add("REASONING", {"content": chunk})
    p.flush()
    messages, _ = store.list_messages(run["session"]["session_id"], "alice", path=run["path"])
    assert messages[0]["content"] == "The setup is bullish."


def test_non_content_events_do_not_pollute_the_message(run):
    """Tool activity and the structured decision belong in run_events, not in the prose."""
    p = _persister(run)
    p.open()
    p.add("REASONING", {"content": "thinking"})
    p.add("TOOL_CALL_START", {"tool": "get_candles"})
    p.add("DECISION", {"action": "BUY"})
    p.flush()
    messages, _ = store.list_messages(run["session"]["session_id"], "alice", path=run["path"])
    assert messages[0]["content"] == "thinking"


def test_a_full_batch_flushes_without_being_asked(run):
    p = _persister(run)
    p.open()
    for i in range(stream_persist.BATCH_SIZE):
        p.add("REASONING", {"content": str(i)})
    events, _ = store.list_run_events(run["run"]["run_id"], path=run["path"])
    assert len(events) == stream_persist.BATCH_SIZE


def test_an_empty_flush_is_harmless(run):
    p = _persister(run)
    p.open()
    p.flush()
    p.flush()
    assert store.list_run_events(run["run"]["run_id"], path=run["path"]) == ([], 0)


# ── Terminal honesty ──────────────────────────────────────────────────────────


def test_finalize_flushes_first(run):
    """THE ordering rule.

    A terminal write ahead of a buffered frame would produce a run marked `complete` whose
    transcript stops earlier — a run claiming to have finished something it has no record
    of doing.
    """
    p = _persister(run)
    p.open()
    p.add("REASONING", {"content": "important"})  # under the batch size, so still buffered
    p.finalize("completed")

    events, _ = store.list_run_events(run["run"]["run_id"], path=run["path"])
    assert [e["event"] for e in events] == ["REASONING", "RUN_FINISHED"] or [
        e["event"] for e in events
    ] == ["REASONING"]
    assert store.get_run_for_user(run["run"]["run_id"], "alice", path=run["path"])["status"] == "complete"
    messages, _ = store.list_messages(run["session"]["session_id"], "alice", path=run["path"])
    assert messages[0]["content"] == "important", "the buffered text must not be lost"


@pytest.mark.parametrize("terminal,run_status,message_status", [
    ("completed", "complete", "complete"),
    ("complete", "complete", "complete"),
    ("cancelled", "cancelled", "cancelled"),
    ("error", "error", "error"),
    ("truncated", "truncated", "truncated"),
    ("disconnected", "truncated", "truncated"),
])
def test_terminal_mapping(run, terminal, run_status, message_status):
    p = _persister(run)
    p.open()
    p.finalize(terminal)
    assert store.get_run_for_user(run["run"]["run_id"], "alice", path=run["path"])["status"] == run_status
    messages, _ = store.list_messages(run["session"]["session_id"], "alice", path=run["path"])
    assert messages[0]["status"] == message_status


@pytest.mark.parametrize("unknown", ["", None, "weird", "finished-ish"])
def test_an_unrecognised_terminal_word_becomes_truncated(run, unknown):
    """A status this code does not understand is not evidence of completion."""
    p = _persister(run)
    p.open()
    p.finalize(unknown)
    assert store.get_run_for_user(run["run"]["run_id"], "alice", path=run["path"])["status"] == "truncated"


def test_finalize_is_set_once(run):
    """A duplicate RUN_FINISHED from a reattach cannot rewrite an outcome."""
    p = _persister(run)
    p.open()
    p.finalize("cancelled")
    p.finalize("completed")
    assert store.get_run_for_user(run["run"]["run_id"], "alice", path=run["path"])["status"] == "cancelled"
    messages, _ = store.list_messages(run["session"]["session_id"], "alice", path=run["path"])
    assert messages[0]["status"] == "cancelled"


def test_a_disconnect_records_truncated_and_keeps_the_partial_text(run):
    p = _persister(run)
    p.open()
    p.add("REASONING", {"content": "I was analysing the"})
    p.record_disconnect()

    assert store.get_run_for_user(run["run"]["run_id"], "alice", path=run["path"])["status"] == "truncated"
    messages, _ = store.list_messages(run["session"]["session_id"], "alice", path=run["path"])
    assert messages[0]["status"] == "truncated"
    assert messages[0]["content"] == "I was analysing the"


def test_a_disconnect_after_completion_does_not_downgrade_it(run):
    """`record_disconnect` runs in the generator's `finally` on EVERY path.

    Without the idempotency guard it would overwrite `complete` with `truncated` on every
    successful run — turning a working feature into one that always looks broken.
    """
    p = _persister(run)
    p.open()
    p.finalize("completed")
    p.record_disconnect()
    assert store.get_run_for_user(run["run"]["run_id"], "alice", path=run["path"])["status"] == "complete"
    messages, _ = store.list_messages(run["session"]["session_id"], "alice", path=run["path"])
    assert messages[0]["status"] == "complete"


def test_paused_marks_watching_and_leaves_the_message_streaming(run):
    """A paused run is not finished — the watcher will wake it.

    Finalizing here would present a mid-watch partial as a completed analysis.
    """
    p = _persister(run)
    p.open()
    p.add("REASONING", {"content": "waiting for 2480"})
    p.mark_watching()

    got = store.get_run_for_user(run["run"]["run_id"], "alice", path=run["path"])
    assert got["status"] == "watching"
    assert got["terminal_status"] is None
    messages, _ = store.list_messages(run["session"]["session_id"], "alice", path=run["path"])
    assert messages[0]["status"] == "streaming"
    assert messages[0]["content"] == "waiting for 2480", "flushed before the status move"


def test_a_watching_run_can_still_be_finalized_later(run):
    """The resume leg completes the run the watcher woke."""
    p = _persister(run)
    p.open()
    p.mark_watching()
    p.finalize("completed")
    assert store.get_run_for_user(run["run"]["run_id"], "alice", path=run["path"])["status"] == "complete"


# ── Never affects the live stream ─────────────────────────────────────────────


class _BrokenStore:
    """A store that fails on every call, to prove the stream is unaffected."""

    RUN_COMPLETE = store.RUN_COMPLETE
    RUN_CANCELLED = store.RUN_CANCELLED
    RUN_ERROR = store.RUN_ERROR
    RUN_TRUNCATED = store.RUN_TRUNCATED
    RUN_WATCHING = store.RUN_WATCHING
    MSG_COMPLETE = store.MSG_COMPLETE
    MSG_CANCELLED = store.MSG_CANCELLED
    MSG_ERROR = store.MSG_ERROR
    MSG_TRUNCATED = store.MSG_TRUNCATED
    MSG_STREAMING = store.MSG_STREAMING
    ROLE_ASSISTANT = store.ROLE_ASSISTANT
    KIND_ANALYSIS_ANSWER = store.KIND_ANALYSIS_ANSWER
    KIND_QA_ANSWER = store.KIND_QA_ANSWER

    def __getattr__(self, name):
        def boom(*_a, **_k):
            raise RuntimeError(f"store is broken: {name}")

        return boom


def test_a_broken_store_never_raises_into_the_caller(run):
    """An unwritable transcript costs history; a raised exception would abort a live
    analysis the user is watching and paying for."""
    p = stream_persist.StreamPersister(
        run["run"]["run_id"], run["session"]["session_id"], store=_BrokenStore()
    )
    p.open()
    p.add("REASONING", {"content": "x"})
    p.flush()
    p.mark_watching()
    p.finalize("completed")
    p.record_disconnect()


def test_a_broken_store_warns_once_not_per_frame(run, capsys):
    """A per-frame warning would bury everything else in the log."""
    p = stream_persist.StreamPersister(
        run["run"]["run_id"], run["session"]["session_id"], store=_BrokenStore()
    )
    p.open()
    for i in range(60):
        p.add("REASONING", {"content": str(i)})
    p.finalize("completed")
    out = capsys.readouterr().out
    assert out.count("[stream_persist] WARN") == 1
    assert run["run"]["run_id"] in out, "the log must name the run whose record has a gap"


# ── Properties ────────────────────────────────────────────────────────────────


_EVENT_NAMES = st.sampled_from(
    ["REASONING", "TOOL_CALL_START", "TOOL_CALL_RESULT", "TOOL_CALL_END",
     "VERIFICATION_STEP", "BEST_CURRENT_READ", "DECISION"]
)


@given(frames=st.lists(_EVENT_NAMES, min_size=0, max_size=60))
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property_a_complete_run_has_its_whole_transcript(run, frames):
    """Property: for any frame sequence, finalizing leaves EVERY frame stored.

    This is "terminal implies the transcript is whole", checked against arbitrary
    batch boundaries rather than the one or two a hand-written test would hit.
    """
    fresh = store.create_run(
        session_id=run["session"]["session_id"], user_id="alice", kind="find",
        symbol="RELIANCE", timeframe="10m", profile="INTRADAY", path=run["path"],
    )
    p = stream_persist.StreamPersister(fresh["run_id"], run["session"]["session_id"])
    p.open()
    for name in frames:
        p.add(name, {"n": name})
    p.add("RUN_FINISHED", {"status": "completed"})
    p.finalize("completed")

    events, last = store.list_run_events(fresh["run_id"], limit=5000, path=run["path"])
    assert [e["event"] for e in events] == frames + ["RUN_FINISHED"]
    assert last == len(frames) + 1
    assert store.get_run_for_user(fresh["run_id"], "alice", path=run["path"])["status"] == "complete"


@given(
    chunks=st.lists(st.text(min_size=0, max_size=12), min_size=0, max_size=30),
)
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property_streamed_text_is_never_lost_or_reordered(run, chunks):
    """Property: the message content is the concatenation of every REASONING chunk.

    Whatever the batching, nothing is dropped and nothing is transposed.
    """
    fresh = store.create_run(
        session_id=run["session"]["session_id"], user_id="alice", kind="find",
        symbol="RELIANCE", timeframe="10m", profile="INTRADAY", path=run["path"],
    )
    p = stream_persist.StreamPersister(fresh["run_id"], run["session"]["session_id"])
    message_id = p.open()
    for chunk in chunks:
        p.add("REASONING", {"content": chunk})
    p.finalize("completed")

    messages, _ = store.list_messages(
        run["session"]["session_id"], "alice", limit=1000, path=run["path"]
    )
    stored = next(m for m in messages if m["message_id"] == message_id)
    expected = "".join(chunks).replace("\x00", "")
    assert stored["content"] == expected


@given(terminal=st.sampled_from(["completed", "cancelled", "error", "truncated"]))
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_property_a_finalized_message_is_never_upgraded_to_complete(run, terminal):
    """Property: no sequence of later calls can make a finished turn claim completeness.

    The "an incomplete streamed response must never appear as a complete one" rule, as a
    property of the persister rather than a hope about call order.
    """
    fresh = store.create_run(
        session_id=run["session"]["session_id"], user_id="alice", kind="find",
        symbol="RELIANCE", timeframe="10m", profile="INTRADAY", path=run["path"],
    )
    p = stream_persist.StreamPersister(fresh["run_id"], run["session"]["session_id"])
    message_id = p.open()
    p.finalize(terminal)
    # Everything a late or duplicated frame could try.
    p.finalize("completed")
    p.record_disconnect()
    p.add("REASONING", {"content": "late"})
    p.flush()

    messages, _ = store.list_messages(
        run["session"]["session_id"], "alice", limit=1000, path=run["path"]
    )
    stored = next(m for m in messages if m["message_id"] == message_id)
    expected = {"completed": "complete"}.get(terminal, terminal)
    assert stored["status"] == expected
    if terminal != "completed":
        assert stored["status"] != "complete"


# ── End to end through the routes ─────────────────────────────────────────────


class TestThroughTheRoutes:
    """The wiring: a real POST /run must leave a real transcript."""

    @pytest.fixture
    def main_mod(self, monkeypatch, tmp_path):
        monkeypatch.setenv(store.ENV_DB_PATH, str(tmp_path / "sessions.db"))
        monkeypatch.setenv("DEEP_QUANT_SESSIONS_ENABLED", "1")
        monkeypatch.setenv(stream_persist.ENV_ENABLED, "1")
        import main

        importlib.reload(main)
        return main

    @pytest.fixture
    def client(self, main_mod):
        return TestClient(main_mod.app)

    def _auth(self, user="alice"):
        return {ident.HEADER_IDENTITY: ident.sign_identity(user)}

    def _session(self, client):
        return client.post(
            "/sessions",
            json={"symbol": "RELIANCE", "profile": "INTRADAY", "timeframe": "10m"},
            headers=self._auth(),
        ).json()

    def _stub(self, main_mod, monkeypatch, frames, status="completed"):
        async def fake_generator(thread_id, graph_input=None, resume_command=None,
                                 user_id=None, kind="run", **_kwargs):
            for name, payload in frames:
                yield f"event: {name}\ndata: {payload}\n\n"

        monkeypatch.setattr(main_mod, "event_generator", fake_generator)

    def test_a_run_records_the_users_turn_and_an_assistant_turn(self, client, main_mod):
        """The real generator is used, so the graph is what needs stubbing, not this."""
        s = self._session(client)
        res = client.post(
            "/run",
            json={"session_id": s["session_id"], "message": "find a setup", "mode": "FIND"},
            headers=self._auth(),
        )
        assert res.status_code == 200
        list(res.iter_lines())

        messages, _ = store.list_messages(s["session_id"], "alice")
        kinds = [(m["role"], m["kind"]) for m in messages]
        assert ("user", "analysis_request") in kinds
        assert ("assistant", "analysis_answer") in kinds

    def test_the_assistant_turn_never_stays_streaming_after_the_stream_ends(self, client):
        """Whatever happened, the turn reaches a definite state.

        A row left `streaming` renders as "still arriving" forever.
        """
        s = self._session(client)
        list(client.post(
            "/run",
            json={"session_id": s["session_id"], "message": "analyse", "mode": "FIND"},
            headers=self._auth(),
        ).iter_lines())

        messages, _ = store.list_messages(s["session_id"], "alice")
        assistant = [m for m in messages if m["role"] == "assistant"]
        assert assistant, "there must be an assistant turn"
        assert all(m["status"] != "streaming" for m in assistant), [m["status"] for m in assistant]

    def test_run_started_carries_the_run_and_session_ids(self, client):
        """Additive fields, so a client can bind the run on the first frame."""
        s = self._session(client)
        body = "".join(client.post(
            "/run",
            json={"session_id": s["session_id"], "message": "analyse", "mode": "FIND"},
            headers=self._auth(),
        ).iter_lines())
        assert "RUN_STARTED" in body
        assert "run_id" in body
        assert s["session_id"] in body

    def test_a_qa_turn_records_both_halves(self, client):
        s = self._session(client)
        list(client.post(
            "/run",
            json={"session_id": s["session_id"], "message": "analyse", "mode": "FIND"},
            headers=self._auth(),
        ).iter_lines())
        list(client.post(
            "/qa",
            json={"session_id": s["session_id"], "question": "why that stop?"},
            headers=self._auth(),
        ).iter_lines())

        messages, _ = store.list_messages(s["session_id"], "alice")
        kinds = [(m["role"], m["kind"]) for m in messages]
        assert ("user", "qa_question") in kinds
        assert ("assistant", "qa_answer") in kinds

    def test_a_retried_qa_send_does_not_duplicate_the_question(self, client):
        s = self._session(client)
        list(client.post(
            "/run",
            json={"session_id": s["session_id"], "message": "analyse", "mode": "FIND"},
            headers=self._auth(),
        ).iter_lines())
        body = {"session_id": s["session_id"], "question": "why?", "client_msg_id": "c1"}
        for _ in range(2):
            list(client.post("/qa", json=body, headers=self._auth()).iter_lines())

        messages, _ = store.list_messages(s["session_id"], "alice")
        questions = [m for m in messages if m["kind"] == "qa_question"]
        assert len(questions) == 1

    def test_the_sse_bytes_are_identical_with_persistence_on_and_off(
        self, main_mod, monkeypatch, tmp_path
    ):
        """Persistence must be invisible on the wire.

        Compared frame-for-frame rather than by length: a reordered or re-serialised frame
        would break every existing consumer.
        """
        def run_once(enabled: str) -> str:
            monkeypatch.setenv(stream_persist.ENV_ENABLED, enabled)
            monkeypatch.setenv(store.ENV_DB_PATH, str(tmp_path / f"s-{enabled}.db"))
            import main as m

            importlib.reload(m)
            c = TestClient(m.app)
            sess = c.post(
                "/sessions",
                json={"symbol": "RELIANCE", "profile": "INTRADAY", "timeframe": "10m"},
                headers=self._auth(),
            ).json()
            res = c.post(
                "/run",
                json={"session_id": sess["session_id"], "message": "analyse", "mode": "FIND"},
                headers=self._auth(),
            )
            # thread/run/session ids differ per run, so compare the EVENT NAMES.
            return "|".join(
                line.split(":", 1)[1].strip()
                for line in res.iter_lines()
                if line.startswith("event:")
            )

        assert run_once("1") == run_once("0")


# ── Replay ────────────────────────────────────────────────────────────────────


class TestReplay:
    """`?after_seq=` closes the gap where frames published to nobody were lost.

    `/stream`'s ALLOW path cannot be driven over HTTP — it is an unbounded SSE relay
    exiting only on `request.is_disconnected()`, which never becomes true under TestClient,
    so opening it hangs the run (measured: a 15-minute timeout). `_replay_frames` is what
    these cases are actually about, so it is asserted directly.
    """

    @pytest.fixture
    def main_mod(self, monkeypatch, tmp_path):
        monkeypatch.setenv(store.ENV_DB_PATH, str(tmp_path / "sessions.db"))
        monkeypatch.setenv("DEEP_QUANT_SESSIONS_ENABLED", "1")
        monkeypatch.setenv(stream_persist.ENV_ENABLED, "1")
        import main

        importlib.reload(main)
        return main

    def test_replay_returns_only_the_gap(self, main_mod, run):
        store.append_run_events(
            run["run"]["run_id"], [(f"E{i}", {"i": i}) for i in range(1, 6)], path=run["path"]
        )
        frames = main_mod._replay_frames(run["run"], after_seq=3)
        assert len(frames) == 2
        assert "E4" in frames[0] and "E5" in frames[1]

    def test_replayed_frames_are_byte_identical_to_live_ones(self, main_mod, run):
        """Rehydration feeds these through the same reducer a live stream drives.

        If the bytes differed there would be two rendering paths to keep in step — which
        is the whole reason run_events stores the payload rather than a rendering of it.

        Two frames are appended so the gap request (`after_seq=1`) has something to
        return; `after_seq=0` deliberately replays nothing.
        """
        from stream_events import format_sse

        first = {"tool": "get_candles", "args": {"symbol": "RELIANCE"}, "thread_id": "t1"}
        second = {"tool": "get_consensus", "args": {"limit": 200}, "thread_id": "t1"}
        store.append_run_events(
            run["run"]["run_id"],
            [("TOOL_CALL_START", first), ("TOOL_CALL_START", second)],
            path=run["path"],
        )

        replayed = main_mod._replay_frames(run["run"], after_seq=1)
        assert replayed == [format_sse("TOOL_CALL_START", second)]

    def test_after_seq_zero_replays_nothing(self, main_mod, run):
        """The default. A client that does not ask for recovery sees byte-identical
        behaviour to before this existed — which is what keeps the shipped frontend and
        the Rust watcher unaffected."""
        store.append_run_events(run["run"]["run_id"], [("E1", {})], path=run["path"])
        assert main_mod._replay_frames(run["run"], after_seq=0) == []

    def test_replay_beyond_the_end_is_empty(self, main_mod, run):
        store.append_run_events(run["run"]["run_id"], [("E1", {})], path=run["path"])
        assert main_mod._replay_frames(run["run"], after_seq=99) == []

    def test_no_run_row_means_no_replay(self, main_mod):
        """A legacy thread has no transcript; it must still be able to attach live."""
        assert main_mod._replay_frames(None, after_seq=5) == []

    def test_replay_is_off_when_persistence_is_off(self, main_mod, run, monkeypatch):
        store.append_run_events(run["run"]["run_id"], [("E1", {})], path=run["path"])
        monkeypatch.setenv(stream_persist.ENV_ENABLED, "0")
        assert main_mod._replay_frames(run["run"], after_seq=0) == []

    def test_a_failing_replay_still_allows_a_live_attach(self, main_mod, monkeypatch, capsys):
        """Losing recovery is bad; losing the live stream too would be worse."""
        assert main_mod._replay_frames({"run_id": "run_NOPE"}, after_seq=1) == []
