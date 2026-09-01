import asyncio
import time
import uvicorn
import os
from contextlib import asynccontextmanager
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
#
# `import graph as graph_module` rather than `from graph import graph`, and every use
# below is `graph_module.graph`. That is load-bearing, not style: the durable
# checkpointer must be built inside the running event loop
# (`AsyncSqliteSaver.__init__` calls `asyncio.get_running_loop()`), so the lifespan
# below REBINDS `graph_module.graph` at startup. A `from graph import graph` binding is
# taken once at import and would keep pointing at the MemorySaver-backed graph — the
# service would look like it had a durable checkpointer while still losing every
# thread on restart, which is the exact failure this phase exists to remove and the
# kind that tests using the module attribute would not catch.
import graph as graph_module  # noqa: E402 - see the note above
from graph import set_run_llm_credentials  # noqa: E402 - pure function, safe to bind

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

# Verified caller identity. ``user_id`` arriving in the request body is
# self-asserted — the browser puts it there and nothing checks it — which is
# survivable while this service only streams analysis but not once sessions and
# transcripts are stored per user. These resolve the caller from a MAC'd assertion
# minted by the Next tier after it has verified the httpOnly session cookie.
#
# Imported eagerly and not defensively, for the same reason as the entitlement
# gate: an import failure must break the service loudly rather than silently
# degrade to trusting the body.
import internal_identity
from internal_identity import require_service, resolve_user

# Durable LangGraph checkpointer. Pure configuration + lifecycle; it imports nothing
# heavy at module scope (the saver package is imported lazily so a deployment without it
# degrades to MemorySaver rather than failing to import `main`).
import checkpointer

# Durable transcript writer. Threaded through `_run_events` like `tracker` is, because
# every emit site already holds the (name, payload) pair. A no-op when there is no run row
# or the flag is off, so no call site branches on whether persistence is enabled.
import stream_persist

# Tamper-evident interaction log (compliance blocker P5): what was published, to
# whom, and when. Imported eagerly for the same reason as the entitlement gate —
# a silently disabled audit log is worse than a crash, because it looks like
# compliance. It depends only on the standard library, so there is nothing here
# that can realistically fail to import.
import interaction_log

# Event_Source adapter for `/events/calendar` (see that route). Imported eagerly:
# it has no import-time I/O and no optional dependency, so a failure here would be
# a genuine packaging fault worth surfacing at boot rather than on first use.
import event_calendar

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

def _reconcile_stale_runs() -> None:
    """Mark runs that claim to be live but cannot be, now that a restart has happened.

    The anti-fabrication pass. A process that dies mid-stream leaves a run row saying
    ``running`` and an assistant message saying ``streaming``; after a restart there is
    no producer for either, so presenting them unchanged would show a half-written
    answer as if it were still arriving — and, once complete, as if it had succeeded.
    The durable checkpointer is what makes the distinction decidable: a run whose thread
    still reports a pending ``next`` is genuinely resumable and stays ``watching``,
    while one that does not is ``truncated``.

    A no-op until the session store lands (migration plan T3.1). Wired here in the same
    commit as the durable checkpointer because it is the checkpointer that makes it
    possible, and because a reconciliation pass that gets added later, separately, is one
    that gets forgotten.
    """
    try:
        import session_store
    except ImportError:
        # Only reachable if the module were removed. Kept as a guard rather than a bare
        # import so a rollback that deletes session_store.py degrades to "no
        # reconciliation" instead of preventing the service from starting.
        return
    try:
        adjusted = session_store.reconcile_stale_runs(graph_module.graph)
        print(f"[checkpointer] startup reconciliation: {adjusted} run(s) marked truncated.")
    except Exception as exc:  # noqa: BLE001 - never block startup on this
        print(f"[checkpointer] WARN: startup reconciliation failed ({exc}).")


