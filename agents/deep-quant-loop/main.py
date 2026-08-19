import asyncio
import time
import uvicorn
import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langgraph.types import Command

# ── Prometheus surface (:9109) — started FIRST, deliberately ──────────────────
# This import and the serve() below sit above `from graph import ...` because
# graph.py is a ~345 KB module that pulls in langgraph, langchain and the whole
# tool layer. Importing it takes real time, and a failure inside it (a missing
# dependency, a bad env var read at import time) kills the process before uvicorn
# ever binds. With the listener already up, that window is the difference between
# scraping an honest `idle` and scraping nothing at all — and "nothing at all"
# looks exactly like a container that was never deployed.
#
# The listener runs on its own thread rather than as a FastAPI route: the failure
# worth catching here is a wedged event loop, and an `@app.get("/metrics")` route
# would be starved by the very stall it is meant to report. See service_metrics.py.
from service_metrics import metrics as svc_metrics

_metrics_server = svc_metrics.serve()

# Import the compiled LangGraph state machine + the per-run LLM credential hook.
from graph import graph, set_run_llm_credentials  # noqa: E402 - see the note above

# Per-user OpenRouter key resolution (backend internal endpoint, droplet
# IP-whitelisted). Each run binds the requesting user's key instead of a shared
# env key.
from api_key_resolver import (
    resolve_openrouter_key,
    openrouter_base_url,
    ApiKeyResolutionError,
)
from run_context import set_run_user_id

# RESEARCH SKU entitlement gate (compliance blocker P1). Imported eagerly and
# NOT defensively: unlike telemetry, this is a regulatory control, so an import
# failure must break the service loudly rather than silently disable the gate.
from entitlements import (
    EntitlementError,
    ENTITLEMENT_ERROR_CODE,
    require_research_entitlement,
)

# Tamper-evident interaction log (compliance blocker P5): what was published, to
# whom, and when. Imported eagerly for the same reason as the entitlement gate —
# a silently disabled audit log is worse than a crash, because it looks like
# compliance. It depends only on the standard library, so there is nothing here
# that can realistically fail to import.
import interaction_log

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
    # Imported for instrumentation only: the tool's terminal event is where its
    # success/failure verdict already exists.
    TOOL_CALL_END,
)

# Session Telemetry (measurement-only, best-effort). Imported defensively so a
# telemetry import failure can NEVER break the run/resume endpoints (Requirements
# 6.4, 10.3): telemetry is a non-invasive observation layer, so if it cannot even
# be imported the endpoints fall back to the bare, un-teed event_generator.
try:
    import telemetry  # type: ignore
except Exception as _telemetry_import_error:  # noqa: BLE001 - never block the app
    telemetry = None  # type: ignore
    print(
        f"[main] WARN: session telemetry unavailable ({_telemetry_import_error}); "
        "run/resume will stream without telemetry."
    )

# The telemetry layer degrades silently by design, which means a deployment can
# lose all trade-outcome recording with no other outward sign. Export the fact so
# the absence is at least visible on a dashboard.
svc_metrics.set_telemetry_available(telemetry is not None)

app = FastAPI(title="LangGraph Deep Quant Loop Service")


def _ensure_compliance_stores() -> None:
    """Create the P2/P5 tables and their append-only triggers at startup.

    Both stores create themselves on first write, so this is not required for
    correctness — it is required for the guarantee to hold from the first
    interaction rather than from the first successful one. Without it, the window
    between process start and the first write is a window in which the tables do
    not exist and an ``UPDATE`` would be refused by nothing.

    Guarded: an unwritable store must not stop the service from starting, because
    the endpoints already degrade to a WARN per unwritten row. A failure here is
    the loudest available warning that the compliance record is not working.
    """
    import reco_store

    for label, ensure in (
        ("recommendations (P2)", reco_store.ensure_store),
        ("interactions (P5)", interaction_log.ensure_store),
    ):
        try:
            ensure()
        except Exception as exc:  # noqa: BLE001
            print(f"[compliance] WARN: could not initialise {label}: {exc}")


_ensure_compliance_stores()


def _observe(thread_id: str, entry_kind: str, gen, **entry_kwargs):
    """Wrap ``gen`` (an ``event_generator`` SSE iterator) in the telemetry tee.

    Returns ``telemetry.observe_stream(thread_id, RunEntry(...), gen)`` when the
    telemetry layer is available, otherwise the bare ``gen`` unchanged. The whole
    wrapping — including building the ``RunEntry`` — is guarded so that ANY failure
    (missing module, bad RunEntry kwargs, an error inside observe_stream setup)
    degrades to the un-teed generator. Telemetry can never take the endpoint down
    (Requirements 6.4, 10.3); the tee itself is a passthrough, so the streamed
    bytes are identical either way (Requirement 6.1).
    """
    if telemetry is None:
        return gen
    try:
        entry = telemetry.RunEntry(entry_kind, **entry_kwargs)
        return telemetry.observe_stream(thread_id, entry, gen)
    except Exception as exc:  # noqa: BLE001 - telemetry must never break the stream
        print(f"[main] WARN: telemetry wrap failed ({exc}); streaming without telemetry.")
        return gen


