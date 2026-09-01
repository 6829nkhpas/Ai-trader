"""Run the REAL deep-quant service with the graph replaced by canned frames.

Used by the Playwright job (`.github/workflows/ci.yml`, `e2e`). Everything except the LLM is
real: the FastAPI app, the session store, the SQLite persistence, the SSE assembler, the
identity verification, the ownership checks and the reconnect/replay path. Only
``graph_module.graph`` is swapped, because that is the one component that needs an API key,
live market data and minutes of wall clock.

WHAT THIS PROVES AND WHAT IT DOES NOT
------------------------------------
Proves: session creation, per-session run ownership, frame routing by ``thread_id``, the
``turn`` marker, write-through persistence, rehydration after a reload, and isolation between
two concurrently streaming sessions.

Does NOT prove: anything about model quality, prompt behaviour, market-data correctness, or the
real price-trigger watcher. Those need the full stack and stay manual.

Run:
    LLM_API_KEY=e2e-placeholder \\
    INTERNAL_IDENTITY_SECRET=<same as the frontend> \\
    DEEP_QUANT_SESSIONS_ENABLED=1 DEEP_QUANT_PERSIST_STREAM=1 \\
    python e2e_stub_server.py
"""

from __future__ import annotations

import asyncio
import os
import sys

# Set before importing `main`: it opens its databases at IMPORT time. A temp dir keeps a CI run
# from inheriting a stale database, and makes each run reproducible.
_STATE_DIR = os.environ.setdefault("E2E_STATE_DIR", os.path.join(os.getcwd(), ".e2e-state"))
os.makedirs(_STATE_DIR, exist_ok=True)


def _wipe_state() -> None:
    """Clear the throwaway state directory.

    Called from `main_entry` only, NEVER at import. As an import-time side effect this deleted the
    databases of whatever else had imported this module — including a pytest session, where the fixture
    that imports it runs alongside 2000 other tests. Import must stay inert.

    The wipe itself is correctness, not hygiene: the tab bar lists every active session the user owns, so
    sessions left by a previous run make a fresh run's list non-empty and assertions about what the user
    just created fail against leftovers.
    """
    if (os.getenv("E2E_KEEP_STATE") or "").strip().lower() in ("1", "true", "yes", "on"):
        print("[e2e] E2E_KEEP_STATE set - state preserved", flush=True)
        return

    import shutil

    # NOT `ignore_errors=True`. That is what made this fail invisibly: on Windows a database another
    # handle still holds cannot be deleted, the error was swallowed, and 31 sessions from previous runs
    # survived — which then presented as tab counts depending on how many times the suite had been run.
    # The outcome is now reported either way, so a failed wipe is visible rather than assumed.
    before = 0
    try:
        before = len(os.listdir(_STATE_DIR))
    except OSError:
        pass
    try:
        shutil.rmtree(_STATE_DIR)
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(
            f"[e2e] WARN: could not clear {_STATE_DIR} ({exc}). Leftover sessions will make tab counts "
            f"depend on run history. Delete it manually, or have the CI step do it before starting this "
            f"server.",
            flush=True,
        )
    os.makedirs(_STATE_DIR, exist_ok=True)
    after = len(os.listdir(_STATE_DIR))
    print(f"[e2e] state dir {_STATE_DIR}: {before} entries -> {after}", flush=True)
for _var, _name in (
    ("SESSIONS_DB_PATH", "sessions.db"),
    ("LANGGRAPH_CHECKPOINT_DB", "checkpoints.db"),
    ("COMPLIANCE_DB_PATH", "compliance.db"),
    ("TELEMETRY_DB_PATH", "telemetry.db"),
    ("JOURNAL_DB_PATH", "journal.db"),
):
    os.environ.setdefault(_var, os.path.join(_STATE_DIR, _name))

# A placeholder is enough: with `LLM_API_KEY` set the service takes the SHARED-KEY branch and
# never calls the per-user resolver, so no auth backend is needed. The stub graph never uses it.
os.environ.setdefault("LLM_API_KEY", "e2e-placeholder")
# The SKU gate would otherwise refuse the run for a user with no entitlement record.
os.environ.setdefault("SKU_ENFORCE", "0")
os.environ.setdefault("DEEP_QUANT_SESSIONS_ENABLED", "1")
os.environ.setdefault("DEEP_QUANT_PERSIST_STREAM", "1")

# The wipe has to happen BEFORE `import main`, and only when this file is run as a script.
#
# `import main` opens all five SQLite databases at import time. Wiping afterwards (from `main_entry`)
# could not work on Windows: `rmtree` cannot delete a file another handle holds open, and
# `ignore_errors=True` swallowed the failure — leaving 31 sessions from previous runs and making the tab
# counts depend on how many times the suite had been run.
#
# Guarded by `__name__` so importing this module (the pytest fixture does) still deletes nothing. That
# guard is the whole reason this is safe to run at module scope.
if __name__ == "__main__":
    _wipe_state()

import main  # noqa: E402  (must follow the env setup and the wipe above)


