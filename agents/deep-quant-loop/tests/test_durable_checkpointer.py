"""Durable_Checkpointer tests — thread state must survive a restart.

The claim being tested is narrow and specific: **a run parked at an ``interrupt()``
is still resumable after the process that started it is gone.** That is the exact
thing ``MemorySaver`` could not do, and the reason ``/resume`` returned 400 and
``/qa`` answered ungrounded after every redeploy.

A restart is simulated the honest way — a second event loop and a second saver over
the same file — because ``AsyncSqliteSaver`` binds to the loop it is constructed in,
so a new loop genuinely is a new saver. A test that reused one saver would prove
nothing about durability.

A minimal ``StateGraph`` stands in for the real workflow deliberately: this is a test
of the persistence backend, and driving `graph.py` would need an LLM and turn a
sub-second test into an integration run.
"""

from __future__ import annotations

import asyncio
import os
from operator import add
from typing import Annotated, TypedDict

import pytest

import checkpointer

# The saver package is a hard requirement (requirements.txt), but the service degrades
# without it, so the suite should too rather than erroring at collection.
aio = pytest.importorskip(
    "langgraph.checkpoint.sqlite.aio", reason="langgraph-checkpoint-sqlite is not installed"
)

from langgraph.graph import END, StateGraph  # noqa: E402
from langgraph.types import Command, interrupt  # noqa: E402


class _S(TypedDict):
    steps: Annotated[list, add]


def _first(_state):
    return {"steps": ["first"]}


def _waiter(_state):
    payload = interrupt({"ask": "price"})
    return {"steps": [f"resumed:{payload}"]}


def _workflow():
    wf = StateGraph(_S)
    wf.add_node("first", _first)
    wf.add_node("waiter", _waiter)
    wf.set_entry_point("first")
    wf.add_edge("first", "waiter")
    wf.add_edge("waiter", END)
    return wf


# ── Configuration ─────────────────────────────────────────────────────────────


def test_path_is_read_per_call_not_captured_at_import(monkeypatch):
    monkeypatch.delenv(checkpointer.ENV_CHECKPOINT_DB, raising=False)
    assert checkpointer.checkpoint_db_path() is None
    monkeypatch.setenv(checkpointer.ENV_CHECKPOINT_DB, "/data/checkpoints.db")
    assert checkpointer.checkpoint_db_path() == "/data/checkpoints.db"


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_path_means_in_memory(monkeypatch, value):
    monkeypatch.setenv(checkpointer.ENV_CHECKPOINT_DB, value)
    assert checkpointer.checkpoint_db_path() is None


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("", False), ("nope", False),
])
def test_strict_msgpack_switch(monkeypatch, value, expected):
    monkeypatch.setenv(checkpointer.ENV_STRICT_MSGPACK, value)
    assert checkpointer.strict_msgpack_enabled() is expected


def test_hardening_advisory_states_the_risk_when_off(monkeypatch):
    """The threat model changed in this phase; the log has to say so.

    With MemorySaver the checkpoint was process memory. It is a file now.
    """
    monkeypatch.delenv(checkpointer.ENV_STRICT_MSGPACK, raising=False)
    text = checkpointer.describe_hardening()
    assert "OFF" in text
    assert "LANGGRAPH_STRICT_MSGPACK" in text


def test_hardening_advisory_confirms_when_on(monkeypatch):
    monkeypatch.setenv(checkpointer.ENV_STRICT_MSGPACK, "true")
    assert "ON" in checkpointer.describe_hardening()


def test_all_log_output_is_ascii(monkeypatch):
    """Read through `docker compose logs`; non-ASCII arrives mangled."""
    for value in ("", "true"):
        monkeypatch.setenv(checkpointer.ENV_STRICT_MSGPACK, value)
        line = checkpointer.describe_hardening()
        assert line.isascii(), repr(line)


# ── Degradation, not refusal ──────────────────────────────────────────────────


def test_unconfigured_yields_none_with_a_reason(monkeypatch):
    monkeypatch.delenv(checkpointer.ENV_CHECKPOINT_DB, raising=False)

    async def go():
        durable = checkpointer.DurableCheckpointer()
        async with durable as saver:
            assert saver is None
            assert durable.reason and "no LANGGRAPH_CHECKPOINT_DB" in durable.reason

    asyncio.run(go())


