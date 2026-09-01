"""Durable_Checkpointer — LangGraph thread state that survives a restart.

What was broken
---------------
``graph.py`` compiled the workflow against ``MemorySaver()``, so every thread lived in
the process heap. A container restart — a redeploy, an OOM kill, a crash — destroyed
all of it, with two user-visible consequences:

* ``POST /qa`` answers **ungrounded**. Q&A works by appending the question to the same
  ``thread_id`` and letting ``build_qa_context`` read the checkpointed decision and tool
  results. With no checkpoint there is no ``Session_Analysis_Context``, and the answer
  is a plausible-sounding paragraph about nothing in particular.
* ``POST /resume`` returns **400**. `graph.get_state(config).next` is empty, so a run
  parked at ``watch_price_condition`` can never be woken and the terminal sits in
  WATCHING forever.

Neither failure announces itself. That is the point of fixing it before anything is
built on top.

Why the lifespan, and why not simpler
-------------------------------------
Two things were measured rather than assumed, and both rule out the tidier designs:

1. ``AsyncSqliteSaver.__init__`` calls ``asyncio.get_running_loop()``, so it CANNOT be
   constructed at module import (``RuntimeError: no running event loop``). It binds to
   the loop it will serve, which means it has to be created inside the running server.
2. The synchronous ``SqliteSaver`` *can* be built at import time, and even exposes
   ``aget_tuple`` — but that method raises
   ``NotImplementedError("The SqliteSaver does not support async methods")``. This graph
   is driven exclusively through ``graph.astream``, so the sync saver is not a fallback,
   it is a runtime error on the first request.

Hence: build inside a FastAPI lifespan, recompile the workflow against it, and rebind
``graph_module.graph``. ``main.py`` reaches the graph by attribute for exactly this
reason.

Verified end to end before shipping (two separate event loops over one file, which is
what a restart looks like): a run parked at ``interrupt()`` reports ``next=('w',)``
after the reopen and resumes with ``Command(resume=...)`` to completion.

Failure posture
---------------
Deliberately NOT fail-closed. An unconfigured or unopenable checkpoint database leaves
the MemorySaver-backed graph in place and logs loudly. Refusing to start would convert
"Q&A grounding is lost on restart" — a real but bounded degradation the service has
lived with since it shipped — into a total outage. The loud log is what stops that
degradation from being silent, which was the actual defect.
"""

from __future__ import annotations

import os
from typing import Optional

ENV_CHECKPOINT_DB = "LANGGRAPH_CHECKPOINT_DB"
ENV_STRICT_MSGPACK = "LANGGRAPH_STRICT_MSGPACK"


def checkpoint_db_path() -> Optional[str]:
    """The configured checkpoint database, or ``None`` for in-memory.

    Read per call rather than captured at import so a test can point it somewhere
    temporary without reimporting, matching ``hashchain.db_path()``.
    """
    raw = (os.getenv(ENV_CHECKPOINT_DB) or "").strip()
    return raw or None


def strict_msgpack_enabled() -> bool:
    """Whether checkpoint deserialisation is restricted to an allowlist of types."""
    return (os.getenv(ENV_STRICT_MSGPACK) or "").strip().lower() in ("1", "true", "yes", "on")


def describe_hardening() -> str:
    """The advisory line about checkpoint deserialisation hardening.

    Worth a log line because the threat model genuinely changed in this phase. With
    ``MemorySaver`` the checkpoint was process memory; it is now a FILE, and
    ``langgraph-checkpoint`` will deserialise arbitrary types out of it unless
    ``LANGGRAPH_STRICT_MSGPACK`` is set.

    It is NOT enabled by default here, and that is a considered trade rather than an
    oversight. Strict mode BLOCKS any type not on its allowlist, so switching it on
    blind risks breaking Q&A grounding on real graph state — an availability failure,
    for a threat that requires an attacker who can already write inside the container.
    Non-strict mode logs exactly what it *would* block ("Set LANGGRAPH_STRICT_MSGPACK=
    true to block now, or add ... to allowed_msgpack_modules"), so the safe order is:
    run, read those warnings, then enable. See docs/DEPLOYMENT.md section 7.1.
    """
    if strict_msgpack_enabled():
        return (
            "[checkpointer] ok strict msgpack deserialisation is ON: only allowlisted "
            "types load from the checkpoint file."
        )
    return (
        "[checkpointer] note strict msgpack deserialisation is OFF. The checkpoint is a "
        "file now, not process memory, so untrusted content in it would be deserialised. "
        "Watch the logs for 'Blocked deserialization' / 'Set LANGGRAPH_STRICT_MSGPACK' "
        "advisories during real runs, then set LANGGRAPH_STRICT_MSGPACK=true."
    )


def _ensure_parent_dir(path: str) -> None:
    """Create the containing directory if it is missing.

    Delegates to ``state_paths.ensure_parent_dir`` — the same helper the session store
    uses — so there is one implementation rather than one per store. Kept as a thin
    wrapper so this module's tests can target it directly and so a missing
    ``state_paths`` degrades instead of breaking checkpoint setup.
    """
    try:
        import state_paths

        state_paths.ensure_parent_dir(path)
    except Exception:  # noqa: BLE001
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)


class DurableCheckpointer:
    """Async context manager yielding a durable checkpointer, or ``None``.

    ``None`` means "keep the module default" and is returned for every non-fatal
    condition: no path configured, the package missing, the file unopenable. Callers
    branch on it rather than catching, so the degradation path is explicit at the call
    site instead of buried in a handler.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path if path is not None else checkpoint_db_path()
        self._cm = None
        self.saver = None
        self.reason: Optional[str] = None

    async def __aenter__(self):
        if not self.path:
            self.reason = "no LANGGRAPH_CHECKPOINT_DB configured"
            return None

        try:
            # Imported here, not at module scope, so a deployment without the package
            # degrades to MemorySaver rather than failing to import `main` at all.
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        except Exception as exc:  # noqa: BLE001
            self.reason = (
                f"langgraph-checkpoint-sqlite is not installed ({exc}); "
                f"add it to requirements.txt"
            )
            return None

        try:
            _ensure_parent_dir(self.path)
            # `from_conn_string` is an async context manager. It is entered manually
            # here rather than with `async with`, because the saver has to stay open
            # for the whole application lifetime — a FastAPI lifespan is itself the
            # `async with`, and nesting a second one inside a generator would close the
            # connection at the wrong moment.
            #
            # WAL mode and the checkpoints/writes tables are created by the saver's own
            # `setup()`, so there is nothing to do here.
            self._cm = AsyncSqliteSaver.from_conn_string(self.path)
            self.saver = await self._cm.__aenter__()
            return self.saver
        except Exception as exc:  # noqa: BLE001
            self.reason = f"could not open {self.path} ({exc})"
            self._cm = None
            self.saver = None
            return None

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if self._cm is None:
            return False
        try:
            await self._cm.__aexit__(exc_type, exc, tb)
        except Exception as close_exc:  # noqa: BLE001
            # A failure closing the checkpoint DB must not mask an in-flight exception
            # or turn shutdown into a crash loop.
            print(f"[checkpointer] WARN: error closing the checkpoint database: {close_exc}")
        finally:
            self._cm = None
            self.saver = None
        return False