def _prune_run_events() -> None:
    """Drop glass-box transcripts for finished runs past the retention window.

    At startup rather than on a timer, deliberately. ``run_events`` is the only unbounded
    table here (hundreds to thousands of rows per run), but it grows at the pace of user
    analyses, not of market ticks — so a sweep per deploy is ample, and it avoids adding a
    background task to a service whose event loop is the thing that must not be blocked.
    If this service ever runs for months without a deploy, that is the point to add a timer.

    Application data only. ``compliance.db`` has a five-year retention duty and its tables
    ABORT on DELETE; the pruner opens ``SESSIONS_DB_PATH`` and nothing else, and there is a
    test asserting the compliance file's hash is unchanged across a prune.

    Sessions, runs and messages are never pruned — losing an old transcript costs
    frame-by-frame replay, losing the conversation would defeat the point.
    """
    try:
        import session_store

        removed = session_store.prune_run_events()
        if removed:
            print(
                f"[sessions] pruned {removed} transcript frame(s) older than "
                f"{session_store.retention_days()} day(s)."
            )
    except Exception as exc:  # noqa: BLE001 - never block startup on housekeeping
        print(f"[sessions] WARN: transcript pruning skipped ({exc}).")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Install the durable LangGraph checkpointer for the life of the server.

    This is the only place a durable checkpointer CAN be installed. Measured:
    ``AsyncSqliteSaver.__init__`` calls ``asyncio.get_running_loop()``, so it cannot be
    built at import time, and the synchronous ``SqliteSaver`` raises
    ``NotImplementedError`` from ``aget_tuple`` — which is fatal here because the graph
    runs exclusively through ``astream``. See ``checkpointer.py`` for both traces.

    ``graph_module.graph`` is REBOUND rather than passed around, so nothing downstream
    has to learn about checkpointing; ``main.py`` reaches the graph by attribute for
    precisely this reason.

    Degrades rather than refuses. If the checkpoint DB is unconfigured or unopenable the
    MemorySaver-backed graph stays in place, because refusing to start would turn a
    bounded degradation the service has always had into a total outage. What it must not
    do is degrade *quietly* — that was the actual defect — so the fallback says exactly
    what is lost.
    """
    durable = checkpointer.DurableCheckpointer()
    async with durable as saver:
        if saver is not None:
            graph_module.graph = graph_module.compile_with(saver)
            print(
                f"[checkpointer] ok durable LangGraph checkpoints at {durable.path} -> "
                f"Q&A grounding and paused watch runs now survive a restart."
            )
            print(checkpointer.describe_hardening())
            _reconcile_stale_runs()
            _prune_run_events()
        else:
            # `durable.reason` is set by __aenter__, which is why the instance is held
            # rather than re-created here: a fresh DurableCheckpointer() has reason=None
            # and would report "unconfigured" for a database that failed to OPEN.
            print(
                f"[checkpointer] !! IN-MEMORY checkpoints ({durable.reason or 'unconfigured'}) "
                f"-> thread state is LOST on restart, so /qa answers ungrounded and "
                f"/resume returns 400 after a redeploy. Set LANGGRAPH_CHECKPOINT_DB to a "
                f"path under the durable volume."
            )
        yield


app = FastAPI(title="LangGraph Deep Quant Loop Service", lifespan=lifespan)


def _mount_session_api() -> None:
    """Mount the Find Quant session/run/message routes when the flag is on.

    Conditional at import because a router cannot be mounted per request, so the flag is
    a container restart — the same cost as every other switch here. With it off the routes
    are genuinely absent (404), not merely refusing, so the surface cannot be probed at
    all before it is meant to exist.

    Guarded: a failure to import the router must not stop the service from serving
    analysis, which is what it does today and what every existing client depends on.
    """
    try:
        import session_api
    except Exception as exc:  # noqa: BLE001
        print(f"[sessions] WARN: session API unavailable ({exc}); routes not mounted.")
        return
    if not session_api.sessions_enabled():
        print(
            "[sessions] note session API not mounted "
            "(DEEP_QUANT_SESSIONS_ENABLED is off). /sessions and /runs return 404."
        )
        return
    app.include_router(session_api.router)
    print("[sessions] ok session API mounted: /sessions, /runs")


_mount_session_api()


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

# Refuse to start with identity enforcement ON and no usable secret. Deliberately at
# import, before the app serves anything, and deliberately fatal: a session store
# guarded by an absent secret would fail every request closed at the boundary, which
# presents as a total outage with an unrelated-looking cause. Naming the real problem
# once, here, is worth more than a WARN nobody correlates. A no-op when enforcement
# is off, which is the default.
internal_identity.assert_startup_config()


def _ensure_session_store() -> None:
    """Create the Find Quant Trade session schema at startup.

    So the first request meets a ready database instead of paying for the DDL inside a
    user-facing call, and — more usefully — so an unwritable path is discovered at boot
    rather than mid-stream, where it would surface as a lost transcript.

    Guarded like the compliance stores: this is user-visible conversation data, not a
    regulatory control, so an unwritable store degrades the feature rather than
    preventing the service from serving analysis. The WARN is the operator's signal.
    """
    try:
        import session_store

        session_store.ensure_store()
        counts = session_store.stats()
        if counts:
            print(
                f"[sessions] ok store ready at {session_store.db_path()} "
                f"(sessions={counts.get('sessions', 0)}, runs={counts.get('runs', 0)}, "
                f"messages={counts.get('messages', 0)}, events={counts.get('run_events', 0)})"
            )
    except Exception as exc:  # noqa: BLE001
        print(
            f"[sessions] WARN: could not initialise the session store ({exc}); "
            f"Find Quant Trade sessions will not persist."
        )


_ensure_session_store()


def _report_state_paths() -> None:
    """Log where every durable database actually is, and warn when it is not durable.

    This exists because the failure it detects is invisible. ``docker-compose.prod.yml``
    declared no volume for this service, so all four SQLite files were written into a
    container layer and destroyed on every redeploy — including ``compliance.db``,
    whose append-only hash chain silently restarted from genesis each time. A fresh
    empty database is indistinguishable from a working one, so nothing ever said so.

    Paths are taken from the OWNING modules rather than re-derived from the
    environment here. ``hashchain.db_path()`` reads ``COMPLIANCE_DB_PATH`` per call,
    ``journal.JOURNAL_DB_PATH`` captures its env at import, and telemetry resolves
    through its own config; asking each module where it will actually write is the
    only way this report cannot drift from reality. A report that confidently names
    the wrong file is worse than no report.

    ``local`` keys off whether the durable directory EXISTS, which is a real
    discriminator rather than a guess: the Dockerfile ``mkdir -p /data`` means the
    directory is always present inside the container whether or not the volume got
    mounted, so a missing ``/data`` means a developer's checkout and a present
    ``/data`` with a non-durable DB path means a genuinely misconfigured deployment.

    Guarded end to end — this is observability, and it must never be the reason the
    service fails to serve.
    """
    try:
        import state_paths

        entries = []

        # Compliance (P2 recommendations + P5 interactions) — the critical one.
        try:
            import hashchain

            entries.append(state_paths.StateEntry("compliance (P2/P5)", hashchain.db_path(), critical=True))
        except Exception as exc:  # noqa: BLE001
            print(f"[state] WARN: could not resolve the compliance path ({exc}).")

        # Find Quant Trade sessions/runs/messages. Asked of the owning module rather
        # than re-derived from the environment, so the report cannot name a different
        # file from the one that is actually written.
        try:
            import session_store

            entries.append(
                state_paths.StateEntry("sessions (find-quant)", session_store.db_path())
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[state] WARN: could not resolve the session store path ({exc}).")

        # Durable LangGraph checkpoints. Unset => MemorySaver, reported honestly after
        # the inventory rather than as a missing file (migration plan T2.1).
        _ckpt = (os.getenv("LANGGRAPH_CHECKPOINT_DB") or "").strip()
        if _ckpt:
            entries.append(state_paths.StateEntry("langgraph checkpoints", _ckpt))

        try:
            import journal

            entries.append(state_paths.StateEntry("trade journal", journal.JOURNAL_DB_PATH))
        except Exception as exc:  # noqa: BLE001
            print(f"[state] WARN: could not resolve the journal path ({exc}).")

        if telemetry is not None:
            try:
                entries.append(
                    state_paths.StateEntry("telemetry", telemetry.resolve_telemetry_config().db_path)
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[state] WARN: could not resolve the telemetry path ({exc}).")

        dirs = state_paths.state_dirs()
        local = not any(os.path.isdir(d) for d in dirs)
        state_paths.report_state_paths(entries, dirs, local=local)

        # Emitted after the inventory so the report reads as one block. Plain ASCII:
        # these lines are read through `docker compose logs`, where an em-dash on a
        # non-UTF-8 console arrives as mojibake.
        if not _ckpt:
            print(
                "[state] !! langgraph checkpoints: IN-MEMORY (MemorySaver) -> thread state, "
                "Q&A grounding and paused watch runs are LOST on restart, so /qa answers "
                "ungrounded and /resume returns 400 after a redeploy. Set "
                "LANGGRAPH_CHECKPOINT_DB to a path under the durable volume."
            )
    except Exception as exc:  # noqa: BLE001 - never block startup on a log line
        print(f"[state] WARN: state path report unavailable ({exc}).")


_report_state_paths()


# ── Session ownership (multi-session migration) ──────────────────────────────
#
# Before this, `GET /stream/{thread_id}` returned a thread's entire research stream to
# anyone who knew the id, and `POST /cancel` took no user id at all — any caller could
# stop any run. Both were survivable only because thread ids were ephemeral and nothing
# was stored per user. Once conversations persist, they are not.
#
# The pattern throughout: resolve the run from the store, compare `run["user_id"]` to the
# VERIFIED caller, and answer 404 — never 403 — on a mismatch. `runs.user_id` is
# denormalised for exactly this, so each check is one indexed read with no join, which
# matters on `/stream`, a long-lived attach on the hot path.


def _sessions_active() -> bool:
    """Whether the session store is participating in the request path.

    When off, the ownership checks below are skipped and every route behaves exactly as
    it did pre-migration. That is what lets Phase 4 ship dark.
    """
    try:
        import session_api

        return session_api.sessions_enabled()
    except Exception:  # noqa: BLE001
        return False


def _require_session() -> bool:
    """Whether `/run` and `/qa` refuse a request that names no session.

    Flipped last (migration plan T11.1), together with identity enforcement. Until then a
    legacy body still works, so a client mid-deploy is not broken by a server restart.
    """
    return (os.getenv("DEEP_QUANT_REQUIRE_SESSION") or "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _owned_session(session_id: str, user_id):
    """The caller's session, or a 404 HTTPException.

    404 rather than 403 for the same reason as the session API: a 403 confirms the id
    exists, which makes the endpoint an enumeration oracle.
    """
    import session_store

    if not user_id:
        raise HTTPException(status_code=401, detail="authentication required")
    session = session_store.get_session_for_user(session_id, user_id)
    if session is None or session["status"] == session_store.SESSION_DELETED:
        raise HTTPException(status_code=404, detail="session not found")
    return session


def _owned_run_for_thread(thread_id: str, user_id):
    """The run behind ``thread_id``, if this caller owns it. ``None`` when unknown.

    Three distinct outcomes, deliberately:

      * a run exists and the caller owns it  -> the run
      * a run exists and they do not         -> 404 (raised)
      * NO run row at all                    -> ``None``

    The third case is what keeps the legacy client working. Threads created before this
    phase — or created by a `/run` that carried no `session_id` — have no run row, so
    there is no owner to compare against and refusing them would break every in-flight
    watch across the deploy. It is a real gap, it closes when `DEEP_QUANT_REQUIRE_SESSION`
    is flipped and every thread has a row, and it is narrower than the status quo where
    even KNOWN threads were unprotected.
    """
    import session_store

    run = session_store.get_run_by_thread(thread_id)
    if run is None:
        return None
    if not user_id or run["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="not found")
    return run


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
    # OPTIONAL as of the multi-session migration, and that is a compatibility decision
    # rather than laxness. The browser used to mint this as
    # `thread_${symbol}_${Date.now()}` — guessable to the second, which mattered because
    # GET /stream/{thread_id} had no ownership check. It is now minted SERVER-side by
    # `session_store.create_run` whenever `session_id` is supplied.
    #
    # A body carrying thread_id and no session_id takes the pre-migration path verbatim,
    # so the existing frontend, and any client mid-deploy, keeps working until
    # DEEP_QUANT_REQUIRE_SESSION is flipped.
    thread_id: Optional[str] = None
    # The owning Find Quant session. Required once DEEP_QUANT_REQUIRE_SESSION=1.
    session_id: Optional[str] = None
    # Client idempotency key for the analysis_request message, so a retried press cannot
    # duplicate the user's turn.
    client_msg_id: Optional[str] = None
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
    # Trade_QA_Mode follow-up question. Answered on the SAME thread as the analysis it is
    # about, so the graph's QA node reads that thread's persisted
    # Session_Analysis_Context from the checkpointer (R18.1, R18.5) without re-running.
    #
    # Grounding is now named EXPLICITLY rather than implied by whatever thread the client
    # happened to be holding: supply `session_id`, and optionally `context_run_id` to ask
    # about a specific earlier run. Omitting `context_run_id` grounds in the session's
    # `active_run_id`. The resolved run is recorded on both message rows, so the
    # transcript states its own grounding afterwards instead of leaving it to inference.
    thread_id: Optional[str] = None
    session_id: Optional[str] = None
    context_run_id: Optional[str] = None
    client_msg_id: Optional[str] = None
    question: str
    # Optional LLM model override for this Q&A turn ('' / None => default).
    model: Optional[str] = None
    # Authenticated user id for resolving this user's OpenRouter key.
    user_id: Optional[str] = None

class CancelRequest(BaseModel):
    # User-requested cancellation of an in-flight /run. The proxy also aborts its own
    # streaming task (dropping the HTTP connection); this flag is the belt-and-suspenders
    # path that breaks the graph.astream loop at the next step boundary even before the
    # disconnect is detected server-side.
    #
    # Either identifier works. `run_id` is preferred because it is the one this service
    # minted and can check ownership on directly; `thread_id` is the legacy form and is
    # resolved to a run when one exists.
    thread_id: Optional[str] = None
    run_id: Optional[str] = None

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

async def event_generator(
    thread_id: str,
    graph_input=None,
    resume_command=None,
    user_id=None,
    kind: str = "run",
    run_id=None,
    session_id=None,
):
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
    # The durable transcript. A no-op when `run_id` is absent (the legacy path, where no
    # run row exists) or when DEEP_QUANT_PERSIST_STREAM is off, so callers never branch.
    persist = stream_persist.StreamPersister(run_id, session_id, kind=kind)
    persist.open()
    try:
        async for frame in _run_events(
            thread_id,
            tracker,
            graph_input=graph_input,
            resume_command=resume_command,
            user_id=user_id,
            outcome=outcome,
            persist=persist,
            kind=kind,
        ):
            yield frame
    finally:
        # All three idempotent — a run that reached a terminal event has already
        # recorded its own outcome, so these only take effect when the client
        # dropped the stream before the run finished.
        #
        # `persist.record_disconnect()` is what makes a half-received answer read as
        # `truncated` rather than sitting at `streaming` forever, and it deliberately does
        # NOT stop the run: the graph keeps executing and the fan-out hub keeps
        # publishing, so a reattaching client still gets the rest.
        tracker.finish("disconnected")
        outcome.record("disconnected")
        persist.record_disconnect()


async def _run_events(
    thread_id: str,
    tracker,
    graph_input=None,
    resume_command=None,
    user_id=None,
    outcome=None,
    persist=None,
    kind: str = "run",
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
    # Recording is threaded through exactly like `tracker`: every emit site already holds
    # the (name, payload) pair, so persisting there costs one line and cannot fall out of
    # step with what was actually sent. Parsing the formatted SSE string back would be both
    # wasteful and a second source of truth about what the frame said.
    _persist = persist if persist is not None else stream_persist.StreamPersister()

    def _stamp(payload):
        """Add the two routing keys every frame must carry.

        ``thread_id`` says WHICH conversation the frame belongs to; ``turn`` says WHAT KIND of
        turn produced it.

        `turn` exists because a Q&A answer streams on the analysis thread — that is how it
        stays grounded in the analysis — and arrives as ordinary REASONING frames. On the wire
        an answer to "why is the stop there?" is then indistinguishable from the agent
        narrating its own scan, so a client has no choice but to append the reply to the
        glass-box transcript. Rehydrating the same session from the stored `qa_answer` rows
        shows that reply as a chat bubble, so one conversation would look different live than
        it does after a reload.

        Applied at the CONSTRUCTION site of every frame rather than at the yield, because each
        site also hands the payload to `_persist.add` — stamping later would persist a frame
        that differs from the one sent, and replay through the same reducer is exactly what
        rehydration depends on.

        Both keys are filled only when absent, so an assembler that already set a more
        specific value keeps it. Additive: every existing consumer ignores unknown keys.
        """
        if not isinstance(payload, dict):
            return payload
        if "thread_id" not in payload:
            payload = {**payload, "thread_id": thread_id}
        if "turn" not in payload:
            payload = {**payload, "turn": kind}
        return payload

    # R17.1: RUN_STARTED is always the first event of the run.
    tracker.stream_event(RUN_STARTED)
    # Additive: `session_id` and `run_id` let a multi-session client bind the run to its
    # session on the very first frame, rather than waiting for a separate response. A
    # consumer that ignores unknown keys is unaffected, which is every existing one.
    started = build_run_started_event(thread_id)
    if _persist.run_id:
        started = {**started, "run_id": _persist.run_id, "session_id": _persist.session_id}
    started = _stamp(started)
    _persist.add(RUN_STARTED, started)
    yield format_sse(RUN_STARTED, started)

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
            _auth_err = _stamp(build_error_event("authentication required: no user_id supplied for LLM access"))
            _persist.add(ERROR, _auth_err)
            _persist.finalize("error", detail=_auth_err.get("error"))
            yield format_sse(ERROR, _auth_err)
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
            _key_error_event = _stamp(build_error_event(f"LLM key unavailable: {_key_err}"))
            _persist.add(ERROR, _key_error_event)
            _persist.finalize("error", detail=_key_error_event.get("error"))
            yield format_sse(ERROR, _key_error_event)
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
            persisted = graph_module.graph.get_state(config)
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
        async for event in graph_module.graph.astream(target_input, config, stream_mode="updates"):
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
                    payload = _stamp(payload)
                    # Persisted AFTER the stamp, so a replayed frame is byte-identical to the
                    # one the live client received — which is what lets rehydration feed
                    # stored frames through the same reducer.
                    _persist.add(name, payload)
                    yield format_sse(name, payload)

        if cancelled:
            # User-requested stop: emit a clean terminal event so the frontend
            # always transitions out of 'running'. No DECISION is emitted.
            tracker.stream_event(RUN_FINISHED)
            tracker.finish("cancelled")
            if outcome is not None:
                outcome.record("cancelled")
            _cancelled_event = _stamp(build_run_finished_event(thread_id, "cancelled"))
            _persist.add(RUN_FINISHED, _cancelled_event)
            _persist.finalize("cancelled")
            yield format_sse(RUN_FINISHED, _cancelled_event)
        else:
            # R17.2/R17.6: a completed or paused run ends with a single terminal
            # RUN_FINISHED event stating which it was.
            state = graph_module.graph.get_state(config)
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
            _finished_event = _stamp(build_run_finished_event(thread_id, status))
            _persist.add(RUN_FINISHED, _finished_event)
            # `paused` is NOT terminal — the watcher will wake this run — so the run moves
            # to `watching` and the assistant message deliberately stays `streaming`,
            # because more of the answer is genuinely still coming. Finalizing here would
            # present a mid-watch partial as a finished analysis.
            if status == "paused":
                _persist.mark_watching()
            else:
                _persist.finalize(status)
            yield format_sse(RUN_FINISHED, _finished_event)

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
        _error_event = _stamp(build_error_event(err_msg))
        _persist.add(ERROR, _error_event)
        _persist.finalize("error", detail=err_msg)
        yield format_sse(ERROR, _error_event)
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


def _open_run_for_request(payload: "RunRequest", user_id):
    """Create the run row for a `/run`, or ``None`` to take the legacy path.

    Returns the run when ``session_id`` is supplied and the session store is active. The
    returned row carries the SERVER-minted ``thread_id``, which is what retires
    ``thread_${symbol}_${Date.now()}``.

    ``None`` means "use the client's ``thread_id``, persist nothing" — the pre-migration
    behaviour, preserved so the shipped frontend and any client mid-deploy keep working.
    ``DEEP_QUANT_REQUIRE_SESSION=1`` turns that into a 422 once every client has caught up.

    The run snapshots symbol/timeframe/profile from the SESSION, not from the request
    body. That is the fix for the whole class of bug where a run executed with whatever
    the global chart happened to be showing: the session owns its trading context, so a
    request cannot ask for one session's conversation to be analysed with another's
    timeframe. The body's values are still what the graph receives — they have to be, or a
    VERIFY of specific numbers would change under the user — but the *recorded* context is
    the session's.
    """
    if not payload.session_id:
        if _require_session():
            raise HTTPException(
                status_code=422,
                detail="session_id is required: create a session with POST /sessions first",
            )
        return None

    if not _sessions_active():
        # A client asking for a session while the store is off is a deployment mismatch,
        # not a client error. Say so rather than silently dropping the association and
        # persisting nothing.
        raise HTTPException(
            status_code=503,
            detail="session persistence is not enabled on this deployment",
        )

    import session_store

    session = _owned_session(payload.session_id, user_id)
    kind = session_store.RUN_VERIFY if (payload.mode or "").upper() == "VERIFY" else session_store.RUN_FIND
    run = session_store.create_run(
        session_id=session["session_id"],
        user_id=user_id,
        kind=kind,
        symbol=session["symbol"],
        timeframe=session["timeframe"],
        profile=session["profile"],
        model=payload.model,
        manual_trade=payload.manual_trade,
    )
    if run is None:
        # Owned a moment ago, gone now — archived or deleted between the two reads.
        raise HTTPException(status_code=404, detail="session not found")

    # The user's turn, recorded before any analysis. Idempotent on client_msg_id so a
    # retried press cannot duplicate it.
    session_store.create_message(
        session_id=session["session_id"],
        role=session_store.ROLE_USER,
        kind=session_store.KIND_ANALYSIS_REQUEST,
        status=session_store.MSG_COMPLETE,
        content=payload.message,
        run_id=run["run_id"],
        client_msg_id=payload.client_msg_id,
    )
    return run


def _resolve_qa_thread(payload: "QARequest", user_id):
    """``(thread_id, run_id, session_id)`` for a Q&A turn. The latter two may be ``None``.

    The thread decides what the answer is grounded in; the run and session decide where the
    turn is recorded. Both are returned together because they are resolved from the same
    lookup and separating them invited a second, divergent one.

    Grounding is now stated by the request rather than inferred from whatever thread the
    client was holding. Resolution order:

      1. ``context_run_id`` — ask about a SPECIFIC earlier run in the session. This is what
         makes multiple FIND runs per session usable: without it, "why that stop?" after a
         second run could only ever mean the second one.
      2. otherwise ``session.active_run_id`` — the newest run, i.e. the common case.
      3. otherwise the legacy ``thread_id`` from the body.

    The old client read its thread id from the flat top-level store field — meaning
    "whatever session is currently on screen" — so switching tabs mid-question asked about
    the wrong analysis. Naming the run removes the ambiguity entirely.
    """
    if payload.session_id:
        if not _sessions_active():
            raise HTTPException(
                status_code=503,
                detail="session persistence is not enabled on this deployment",
            )
        import session_store

        session = _owned_session(payload.session_id, user_id)
        run_id = payload.context_run_id or session["active_run_id"]
        if not run_id:
            raise HTTPException(
                status_code=409,
                detail="this session has no analysis to ask about yet; run FIND or VERIFY first",
            )
        run = session_store.get_run_for_user(run_id, user_id)
        if run is None or run["session_id"] != session["session_id"]:
            # Cross-session grounding would answer from the wrong analysis. Cross-user is
            # already impossible — get_run_for_user is owner-scoped.
            raise HTTPException(
                status_code=422, detail="context_run_id is not a run of this session"
            )
        return run["thread_id"], run["run_id"], session["session_id"]

    if _require_session():
        raise HTTPException(
            status_code=422, detail="session_id is required for Q&A"
        )
    if not payload.thread_id:
        raise HTTPException(
            status_code=422, detail="either session_id (preferred) or thread_id is required"
        )
    # Legacy path. Ownership is checked when a run row exists for the thread; a thread
    # with no row predates this phase and cannot be attributed to anyone.
    #
    # When a row DOES exist the turn is still recorded against it, so a client that has not
    # yet moved to `session_id` still gets a persisted conversation.
    legacy_run = _owned_run_for_thread(payload.thread_id, user_id)
    if legacy_run is not None:
        return payload.thread_id, legacy_run["run_id"], legacy_run["session_id"]
    return payload.thread_id, None, None


async def _tee_publish(thread_id: str, gen):
    """Wrap an ``event_generator`` SSE iterator so every frame it yields is ALSO
    published to the per-thread fan-out hub (for re-attached GET /stream clients),
    then yielded unchanged to the original HTTP caller. Passthrough: the bytes the
    direct caller receives are identical whether or not anyone is subscribed."""
    async for frame in gen:
        _publish_frame(thread_id, frame)
        yield frame

@app.post("/run")
async def run_agent(payload: RunRequest, request: Request):
    """
    Start or continue the Deep Quant LLM ReAct loop, returning an SSE stream.

    Gated by the RESEARCH SKU entitlement (compliance blocker P1) for every mode
    except VERIFY. The check runs first, before any graph input is built, so an
    unentitled caller triggers no analysis at all.

    The request is logged to the tamper-evident interaction log (compliance
    blocker P5) BEFORE the gate, so a refused request leaves a trace too — a log
    that recorded only permitted traffic could not show the gate working.

    The user id comes from ``internal_identity.resolve_user``, not from the body.
    With ``DEEP_QUANT_REQUIRE_IDENTITY`` off it still falls back to
    ``payload.user_id`` — i.e. today's behaviour, unchanged — but a verified
    ``X-StratAI-Identity`` assertion wins when one is present, so the boundary is
    exercised for real before it is enforced. Everything downstream (the interaction
    log, the entitlement gate, the LLM key resolution) reads the RESOLVED id, so
    flipping the flag changes who those see and nothing else.
    """
    user_id = resolve_user(request, payload.user_id, surface="/run")
    run_row = _open_run_for_request(payload, user_id)
    thread_id = run_row["thread_id"] if run_row else payload.thread_id
    if not thread_id:
        raise HTTPException(
            status_code=422,
            detail="either session_id (preferred) or thread_id is required",
        )

    _log_request(
        interaction_log.KIND_RUN,
        thread_id=thread_id,
        user_id=user_id,
        content=payload.message,
        mode=payload.mode,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        profile=payload.profile,
        model=payload.model,
    )
    refusal = _guard_research(
        user_id,
        payload.mode,
        kind=interaction_log.KIND_RUN,
        thread_id=thread_id,
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
    gen = event_generator(
        thread_id,
        graph_input=initial_state,
        user_id=user_id,
        kind="run",
        run_id=run_row["run_id"] if run_row else None,
        session_id=run_row["session_id"] if run_row else None,
    )
    # Best-effort telemetry tee (passthrough; falls back to bare gen on any failure).
    gen = _observe(
        thread_id,
        "run",
        gen,
        symbol=payload.symbol,
        timeframe=payload.timeframe,
        mode=payload.mode,
    )
    # Fan-out tee: also publish frames to any re-attached GET /stream client so a
    # later server-initiated resume (heartbeat/target) can reach the desktop even
    # after this /run stream ends at the watch pause.
    gen = _tee_publish(thread_id, gen)
    return StreamingResponse(gen, media_type="text/event-stream")

@app.post("/resume")
async def resume_agent(payload: ResumeRequest, request: Request):
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

    AUTHENTICATED AS A SERVICE, NOT AS A USER. The caller here is the headless price
    watcher in the Rust tool-server (``tool-server/src/main.rs::post_resume``), which
    has no user session and cannot present a user identity — pretending otherwise
    would be exactly the fake authentication this boundary exists to avoid. It
    presents ``X-StratAI-Service`` instead. ``payload.user_id`` continues to carry the
    id the ORIGINATING run registered (via ``run_context.set_run_user_id``) so the
    LLM key still resolves for the right user; once runs are persisted (migration plan
    T4.2) that id is read from the run row rather than the body.

    Enforcement is off by default, and ``require_service`` returns ``None`` in that
    mode rather than refusing — the watcher must keep working through every phase of
    the rollout. That is a hard requirement, not a convenience.
    """
    require_service(request, surface="/resume")

    # The owning user is READ FROM THE RUN ROW, not taken from the body.
    #
    # The watcher forwards the id the originating run registered via
    # `run_context.set_run_user_id`, which works — but that value has travelled through a
    # background process and back, and it is what this user's LLM key is resolved against.
    # The run row is the authoritative record of whose analysis this is, so it wins when
    # present. The body stays as the fallback for threads that predate the session store,
    # which is what keeps existing price watches resuming across the deploy.
    user_id = payload.user_id
    resume_run_id = None
    resume_session_id = None
    if _sessions_active():
        try:
            import session_store

            run = session_store.get_run_by_thread(payload.thread_id)
            if run is not None:
                user_id = run["user_id"]
                # A resume CONTINUES the original run, so its frames append to that run's
                # transcript rather than starting a new one. That is what makes a
                # watcher-triggered wake show up in the same glass box the user was
                # watching, instead of appearing as an unrelated event.
                resume_run_id = run["run_id"]
                resume_session_id = run["session_id"]
        except Exception as exc:  # noqa: BLE001 - a store fault must not break a resume
            print(f"[resume] WARN: could not resolve the run owner ({exc}); using the body id.")

    _log_request(
        interaction_log.KIND_RESUME,
        thread_id=payload.thread_id,
        user_id=user_id,
        content=f"trigger_kind={payload.trigger_kind}",
    )
    refusal = _guard_research(
        user_id,
        "FIND",
        kind=interaction_log.KIND_RESUME,
        thread_id=payload.thread_id,
    )
    if refusal is not None:
        return refusal

    config = {"configurable": {"thread_id": payload.thread_id}}
    state = graph_module.graph.get_state(config)
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
        user_id=user_id,
        kind="resume",
        run_id=resume_run_id,
        session_id=resume_session_id,
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

