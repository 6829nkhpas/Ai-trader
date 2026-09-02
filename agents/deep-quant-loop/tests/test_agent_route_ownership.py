"""Ownership on the evolved agent routes — and the watcher contract, finally tested.

Two things live here.

**The security half.** ``GET /stream/{thread_id}`` used to return a thread's entire
research stream — reasoning, tool results, the committed decision — to anyone who
presented the id, with no identity check at all; and ``POST /cancel`` took no user id
whatsoever, so any caller could stop any run. Both are checked against the run row now,
and both answer **404** rather than 403 so the id is never confirmed.

**The regression half.** The paused -> ``/resume`` -> hub-reattach sequence is the
contract the headless Rust watcher depends on, and before this file **nothing tested it**.
It is the single most breakable thing in this migration: the watcher POSTs ``/resume`` and
*discards* the response, so the fan-out hub is the only path by which a heartbeat or target
trigger reaches a browser. If that breaks, a price watch silently never fires and the
terminal sits in WATCHING forever.
"""

from __future__ import annotations

import asyncio
import importlib
import json

import pytest
from fastapi.testclient import TestClient

import internal_identity as ident

SECRET = "o" * 64
SERVICE_SECRET = "p" * 64


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(ident.ENV_IDENTITY_SECRET, SECRET)
    monkeypatch.setenv(ident.ENV_SERVICE_SECRET, SERVICE_SECRET)
    monkeypatch.setenv("DEEP_QUANT_SESSIONS_ENABLED", "1")
    monkeypatch.delenv(ident.ENV_REQUIRE_IDENTITY, raising=False)
    monkeypatch.delenv("DEEP_QUANT_REQUIRE_SESSION", raising=False)
    monkeypatch.delenv("SKU_ENFORCE", raising=False)
    ident._warned_unenforced = False
    yield
    ident._warned_unenforced = False


@pytest.fixture
def main_mod(monkeypatch):
    monkeypatch.setenv("DEEP_QUANT_SESSIONS_ENABLED", "1")
    import main

    importlib.reload(main)
    return main


@pytest.fixture
def client(main_mod):
    return TestClient(main_mod.app)


@pytest.fixture
def stub_graph(main_mod, monkeypatch):
    """Replace event_generator with a canned two-frame stream.

    The routes under test are about ownership and routing, not analysis; a real run needs
    an LLM, market data and minutes of wall clock.
    """
    calls = []

    async def fake_generator(thread_id, graph_input=None, resume_command=None, user_id=None, kind="run", **_kwargs):
        calls.append({"thread_id": thread_id, "user_id": user_id, "kind": kind})
        yield f'event: RUN_STARTED\ndata: {{"thread_id": "{thread_id}"}}\n\n'
        yield f'event: RUN_FINISHED\ndata: {{"thread_id": "{thread_id}", "status": "completed"}}\n\n'

    monkeypatch.setattr(main_mod, "event_generator", fake_generator)
    return calls


def _auth(user: str) -> dict:
    return {ident.HEADER_IDENTITY: ident.sign_identity(user)}


def _service() -> dict:
    return {ident.HEADER_SERVICE: ident.sign_service("tool-server")}


def _session(client, user="alice", **over):
    body = {"symbol": "RELIANCE", "profile": "INTRADAY", "timeframe": "10m"}
    body.update(over)
    return client.post("/sessions", json=body, headers=_auth(user)).json()


def _drain(response) -> str:
    return "".join(line for line in response.iter_lines())


# ── /run creates the run row and mints the thread ─────────────────────────────


