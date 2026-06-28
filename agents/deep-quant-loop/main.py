import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langgraph.types import Command

# Import the compiled LangGraph state machine
from graph import graph

# F&O read layer + analytics (F1/F2) and the agent options-bias classifier (F3).
# The /options/snapshot endpoint (F4 transport seam) strictly COMPOSES these
# existing functions and adds no analytics of its own (see the F4 design, AD-2).
from datetime import datetime, timezone, timedelta
from options import (
    read_latest_and_prior_snapshot,
    compute_options_analytics,
    _escape_sql_literal,
    _questdb_select,
)
from options_bias import classify_options_bias, resolve_options_bias_config

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

# ── F&O snapshot endpoint (F4 transport seam — composition only) ──────────────
# The frontend F&O section consumes F1/F2/F3 through this single thin, read-only
# endpoint. It performs NO analytics of its own (Requirements 9.1, 9.5): it reads
# the chain strikes via the existing F2 read layer, calls
# ``options.compute_options_analytics`` (F2) and ``options_bias.classify_options_bias``
# (F3) verbatim, and assembles their outputs — preserving every ``null`` leaf as
# ``null`` — into the IPC payload the bridge proxies to the UI. When no snapshot
# exists it returns the F2 ``Unavailable_Marker`` shape unchanged (Requirements
# 8.1, 8.4).

# NSE trading session in IST (UTC+5:30): 09:15–15:30, Monday–Friday. Used only to
# label the payload ``open``/``closed`` so the UI can show a live vs most-recent
# indicator (Requirement 8.4); this is a status flag, not an analytic.
_IST_OFFSET = timedelta(hours=5, minutes=30)
_SESSION_OPEN_MIN = 9 * 60 + 15
_SESSION_CLOSE_MIN = 15 * 60 + 30


def _now_ist() -> datetime:
    """Current wall-clock time in IST (UTC+5:30)."""
    return datetime.now(timezone.utc) + _IST_OFFSET


def _market_status() -> str:
    """Return ``"open"`` during the NSE session, otherwise ``"closed"``."""
    now = _now_ist()
    if now.weekday() >= 5:  # Saturday / Sunday
        return "closed"
    minute_of_day = now.hour * 60 + now.minute
    if _SESSION_OPEN_MIN <= minute_of_day <= _SESSION_CLOSE_MIN:
        return "open"
    return "closed"


def _resolve_nearest_expiry(underlying: str) -> str:
    """Resolve the nearest available expiry for ``underlying`` (composition only).

    Reads the distinct expiries that have ``option_chain_snapshots`` for the
    underlying (via the existing F2 read primitives), then picks the nearest one:
    the soonest expiry on or after today's IST date, falling back to the latest
    available expiry when every stored expiry is already in the past. F1 stores
    expiries as ISO ``YYYY-MM-DD`` strings, which sort chronologically. Returns
    ``""`` (never raises) when no expiry exists for the underlying.
    """
    u = _escape_sql_literal(underlying)
    rows = _questdb_select(
        "SELECT DISTINCT expiry FROM option_chain_snapshots "
        f"WHERE underlying='{u}' ORDER BY expiry ASC"
    )
    if not rows:
        return ""
    expiries = [
        row[0]
        for row in rows
        if isinstance(row, (list, tuple)) and row
        and isinstance(row[0], str) and row[0].strip()
    ]
    if not expiries:
        return ""
    today = _now_ist().strftime("%Y-%m-%d")
    upcoming = [e for e in expiries if e >= today]
    return upcoming[0] if upcoming else expiries[-1]


def _representative_iv(strike, ce_iv, pe_iv, spot):
    """Select the single per-strike IV to surface, mirroring F2's skew rule.

    F2 builds its IV-skew from the out-of-the-money side (put IV at/below spot,
    call IV above spot), falling back to whichever leg is solvable when spot is
    unavailable. This reuses that exact selection so the surfaced ``iv`` is
    consistent with the analytics result — it composes (never recomputes) the
    already-computed per-strike IVs and preserves ``null`` as ``null``.
    """
    if spot is not None and isinstance(strike, (int, float)) and not isinstance(strike, bool):
        return pe_iv if strike <= spot else ce_iv
    return ce_iv if ce_iv is not None else pe_iv