def _replay_frames(run, after_seq: int) -> list:
    """Formatted SSE frames for a run after ``after_seq``. Empty when not applicable.

    Returns a list rather than a generator so the read happens BEFORE the relay starts —
    a lazy read inside the relay would run after the subscription and could interleave
    with live frames in an order that depends on scheduling.

    ``after_seq=0`` (the default) replays nothing, so a client that does not ask for
    recovery gets byte-identical behaviour to before this existed. That is what keeps the
    shipped frontend and the Rust watcher unaffected.

    Frames are re-framed through ``format_sse`` from the stored payload, so a replayed
    frame is identical to the one the live client received — the whole point of storing
    the payload rather than a rendering of it.
    """
    if run is None or after_seq <= 0 or not stream_persist.enabled():
        return []
    try:
        import session_store

        events, _last = session_store.list_run_events(run["run_id"], after_seq=after_seq)
        return [format_sse(e["event"], e["data"]) for e in events]
    except Exception as exc:  # noqa: BLE001 - a failed replay must still allow a live attach
        print(f"[stream] WARN: replay from seq={after_seq} failed for {run['run_id']} ({exc}).")
        return []


@app.get("/stream/{thread_id}")
async def stream_thread(thread_id: str, request: Request, after_seq: int = 0):
    """Long-lived re-attach channel for a thread's server-initiated resumes.

    OWNERSHIP IS NOW ENFORCED. This route used to return a thread's ENTIRE research
    stream — reasoning, tool results, the committed trade decision — to anyone who
    presented the id, with no identity check whatsoever. Combined with client-minted
    ``thread_${symbol}_${Date.now()}`` ids, knowing a symbol and roughly when someone ran
    it was enough. Server-minted opaque ids removed the guessing; this removes the
    unauthorised read.

    Answers 404, not 403, so the id is not confirmed. A thread with no run row is still
    served (see ``_owned_run_for_thread``): those predate the session store and cannot be
    attributed to anyone, and refusing them would break every in-flight price watch across
    a deploy. That gap closes when ``DEEP_QUANT_REQUIRE_SESSION`` is flipped.

    The desktop opens this AFTER its /run stream ends in a paused (watching)
    state and keeps it open for the whole watching lifecycle. Every frame the
    fan-out hub publishes for ``thread_id`` — from any /resume the headless
    watcher triggers (heartbeat or target) — is relayed here, so those resumes
    reach the UI over the same deep-quant-stream event as the live run.

    A keepalive comment is sent on idle so proxies/clients don't time the
    connection out during a long wait. The subscriber is always removed on
    disconnect (client close or server shutdown), and an empty thread bucket is
    pruned so the hub never leaks."""
    run = _owned_run_for_thread(thread_id, resolve_user(request, None, surface="/stream"))

    # Subscribe BEFORE replaying, not after.
    #
    # The other order looks natural and loses frames: anything emitted between the end of
    # the replay read and the subscription would fall in the gap this parameter exists to
    # close. Subscribing first means such a frame is queued, and the `seq` filter below
    # discards it if the replay already covered it — duplication is cheap to detect,
    # loss is not detectable at all.
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _SUBSCRIBERS.setdefault(thread_id, set()).add(queue)
    _refresh_subscriber_gauge()

    replay = _replay_frames(run, after_seq)

    async def relay():
        try:
            # Everything the client missed while nobody was attached. Without this,
            # `_publish_frame` returns early on an empty subscriber set, so every frame
            # emitted between the /run stream ending and this GET landing was lost with no
            # way to recover it — a real hole, not a theoretical one, because that gap is
            # exactly when a paused run's client is reconnecting.
            for frame in replay:
                yield frame

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
async def qa_agent(payload: QARequest, request: Request):
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
    user_id = resolve_user(request, payload.user_id, surface="/qa")
    thread_id, qa_run_id, qa_session_id = _resolve_qa_thread(payload, user_id)

    # The user's question, recorded before the answer streams. Idempotent on
    # client_msg_id, so a retried send cannot show the question twice — which is both
    # visible and unfixable after the fact.
    if qa_session_id and stream_persist.enabled():
        try:
            import session_store

            session_store.create_message(
                session_id=qa_session_id,
                role=session_store.ROLE_USER,
                kind=session_store.KIND_QA_QUESTION,
                status=session_store.MSG_COMPLETE,
                content=payload.question,
                run_id=qa_run_id,
                client_msg_id=payload.client_msg_id,
            )
        except Exception as exc:  # noqa: BLE001 - a lost record must not lose the answer
            print(f"[sessions] WARN: Q&A question not recorded ({exc}).")

    _log_request(
        interaction_log.KIND_QA,
        thread_id=thread_id,
        user_id=user_id,
        content=payload.question,
        mode="QA",
        model=payload.model,
    )
    refusal = _guard_research(
        user_id,
        "QA",
        kind=interaction_log.KIND_QA,
        thread_id=thread_id,
    )
    if refusal is not None:
        return refusal

    qa_input = {
        "messages": [("user", payload.question)],
        "mode": "QA",
        "model": payload.model,
    }
    gen = event_generator(
        thread_id,
        graph_input=qa_input,
        user_id=user_id,
        kind="qa",
        run_id=qa_run_id,
        session_id=qa_session_id,
    )
    # Fan-out tee — NEW here, and a bug fix rather than symmetry for its own sake.
    # `/run` and `/resume` were teed; `/qa` was not, so a client attached to
    # GET /stream/{thread_id} (which is every client whose run parked at a price watch)
    # received no Q&A frames at all. On the multi-session frontend the hub is the routing
    # path, so an un-teed Q&A answer would simply never arrive.
    gen = _tee_publish(thread_id, gen)
    return StreamingResponse(gen, media_type="text/event-stream")