# ── Interaction log (compliance blocker P5) ──────────────────────────────────
# Every request that reaches a research surface, and how it ended, appended to the
# tamper-evident store in `interaction_log.py`. The two wrappers below own the
# failure posture: the STORE raises (a dropped audit row is the defect it exists to
# prevent) and these swallow it with a WARN, because an endpoint that 500s on an
# unwritable log trades a compliance gap for an outage. The WARN is the operator's
# signal that an interaction went unrecorded.


def _log_request(kind: str, **fields) -> None:
    """Append an inbound-request row. Never raises into the request path."""
    try:
        interaction_log.record_request(kind=kind, **fields)
    except Exception as exc:  # noqa: BLE001
        print(f"[interaction_log] WARN: request row for kind={kind} not written: {exc}")


def _log_outcome(kind: str, status: str, **fields) -> None:
    """Append a terminal-outcome row. Never raises into the request path."""
    try:
        interaction_log.record_outcome(kind=kind, status=status, **fields)
    except Exception as exc:  # noqa: BLE001
        print(f"[interaction_log] WARN: outcome row for kind={kind} not written: {exc}")


class _InteractionOutcome:
    """One-shot terminal-outcome recorder for one interaction.

    Deliberately shaped like the metrics ``tracker``: the terminal branches inside
    ``_run_events`` record the real outcome, and ``event_generator``'s ``finally``
    records ``disconnected`` only if nothing did. Without the one-shot guard a
    client that hangs up right after a completed run would produce two contradictory
    outcome rows for one interaction — and in an append-only store, neither could
    afterwards be marked as the wrong one.
    """

    def __init__(self, kind: str, thread_id: str, user_id=None, mode=None, model=None):
        self.kind = kind
        self.thread_id = thread_id
        self.user_id = user_id
        self.mode = mode
        self.model = model
        self._recorded = False

    def record(self, status: str, content=None, detail=None, refusal_category=None) -> None:
        if self._recorded:
            return
        self._recorded = True
        _log_outcome(
            self.kind,
            status,
            thread_id=self.thread_id,
            user_id=self.user_id,
            mode=self.mode,
            model=self.model,
            content=content,
            detail=detail,
            refusal_category=refusal_category,
        )


def _final_answer_and_refusal(state):
    """Extract the answer text and any personalisation-refusal category from state.

    For a Q&A turn the last message is the answer the client actually received, so
    it is what the log must store — the P5 record has to answer "what did you tell
    them?", which a status alone does not.

    ``_personalisation_refusal`` is the category the P8a guardrail stamped on a
    refusal it generated WITHOUT calling the model. Recording it turns the
    guardrail from an assertion into evidence: the log shows the RA/IA boundary
    being enforced, per-turn, rather than merely claimed.

    Total by construction — a shape it does not recognise yields ``(None, None)``
    rather than raising into a terminal stream branch.
    """
    try:
        values = getattr(state, "values", None)
        messages = values.get("messages") if isinstance(values, dict) else None
        if not messages:
            return None, None
        last = messages[-1]
        content = getattr(last, "content", None)
        text = content if isinstance(content, str) else None
        extra = getattr(last, "additional_kwargs", None)
        category = extra.get("_personalisation_refusal") if isinstance(extra, dict) else None
        return text, (category if isinstance(category, str) and category else None)
    except Exception as exc:  # noqa: BLE001
        print(f"[interaction_log] WARN: could not read the final answer: {exc}")
        return None, None


# ── Pydantic Request Models ──────────────────────────────────────────────────


from typing import Optional

class RunRequest(BaseModel):
    thread_id: str
    message: str
    mode: Optional[str] = "FIND"
    symbol: Optional[str] = "N/A"
    # Authenticated user id, forwarded by the desktop. Used to resolve this
    # user's OpenRouter key from the backend internal endpoint for the run.
    user_id: Optional[str] = None
    manual_trade: Optional[dict] = None
    timeframe: Optional[str] = None
    # Workspace profile selected in the terminal (INTRADAY / SWING / INVESTOR /
    # FNO). Threaded into the graph state so the agent adapts its data gathering
    # and analysis horizon to the section the user is in. Defaults to INTRADAY.
    profile: Optional[str] = "INTRADAY"
    # Expiry selected in the F&O workspace, as an ISO "YYYY-MM-DD" string. Threaded
    # into the graph state so an FNO-profile run analyzes the exact expiry the user
    # is viewing (empty/None => the options engine's nearest available expiry).
    fno_expiry: Optional[str] = None
    # Optional LLM model override chosen in the composer ('' / None => the
    # deployment default LLM_MODEL). Threaded into the graph state so the run's
    # model binding uses it (resolved against the same provider gateway).
    model: Optional[str] = None