class TestRunWithSession:
    def test_the_server_mints_the_thread_id(self, client, stub_graph):
        """`thread_${symbol}_${Date.now()}` is retired.

        Guessable to the second, and `/stream` had no ownership check — so knowing the
        symbol and roughly the time was enough to read someone's research.
        """
        s = _session(client)
        res = client.post(
            "/run",
            json={"session_id": s["session_id"], "message": "analyse", "mode": "FIND"},
            headers=_auth("alice"),
        )
        assert res.status_code == 200
        _drain(res)
        assert stub_graph[0]["thread_id"].startswith("thread_")
        assert "RELIANCE" not in stub_graph[0]["thread_id"]

    def test_the_run_and_the_users_turn_are_recorded(self, client, stub_graph):
        import session_store as store

        s = _session(client)
        _drain(client.post(
            "/run",
            json={"session_id": s["session_id"], "message": "find a setup", "mode": "FIND"},
            headers=_auth("alice"),
        ))
        runs = store.list_runs(s["session_id"], "alice")
        assert len(runs) == 1
        assert runs[0]["kind"] == "find"
        messages, _ = store.list_messages(s["session_id"], "alice")
        assert [(m["role"], m["kind"], m["content"]) for m in messages] == [
            ("user", "analysis_request", "find a setup")
        ]

    def test_verify_is_recorded_as_a_verify_run_with_its_inputs(self, client, stub_graph):
        """So a reopened VERIFY session can show WHAT was verified, not just the verdict."""
        import session_store as store

        s = _session(client)
        manual = {"side": "BUY", "entry": 2470.0, "stop_loss": 2435.0, "take_profit": 2550.0}
        _drain(client.post(
            "/run",
            json={"session_id": s["session_id"], "message": "verify", "mode": "VERIFY",
                  "manual_trade": manual},
            headers=_auth("alice"),
        ))
        run = store.list_runs(s["session_id"], "alice")[0]
        assert run["kind"] == "verify"
        assert run["manual_trade"] == manual

    def test_the_run_snapshots_the_SESSIONS_context_not_the_bodys(self, client, stub_graph):
        """The fix for "Session A ran with Session B's timeframe".

        The session owns its trading context, so a request cannot cause one session's
        conversation to be recorded against another's timeframe.
        """
        import session_store as store

        s = _session(client, timeframe="10m")
        _drain(client.post(
            "/run",
            json={"session_id": s["session_id"], "message": "analyse", "mode": "FIND",
                  "timeframe": "5m", "symbol": "TCS", "profile": "SWING"},
            headers=_auth("alice"),
        ))
        run = store.list_runs(s["session_id"], "alice")[0]
        assert run["timeframe"] == "10m"
        assert run["symbol"] == "RELIANCE"
        assert run["profile"] == "INTRADAY"

    def test_two_finds_in_one_session_get_two_runs_and_two_threads(self, client, stub_graph):
        import session_store as store

        s = _session(client)
        for _ in range(2):
            _drain(client.post(
                "/run",
                json={"session_id": s["session_id"], "message": "analyse", "mode": "FIND"},
                headers=_auth("alice"),
            ))
        runs = store.list_runs(s["session_id"], "alice")
        assert len({r["run_id"] for r in runs}) == 2
        assert len({r["thread_id"] for r in runs}) == 2

    def test_client_msg_id_makes_a_retried_press_idempotent(self, client, stub_graph):
        import session_store as store

        s = _session(client)
        body = {"session_id": s["session_id"], "message": "analyse", "mode": "FIND",
                "client_msg_id": "press-1"}
        _drain(client.post("/run", json=body, headers=_auth("alice")))
        _drain(client.post("/run", json=body, headers=_auth("alice")))
        messages, _ = store.list_messages(s["session_id"], "alice")
        assert len(messages) == 1, "the user's turn must not be duplicated by a retry"

    def test_another_users_session_is_404(self, client, stub_graph):
        s = _session(client, user="alice")
        res = client.post(
            "/run",
            json={"session_id": s["session_id"], "message": "analyse", "mode": "FIND"},
            headers=_auth("bob"),
        )
        assert res.status_code == 404
        assert stub_graph == [], "no analysis may run against someone else's session"

    def test_an_unknown_session_is_404(self, client, stub_graph):
        res = client.post(
            "/run",
            json={"session_id": "sess_NOPE", "message": "analyse"},
            headers=_auth("alice"),
        )
        assert res.status_code == 404
        assert stub_graph == []


class TestRunBackwardCompatibility:
    def test_the_legacy_thread_id_path_still_works(self, client, stub_graph):
        """The shipped frontend must keep working across the deploy."""
        res = client.post(
            "/run",
            json={"thread_id": "thread_RELIANCE_123", "message": "analyse", "user_id": "alice"},
        )
        assert res.status_code == 200
        _drain(res)
        assert stub_graph[0]["thread_id"] == "thread_RELIANCE_123"

    def test_a_body_with_neither_identifier_is_422(self, client, stub_graph):
        res = client.post("/run", json={"message": "analyse"})
        assert res.status_code == 422
        assert stub_graph == []

    def test_require_session_closes_the_legacy_path(self, client, stub_graph, monkeypatch):
        monkeypatch.setenv("DEEP_QUANT_REQUIRE_SESSION", "1")
        res = client.post("/run", json={"thread_id": "thread_legacy", "message": "analyse"})
        assert res.status_code == 422
        assert "session_id is required" in res.json()["detail"]
        assert stub_graph == []

    def test_asking_for_a_session_on_a_deployment_without_the_store_is_503(
        self, monkeypatch, stub_graph
    ):
        """A deployment mismatch, not a client error.

        Silently dropping the association and persisting nothing would be worse: the client
        would believe its conversation was being recorded.
        """
        monkeypatch.setenv("DEEP_QUANT_SESSIONS_ENABLED", "0")
        import main

        importlib.reload(main)
        off = TestClient(main.app)
        res = off.post(
            "/run", json={"session_id": "sess_x", "message": "analyse"}, headers=_auth("alice")
        )
        assert res.status_code == 503