@app.post("/cancel")
async def cancel_agent(payload: CancelRequest, request: Request):
    """
    Request cancellation of an in-flight /run.

    OWNERSHIP IS NOW ENFORCED. This endpoint previously took no user id at all, so any
    caller who knew (or guessed — ids were ``thread_${symbol}_${Date.now()}``) a thread id
    could stop somebody else's analysis mid-run. It is checked against the run row now, and
    answers 404 rather than 403 so the id is not confirmed.

    "Stopping is always allowed" still holds for the ENTITLEMENT gate — there is
    deliberately no research check here, because refusing to let an unentitled user stop a
    run they somehow started would leave it burning credits. That is a different question
    from whether the run is theirs to stop.

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
    user_id = resolve_user(request, None, surface="/cancel")

    thread_id = payload.thread_id
    if payload.run_id:
        if not _sessions_active():
            raise HTTPException(
                status_code=503,
                detail="session persistence is not enabled on this deployment",
            )
        import session_store

        run = session_store.get_run_for_user(payload.run_id, user_id) if user_id else None
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        thread_id = run["thread_id"]
    elif thread_id:
        # Legacy form. Ownership is enforced when a run row exists for this thread; a
        # thread with no row predates the session store and has no recorded owner.
        _owned_run_for_thread(thread_id, user_id)
    else:
        raise HTTPException(status_code=422, detail="either run_id or thread_id is required")

    _log_request(
        interaction_log.KIND_CANCEL,
        thread_id=thread_id,
        user_id=user_id,
    )
    _CANCELLED.add(thread_id)
    svc_metrics.cancellation_requested()
    print(f"[cancel] Cancellation requested for thread={thread_id}")
    return {"status": "cancelling", "thread_id": thread_id}

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


# ── Event_Source adapter endpoint ────────────────────────────────────────────


@app.get("/events/calendar")
def events_calendar(symbol: str = ""):
    """Serve one symbol's upcoming Scheduled_Event dates to `get_event_risk`.

    This is the endpoint `EVENT_CALENDAR_API_URL` points at. The agent's reader
    calls it as `GET /events/calendar?symbol=RELIANCE` and parses a list of
    `{symbol, date}` records, which is exactly what this returns; all of NSE's
    quirks (cookie priming, `DD-Mon-YYYY` dates, non-earnings board meetings) are
    handled in `event_calendar.py` so the agent's reader stays vendor-agnostic.

    Served from THIS service rather than a new one on purpose. The address is then
    identical in every environment — `http://127.0.0.1:${DEEP_QUANT_PORT}` — with no
    service discovery, compose topology or DNS to get wrong between local and
    production, which is the usual way a second service diverges between the two.

    A synchronous `def`, so FastAPI runs it on the threadpool: the upstream fetch is
    blocking `httpx`, and running it here would otherwise stall the event loop and
    every live run with it (same reasoning as `/options/snapshot`). This also makes
    the self-call safe — the agent's tools are sync, and LangChain runs a sync tool
    via `run_in_executor`, so the calling thread is a worker and the loop stays free
    to serve this request.

    Status codes are part of the contract, because the agent turns them into two
    DIFFERENT and non-interchangeable readings:

      * `200 []`  -> "no upcoming scheduled event known for symbol" — we looked, the
                     calendar is clear.
      * non-2xx   -> "event source retrieval failed" — we are blind.

    So an upstream failure returns 503 and never an empty list. Answering `[]` when
    the calendar could not be read would report a clear diary for a company that
    might report tomorrow, and the gate would let a multi-session position sit
    straight through the event.
    """
    started = time.monotonic()
    requested = symbol.strip() if isinstance(symbol, str) else ""
    if not requested:
        # A missing symbol is a caller error, not an empty calendar — same
        # distinction as above, so it must not answer 200 [].
        raise HTTPException(status_code=422, detail="symbol query parameter is required")

    try:
        rows, stale = event_calendar.events_for_symbol(requested)
    except event_calendar.EventCalendarUnavailable as exc:
        took = time.monotonic() - started
        print(
            f"[EventCalendar] {requested}: unavailable after {took:.2f}s -> 503 ({exc})"
        )
        raise HTTPException(
            status_code=503, detail=f"event calendar unavailable: {exc}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - an unexpected fault is still "blind"
        took = time.monotonic() - started
        print(f"[EventCalendar] {requested}: unexpected fault after {took:.2f}s -> 503 ({exc})")
        raise HTTPException(
            status_code=503, detail=f"event calendar unavailable: {exc}"
        ) from exc

    print(
        f"[EventCalendar] {requested}: {len(rows)} event(s)"
        f"{' (from stale cache)' if stale else ''} in {time.monotonic() - started:.2f}s"
    )
    return rows


# ── Entrypoint ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("DEEP_QUANT_HOST", "0.0.0.0")
    port = int(os.getenv("DEEP_QUANT_PORT", "8086"))
    uvicorn.run("main:app", host=host, port=port, reload=True)