def test_unopenable_path_degrades_rather_than_raising(tmp_path):
    """A bad path must not take the service down.

    Refusing to start would turn "Q&A grounding is lost on restart" — a bounded
    degradation this service shipped with — into a total outage.
    """
    # A file standing where a directory needs to be: the parent cannot be created.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    target = str(blocker / "nested" / "ckpt.db")

    async def go():
        durable = checkpointer.DurableCheckpointer(target)
        async with durable as saver:
            assert saver is None
            assert durable.reason, "a degradation must always say why"

    asyncio.run(go())


def test_missing_package_degrades(monkeypatch, tmp_path):
    """A deployment without the saver installed must still import and serve."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "langgraph.checkpoint.sqlite.aio":
            raise ImportError("simulated: package absent")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    async def go():
        durable = checkpointer.DurableCheckpointer(str(tmp_path / "ckpt.db"))
        async with durable as saver:
            assert saver is None
            assert "not installed" in (durable.reason or "")

    asyncio.run(go())


def test_exit_is_safe_when_never_opened(tmp_path):
    """__aexit__ must tolerate a checkpointer that never opened."""

    async def go():
        durable = checkpointer.DurableCheckpointer(None)
        await durable.__aenter__()
        await durable.__aexit__(None, None, None)

    asyncio.run(go())


def test_parent_directory_is_created(tmp_path):
    """A path under a fresh volume mount must not fail with 'unable to open'."""
    target = tmp_path / "deep" / "nested" / "ckpt.db"

    async def go():
        async with checkpointer.DurableCheckpointer(str(target)) as saver:
            assert saver is not None

    asyncio.run(go())
    assert target.parent.is_dir()


# ── The durability claim ──────────────────────────────────────────────────────


def test_state_survives_a_restart(tmp_path):
    """Two event loops, two savers, one file: the values persist."""
    path = str(tmp_path / "ckpt.db")
    cfg = {"configurable": {"thread_id": "t-durable"}}

    async def first_process():
        async with checkpointer.DurableCheckpointer(path) as saver:
            graph = _workflow().compile(checkpointer=saver)
            async for _ in graph.astream({"steps": []}, cfg, stream_mode="updates"):
                pass

    async def second_process():
        async with checkpointer.DurableCheckpointer(path) as saver:
            graph = _workflow().compile(checkpointer=saver)
            return await graph.aget_state(cfg)

    asyncio.run(first_process())
    assert os.path.exists(path), "the checkpoint file must actually be on disk"

    state = asyncio.run(second_process())
    assert state.values["steps"] == ["first"]


def test_a_paused_run_is_still_resumable_after_a_restart(tmp_path):
    """THE test for this phase.

    Before: `graph.get_state(config).next` was empty after a restart, so `/resume`
    raised HTTP 400 and a watcher-triggered wake could never land — the terminal sat
    in WATCHING forever. This asserts the whole path: park at the interrupt, drop the
    process, observe `next` is still populated, resume to completion.
    """
    path = str(tmp_path / "ckpt.db")
    cfg = {"configurable": {"thread_id": "t-paused"}}

    async def start_and_park():
        async with checkpointer.DurableCheckpointer(path) as saver:
            graph = _workflow().compile(checkpointer=saver)
            async for _ in graph.astream({"steps": []}, cfg, stream_mode="updates"):
                pass
            return (await graph.aget_state(cfg)).next

    async def restart_and_resume():
        async with checkpointer.DurableCheckpointer(path) as saver:
            graph = _workflow().compile(checkpointer=saver)
            # This is the exact check `/resume` performs before accepting the handoff.
            pending = (await graph.aget_state(cfg)).next
            assert pending, "a paused run must remain resumable across a restart"
            async for _ in graph.astream(
                Command(resume={"candle": 42}), cfg, stream_mode="updates"
            ):
                pass
            return await graph.aget_state(cfg)

    assert asyncio.run(start_and_park()) == ("waiter",)

    final = asyncio.run(restart_and_resume())
    assert final.values["steps"] == ["first", "resumed:{'candle': 42}"]
    assert not final.next, "the run should be complete after resuming"


def test_memory_saver_does_NOT_survive_a_restart(tmp_path):
    """The control case, so the test above is measuring something.

    If this ever starts passing, the durability test has stopped proving anything.
    """
    from langgraph.checkpoint.memory import MemorySaver

    cfg = {"configurable": {"thread_id": "t-memory"}}

    async def run_once():
        graph = _workflow().compile(checkpointer=MemorySaver())
        async for _ in graph.astream({"steps": []}, cfg, stream_mode="updates"):
            pass

    async def fresh_saver_sees_nothing():
        graph = _workflow().compile(checkpointer=MemorySaver())
        return await graph.aget_state(cfg)

    asyncio.run(run_once())
    state = asyncio.run(fresh_saver_sees_nothing())
    assert not state.next
    assert not state.values


def test_separate_threads_do_not_share_state(tmp_path):
    """One file, many threads: a session store keyed on thread_id depends on this."""
    path = str(tmp_path / "ckpt.db")

    async def go():
        async with checkpointer.DurableCheckpointer(path) as saver:
            graph = _workflow().compile(checkpointer=saver)
            for tid in ("a", "b"):
                async for _ in graph.astream(
                    {"steps": []}, {"configurable": {"thread_id": tid}}, stream_mode="updates"
                ):
                    pass
            a = await graph.aget_state({"configurable": {"thread_id": "a"}})
            b = await graph.aget_state({"configurable": {"thread_id": "b"}})
            unknown = await graph.aget_state({"configurable": {"thread_id": "zzz"}})
            return a.values, b.values, unknown.values

    a, b, unknown = asyncio.run(go())
    assert a["steps"] == ["first"]
    assert b["steps"] == ["first"]
    assert not unknown


# ── graph.py's swap seam ──────────────────────────────────────────────────────


def test_graph_module_exposes_compile_with_and_a_default():
    """`main.py` rebinds `graph_module.graph`; both halves of that must exist."""
    import graph as graph_module

    assert callable(graph_module.compile_with)
    assert graph_module.graph is not None


def test_rebinding_graph_is_visible_through_the_module_attribute(tmp_path):
    """The bug the `import graph as graph_module` change prevents.

    With `from graph import graph`, `main` would hold the MemorySaver-backed graph
    forever: the service would report a durable checkpointer while still losing every
    thread on restart. Reading through the module attribute is what makes the lifespan's
    rebind take effect.
    """
    import graph as graph_module

    original = graph_module.graph
    try:
        sentinel = object()
        graph_module.graph = sentinel
        import importlib

        assert importlib.import_module("graph").graph is sentinel
    finally:
        graph_module.graph = original


# ── The lifespan wiring ───────────────────────────────────────────────────────
#
# The tests above prove the checkpointer works. These prove `main.py` actually
# installs it — a different claim, and the one that silently regresses. `TestClient`
# as a context manager is what runs a FastAPI lifespan.


def test_lifespan_installs_the_durable_checkpointer(tmp_path, monkeypatch, capsys):
    from fastapi.testclient import TestClient

    import graph as graph_module
    import main

    monkeypatch.setenv(checkpointer.ENV_CHECKPOINT_DB, str(tmp_path / "ckpt.db"))
    before = graph_module.graph
    try:
        with TestClient(main.app):
            assert graph_module.graph is not before, (
                "the lifespan must recompile the graph against the durable saver"
            )
            # The saver is on the compiled graph, so this is what `/qa` and `/resume`
            # will actually use.
            assert type(graph_module.graph.checkpointer).__name__ == "AsyncSqliteSaver"
        out = capsys.readouterr().out
        assert "durable LangGraph checkpoints" in out
    finally:
        graph_module.graph = before


def test_lifespan_leaves_memorysaver_in_place_when_unconfigured(monkeypatch, capsys):
    """Unconfigured must still serve — and must say what is lost."""
    from fastapi.testclient import TestClient

    import graph as graph_module
    import main

    monkeypatch.delenv(checkpointer.ENV_CHECKPOINT_DB, raising=False)
    before = graph_module.graph
    try:
        with TestClient(main.app):
            assert graph_module.graph is before
        out = capsys.readouterr().out
        assert "IN-MEMORY checkpoints" in out
        # A silent degradation was the original defect; the log has to name the cost.
        assert "/resume returns 400" in out
    finally:
        graph_module.graph = before


def test_lifespan_reports_the_open_failure_not_just_unconfigured(tmp_path, monkeypatch, capsys):
    """A database that failed to OPEN must not be reported as 'unconfigured'.

    This is why the lifespan holds the DurableCheckpointer instance instead of calling
    `DurableCheckpointer()` again inside the log line — a fresh instance has
    `reason is None` and would misreport an open failure as a missing config.
    """
    from fastapi.testclient import TestClient

    import graph as graph_module
    import main

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setenv(checkpointer.ENV_CHECKPOINT_DB, str(blocker / "nested" / "ckpt.db"))

    before = graph_module.graph
    try:
        with TestClient(main.app):
            pass
        out = capsys.readouterr().out
        assert "IN-MEMORY checkpoints" in out
        assert "unconfigured" not in out, "an open failure must report the real reason"
    finally:
        graph_module.graph = before


def test_run_still_streams_with_the_durable_checkpointer(tmp_path, monkeypatch):
    """Backward compatibility: the SSE surface is unchanged by the swap.

    `event_generator` is stubbed so this exercises the routing/lifespan path without an
    LLM — the point is that installing a durable checkpointer does not disturb the
    request path.
    """
    from fastapi.testclient import TestClient

    import graph as graph_module
    import main

    monkeypatch.setenv(checkpointer.ENV_CHECKPOINT_DB, str(tmp_path / "ckpt.db"))
    monkeypatch.delenv("DEEP_QUANT_REQUIRE_IDENTITY", raising=False)
    monkeypatch.delenv("SKU_ENFORCE", raising=False)

    async def fake_generator(thread_id, graph_input=None, resume_command=None, user_id=None, kind="run", **_kwargs):
        yield "event: RUN_STARTED\ndata: {}\n\n"
        yield "event: RUN_FINISHED\ndata: {\"status\": \"completed\"}\n\n"

    monkeypatch.setattr(main, "event_generator", fake_generator)

    before = graph_module.graph
    try:
        with TestClient(main.app) as client:
            res = client.post(
                "/run",
                json={"thread_id": "t1", "message": "analyse", "mode": "FIND", "user_id": "u"},
            )
            assert res.status_code == 200
            assert "RUN_STARTED" in res.text
            assert "RUN_FINISHED" in res.text
    finally:
        graph_module.graph = before


def test_resume_reads_the_graph_through_the_module_attribute(tmp_path, monkeypatch):
    """`/resume`'s pause check must see the REBOUND graph, not the import-time one.

    If `/resume` held a stale `from graph import graph` binding it would check the
    MemorySaver-backed graph — always empty after a restart — and 400 every
    watcher handoff while the durable checkpointer sat there unused. Asserted by
    rebinding to a stub that reports a pending step and checking `/resume` accepts it.
    """
    from fastapi.testclient import TestClient

    import graph as graph_module
    import main

    monkeypatch.delenv("DEEP_QUANT_REQUIRE_IDENTITY", raising=False)
    monkeypatch.delenv("SKU_ENFORCE", raising=False)

    class _State:
        next = ("waiter",)
        values = {"profile": "INTRADAY"}

    class _StubGraph:
        def get_state(self, _config):
            return _State()

    async def fake_generator(thread_id, graph_input=None, resume_command=None, user_id=None, kind="run", **_kwargs):
        yield "event: RUN_FINISHED\ndata: {\"status\": \"completed\"}\n\n"

    monkeypatch.setattr(main, "event_generator", fake_generator)

    before = graph_module.graph
    try:
        with TestClient(main.app) as client:
            graph_module.graph = _StubGraph()  # after startup, as the lifespan would
            res = client.post(
                "/resume",
                json={"thread_id": "t1", "triggered_candle": {"close": 1}, "trigger_kind": "target"},
            )
            # 400 here would mean /resume was looking at a different graph object.
            assert res.status_code == 200, res.text
    finally:
        graph_module.graph = before