# ── /qa grounding ─────────────────────────────────────────────────────────────


class TestQaGrounding:
    def test_qa_grounds_in_the_sessions_active_run(self, client, stub_graph):
        s = _session(client)
        _drain(client.post(
            "/run", json={"session_id": s["session_id"], "message": "analyse"}, headers=_auth("alice")
        ))
        analysis_thread = stub_graph[0]["thread_id"]

        _drain(client.post(
            "/qa",
            json={"session_id": s["session_id"], "question": "why that stop?"},
            headers=_auth("alice"),
        ))
        assert stub_graph[1]["kind"] == "qa"
        assert stub_graph[1]["thread_id"] == analysis_thread, (
            "a Q&A turn must be answered on the thread whose analysis it is about"
        )

    def test_context_run_id_selects_an_EARLIER_run(self, client, stub_graph):
        """What makes multiple runs per session usable.

        Without it, "why that stop?" after a second FIND could only ever mean the second
        one — and the old client had no way to say otherwise, because it read its thread id
        from a single flat "current" field.
        """
        import session_store as store

        s = _session(client)
        for _ in range(2):
            _drain(client.post(
                "/run", json={"session_id": s["session_id"], "message": "analyse"},
                headers=_auth("alice"),
            ))
        first, second = store.list_runs(s["session_id"], "alice")
        assert first["thread_id"] != second["thread_id"]

        _drain(client.post(
            "/qa",
            json={"session_id": s["session_id"], "question": "about the first one",
                  "context_run_id": first["run_id"]},
            headers=_auth("alice"),
        ))
        assert stub_graph[-1]["thread_id"] == first["thread_id"]

    def test_qa_before_any_run_is_409(self, client, stub_graph):
        s = _session(client)
        res = client.post(
            "/qa", json={"session_id": s["session_id"], "question": "why?"}, headers=_auth("alice")
        )
        assert res.status_code == 409
        assert "no analysis to ask about" in res.json()["detail"]
        assert stub_graph == []

    def test_another_users_session_cannot_be_questioned(self, client, stub_graph):
        s = _session(client, user="alice")
        _drain(client.post(
            "/run", json={"session_id": s["session_id"], "message": "analyse"}, headers=_auth("alice")
        ))
        res = client.post(
            "/qa", json={"session_id": s["session_id"], "question": "what did she find?"},
            headers=_auth("bob"),
        )
        assert res.status_code == 404
        assert len(stub_graph) == 1, "no Q&A may run against someone else's session"

    def test_a_cross_session_context_run_is_422(self, client, stub_graph):
        """Grounding in the wrong analysis is a correctness bug even within one user."""
        import session_store as store

        a = _session(client, timeframe="10m")
        b = _session(client, timeframe="5m")
        for sess in (a, b):
            _drain(client.post(
                "/run", json={"session_id": sess["session_id"], "message": "analyse"},
                headers=_auth("alice"),
            ))
        b_run = store.list_runs(b["session_id"], "alice")[0]
        res = client.post(
            "/qa",
            json={"session_id": a["session_id"], "question": "why?", "context_run_id": b_run["run_id"]},
            headers=_auth("alice"),
        )
        assert res.status_code == 422

    def test_a_cross_user_context_run_is_422(self, client, stub_graph):
        import session_store as store

        alice = _session(client, user="alice")
        _drain(client.post(
            "/run", json={"session_id": alice["session_id"], "message": "analyse"},
            headers=_auth("alice"),
        ))
        alice_run = store.list_runs(alice["session_id"], "alice")[0]
        bob = _session(client, user="bob")
        _drain(client.post(
            "/run", json={"session_id": bob["session_id"], "message": "analyse"}, headers=_auth("bob")
        ))
        res = client.post(
            "/qa",
            json={"session_id": bob["session_id"], "question": "why?",
                  "context_run_id": alice_run["run_id"]},
            headers=_auth("bob"),
        )
        assert res.status_code == 422

    def test_the_legacy_thread_id_path_still_works(self, client, stub_graph):
        res = client.post(
            "/qa", json={"thread_id": "thread_legacy", "question": "why?", "user_id": "alice"}
        )
        assert res.status_code == 200
        _drain(res)
        assert stub_graph[0]["thread_id"] == "thread_legacy"

    def test_qa_frames_reach_a_hub_subscriber(self, main_mod, monkeypatch):
        """`/qa` was NOT teed to the fan-out hub. `/run` and `/resume` were.

        So a client attached to GET /stream — which is every client whose run parked at a
        price watch — received no Q&A frames at all. On the multi-session frontend the hub
        is the routing path, so an un-teed answer would simply never arrive.
        """
        published = []
        monkeypatch.setattr(main_mod, "_publish_frame", lambda tid, frame: published.append((tid, frame)))

        async def fake_generator(thread_id, graph_input=None, resume_command=None, user_id=None, kind="run", **_kwargs):
            yield "event: REASONING\ndata: {\"content\": \"because ATR\"}\n\n"
            yield "event: RUN_FINISHED\ndata: {\"status\": \"completed\"}\n\n"

        monkeypatch.setattr(main_mod, "event_generator", fake_generator)
        client = TestClient(main_mod.app)
        _drain(client.post(
            "/qa", json={"thread_id": "thread_qa", "question": "why?", "user_id": "alice"}
        ))
        assert [t for t, _ in published] == ["thread_qa", "thread_qa"]
        assert any("REASONING" in frame for _, frame in published)


