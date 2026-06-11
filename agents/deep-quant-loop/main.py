import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langgraph.types import Command

# Import the compiled LangGraph state machine
from graph import graph

# Pure glass-box stream helpers (reasoning splitter, event-payload builders,
# run-lifecycle builders, and the ordered per-update event expansion). Factored
# into stream_events.py so they are unit/property-testable in isolation
# (Requirements 16 and 17). main.py only orchestrates the live SSE ordering
# around them: RUN_STARTED first, per-update events in step order, and a single
# terminal RUN_FINISHED (completed/paused) or ERROR last.
from stream_events import (
    format_sse,
    node_update_events,
    build_run_started_event,
    build_run_finished_event,
    build_error_event,
    RUN_STARTED,
    RUN_FINISHED,
    ERROR,
)

app = FastAPI(title="LangGraph Deep Quant Loop Service")

# ── Pydantic Request Models ──────────────────────────────────────────────────

from typing import Optional

class RunRequest(BaseModel):
    thread_id: str
    message: str
    mode: Optional[str] = "FIND"
    symbol: Optional[str] = "N/A"
    manual_trade: Optional[dict] = None
    timeframe: Optional[str] = None

class ResumeRequest(BaseModel):
    thread_id: str
    triggered_candle: dict
    trigger_kind: Optional[str] = "target"

class QARequest(BaseModel):
    # Trade_QA_Mode follow-up question. Reuses the SAME thread_id so the run
    # answers from the thread's persisted Session_Analysis_Context via the
    # MemorySaver checkpointer (R18.1, R18.5) without re-running analysis.
    thread_id: str
    question: str

# ── SSE Generator ────────────────────────────────────────────────────────────

async def event_generator(thread_id: str, graph_input=None, resume_command=None):
    """Stream the run as ordered glass-box Server-Sent Events.

    Ordering and resilience guarantees (Requirement 17):

      * ``RUN_STARTED`` is emitted before any other event (R17.1).
      * Each LangGraph node update is expanded — via the pure
        ``node_update_events`` helper — into REASONING / TOOL_CALL_* /
        VERIFICATION_STEP / DECISION events, in the order the underlying steps
        occurred, so a tool call's ``TOOL_CALL_START`` always precedes its
        ``TOOL_CALL_RESULT`` and ``TOOL_CALL_END`` (R17.3, R17.4).
      * A run that completes or pauses ends with exactly one ``RUN_FINISHED``
        event stating ``completed``/``paused`` as the final event (R17.2, R17.6).
      * If the LLM stream fails mid-run, an ``ERROR`` event is emitted and **no**
        ``DECISION`` (nor ``RUN_FINISHED``) follows — the failure surfaces a clean
        analysis-unavailable error rather than a fabricated trade plan
        (R17.5, R5.5).
      * Every payload is framed through ``format_sse``, which normalizes it to a
        valid JSON object (R17.7).
    """
    # R17.1: RUN_STARTED is always the first event of the run.
    yield format_sse(RUN_STARTED, build_run_started_event(thread_id))

    config = {"configurable": {"thread_id": thread_id}}
    target_input = resume_command if resume_command is not None else graph_input

    try:
        # Iterate over the async updates generator, preserving step order (R17.4).
        async for event in graph.astream(target_input, config, stream_mode="updates"):
            for node_name, node_data in event.items():
                # node_update_events emits REASONING/TOOL_CALL_* before any
                # DECISION for the update, keeping TOOL_CALL_START ahead of its
                # RESULT/END (R17.3) and surfacing events in step order (R17.4).
                for name, payload in node_update_events(node_data):
                    yield format_sse(name, payload)

        # R17.2/R17.6: a completed or paused run ends with a single terminal
        # RUN_FINISHED event stating which it was.
        state = graph.get_state(config)
        status = "paused" if state.next else "completed"
        yield format_sse(RUN_FINISHED, build_run_finished_event(thread_id, status))

    except Exception as e:
        err_msg = str(e)
        print(f"[event_generator] X LangGraph streaming failed: {err_msg}. Surfacing error (no fallback).")

        # R17.5/R5.5: a failed LLM stream surfaces a clean ERROR and emits no
        # DECISION (and no RUN_FINISHED) for the run. We rely exclusively on the
        # live LLM analysis backed by real market data — never a fabricated plan.
        yield format_sse(ERROR, build_error_event(err_msg))

# ── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/run")
async def run_agent(payload: RunRequest):
    """
    Start or continue the Deep Quant LLM ReAct loop, returning an SSE stream.
    """
    initial_state = {
        "messages": [("user", payload.message)],
        "mode": payload.mode,
        "symbol": payload.symbol,
        "manual_trade": payload.manual_trade,
        "timeframe": payload.timeframe
    }
    return StreamingResponse(
        event_generator(payload.thread_id, graph_input=initial_state),
        media_type="text/event-stream"
    )

@app.post("/resume")
async def resume_agent(payload: ResumeRequest):
    """
    Resumes a paused state graph run and returns the subsequent execution as an SSE stream.
    """
    config = {"configurable": {"thread_id": payload.thread_id}}
    state = graph.get_state(config)
    if not state.next:
        raise HTTPException(
            status_code=400,
            detail=f"Thread_id '{payload.thread_id}' is not in a paused/interruptible state."
        )
    
    return StreamingResponse(
        event_generator(
            payload.thread_id,
            resume_command=Command(resume={
                "candle": payload.triggered_candle,
                "trigger_kind": payload.trigger_kind,
            }),
        ),
        media_type="text/event-stream"
    )

@app.post("/qa")
async def qa_agent(payload: QARequest):
    """
    Ask a free-form Trade_QA_Mode question about a prior analysis, returning an
    SSE stream.

    The request reuses the SAME ``thread_id`` as the original analysis run, so
    the compiled graph's conditional entry point routes ``mode == "QA"`` to the
    Q&A handler, which grounds its answer in the thread's persisted
    Session_Analysis_Context (multi-TF bias, S/R levels, indicators, patterns,
    sentiment, and the Declared_Trade + defensibility record) via the MemorySaver
    checkpointer (R18.1, R18.5). The question is appended to the existing thread
    state (the ``messages`` reducer adds it on top of the checkpointed history),
    so prior analysis remains available to subsequent questions.

    The answer is streamed through the SAME ``event_generator``/SSE conventions
    used for analysis runs — RUN_STARTED first, then REASONING / TOOL_CALL_*
    events in step order, then a terminal RUN_FINISHED — so the UI renders Q&A
    answers identically to run transparency (R18.7). The Q&A nodes never emit a
    ``decision`` update, so the committed Declared_Trade is left untouched
    (R18.6).
    """
    qa_input = {
        "messages": [("user", payload.question)],
        "mode": "QA",
    }
    return StreamingResponse(
        event_generator(payload.thread_id, graph_input=qa_input),
        media_type="text/event-stream"
    )

# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8086, reload=True)
