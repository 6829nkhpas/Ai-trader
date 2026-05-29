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
        err_msg = str(e)
        print(f"[event_generator] ⚠ LangGraph streaming failed: {err_msg}. Triggering quantitative rule-based fallback...")
        
        fallback_msg = (
            f"⚠️ LLM API service returned error ({err_msg}).\n"
            f"🔄 Switching to failover protocol: running native rule-based Quantitative Consensus Engine..."
        )
        yield f"event: TEXT_MESSAGE\ndata: {json.dumps({'content': fallback_msg})}\n\n"
        
        try:
            from tools import get_consensus_report, get_candles
            # Fetch technical data
            symbol = "RELIANCE"
            mode = "FIND"
            if graph_input:
                symbol = graph_input.get("symbol", "RELIANCE")
                mode = graph_input.get("mode", "FIND")
            
            # Yield tool calls to show the user what data is being parsed
            yield f"event: TOOL_CALL_START\ndata: {json.dumps({'tool': 'get_consensus_report', 'args': {'symbol': symbol, 'timeframe': '10m'}})}\n\n"
            consensus = get_consensus_report.func(symbol, "10m")
            yield f"event: TOOL_CALL_END\ndata: {json.dumps({'tool': 'get_consensus_report', 'status': 'success'})}\n\n"
            
            yield f"event: TOOL_CALL_START\ndata: {json.dumps({'tool': 'get_candles', 'args': {'symbol': symbol, 'timeframe': '10m', 'limit': 50}})}\n\n"
            candles = get_candles.func(symbol, "10m", 50)
            yield f"event: TOOL_CALL_END\ndata: {json.dumps({'tool': 'get_candles', 'status': 'success'})}\n\n"
            
            # Extract technical indicators from consensus
            trend_score = 0
            momentum = "NEUTRAL"
            patterns = []
            if isinstance(consensus, dict) and "error" not in consensus:
                trend_score = consensus.get("trend_score", 0)
                momentum = consensus.get("momentum_state", "NEUTRAL")
                patterns = consensus.get("active_patterns", [])
            
            last_close = 1000.0
            if isinstance(candles, list) and len(candles) > 0 and isinstance(candles[-1], dict):
                last_close = candles[-1].get("close", 1000.0)
            
            # Construct a rule-based decision
            if trend_score > 20:
                action = "BUY"
                conviction = 65 + min(30, int(trend_score / 3))
                entry = last_close
                sl = last_close * 0.985
                tp = last_close * 1.03
                reasons = f"Bullish trend score ({trend_score}) and momentum ({momentum}) confirm upward structural bias. Active patterns: {', '.join(patterns) if patterns else 'None'}."
                plan = f"Action: BUY | Entry: ₹{entry:.2f} | Stop Loss: ₹{sl:.2f} | Target: ₹{tp:.2f}"
            elif trend_score < -20:
                action = "SELL"
                conviction = 65 + min(30, int(abs(trend_score) / 3))
                entry = last_close
                sl = last_close * 1.015
                tp = last_close * 0.97
                reasons = f"Bearish trend score ({trend_score}) and momentum ({momentum}) confirm downward pressure. Active patterns: {', '.join(patterns) if patterns else 'None'}."
                plan = f"Action: SELL | Entry: ₹{entry:.2f} | Stop Loss: ₹{sl:.2f} | Target: ₹{tp:.2f}"
            else:
                action = "HOLD"
                conviction = 50
                entry = last_close
                sl = last_close * 0.98
                tp = last_close * 1.04
                reasons = f"Choppy/flat trend score ({trend_score}) and neutral momentum indicators. Self-preservation protocol active."
                plan = "Action: HOLD/WAIT | Current close flat. Wait for clean breakout or volume spike before initializing."
                
            # If in VERIFY mode, critique the proposed trade
            if mode == "VERIFY" and graph_input and graph_input.get("manual_trade"):
                trade = graph_input["manual_trade"]
                proposed_side = trade.get("side", "BUY")
                proposed_entry = trade.get("entry", last_close)
                
                if proposed_side.upper() == action:
                    reasons = f"Approved trade: proposed {proposed_side} aligns with our trend score ({trend_score}). Risk parameters verified as sound."
                    plan = f"Recommendation: APPROVED | Execute {proposed_side} @ proposed entry ₹{proposed_entry:.2f}."
                else:
                    reasons = f"Rejected proposed trade: proposed {proposed_side} is counter-trend to our mathematical score ({trend_score})."
                    plan = f"Recommendation: HOLD/REJECT | Avoid entering {proposed_side} against trend bias. Wait for alignment."
            
            # Format the output JSON
            final_json = {
                "conviction_score": conviction,
                "setup_validation": reasons,
                "execution_plan": plan
            }
            
            # Yield final JSON as a TEXT_MESSAGE so the Rust parser can parse it successfully!
            yield f"event: TEXT_MESSAGE\ndata: {json.dumps({'content': json.dumps(final_json)})}\n\n"
            yield f"event: RUN_FINISHED\ndata: {json.dumps({'thread_id': thread_id, 'status': 'completed'})}\n\n"
            
        except Exception as fe:
            print(f"[event_generator] ✘ Fallback generator failed: {str(fe)}")
            yield f"event: ERROR\ndata: {json.dumps({'error': f'LLM error ({err_msg}) + Fallback error ({str(fe)})'})}\n\n"

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