# ── /stream ownership ─────────────────────────────────────────────────────────


class TestStreamOwnership:
    def test_another_user_cannot_attach_to_a_thread(self, client, stub_graph):
        """The unauthenticated read of someone else's research, closed.

        Reasoning, tool results and the committed trade decision all flow over this
        channel.
        """
        import session_store as store

        s = _session(client, user="alice")
        _drain(client.post(
            "/run", json={"session_id": s["session_id"], "message": "analyse"}, headers=_auth("alice")
        ))
        thread = store.list_runs(s["session_id"], "alice")[0]["thread_id"]

        res = client.get(f"/stream/{thread}", headers=_auth("bob"))
        assert res.status_code == 404

    def test_an_anonymous_caller_cannot_attach_to_an_owned_thread(self, client, stub_graph):
        import session_store as store

        s = _session(client, user="alice")
        _drain(client.post(
            "/run", json={"session_id": s["session_id"], "message": "analyse"}, headers=_auth("alice")
        ))
        thread = store.list_runs(s["session_id"], "alice")[0]["thread_id"]
        assert client.get(f"/stream/{thread}").status_code == 404

    # The REFUSAL cases above go through HTTP, because the guard runs before the
    # subscription and the response returns immediately.
    #
    # The ALLOW cases cannot. `/stream` is an unbounded SSE relay — `while True` with a
    # 20s keepalive, exiting only on `request.is_disconnected()`, which never becomes true
    # under TestClient — so opening it hangs the test run (measured: a 15-minute timeout).
    # Asserting the guard directly is the honest test of what these cases are actually
    # about, which is the ownership decision, not the relay.

    def test_the_owner_passes_the_guard(self, client, stub_graph, main_mod):
        import session_store as store

        s = _session(client, user="alice")
        _drain(client.post(
            "/run", json={"session_id": s["session_id"], "message": "analyse"}, headers=_auth("alice")
        ))
        thread = store.list_runs(s["session_id"], "alice")[0]["thread_id"]
        run = main_mod._owned_run_for_thread(thread, "alice")
        assert run is not None and run["thread_id"] == thread

    def test_a_non_owner_is_refused_by_the_guard(self, client, stub_graph, main_mod):
        from fastapi import HTTPException

        import session_store as store

        s = _session(client, user="alice")
        _drain(client.post(
            "/run", json={"session_id": s["session_id"], "message": "analyse"}, headers=_auth("alice")
        ))
        thread = store.list_runs(s["session_id"], "alice")[0]["thread_id"]
        with pytest.raises(HTTPException) as exc:
            main_mod._owned_run_for_thread(thread, "bob")
        assert exc.value.status_code == 404

    def test_a_thread_with_no_run_row_passes_the_guard(self, client, main_mod):
        """Legacy threads have no recorded owner.

        Refusing them would break every in-flight price watch across a deploy. The gap
        closes when DEEP_QUANT_REQUIRE_SESSION is flipped and every thread has a row; it is
        narrower than the status quo, where even KNOWN threads were unprotected.
        """
        assert main_mod._owned_run_for_thread("thread_legacy_unknown", "alice") is None
        assert main_mod._owned_run_for_thread("thread_legacy_unknown", None) is None


