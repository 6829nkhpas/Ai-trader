import json
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langgraph.types import Command

# Import the compiled LangGraph state machine
from graph import graph

app = FastAPI(title="LangGraph Deep Quant Loop Service")

# ── Pydantic Request Models ──────────────────────────────────────────────────

from typing import Optional

class RunRequest(BaseModel):
    thread_id: str
    message: str
    mode: Optional[str] = "FIND"
    symbol: Optional[str] = "N/A"
    manual_trade: Optional[dict] = None

class ResumeRequest(BaseModel):
    thread_id: str
    triggered_candle: dict

# ── SSE Generator ────────────────────────────────────────────────────────────

async def event_generator(thread_id: str, graph_input=None, resume_command=None):
    # Yield RUN_STARTED event
    yield f"event: RUN_STARTED\ndata: {json.dumps({'thread_id': thread_id})}\n\n"
    
    config = {"configurable": {"thread_id": thread_id}}
    target_input = resume_command if resume_command is not None else graph_input

    try:
        # Iterate over the async updates generator
        async for event in graph.astream(target_input, config, stream_mode="updates"):
            for node_name, node_data in event.items():
                if "messages" in node_data:
                    for msg in node_data["messages"]:
                        # 1. Check for tool calls (TOOL_CALL_START)
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                tool_name = tc.get("name")
                                yield f"event: TOOL_CALL_START\ndata: {json.dumps({'tool': tool_name, 'args': tc.get('args')})}\n\n"
                        
                        # 2. Check for text message / reasoning (TEXT_MESSAGE)
                        # We only want reasoning text from AIMessages, NOT raw JSON outputs from ToolMessages
                        msg_type = type(msg).__name__
                        if "AIMessage" in msg_type and hasattr(msg, "content") and msg.content:
                            yield f"event: TEXT_MESSAGE\ndata: {json.dumps({'content': msg.content})}\n\n"
                        elif "ToolMessage" in msg_type:
                            tool_name = getattr(msg, "name", "tool")
                            yield f"event: TOOL_CALL_END\ndata: {json.dumps({'tool': tool_name, 'status': 'success'})}\n\n"

        # After the stream finishes, check if the graph is paused or completed
        state = graph.get_state(config)
        status = "paused" if state.next else "completed"
        
        yield f"event: RUN_FINISHED\ndata: {json.dumps({'thread_id': thread_id, 'status': status})}\n\n"

    except Exception as e:
        yield f"event: ERROR\ndata: {json.dumps({'error': str(e)})}\n\n"

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
        "manual_trade": payload.manual_trade
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
        event_generator(payload.thread_id, resume_command=Command(resume=payload.triggered_candle)),
        media_type="text/event-stream"
    )

# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8086, reload=True)
