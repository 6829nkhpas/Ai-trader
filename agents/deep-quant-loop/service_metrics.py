"""Prometheus instrumentation for the Deep Quant LangGraph service (:9109).

This is the Python port of the Rust ``service-metrics`` crate's *contract* — the
crate itself cannot be linked from here, so what is mirrored is the part that
actually matters to the consumer: identical metric names (``deep_quant_`` prefix),
a ``service`` label on every series, ``/metrics`` ``/health`` ``/ready`` on one
port, and a readiness body whose ``status`` is one of ``ok`` / ``stalled`` /
``idle``. ``status-api`` matches on exactly those names, so renaming one here
drops this service out of the admin panel as "unknown" — which reads identically
to "never deployed".

Why the listener is a separate thread and not a FastAPI route
-------------------------------------------------------------
The interesting failure of this service is a **wedged event loop**: a LangGraph
run blocked in a synchronous call (a QuestDB read, a blocking HTTP client) starves
every coroutine, including any ``@app.get("/metrics")`` handler. Prometheus would
then see a scrape *timeout*, which is indistinguishable from a dead container —
and the one fact worth knowing (the process is alive but not advancing) would be
lost. A plain threaded HTTP server on its own port keeps answering through that,
so the exposition still reports an ageing ``last_work_age_seconds`` and ``/ready``
still flips to 503. That distinction is the whole point of the exercise.

What counts as work
-------------------
A **completed graph step** — one LangGraph node update that produced events —
plus the terminal event of a run. The monitoring plan named "graph run finished"
as the beat site; that grain is too coarse in practice, because a legitimate
FIND-mode run can take ten-plus minutes of LLM turns, and beating only at the end
would make a healthy long run indistinguishable from one wedged on a hung
provider call for the same duration. Beating per node advance separates them: a
run that is progressing keeps the age near zero, a run stuck inside one LLM call
lets it grow.

Why staleness is gated on in-flight runs, not on the clock
----------------------------------------------------------
Unlike the tick pipeline, this service is **demand-driven**: nobody asking for an
analysis is the normal state for most of the day, and silence then is not a
failure. So ``work_expected`` is 1 only while at least one run is in flight, and
staleness is only ever a stall while that holds. Consequently the market session
is reported as *context* but never widens the threshold — a run started at 02:00
should still advance at 02:00, and widening would hide exactly the wedge this
module exists to catch. (The Rust services do widen, correctly: a tick consumer
with nothing to consume is healthy.)

A paused run is not in flight. When the graph pauses at ``watch_price_condition``
the generator returns and the stream ends — the run may sit paused for hours
waiting on a price, and that is a normal outcome (``outcome="paused"``), not a
stall.

Known limitation: this instruments the *transport* — runs, steps, tool calls,
outcomes. It cannot tell a good analysis from a bad one. A model returning
confident nonsense on every run reads here as perfectly healthy; the instruments
for that live in ``telemetry.py`` (outcome classification against realised
prices), not in a liveness surface.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    from prometheus_client.gc_collector import GCCollector
    from prometheus_client.platform_collector import PlatformCollector
    from prometheus_client.process_collector import ProcessCollector

    _PROM_AVAILABLE = True
except Exception as _prom_import_error:  # noqa: BLE001 - never block the service
    _PROM_AVAILABLE = False
    print(
        f"[service_metrics] WARN: prometheus_client unavailable ({_prom_import_error}); "
        "the service runs uninstrumented and will be reported as down."
    )


# ── Identity ─────────────────────────────────────────────────────────────────

SERVICE = "deep-quant"
#: Prometheus metric prefix. Dashes are illegal in metric names; the `service`
#: label keeps the real (compose) name so dashboards match the container.
PREFIX = "deep_quant"
VERSION = "1.0.0"
DEFAULT_METRICS_PORT = 9109

#: Seconds a run may go without advancing a graph node before it reads as stalled.
#: Sized against the slowest legitimate single step — one LLM turn against a
#: reasoning model over a large prompt, which can genuinely take minutes. Env
#: override so a deployment on a slower provider can widen it without a rebuild.
try:
    STALL_SECONDS = float(os.getenv("DEEP_QUANT_STALL_SECONDS", "300"))
except (TypeError, ValueError):
    STALL_SECONDS = 300.0
if not (STALL_SECONDS > 0):
    STALL_SECONDS = 300.0


# ── Bounded label values ─────────────────────────────────────────────────────
# Every label value below is fixed at import so the series exist at zero before
# anything happens. `rate()` over a missing series renders as "no data", which on
# a dashboard is visually identical to a failed scrape — pre-creating them is what
# makes a genuine zero readable as a zero.

RUN_KINDS = ("run", "resume", "qa")

#: Terminal outcomes of a run.
#:  completed    — the graph reached its end.
#:  paused       — the graph is waiting at an interrupt (watch_price_condition).
#:                 A normal, indefinite state; the run is NOT in flight while paused.
#:  cancelled    — the user requested a stop and the loop broke at a step boundary.
#:  error        — the graph raised; an ERROR event was streamed and no DECISION.
#:  auth_error   — no user_id supplied while running in per-user credential mode.
#:  key_error    — the user's LLM key could not be resolved from the backend.
#:  disconnected — the client dropped the stream mid-run (generator closed).
RUN_OUTCOMES = (
    "completed",
    "paused",
    "cancelled",
    "error",
    "auth_error",
    "key_error",
    "disconnected",
)

#: `shared` = a deployment-wide LLM_API_KEY was configured (beta mode);
#: `resolved` = the requesting user's key came back from the backend.
#: The split matters: a deployment silently falling back between the two modes is
#: a billing and isolation bug, and here it is visible as a step change.
KEY_OUTCOMES = ("shared", "resolved", "missing_user", "failed")

#: Mirrors stream_events.py. Kept as a literal rather than imported so this
#: module has no dependency on the graph side of the service (it must be
#: importable and serving before graph.py finishes loading — see main.py).
STREAM_EVENTS = (
    "RUN_STARTED",
    "RUN_FINISHED",
    "ERROR",
    "REASONING",
    "TOOL_CALL_START",
    "TOOL_CALL_RESULT",
    "TOOL_CALL_END",
    "VERIFICATION_STEP",
    "DECISION",
    "BEST_CURRENT_READ",
)

TOOL_STATUSES = ("success", "failure")

#: Mirrors the `reason_code` markers main.py emits from /options/snapshot.
OPTIONS_OUTCOMES = ("ok", "no_expiry", "no_snapshot", "analytics_degraded", "error")

#: Tool names arrive from LLM output, so they are not a closed set — a
#: hallucinated name would otherwise mint a permanent series on every scrape.
#: Names are admitted on first sight up to this cap, after which everything folds
#: into `other`. The cap is well above the ~25 registered tools, so in normal
#: operation nothing is ever folded.
MAX_TOOL_LABELS = 64

#: Run durations span three orders of magnitude here: a QA turn answers in
#: seconds, a FIND-mode debate runs for many minutes. Buckets are spread to keep
#: both ends resolvable rather than piling the whole distribution into +Inf.
RUN_DURATION_BUCKETS = (1, 5, 15, 30, 60, 120, 300, 600, 1200, 2400)

#: The options snapshot is a QuestDB read plus pure analytics — sub-second when
#: healthy, and a slow QuestDB is the thing worth seeing.
SNAPSHOT_DURATION_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)


# ── Market session (context only) ────────────────────────────────────────────

_IST_OFFSET = timedelta(hours=5, minutes=30)
_SESSION_OPEN_MIN = 9 * 60 + 15
_SESSION_CLOSE_MIN = 15 * 60 + 30


def market_session(at: Optional[datetime] = None) -> str:
    """Classify an instant as ``open`` / ``closed`` / ``weekend`` in IST.

    India observes no daylight saving, so the fixed UTC+05:30 shift is exact
    rather than an approximation. The weekday is read AFTER the shift: 20:00 UTC
    on a Friday is already Saturday in IST, and a naive UTC weekday check would
    call that a weekday.

    Both session bounds are inclusive — the closing auction lands on 15:30.

    Reported for context only. Unlike the Rust services this does NOT widen the
    stall threshold; see the module docstring.
    """
    instant = at if at is not None else datetime.now(timezone.utc)
    ist = instant + _IST_OFFSET
    # Saturday = 5, Sunday = 6.
    if ist.weekday() >= 5:
        return "weekend"
    minutes = ist.hour * 60 + ist.minute
    return "open" if _SESSION_OPEN_MIN <= minutes <= _SESSION_CLOSE_MIN else "closed"


# ── Heartbeat ────────────────────────────────────────────────────────────────


class Heartbeat:
    """Tracks when the service last completed a unit of real work.

    Before the first beat the age is measured from process start, so a service
    that boots and never advances a single graph node looks stale immediately
    rather than reporting a comfortable zero.
    """

    def __init__(self) -> None:
        now = time.monotonic()
        self.started = now
        self.last_work: Optional[float] = None
        self.work_count = 0
        # Beats arrive from the event loop thread; the HTTP listener reads them
        # from its own thread. Int/float assignment is atomic under the GIL, but
        # `work_count += 1` is not, and runs are concurrent here.
        self._lock = threading.Lock()

    def beat(self) -> None:
        with self._lock:
            self.last_work = time.monotonic()
            self.work_count += 1

    def has_worked(self) -> bool:
        return self.last_work is not None

    def last_work_age_seconds(self) -> float:
        reference = self.last_work if self.last_work is not None else self.started
        # monotonic() cannot go backwards, but max() costs nothing and makes the
        # invariant explicit for a reader.
        return max(0.0, time.monotonic() - reference)

    def uptime_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started)


# ── The metrics handle ───────────────────────────────────────────────────────


class DeepQuantMetrics:
    """The service's Prometheus surface.

    Construction never raises. If the registry cannot be built (prometheus_client
    missing, a name collision from a second instance in one process) the handle
    degrades to inert: every method becomes a no-op and ``serve()`` returns None.
    That keeps every call site unconditional — instrumentation must never be able
    to take down the service it observes.
    """

    def __init__(self) -> None:
        self.heartbeat = Heartbeat()
        self._runs_in_flight = 0
        self._lock = threading.Lock()
        self._tool_labels: set[str] = set()
        self.inner = self._build() if _PROM_AVAILABLE else None

    # ── Construction ─────────────────────────────────────────────────────────

    def _build(self):
        try:
            registry = CollectorRegistry()

            def gauge(name, help_text, labels=()):
                return Gauge(name, help_text, ("service", *labels), registry=registry)

            def counter(name, help_text, labels=()):
                return Counter(name, help_text, ("service", *labels), registry=registry)

            inner = type("Inner", (), {})()
            inner.registry = registry

            # ── The shared contract ──────────────────────────────────────────
            inner.up = gauge(
                f"{PREFIX}_up",
                "1 while the metrics listener is serving. Absence of the metric "
                "(a scrape failure) is the real down signal.",
            )
            inner.up.labels(SERVICE).set(1)

            inner.build_info = gauge(
                f"{PREFIX}_build_info",
                "Always 1; the labels carry the build identity.",
                ("version",),
            )
            inner.build_info.labels(SERVICE, VERSION).set(1)

            inner.uptime = gauge(
                f"{PREFIX}_uptime_seconds",
                "Seconds since the process started. A resetting value means crash-looping.",
            )
            inner.last_work_age = gauge(
                f"{PREFIX}_last_work_age_seconds",
                "Seconds since a LangGraph run last advanced a node. The primary "
                "working-vs-failing signal, but only meaningful while "
                f"{PREFIX}_work_expected is 1 — see that metric.",
            )
            inner.stall_threshold = gauge(
                f"{PREFIX}_stall_threshold_seconds",
                "Age above which an in-flight run is considered wedged. Exported so "
                "alert rules compare two metrics instead of hardcoding the constant. "
                "NOT widened off-session: a run started at 02:00 must still advance.",
            )
            inner.session = gauge(
                f"{PREFIX}_market_session_open",
                "1 during NSE trading hours (09:15-15:30 IST, weekdays), else 0. "
                "Context for reading run volume; it does not affect the stall threshold.",
            )
            inner.work_expected = gauge(
                f"{PREFIX}_work_expected",
                "1 while at least one run is in flight. This service is demand-driven: "
                "with no runs there is nothing to do, and staleness is only a stall "
                "while this is 1.",
            )
            inner.work_total = counter(
                f"{PREFIX}_work_completed_total",
                "Graph node advances completed since start. Its rate is the throughput "
                "signal; flat while a run is in flight means the run is wedged.",
            )

            # ── Runs ─────────────────────────────────────────────────────────
            inner.runs_started = counter(
                f"{PREFIX}_runs_started_total",
                "Runs begun, by entry point. `run` = a fresh analysis, `resume` = a "
                "watcher-triggered continuation, `qa` = a follow-up question on an "
                "existing thread.",
                ("kind",),
            )
            inner.runs = counter(
                f"{PREFIX}_runs_total",
                "Runs that reached a terminal state, by entry point and outcome. "
                "`paused` is a normal outcome (the graph is waiting on a price), not "
                "a failure. Compare against runs_started_total: a persistent gap means "
                "runs are neither finishing nor erroring — the wedge case.",
                ("kind", "outcome"),
            )
            inner.run_duration = Histogram(
                f"{PREFIX}_run_duration_seconds",
                "Wall-clock duration of a run, from the first streamed event to its "
                "terminal one, by outcome. A `paused` run's duration measures only the "
                "analysis leg, not the wait.",
                ("service", "kind", "outcome"),
                buckets=RUN_DURATION_BUCKETS,
                registry=registry,
            )
            inner.runs_in_flight = gauge(
                f"{PREFIX}_runs_in_flight",
                "Runs currently streaming. Drives work_expected; a value that never "
                "returns to 0 means generators are leaking.",
            )

            # ── Graph internals ──────────────────────────────────────────────
            inner.stream_events = counter(
                f"{PREFIX}_stream_events_total",
                "Glass-box SSE events emitted, by type. DECISION vs RUN_FINISHED "
                "divergence shows how often runs end without committing a trade.",
                ("event",),
            )
            inner.tool_calls = counter(
                f"{PREFIX}_tool_calls_total",
                "Tool invocations observed on the stream, by tool and terminal status. "
                "A tool failing every call is the usual cause of a degraded analysis "
                "that still completes, so this is where that shows up rather than in "
                "the run outcome.",
                ("tool", "status"),
            )
            inner.key_resolutions = counter(
                f"{PREFIX}_llm_key_resolutions_total",
                "LLM credential resolutions per run. `shared` and `resolved` are the "
                "two supported modes; both being non-zero means the deployment is "
                "silently mixing a shared key with per-user keys.",
                ("outcome",),
            )
            inner.cancellations = counter(
                f"{PREFIX}_cancellations_total",
                "User-requested run cancellations accepted at /cancel. Idempotent and "
                "safe for unknown threads, so this counts requests, not stopped runs.",
            )
            inner.subscribers = gauge(
                f"{PREFIX}_stream_subscribers",
                "Clients attached to GET /stream/{thread_id} for watcher re-attach. "
                "Zero while a thread is paused means a watcher resume would reach "
                "nobody — the terminal sits in WATCHING forever.",
            )

            # ── Options snapshot endpoint ────────────────────────────────────
            inner.options_snapshots = counter(
                f"{PREFIX}_options_snapshots_total",
                "GET /options/snapshot results, by reason_code. Everything other than "
                "`ok` is an honest unavailable marker, not an exception — the F&O panel "
                "renders empty and the cause is only visible here.",
                ("outcome",),
            )
            inner.options_duration = Histogram(
                f"{PREFIX}_options_snapshot_duration_seconds",
                "Time to assemble an options snapshot (QuestDB read + F2/F3 analytics). "
                "This endpoint is synchronous, so a slow QuestDB here blocks the whole "
                "event loop and stalls live runs with it.",
                ("service",),
                buckets=SNAPSHOT_DURATION_BUCKETS,
                registry=registry,
            )

            inner.telemetry_available = gauge(
                f"{PREFIX}_telemetry_available",
                "1 when the session-telemetry layer imported successfully. It is "
                "imported defensively and degrades silently by design, so without this "
                "a deployment can lose all trade-outcome recording with no other sign.",
            )
            # Set by main.py once it knows; 0 until then is the honest default.
            inner.telemetry_available.labels(SERVICE).set(0)

            # Pre-create every fixed label series at zero.
            #
            # Counters carrying only the `service` label need this just as much as
            # the multi-label ones — arguably more, since work_completed_total is
            # THE throughput series and a dashboard panel reading "no data" on a
            # freshly restarted service is indistinguishable from one reading "no
            # data" because the scrape failed.
            inner.work_total.labels(SERVICE)
            inner.cancellations.labels(SERVICE)
            for kind in RUN_KINDS:
                inner.runs_started.labels(SERVICE, kind)
                for outcome in RUN_OUTCOMES:
                    inner.runs.labels(SERVICE, kind, outcome)
            for outcome in KEY_OUTCOMES:
                inner.key_resolutions.labels(SERVICE, outcome)
            for event in STREAM_EVENTS:
                inner.stream_events.labels(SERVICE, event)
            for outcome in OPTIONS_OUTCOMES:
                inner.options_snapshots.labels(SERVICE, outcome)
            # Gauges have no natural zero series until first set; publish them now
            # so a panel is never blank on a service that simply has not been
            # asked to do anything yet.
            inner.runs_in_flight.labels(SERVICE).set(0)
            inner.subscribers.labels(SERVICE).set(0)

            # Runtime metrics: how a wedged event loop and a leaking process show
            # up alongside the domain signals. ProcessCollector reads /proc and
            # simply collects nothing off Linux, which is the dev-machine case.
            for collector in (ProcessCollector, PlatformCollector, GCCollector):
                try:
                    collector(registry=registry)
                except Exception as exc:  # noqa: BLE001 - optional enrichment only
                    print(f"[service_metrics] WARN: {collector.__name__} unavailable ({exc}).")

            return inner
        except Exception as exc:  # noqa: BLE001 - instrumentation must never be fatal
            print(
                f"[service_metrics] WARN: metric registry could not be built ({exc}); "
                "the service runs uninstrumented."
            )
            return None

    # ── Sampled gauges ───────────────────────────────────────────────────────

    def _refresh(self) -> None:
        """Pull current values into the sampled gauges. Called on every scrape and
        readiness check rather than continuously, so nothing on the run path pays
        for it."""
        i = self.inner
        if i is None:
            return
        i.up.labels(SERVICE).set(1)
        i.uptime.labels(SERVICE).set(self.heartbeat.uptime_seconds())
        i.last_work_age.labels(SERVICE).set(self.heartbeat.last_work_age_seconds())
        i.stall_threshold.labels(SERVICE).set(STALL_SECONDS)
        i.session.labels(SERVICE).set(1 if market_session() == "open" else 0)
        with self._lock:
            in_flight = self._runs_in_flight
        i.runs_in_flight.labels(SERVICE).set(in_flight)
        i.work_expected.labels(SERVICE).set(1 if in_flight > 0 else 0)

    def render(self) -> bytes:
        """Prometheus text exposition. Empty on an inert handle."""
        if self.inner is None:
            return b""
        self._refresh()
        return generate_latest(self.inner.registry)

    # ── Readiness ────────────────────────────────────────────────────────────

    def readiness(self) -> dict:
        """Whether the service is advancing the work it has.

        ``stalled`` requires a run to actually be in flight — an idle service with
        an hour-old heartbeat is idle, not broken, and reporting otherwise would
        page every night.
        """
        with self._lock:
            in_flight = self._runs_in_flight
        age = self.heartbeat.last_work_age_seconds()
        work_expected = in_flight > 0
        return {
            "stalled": work_expected and age > STALL_SECONDS,
            "work_expected": work_expected,
            "runs_in_flight": in_flight,
            "age_seconds": age,
            "threshold_seconds": STALL_SECONDS,
            "session": market_session(),
            "work_completed": self.heartbeat.work_count,
            "uptime_seconds": self.heartbeat.uptime_seconds(),
        }

    def readiness_json(self) -> dict:
        """The body ``/health`` and ``/ready`` return.

        The key set is the contract ``status-api`` reads; it matches the Rust
        crate's ``Readiness::to_json`` field for field, plus ``runs_in_flight``
        (which is what makes ``work_expected`` interpretable for this service).
        """
        r = self.readiness()
        if r["stalled"]:
            status = "stalled"
        elif r["work_expected"]:
            status = "ok"
        else:
            status = "idle"
        return {
            "service": SERVICE,
            "status": status,
            "market_session": r["session"],
            "work_expected": r["work_expected"],
            "runs_in_flight": r["runs_in_flight"],
            "last_work_age_seconds": round(r["age_seconds"], 1),
            "stall_threshold_seconds": round(r["threshold_seconds"], 1),
            "work_completed": r["work_completed"],
            "uptime_seconds": round(r["uptime_seconds"], 1),
        }

    # ── Recording ────────────────────────────────────────────────────────────

    def run_started(self, kind: str) -> "RunTracker":
        """Register a run and return the handle that records its progress.

        Always returns a tracker, even on an inert handle, so call sites never
        branch.
        """
        kind = kind if kind in RUN_KINDS else "run"
        with self._lock:
            self._runs_in_flight += 1
        if self.inner is not None:
            self.inner.runs_started.labels(SERVICE, kind).inc()
        return RunTracker(self, kind)

    def _run_finished(self, kind: str, outcome: str, duration: float) -> None:
        outcome = outcome if outcome in RUN_OUTCOMES else "error"
        with self._lock:
            # Clamp rather than trusting the caller: a double-finish that drove
            # this negative would leave work_expected stuck at 0 and silently
            # disable stall detection for the life of the process.
            self._runs_in_flight = max(0, self._runs_in_flight - 1)
        if self.inner is None:
            return
        self.inner.runs.labels(SERVICE, kind, outcome).inc()
        self.inner.run_duration.labels(SERVICE, kind, outcome).observe(duration)

    def graph_step(self) -> None:
        """One LangGraph node advanced. The unit of work."""
        self.heartbeat.beat()
        if self.inner is not None:
            self.inner.work_total.labels(SERVICE).inc()

    def stream_event(self, event: str) -> None:
        if self.inner is None:
            return
        if event not in STREAM_EVENTS:
            return  # never mint a series from a name we do not control
        self.inner.stream_events.labels(SERVICE, event).inc()

    def tool_call(self, tool: str, status: str) -> None:
        """Record a completed tool invocation.

        Tool names come from LLM output, so an unbounded set of them could be
        minted. Names are admitted up to ``MAX_TOOL_LABELS`` and everything past
        that folds into ``other``.
        """
        if self.inner is None:
            return
        status = status if status in TOOL_STATUSES else "failure"
        name = str(tool)[:64] if tool else "unknown"
        with self._lock:
            if name not in self._tool_labels:
                if len(self._tool_labels) >= MAX_TOOL_LABELS:
                    name = "other"
                else:
                    self._tool_labels.add(name)
        self.inner.tool_calls.labels(SERVICE, name, status).inc()

    def key_resolution(self, outcome: str) -> None:
        if self.inner is None or outcome not in KEY_OUTCOMES:
            return
        self.inner.key_resolutions.labels(SERVICE, outcome).inc()

    def cancellation_requested(self) -> None:
        if self.inner is not None:
            self.inner.cancellations.labels(SERVICE).inc()

    def set_stream_subscribers(self, count: int) -> None:
        if self.inner is not None:
            self.inner.subscribers.labels(SERVICE).set(count)

    def options_snapshot(self, outcome: str, duration: float) -> None:
        if self.inner is None:
            return
        outcome = outcome if outcome in OPTIONS_OUTCOMES else "error"
        self.inner.options_snapshots.labels(SERVICE, outcome).inc()
        self.inner.options_duration.labels(SERVICE).observe(duration)

    def set_telemetry_available(self, available: bool) -> None:
        if self.inner is not None:
            self.inner.telemetry_available.labels(SERVICE).set(1 if available else 0)

    # ── Listener ─────────────────────────────────────────────────────────────

    def serve(self) -> Optional[ThreadingHTTPServer]:
        """Start the metrics listener on a daemon thread.

        Binds 0.0.0.0 so Prometheus reaches it across the compose network; the
        compose files never publish this port to the host. A bind failure is
        logged and returns None — the service continues without instrumentation
        rather than failing to boot.

        Runs off the asyncio loop deliberately: see the module docstring.
        """
        if self.inner is None:
            return None

        try:
            port = int(os.getenv("METRICS_PORT", str(DEFAULT_METRICS_PORT)))
        except (TypeError, ValueError):
            port = DEFAULT_METRICS_PORT

        metrics = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _send(self, code: int, body: bytes, content_type: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's contract
                path = self.path.split("?", 1)[0]
                if path == "/metrics":
                    self._send(200, metrics.render(), CONTENT_TYPE_LATEST)
                    return
                if path in ("/health", "/ready"):
                    body = metrics.readiness_json()
                    # A stalled service is running — restarting it on a stall
                    # turns a visible problem into a crash loop. /health stays
                    # 200; /ready is the one that fails, which is what makes the
                    # stall actionable without being self-inflicted.
                    code = 503 if (path == "/ready" and body["status"] == "stalled") else 200
                    self._send(code, json.dumps(body).encode(), "application/json")
                    return
                self._send(404, b'{"error":"not found"}', "application/json")

            def log_message(self, *args):  # noqa: D102 - silence per-scrape logging
                # Prometheus scrapes every 30s; logging each one buries the
                # service's own output.
                pass

        try:
            server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        except Exception as exc:  # noqa: BLE001 - never fatal
            print(
                f"[service_metrics] WARN: could not bind metrics port {port} ({exc}); "
                "the service continues without instrumentation."
            )
            return None

        server.daemon_threads = True
        thread = threading.Thread(
            target=server.serve_forever,
            name="deep-quant-metrics",
            daemon=True,
        )
        thread.start()
        print(
            f"[service_metrics] Prometheus surface on :{server.server_address[1]} "
            "(/metrics, /health, /ready)"
        )
        return server


class RunTracker:
    """Records one run's progress and its terminal outcome.

    ``finish`` is idempotent: the SSE generator's ``finally`` block calls it to
    catch a client disconnect, and that must not double-count a run that already
    ended normally — nor drive ``runs_in_flight`` negative, which would leave
    ``work_expected`` stuck at 0 and silently disable stall detection.
    """

    __slots__ = ("_metrics", "_kind", "_started", "_finished", "_lock")

    def __init__(self, metrics: DeepQuantMetrics, kind: str) -> None:
        self._metrics = metrics
        self._kind = kind
        self._started = time.monotonic()
        self._finished = False
        self._lock = threading.Lock()

    def graph_step(self) -> None:
        self._metrics.graph_step()

    def stream_event(self, event: str) -> None:
        self._metrics.stream_event(event)

    def tool_call(self, tool: str, status: str) -> None:
        self._metrics.tool_call(tool, status)

    @property
    def finished(self) -> bool:
        return self._finished

    def finish(self, outcome: str) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
        self._metrics._run_finished(
            self._kind, outcome, time.monotonic() - self._started
        )


#: The process-wide handle. Imported by main.py; a module-level singleton because
#: a second registry in one process would collide on every metric name.
metrics = DeepQuantMetrics()

#: Test seam — not part of the service's public surface.
_internals = {
    "Heartbeat": Heartbeat,
    "RunTracker": RunTracker,
    "DeepQuantMetrics": DeepQuantMetrics,
    "STALL_SECONDS": STALL_SECONDS,
    "DEFAULT_METRICS_PORT": DEFAULT_METRICS_PORT,
}