def _build_chain_rows(latest, analytics: dict) -> list:
    """Assemble one chain row per snapshot strike (composition only).

    Each row carries the strike's CE/PE open interest and last price straight
    from the F2 read-layer ``ChainSnapshot`` and the representative per-strike IV
    drawn from the F2 ``per_strike`` output — preserving every ``null`` leaf as
    ``null`` and never fabricating a value. Strikes are exactly those present in
    the snapshot, in the snapshot's ascending order.
    """
    iv_by_strike: dict = {}
    for entry in (analytics.get("per_strike") or []):
        if not isinstance(entry, dict):
            continue
        ce = entry.get("ce") if isinstance(entry.get("ce"), dict) else {}
        pe = entry.get("pe") if isinstance(entry.get("pe"), dict) else {}
        iv_by_strike[entry.get("strike")] = (ce.get("iv"), pe.get("iv"))

    spot = analytics.get("spot")
    chain = []
    for quote in getattr(latest, "strikes", ()) or ():
        ce_iv, pe_iv = iv_by_strike.get(quote.strike, (None, None))
        chain.append({
            "strike": quote.strike,
            "ce_oi": quote.ce_oi,
            "pe_oi": quote.pe_oi,
            "ce_price": quote.ce_price,
            "pe_price": quote.pe_price,
            "iv": _representative_iv(quote.strike, ce_iv, pe_iv, spot),
        })
    return chain


@app.get("/options/snapshot")
def options_snapshot(underlying: str, expiry: str = ""):
    """Return the assembled F&O snapshot for a chain, or an Unavailable_Marker.

    Composes the existing F1/F2/F3 layers (no new analytics — Requirements 9.1,
    9.5):

      1. Resolve the nearest available expiry when ``expiry`` is blank.
      2. Read the latest chain snapshot's strikes via the F2 read layer.
      3. Compute the ``Options_Analytics_Result`` via
         ``options.compute_options_analytics`` (F2).
      4. Derive the ``Options_Bias`` via ``options_bias.classify_options_bias`` (F3).

    On success returns
    ``{ underlying, expiry, snapshot_ts, market_status, chain, analytics, bias }``
    with every ``null`` leaf preserved verbatim. When no snapshot exists (or F2
    otherwise degrades) it returns the F2 ``Unavailable_Marker``
    ``{ underlying, expiry, unavailable: true, reason, last_snapshot_ts? }``
    (Requirements 8.1, 8.4). Never raises into the caller.
    """
    requested_expiry = expiry.strip() if isinstance(expiry, str) else ""

    # 1. Resolve the nearest expiry when none was supplied.
    resolved_expiry = requested_expiry or _resolve_nearest_expiry(underlying)
    if not resolved_expiry:
        return {
            "underlying": underlying,
            "expiry": "",
            "unavailable": True,
            "reason": f"no chain snapshot available for {underlying}",
        }

    # 2. Read the chain strikes via the existing F2 read layer.
    latest, _prior = read_latest_and_prior_snapshot(underlying, resolved_expiry)
    if latest is None:
        return {
            "underlying": underlying,
            "expiry": resolved_expiry,
            "unavailable": True,
            "reason": (
                f"no chain snapshot available for {underlying} / {resolved_expiry}"
            ),
        }

    # 3. Compute analytics (F2). A snapshot exists, so if F2 still degrades to a
    #    marker (e.g. spot unavailable) pass it through with the last snapshot
    #    timestamp so the UI can show the most-recent state (Requirement 8.4).
    analytics = compute_options_analytics(underlying, resolved_expiry)
    if analytics.get("unavailable"):
        marker = dict(analytics)
        marker["last_snapshot_ts"] = latest.snapshot_ts
        return marker

    # 4. Derive the options bias (F3) from the analytics result.
    bias = classify_options_bias(analytics, resolve_options_bias_config())

    return {
        "underlying": underlying,
        "expiry": resolved_expiry,
        "snapshot_ts": analytics.get("snapshot_ts", latest.snapshot_ts),
        "market_status": _market_status(),
        "chain": _build_chain_rows(latest, analytics),
        "analytics": analytics,
        "bias": bias,
    }


# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8086, reload=True)