# The REAL message classes, not a local duck type.
#
# `stream_events.message_events` dispatches on `type(msg).__name__` containing "AIMessage" or
# "ToolMessage". A hand-rolled stand-in silently matched neither, so the stub streamed a run with
# no reasoning in it at all — which is exactly the sort of "test passes, proves nothing" failure
# this job exists to avoid. Using the classes the graph actually emits keeps the stub correct even
# if that dispatch changes.
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402

# One node update per graph tick, shaped as LangGraph delivers them: `{node_name: node_data}`,
# where `node_data` carries `messages` and/or a committed `decision`.
#
# Ordered to exercise every frame KIND the client reducer handles — REASONING, the
# TOOL_CALL_START/RESULT/END triple, and DECISION. A stub that only emitted REASONING would leave
# the tool and decision rendering paths untested end to end.
_ANALYSIS_UPDATES = [
    {"market_analyst": {"messages": [AIMessage(content="Scanning RELIANCE on the 10m timeframe.")]}},
    {
        "market_analyst": {
            "messages": [
                AIMessage(
                    content="Pulling candles before I commit to a read.",
                    tool_calls=[{"name": "get_ohlc", "args": {"symbol": "RELIANCE"}, "id": "call_1"}],
                ),
                ToolMessage(content="240 candles", name="get_ohlc", tool_call_id="call_1"),
            ]
        }
    },
    {"technical_analyst": {"messages": [AIMessage(content="Momentum is intact above 2,450.")]}},
    {
        # `decision` is a key INSIDE a node's data, not a node name. Yielded at the top level it is
        # read as a node called "decision" whose data has no `decision` key, and no DECISION frame
        # is emitted.
        "risk_manager": {
            "decision": {
                "action": "BUY",
                "conviction_score": 72,
                "rationale": "Trend continuation with a defined invalidation.",
                "entry": 2470.0,
                "stop_loss": 2435.0,
                "take_profit": 2550.0,
            }
        }
    },
]

# A Q&A turn answers on the same thread, so it streams the same way. Distinct wording so the test
# can prove the answer landed in the CHAT and not in the glass-box transcript.
_QA_UPDATES = [
    {
        "trade_qa": {
            "messages": [AIMessage(content="The stop sits below the 15m swing low at 2,435.")]
        }
    },
]


def _is_qa(graph_input) -> bool:
    """Whether this invocation is a follow-up question rather than a fresh analysis.

    Detected from the input rather than tracked as server state, so two sessions running
    concurrently cannot make each other emit the wrong script.
    """
    if not isinstance(graph_input, dict):
        return False
    if graph_input.get("mode") == "QA":
        return True
    return bool(graph_input.get("question"))


async def _stub_astream(input=None, config=None, **kwargs):  # noqa: A002 - mirrors LangGraph's name
    updates = _QA_UPDATES if _is_qa(input) else _ANALYSIS_UPDATES
    for update in updates:
        # A real graph does not deliver its whole reasoning in one tick. The delay keeps the
        # frames genuinely incremental, so the test exercises streaming rather than a single
        # flush — which is where the isolation bug this migration fixes actually lived.
        await asyncio.sleep(0.05)
        yield update


class _StubState:
    """What `graph.get_state` returns.

    ``next`` empty means "not paused", so the run reaches a terminal `RUN_FINISHED{completed}`.
    The price-watch interrupt path is deliberately not simulated: a watcher that never fires
    would hang the job, and asserting on a fake pause proves nothing about the real one.
    """

    next = ()
    values = {"messages": [AIMessage(content="The stop sits below the 15m swing low at 2,435.")], "profile": "INTRADAY"}


class _StubGraph:
    """Stands in for a compiled LangGraph.

    Only the two members `main` actually calls are implemented. A wider fake would invite drift
    against the real graph without testing anything more.
    """

    astream = staticmethod(_stub_astream)

    @staticmethod
    def get_state(config):  # noqa: ARG004 - signature must match the real graph
        return _StubState()


def install_stub() -> None:
    """Stub the graph at the COMPILE seam, not on the bound instance.

    `main` reaches the graph as `graph_module.graph` at every call site because the FastAPI
    lifespan rebinds that attribute at startup — the durable checkpointer can only be built
    inside a running loop. Patching only the current instance would therefore be silently undone
    the moment the app starts.

    Replacing `compile_with` means whatever the lifespan compiles IS the stub, so the two cannot
    disagree. The attribute is set too, for the window before startup.
    """
    main.graph_module.compile_with = lambda checkpointer=None: _StubGraph()
    main.graph_module.graph = _StubGraph()
    # Neutralise credential plumbing. The shared-key branch is taken anyway, but this keeps the
    # stub independent of how that branch evolves.
    main.set_run_llm_credentials = lambda *a, **k: None
    print("[e2e] graph stubbed: canned frame sequence, no LLM / market data / QuestDB", flush=True)


def main_entry() -> int:
    import uvicorn

    # NOT wiped here — that happens above, before `import main` opens the database files.
    install_stub()

    port = int(os.getenv("E2E_AGENT_PORT", "8099"))
    print(f"[e2e] deep-quant (stubbed graph) on :{port}", flush=True)
    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main_entry())