class ResumeRequest(BaseModel):
    thread_id: str
    triggered_candle: dict
    trigger_kind: Optional[str] = "target"
    # Optional user id for re-resolving the OpenRouter key on a watcher-triggered
    # resume (falls back to the key persisted on the run's thread when absent).
    user_id: Optional[str] = None

class QARequest(BaseModel):
    # Trade_QA_Mode follow-up question. Reuses the SAME thread_id so the run
    # answers from the thread's persisted Session_Analysis_Context via the
    # MemorySaver checkpointer (R18.1, R18.5) without re-running analysis.
    thread_id: str
    question: str
    # Optional LLM model override for this Q&A turn ('' / None => default).
    model: Optional[str] = None
    # Authenticated user id for resolving this user's OpenRouter key.
    user_id: Optional[str] = None

class CancelRequest(BaseModel):
    # User-requested cancellation of an in-flight /run for this thread_id. The
    # Rust proxy also aborts its own streaming task (dropping the HTTP
    # connection); this flag is the belt-and-suspenders path that breaks the
    # graph.astream loop at the next step boundary even before the disconnect is
    # detected server-side.
    thread_id: str

# ── Cancellation registry ─────────────────────────────────────────────────────
# thread_ids requested to stop. `event_generator` checks membership each step and
# breaks out; the id is always discarded in the generator's `finally` so the set
# never leaks. A plain set is sufficient — writes are single statements and the
# server is single-process asyncio.
_CANCELLED: set[str] = set()

# ── Per-thread SSE fan-out hub (watcher re-attach transport) ───────────────────
# In the thin-client production topology the price watcher runs in the headless
# tool-server container, NOT in-process on the desktop. When it fires a heartbeat
# or target trigger it POSTs /resume here, and the resumed glass-box stream is the
# HTTP RESPONSE to THAT POST — i.e. it goes back to the tool-server, which drains
# and discards it. The desktop's only live stream was the original /run, which
# ended the moment the graph paused at watch_price_condition. So without a
# re-attach channel the desktop never sees heartbeat/target resumes and the
# terminal sits in "WATCHING" forever.
#
# This hub fixes that: every frame `event_generator` yields is ALSO published to
# any subscribers registered for that thread_id. The desktop opens a long-lived
# GET /stream/{thread_id} (see below) that stays attached across the whole
# watching lifecycle — through every resume/heartbeat cycle — so server-initiated
# resumes reach the UI over the SAME deep-quant-stream event as the live run.
#
# `_SUBSCRIBERS[thread_id]` is a set of asyncio.Queue, one per attached client.
# Publishing is best-effort and never blocks the run (full queues drop the frame
# for that subscriber only). Single-process asyncio server ⇒ a plain dict + set is
# race-free (all mutations happen on the event loop).
_SUBSCRIBERS: dict[str, set[asyncio.Queue]] = {}


def _refresh_subscriber_gauge() -> None:
    """Republish the total attached-subscriber count.

    Derived from the hub itself rather than incremented/decremented alongside it,
    so the gauge cannot drift out of step with reality on a disconnect path that
    misses its decrement — the number is only useful if it is exactly right. Zero
    while a thread is paused means a watcher resume would reach nobody and the
    terminal sits in WATCHING forever, which is precisely the bug this hub exists
    to prevent.
    """
    svc_metrics.set_stream_subscribers(sum(len(s) for s in _SUBSCRIBERS.values()))

def _publish_frame(thread_id: str, frame: str) -> None:
    """Best-effort fan-out of one already-formatted SSE frame to all subscribers
    attached to ``thread_id``. Never raises and never blocks the producing run —
    a subscriber whose queue is full simply misses this frame."""
    subs = _SUBSCRIBERS.get(thread_id)
    if not subs:
        return
    for q in list(subs):
        try:
            q.put_nowait(frame)
        except Exception:  # noqa: BLE001 - a slow/full subscriber must not stall the run
            pass

# ── SSE Generator ────────────────────────────────────────────────────────────