# ── /cancel ownership ─────────────────────────────────────────────────────────


class TestCancelOwnership:
    def test_the_owner_can_cancel_by_run_id(self, client, stub_graph):
        import session_store as store

        s = _session(client)
        _drain(client.post(
            "/run", json={"session_id": s["session_id"], "message": "analyse"}, headers=_auth("alice")
        ))
        run = store.list_runs(s["session_id"], "alice")[0]
        res = client.post("/cancel", json={"run_id": run["run_id"]}, headers=_auth("alice"))
        assert res.status_code == 200
        assert res.json()["thread_id"] == run["thread_id"]

    def test_another_user_cannot_cancel_by_run_id(self, client, stub_graph):
        import session_store as store

        s = _session(client, user="alice")
        _drain(client.post(
            "/run", json={"session_id": s["session_id"], "message": "analyse"}, headers=_auth("alice")
        ))
        run = store.list_runs(s["session_id"], "alice")[0]
        res = client.post("/cancel", json={"run_id": run["run_id"]}, headers=_auth("bob"))
        assert res.status_code == 404

    def test_another_user_cannot_cancel_by_thread_id(self, client, stub_graph, main_mod):
        import session_store as store

        s = _session(client, user="alice")
        _drain(client.post(
            "/run", json={"session_id": s["session_id"], "message": "analyse"}, headers=_auth("alice")
        ))
        thread = store.list_runs(s["session_id"], "alice")[0]["thread_id"]
        res = client.post("/cancel", json={"thread_id": thread}, headers=_auth("bob"))
        assert res.status_code == 404
        assert thread not in main_mod._CANCELLED, "a refused cancel must not set the flag"

    def test_an_unknown_run_id_is_404(self, client):
        assert client.post(
            "/cancel", json={"run_id": "run_NOPE"}, headers=_auth("alice")
        ).status_code == 404


# ── The watcher contract — previously untested ────────────────────────────────