async def event_generator(thread_id: str, graph_input=None, resume_command=None, user_id=None, kind: str = "run"):
    """Stream the run as ordered glass-box SSE, tracked for monitoring.

    A thin wrapper around :func:`_run_events` that owns the run's lifecycle in
    the metrics surface. It exists as a separate function purely so the ``finally``
    below cannot be skipped: an SSE consumer that hangs up mid-run closes this
    generator, and without a guaranteed terminal record the run would stay counted
    in ``runs_in_flight`` forever — pinning ``work_expected`` to 1 and reporting a
    permanent stall on a service that is working fine.

    ``kind`` is the entry point (``run`` / ``resume`` / ``qa``), which is the axis
    that makes the run counters readable: a watcher-triggered ``resume`` failing
    while fresh ``run``s succeed is a completely different problem from the
    reverse.

    The same ``finally`` also guarantees the interaction log gets a terminal row
    (compliance blocker P5). A client that drops the stream mid-analysis is a real
    outcome for the record — the interaction happened — so it is logged as
    ``disconnected`` rather than left with a request row and no ending.
    """
    tracker = svc_metrics.run_started(kind)
    mode = graph_input.get("mode") if isinstance(graph_input, dict) else None
    model = graph_input.get("model") if isinstance(graph_input, dict) else None
    outcome = _InteractionOutcome(kind, thread_id, user_id=user_id, mode=mode, model=model)
    try:
        async for frame in _run_events(
            thread_id,
            tracker,
            graph_input=graph_input,
            resume_command=resume_command,
            user_id=user_id,
            outcome=outcome,
        ):
            yield frame
    finally:
        # Both idempotent — a run that reached a terminal event has already
        # recorded its own outcome, so these only take effect when the client
        # dropped the stream before the run finished.
        tracker.finish("disconnected")
        outcome.record("disconnected")