class TestWatcherResumeContract:
    """The paused -> /resume -> hub-reattach sequence the Rust watcher depends on.

    Nothing tested this before. It is the most breakable thing in the migration: the
    watcher POSTs `/resume` and DISCARDS the response body, so the fan-out hub is the only
    path by which a heartbeat or target trigger reaches a browser. Break it and a price
    watch silently never fires — the terminal sits in WATCHING forever, which is exactly
    the bug the hub was added to fix.
    """

    @pytest.fixture
    def paused(self, main_mod, monkeypatch):
        """A graph reporting a pending step, so /resume accepts the handoff."""

        class _State:
            next = ("watch_price_condition",)
            values = {"profile": "INTRADAY"}

        class _StubGraph:
            def get_state(self, _config):
                return _State()

        import graph as graph_module

        before = graph_module.graph
        graph_module.graph = _StubGraph()
        yield
        graph_module.graph = before

    def test_resume_is_accepted_with_the_service_credential(self, main_mod, paused, monkeypatch):
        async def fake_generator(thread_id, graph_input=None, resume_command=None, user_id=None, kind="run", **_kwargs):
            yield "event: RUN_FINISHED\ndata: {\"status\": \"completed\"}\n\n"

        monkeypatch.setattr(main_mod, "event_generator", fake_generator)
        client = TestClient(main_mod.app)
        res = client.post(
            "/resume",
            json={"thread_id": "thread_watch", "triggered_candle": {"close": 2480}, "trigger_kind": "target"},
            headers=_service(),
        )
        assert res.status_code == 200
        _drain(res)

    def test_resume_frames_reach_a_reattached_subscriber(self, main_mod, paused, monkeypatch):
        """THE contract.

        The watcher drains and discards the response, so this fan-out is the only delivery
        path. Asserted at `_publish_frame` because the alternative — a real concurrent SSE
        attach — makes the test about asyncio scheduling rather than about the tee.
        """
        published = []
        monkeypatch.setattr(main_mod, "_publish_frame", lambda tid, frame: published.append((tid, frame)))

        async def fake_generator(thread_id, graph_input=None, resume_command=None, user_id=None, kind="run", **_kwargs):
            yield 'event: RUN_STARTED\ndata: {"thread_id": "thread_watch"}\n\n'
            yield 'event: DECISION\ndata: {"action": "BUY"}\n\n'
            yield 'event: RUN_FINISHED\ndata: {"status": "completed"}\n\n'

        monkeypatch.setattr(main_mod, "event_generator", fake_generator)
        client = TestClient(main_mod.app)
        _drain(client.post(
            "/resume",
            json={"thread_id": "thread_watch", "triggered_candle": {"close": 2480}, "trigger_kind": "target"},
            headers=_service(),
        ))

        assert [t for t, _ in published] == ["thread_watch"] * 3
        assert any("DECISION" in frame for _, frame in published)

    def test_an_unpaused_thread_is_400_not_500(self, main_mod, monkeypatch):
        """4xx is how the watcher learns the run ended, and it stops retrying."""

        class _Finished:
            next = ()
            values = {}

        class _StubGraph:
            def get_state(self, _config):
                return _Finished()

        import graph as graph_module

        before = graph_module.graph
        graph_module.graph = _StubGraph()
        try:
            client = TestClient(main_mod.app)
            res = client.post(
                "/resume",
                json={"thread_id": "thread_done", "triggered_candle": {}, "trigger_kind": "target"},
                headers=_service(),
            )
            assert res.status_code == 400
        finally:
            graph_module.graph = before

    def test_the_owning_user_is_read_from_the_run_row(self, client, stub_graph, paused, main_mod):
        """Not taken from the body.

        The watcher forwards an id that travelled through a background process and back,
        and it is what the LLM key is resolved against. The run row is authoritative.
        """
        import session_store as store

        s = _session(client, user="alice")
        _drain(client.post(
            "/run", json={"session_id": s["session_id"], "message": "analyse"}, headers=_auth("alice")
        ))
        thread = store.list_runs(s["session_id"], "alice")[0]["thread_id"]
        stub_graph.clear()

        _drain(client.post(
            "/resume",
            json={"thread_id": thread, "triggered_candle": {"close": 1}, "trigger_kind": "target",
                  "user_id": "somebody-else"},
            headers=_service(),
        ))
        assert stub_graph[0]["user_id"] == "alice"
        assert stub_graph[0]["kind"] == "resume"

    def test_the_body_user_id_is_used_when_no_run_row_exists(self, main_mod, paused, monkeypatch):
        """Legacy threads must still resolve a key, or an in-flight watch dies on deploy."""
        seen = []

        async def fake_generator(thread_id, graph_input=None, resume_command=None, user_id=None, kind="run", **_kwargs):
            seen.append(user_id)
            yield "event: RUN_FINISHED\ndata: {\"status\": \"completed\"}\n\n"

        monkeypatch.setattr(main_mod, "event_generator", fake_generator)
        client = TestClient(main_mod.app)
        _drain(client.post(
            "/resume",
            json={"thread_id": "thread_legacy", "triggered_candle": {}, "trigger_kind": "target",
                  "user_id": "legacy-user"},
            headers=_service(),
        ))
        assert seen == ["legacy-user"]

    def test_a_user_assertion_cannot_drive_resume_when_enforced(self, main_mod, paused, monkeypatch):
        monkeypatch.setenv(ident.ENV_REQUIRE_IDENTITY, "1")
        client = TestClient(main_mod.app)
        res = client.post(
            "/resume",
            json={"thread_id": "thread_watch", "triggered_candle": {}, "trigger_kind": "target"},
            headers={ident.HEADER_SERVICE: ident.sign_identity("alice")},
        )
        assert res.status_code == 401

    def test_the_resume_request_shape_is_unchanged(self, main_mod, paused, monkeypatch):
        """The watcher sends `heartbeat_seq`, which ResumeRequest does not declare.

        Pydantic drops it silently, and that must keep being true — declaring it would be
        fine, rejecting it would break the watcher.
        """
        async def fake_generator(thread_id, graph_input=None, resume_command=None, user_id=None, kind="run", **_kwargs):
            yield "event: RUN_FINISHED\ndata: {\"status\": \"completed\"}\n\n"

        monkeypatch.setattr(main_mod, "event_generator", fake_generator)
        client = TestClient(main_mod.app)
        res = client.post(
            "/resume",
            json={"thread_id": "thread_watch", "triggered_candle": {"close": 1},
                  "trigger_kind": "heartbeat", "heartbeat_seq": 7, "user_id": "u"},
            headers=_service(),
        )
        assert res.status_code == 200
        _drain(res)