async def _run_events(
    thread_id: str,
    tracker,
    graph_input=None,
    resume_command=None,
    user_id=None,
    outcome=None,
):
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

    ``tracker`` records progress for the metrics surface. Every call on it is
    best-effort and non-throwing by construction, so the stream's guarantees above
    are unaffected by instrumentation.
    """
    # R17.1: RUN_STARTED is always the first event of the run.
    tracker.stream_event(RUN_STARTED)
    yield format_sse(RUN_STARTED, build_run_started_event(thread_id))

    # ── Bind the per-user OpenRouter key for this run (REQUIRED) ─────────────
    # Every LLM call uses the REQUESTING user's OpenRouter key, resolved from the
    # backend internal endpoint. There is no shared/env-based credential fallback:
    # a missing user_id or an unresolvable key surfaces a clean ERROR (never a
    # fabricated plan, never a shared key). The user id is also bound in the run
    # context so a watcher registered during this run can carry it onto its
    # eventual /resume handoff.
    set_run_user_id(user_id)
    # Credential mode:
    #   • SHARED-KEY (beta): a deployment LLM_API_KEY is configured → every run
    #     uses that single shared key against the configured gateway (omniroute).
    #     No per-user resolution and no user_id requirement.
    #   • PER-USER (production): no shared key → resolve the REQUESTING user's key
    #     from the backend internal endpoint (OpenRouter), failing cleanly if it
    #     can't be resolved. Never a silent fallback between the two modes.
    _shared_key = (os.getenv("LLM_API_KEY") or "").strip()
    if _shared_key:
        set_run_llm_credentials(_shared_key, openrouter_base_url())
        svc_metrics.key_resolution("shared")
    else:
        if not (user_id and str(user_id).strip()):
            svc_metrics.key_resolution("missing_user")
            tracker.stream_event(ERROR)
            tracker.finish("auth_error")
            if outcome is not None:
                outcome.record("auth_error", detail="no user_id supplied for LLM access")
            yield format_sse(
                ERROR,
                build_error_event("authentication required: no user_id supplied for LLM access"),
            )
            return
        try:
            _run_key = resolve_openrouter_key(user_id)
            set_run_llm_credentials(_run_key, openrouter_base_url())
            svc_metrics.key_resolution("resolved")
        except ApiKeyResolutionError as _key_err:
            print(f"[main] LLM key resolution failed for user {user_id}: {_key_err}")
            svc_metrics.key_resolution("failed")
            tracker.stream_event(ERROR)
            tracker.finish("key_error")
            if outcome is not None:
                outcome.record("key_error", detail=str(_key_err))
            yield format_sse(ERROR, build_error_event(f"LLM key unavailable: {_key_err}"))
            return

    config = {"configurable": {"thread_id": thread_id}}
    # Stamp the run's workspace profile into the tool config so profile-aware
    # tools (e.g. declare_trade's Trade_Validator R:R floor: INTRADAY 1:1.5 vs
    # 1:2 elsewhere) can resolve it. For a fresh /run the profile rides on the
    # initial state; for a /resume or /qa turn it is read back from the persisted
    # graph state so the same floor applies after a watcher wakes the agent.
    # Guarded and additive: any failure leaves the config exactly as before.
    try:
        run_profile = None
        if isinstance(graph_input, dict):
            run_profile = graph_input.get("profile")
        if not (isinstance(run_profile, str) and run_profile.strip()):
            persisted = graph.get_state(config)
            values = getattr(persisted, "values", None)
            if isinstance(values, dict):
                run_profile = values.get("profile")
        if isinstance(run_profile, str) and run_profile.strip():
            config["configurable"]["profile"] = run_profile.strip()
    except Exception as _profile_err:  # noqa: BLE001 - never break the run on this
        print(f"[main] WARN: could not resolve run profile for tool config ({_profile_err}).")

    target_input = resume_command if resume_command is not None else graph_input

    cancelled = False
    try:
        # Iterate over the async updates generator, preserving step order (R17.4).
        async for event in graph.astream(target_input, config, stream_mode="updates"):
            # THE BEAT SITE. One completed node advance is the unit of real work
            # for this service. Beating here rather than at run completion is what
            # separates a healthy ten-minute FIND run from one wedged on a hung
            # provider call for the same ten minutes — the first keeps the age near
            # zero, the second lets it grow past the threshold.
            tracker.graph_step()

            # Check cancel flag at each step boundary — breaks the loop without
            # waiting for the next LLM/tool round-trip to complete.
            if thread_id in _CANCELLED:
                cancelled = True
                break
            for node_name, node_data in event.items():
                # node_update_events emits REASONING/TOOL_CALL_* before any
                # DECISION for the update, keeping TOOL_CALL_START ahead of its
                # RESULT/END (R17.3) and surfacing events in step order (R17.4).
                for name, payload in node_update_events(node_data):
                    tracker.stream_event(name)
                    # A tool's terminal event carries its success/failure verdict.
                    # Counted here rather than at the tool layer because this is
                    # where the classification already exists — and because a tool
                    # that fails every call still lets the run COMPLETE with a
                    # degraded analysis, so the run outcome alone would never
                    # show it.
                    if name == TOOL_CALL_END and isinstance(payload, dict):
                        tracker.tool_call(
                            payload.get("tool") or "unknown",
                            payload.get("status") or "failure",
                        )
                    # Stamp EVERY event with the run's thread_id so a multi-run
                    # frontend can route each event to the correct session even
                    # when several symbols/profiles are analyzed concurrently
                    # (only RUN_STARTED/RUN_FINISHED carried it before). Additive
                    # and backward-compatible: consumers that ignore thread_id are
                    # unaffected.
                    if isinstance(payload, dict) and "thread_id" not in payload:
                        payload = {**payload, "thread_id": thread_id}
                    yield format_sse(name, payload)

        if cancelled:
            # User-requested stop: emit a clean terminal event so the frontend
            # always transitions out of 'running'. No DECISION is emitted.
            tracker.stream_event(RUN_FINISHED)
            tracker.finish("cancelled")
            if outcome is not None:
                outcome.record("cancelled")
            yield format_sse(RUN_FINISHED, build_run_finished_event(thread_id, "cancelled"))
        else:
            # R17.2/R17.6: a completed or paused run ends with a single terminal
            # RUN_FINISHED event stating which it was.
            state = graph.get_state(config)
            status = "paused" if state.next else "completed"
            tracker.stream_event(RUN_FINISHED)
            # `paused` is a normal outcome, not a failure: the graph is waiting at
            # watch_price_condition for a price that may be hours away. Recording
            # it as terminal is what stops a watching thread from counting as an
            # in-flight run and reporting a stall for the whole wait.
            tracker.finish(status)
            # P5: the terminal row carries the answer text the client received and
            # the personalisation category if the P8a guardrail refused the turn.
            if outcome is not None:
                answer, refusal_category = _final_answer_and_refusal(state)
                outcome.record(
                    status,
                    content=answer,
                    refusal_category=refusal_category,
                )
            yield format_sse(RUN_FINISHED, build_run_finished_event(thread_id, status))

    except Exception as e:
        err_msg = str(e)
        print(f"[event_generator] X LangGraph streaming failed: {err_msg}. Surfacing error (no fallback).")

        # R17.5/R5.5: a failed LLM stream surfaces a clean ERROR and emits no
        # DECISION (and no RUN_FINISHED) for the run. We rely exclusively on the
        # live LLM analysis backed by real market data — never a fabricated plan.
        tracker.stream_event(ERROR)
        tracker.finish("error")
        # P5: a failed interaction is still an interaction, and the reason it
        # failed is what someone reading the log months later needs.
        if outcome is not None:
            outcome.record("error", detail=err_msg)
        yield format_sse(ERROR, build_error_event(err_msg))
    finally:
        # Always discard the cancel flag so the set never leaks across runs.
        _CANCELLED.discard(thread_id)

# ── Endpoints ────────────────────────────────────────────────────────────────

async def _entitlement_refusal_stream(exc: EntitlementError):
    """One-shot SSE stream for a request refused by the RESEARCH SKU gate.

    Follows the same terminal convention as an LLM failure (see R17.5/R5.5 in
    ``event_generator``): a single ``ERROR`` frame and nothing after it — no
    ``DECISION`` and no ``RUN_FINISHED``. Critically, no graph node runs, no LLM
    is called and no market data is fetched, so an unentitled caller receives no
    research output whatsoever.

    The payload carries ``code`` so the desktop can render an upgrade prompt
    instead of a retry prompt — this is a policy refusal, not a transient fault.
    """
    yield format_sse(
        ERROR,
        {"error": str(exc), "code": exc.code, "entitlement_required": True},
    )


def _guard_research(user_id, mode, *, kind=None, thread_id=None):
    """Apply the RESEARCH entitlement gate, returning a refusal response or None.

    Returns a ``StreamingResponse`` carrying the refusal when the request is not
    entitled, or ``None`` when it may proceed. Callers must return the response
    immediately — before constructing graph input.

    A refusal is logged to the interaction log as its own terminal outcome
    (compliance blocker P5) when ``kind`` is supplied. That row is what turns Gate
    0→1's "no recommendation surface reachable by an unlicensed user" from a claim
    into evidence: the log shows the gate refusing, with the user and the time.
    """
    try:
        require_research_entitlement(user_id, mode)
    except EntitlementError as exc:
        print(f"[entitlements] REFUSED mode={mode} user={user_id or '<none>'}: {exc}")
        svc_metrics_entitlement_refused()
        if kind is not None:
            _log_outcome(
                kind,
                "refused_entitlement",
                thread_id=thread_id,
                user_id=user_id,
                mode=mode,
                detail=str(exc),
            )
        return StreamingResponse(
            _entitlement_refusal_stream(exc), media_type="text/event-stream"
        )
    return None


def svc_metrics_entitlement_refused() -> None:
    """Best-effort refusal counter. Never raises into the request path."""
    try:
        counter = getattr(svc_metrics, "entitlement_refused", None)
        if callable(counter):
            counter()
    except Exception:  # noqa: BLE001
        pass


async def _tee_publish(thread_id: str, gen):
    """Wrap an ``event_generator`` SSE iterator so every frame it yields is ALSO
    published to the per-thread fan-out hub (for re-attached GET /stream clients),
    then yielded unchanged to the original HTTP caller. Passthrough: the bytes the
    direct caller receives are identical whether or not anyone is subscribed."""
    async for frame in gen:
        _publish_frame(thread_id, frame)
        yield frame

@app.post("/run")
async def run_agent(payload: RunRequest):
    """
    Start or continue the Deep Quant LLM ReAct loop, returning an SSE stream.

    Gated by the RESEARCH SKU entitlement (compliance blocker P1) for every mode
    except VERIFY. The check runs first, before any graph input is built, so an
    unentitled caller triggers no analysis at all.

    The request is logged to the tamper-evident interaction log (compliance
    blocker P5) BEFORE the gate, so a refused request leaves a trace too — a log
    that recorded only permitted traffic could not show the gate working.
    """
    _log_request(
        interaction_log.KIND_RUN,
        thread_id=payload.thread_id,
        user_id=payload.user_id,
        content=payload.message,
        mode=payload.mode,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        profile=payload.profile,
        model=payload.model,
    )
    refusal = _guard_research(
        payload.user_id,
        payload.mode,
        kind=interaction_log.KIND_RUN,
        thread_id=payload.thread_id,
    )
    if refusal is not None:
        return refusal

    initial_state = {
        "messages": [("user", payload.message)],
        "mode": payload.mode,
        "symbol": payload.symbol,
        "manual_trade": payload.manual_trade,
        "timeframe": payload.timeframe,
        "profile": payload.profile,
        "fno_expiry": payload.fno_expiry,
        "model": payload.model,
    }
    gen = event_generator(payload.thread_id, graph_input=initial_state, user_id=payload.user_id, kind="run")
    # Best-effort telemetry tee (passthrough; falls back to bare gen on any failure).
    gen = _observe(
        payload.thread_id,
        "run",
        gen,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        mode=payload.mode,
    )
    # Fan-out tee: also publish frames to any re-attached GET /stream client so a
    # later server-initiated resume (heartbeat/target) can reach the desktop even
    # after this /run stream ends at the watch pause.
    gen = _tee_publish(payload.thread_id, gen)
    return StreamingResponse(gen, media_type="text/event-stream")

@app.post("/resume")
async def resume_agent(payload: ResumeRequest):
    """
    Resumes a paused state graph run and returns the subsequent execution as an SSE stream.

    Gated as RESEARCH (compliance blocker P1). ``ResumeRequest`` carries no mode,
    but only a run that armed a ``watch_price_condition`` can be paused, and that
    tool is available solely to the analysis modes — VERIFY does not arm watches
    and QA has it disabled outright. A resume therefore always continues a
    RESEARCH run, so it is gated unconditionally rather than inferring a mode.

    Logged to the interaction log (compliance blocker P5) before the gate. A
    watcher-triggered resume is a communication to the client that the client did
    not ask for, which makes it exactly the kind of event the distribution record
    needs to contain: ``trigger_kind`` says what woke it.
    """
    _log_request(
        interaction_log.KIND_RESUME,
        thread_id=payload.thread_id,
        user_id=payload.user_id,
        content=f"trigger_kind={payload.trigger_kind}",
    )
    refusal = _guard_research(
        payload.user_id,
        "FIND",
        kind=interaction_log.KIND_RESUME,
        thread_id=payload.thread_id,
    )
    if refusal is not None:
        return refusal

    config = {"configurable": {"thread_id": payload.thread_id}}
    state = graph.get_state(config)
    if not state.next:
        raise HTTPException(
            status_code=400,
            detail=f"Thread_id '{payload.thread_id}' is not in a paused/interruptible state."
        )

    gen = event_generator(
        payload.thread_id,
        resume_command=Command(resume={
            "candle": payload.triggered_candle,
            "trigger_kind": payload.trigger_kind,
        }),
        user_id=payload.user_id,
        kind="resume",
    )
    # Best-effort telemetry tee. ResumeRequest carries no symbol/timeframe/mode
    # (those belong to the originating /run and are folded into the same Session
    # by thread_id), so only trigger_kind is tagged here.
    gen = _observe(
        payload.thread_id,
        "resume",
        gen,
        trigger_kind=payload.trigger_kind,
    )
    # Fan-out tee: the headless tool-server watcher POSTs /resume and discards the
    # returned stream, so publishing here is what actually delivers heartbeat /
    # target resumes to the desktop's re-attached GET /stream subscriber.
    gen = _tee_publish(payload.thread_id, gen)
    return StreamingResponse(gen, media_type="text/event-stream")

@app.get("/stream/{thread_id}")
async def stream_thread(thread_id: str, request: Request):
    """Long-lived re-attach channel for a thread's server-initiated resumes.

    The desktop opens this AFTER its /run stream ends in a paused (watching)
    state and keeps it open for the whole watching lifecycle. Every frame the
    fan-out hub publishes for ``thread_id`` — from any /resume the headless
    watcher triggers (heartbeat or target) — is relayed here, so those resumes
    reach the UI over the same deep-quant-stream event as the live run.

    A keepalive comment is sent on idle so proxies/clients don't time the
    connection out during a long wait. The subscriber is always removed on
    disconnect (client close or server shutdown), and an empty thread bucket is
    pruned so the hub never leaks."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _SUBSCRIBERS.setdefault(thread_id, set()).add(queue)
    _refresh_subscriber_gauge()

    async def relay():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    frame = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield frame
                except asyncio.TimeoutError:
                    # SSE keepalive comment (ignored by the parser) to hold the
                    # connection open through long watch waits.
                    yield ": keepalive\n\n"
        finally:
            subs = _SUBSCRIBERS.get(thread_id)
            if subs is not None:
                subs.discard(queue)
                if not subs:
                    _SUBSCRIBERS.pop(thread_id, None)
            _refresh_subscriber_gauge()

    return StreamingResponse(relay(), media_type="text/event-stream")

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

    Q&A is a RESEARCH-SKU surface: answering questions about a committed trade
    elaborates a recommendation, so it is gated on the entitlement before any
    graph work begins (compliance blocker P1).

    Both halves of the turn are logged (compliance blocker P5): the question here,
    and the answer at the terminal branch of ``_run_events`` — including the
    personalisation category when the P8a guardrail refused the turn without
    calling the model. A Q&A record with only the question would be the half that
    matters least.
    """
    _log_request(
        interaction_log.KIND_QA,
        thread_id=payload.thread_id,
        user_id=payload.user_id,
        content=payload.question,
        mode="QA",
        model=payload.model,
    )
    refusal = _guard_research(
        payload.user_id,
        "QA",
        kind=interaction_log.KIND_QA,
        thread_id=payload.thread_id,
    )
    if refusal is not None:
        return refusal

    qa_input = {
        "messages": [("user", payload.question)],
        "mode": "QA",
        "model": payload.model,
    }
    return StreamingResponse(
        event_generator(payload.thread_id, graph_input=qa_input, user_id=payload.user_id, kind="qa"),
        media_type="text/event-stream"
    )

@app.post("/cancel")
async def cancel_agent(payload: CancelRequest):
    """
    Request cancellation of an in-flight /run for ``thread_id``.

    Marks the thread cancelled so the live ``event_generator`` breaks out of the
    ``graph.astream`` loop at its next step boundary and emits a terminal
    RUN_FINISHED(status="cancelled"). This is idempotent and safe to call for an
    unknown/already-finished thread_id (the flag is simply discarded when no run
    consumes it — the set is cleared in the generator's ``finally``; a stale flag
    for a thread that never runs is harmless). The Rust proxy also aborts its own
    streaming task, so this endpoint is the cooperative half of a two-sided stop.

    Logged to the interaction log (compliance blocker P5) as a request row. The
    matching terminal row is written by the run's own generator when it breaks out,
    so a cancelled interaction reads as: request → cancel request → outcome
    ``cancelled``. There is no entitlement gate here: stopping is always allowed.
    """
    _log_request(
        interaction_log.KIND_CANCEL,
        thread_id=payload.thread_id,
    )
    _CANCELLED.add(payload.thread_id)
    svc_metrics.cancellation_requested()
    print(f"[cancel] Cancellation requested for thread={payload.thread_id}")
    return {"status": "cancelling", "thread_id": payload.thread_id}

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


def _latest_snapshot_ts(underlying: str):
    """Most-recent ``option_chain_snapshots`` capture timestamp for ``underlying``.

    Reads the single newest snapshot timestamp across *all* expiries for the
    underlying (via the same F2 read primitive) so an ``Unavailable_Marker`` can
    surface ``last_snapshot_ts`` even when the nearest expiry itself has no rows
    (Requirement 2.2). Returns the epoch-ms timestamp, or ``None`` (never raises)
    when no prior snapshot exists or the read degrades.
    """
    try:
        u = _escape_sql_literal(underlying)
        rows = _questdb_select(
            "SELECT max(cast(snapshot_ts AS LONG)) FROM option_chain_snapshots "
            f"WHERE underlying='{u}'"
        )
    except Exception:  # noqa: BLE001 — degrade to sentinel, never raise
        return None
    if not rows:
        return None
    try:
        raw = rows[0][0]
        if raw is None:
            return None
        # F1 stores snapshot_ts in epoch micros; ChainSnapshot exposes epoch ms.
        return int(raw) // 1000
    except (ValueError, TypeError, IndexError):
        return None


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
    """Time and classify a snapshot assembly, delegating the work unchanged.

    The outcome label is read back off the payload's own ``reason_code`` rather
    than tallied alongside it, so the metric cannot disagree with what the F&O
    panel actually received. Everything other than ``ok`` is an honest unavailable
    marker, not an exception — the panel renders empty and the cause is visible
    only here.

    This endpoint is a synchronous ``def``, so FastAPI runs it on the threadpool;
    a slow QuestDB read shows up as latency here rather than blocking the event
    loop and stalling live runs with it.
    """
    started = time.monotonic()
    try:
        payload = _build_options_snapshot(underlying, expiry)
    except Exception:
        # The helper is written not to raise, but an exception escaping it would
        # be exactly the failure worth seeing. Recorded, then re-raised unchanged
        # so behaviour is identical to before instrumentation.
        svc_metrics.options_snapshot("error", time.monotonic() - started)
        raise
    outcome = payload.get("reason_code", "ok") if payload.get("unavailable") else "ok"
    svc_metrics.options_snapshot(outcome, time.monotonic() - started)
    return payload


def _build_options_snapshot(underlying: str, expiry: str = ""):
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
        marker = {
            "underlying": underlying,
            "expiry": "",
            "unavailable": True,
            "reason": f"no chain snapshot available for {underlying}",
            # Machine-readable cause: no expiry could be resolved for the
            # underlying (Requirement 2.2). Human `reason` preserved above.
            "reason_code": "no_expiry",
        }
        # Surface the most-recent capture across all expiries when one exists so
        # the UI can show "most recent as of …" even here (Requirement 2.2).
        last_ts = _latest_snapshot_ts(underlying)
        if last_ts is not None:
            marker["last_snapshot_ts"] = last_ts
        return marker

    # 2. Read the chain strikes via the existing F2 read layer.
    latest, _prior = read_latest_and_prior_snapshot(underlying, resolved_expiry)
    if latest is None:
        marker = {
            "underlying": underlying,
            "expiry": resolved_expiry,
            "unavailable": True,
            "reason": (
                f"no chain snapshot available for {underlying} / {resolved_expiry}"
            ),
            # Machine-readable cause: an expiry resolved but no snapshot rows
            # exist for it (Requirement 2.2). Human `reason` preserved above.
            "reason_code": "no_snapshot",
        }
        # A prior snapshot may still exist for the underlying under a different
        # expiry — surface its timestamp when available (Requirement 2.2).
        last_ts = _latest_snapshot_ts(underlying)
        if last_ts is not None:
            marker["last_snapshot_ts"] = last_ts
        return marker

    # 3. Compute analytics (F2). A snapshot exists, so if F2 still degrades to a
    #    marker (e.g. spot unavailable) pass it through with the last snapshot
    #    timestamp so the UI can show the most-recent state (Requirement 8.4).
    analytics = compute_options_analytics(underlying, resolved_expiry)
    if analytics.get("unavailable"):
        marker = dict(analytics)
        marker["last_snapshot_ts"] = latest.snapshot_ts
        # Machine-readable cause: a snapshot exists but F2 analytics degraded
        # (e.g. spot unavailable) (Requirement 2.2). Human `reason` preserved.
        marker["reason_code"] = "analytics_degraded"
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
    host = os.getenv("DEEP_QUANT_HOST", "0.0.0.0")
    port = int(os.getenv("DEEP_QUANT_PORT", "8086"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
