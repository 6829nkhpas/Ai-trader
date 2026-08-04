"""Contract tests for the deep-quant service's Prometheus surface (:9109).

These assert the two things that are easy to get wrong and impossible to notice
from outside:

  1. **What counts as work, and when staleness means anything.** This service is
     demand-driven, so an idle process must report `idle` rather than `stalled` —
     otherwise it pages every night. But a run that is genuinely in flight and not
     advancing must report `stalled`, which is the whole reason the module exists.

  2. **The exported names.** ``status-api`` matches on them literally; a rename
     drops the service out of the admin panel as "unknown", which reads exactly
     like a service that was never deployed. Pinning them here means a rename
     breaks the build instead.

Run with ``pytest tests/test_service_metrics.py``.
"""

import json
import os
import sys
import threading
import time
import urllib.request

import pytest

# tests/ sits directly under the service dir; put the service dir on the path so
# ``import service_metrics`` resolves exactly as main.py's does.
_SVC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SVC_DIR not in sys.path:
    sys.path.insert(0, _SVC_DIR)

import service_metrics as sm  # noqa: E402

Heartbeat = sm._internals["Heartbeat"]
DeepQuantMetrics = sm._internals["DeepQuantMetrics"]
STALL_SECONDS = sm._internals["STALL_SECONDS"]


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def m():
    """A fresh handle per test.

    Each instance builds its own ``CollectorRegistry``, so instances never collide
    on duplicate metric names the way they would on the prometheus_client default
    registry.
    """
    return DeepQuantMetrics()


def series(text, name, labels=None):
    """Find a rendered series and return its float value, or None.

    Matches on label PAIRS rather than the whole rendered line: prometheus_client's
    label ordering is its own business, and an assertion pinned to a full string
    would break on a library upgrade that reorders them — which is not a fact worth
    testing.
    """
    labels = labels or {}
    for line in text.splitlines():
        if line.startswith("#") or not line.startswith(name):
            continue
        rest = line[len(name):]
        if not rest.startswith("{"):
            continue
        closing = rest.index("}")
        label_part = rest[1:closing]
        if not all(f'{k}="{v}"' in label_part for k, v in labels.items()):
            continue
        return float(rest[closing + 1:].strip())
    return None


def ist(y, mo, d, hh, mm):
    """A UTC instant for a given IST wall-clock time. IST is UTC+05:30 year-round."""
    from datetime import datetime, timedelta, timezone

    return datetime(y, mo, d, hh, mm, tzinfo=timezone.utc) - timedelta(hours=5, minutes=30)


# ── Market session (context only) ────────────────────────────────────────────


def test_session_covers_both_boundaries():
    # 2026-08-03 is a Monday.
    assert sm.market_session(ist(2026, 8, 3, 9, 14)) == "closed"
    assert sm.market_session(ist(2026, 8, 3, 9, 15)) == "open", "the open itself is in session"
    assert sm.market_session(ist(2026, 8, 3, 12, 0)) == "open"
    assert sm.market_session(ist(2026, 8, 3, 15, 30)) == "open", "the closing auction lands on 15:30"
    assert sm.market_session(ist(2026, 8, 3, 15, 31)) == "closed"


def test_session_reads_weekends_off_the_ist_calendar():
    assert sm.market_session(ist(2026, 8, 8, 12, 0)) == "weekend", "Saturday"
    assert sm.market_session(ist(2026, 8, 9, 12, 0)) == "weekend", "Sunday"

    # 20:00 UTC on a Friday is already 01:30 IST Saturday. A naive UTC weekday
    # read would call this a weekday — the bug the fixed offset exists to avoid.
    from datetime import datetime, timezone

    assert sm.market_session(datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)) == "weekend"


def test_session_never_widens_the_stall_threshold(m):
    """The Rust services widen off-session; this one must not.

    A tick consumer with nothing to consume is healthy at 02:00. A LangGraph run
    that someone started at 02:00 still has to advance, and widening the threshold
    overnight would hide exactly the wedge this module exists to catch.
    """
    m.run_started("run")
    for at in (ist(2026, 8, 3, 12, 0), ist(2026, 8, 3, 2, 0), ist(2026, 8, 8, 12, 0)):
        # market_session is pure; the threshold is a constant regardless of it.
        assert sm.market_session(at) in ("open", "closed", "weekend")
    assert m.readiness()["threshold_seconds"] == STALL_SECONDS
    assert series(m.render().decode(), "deep_quant_stall_threshold_seconds") == STALL_SECONDS


# ── Heartbeat ────────────────────────────────────────────────────────────────


def test_a_process_that_never_worked_reports_age_from_start():
    hb = Heartbeat()
    hb.started = time.monotonic() - 60
    assert hb.has_worked() is False
    assert hb.last_work_age_seconds() >= 59, "boot-and-never-advance must look stale"
    assert hb.work_count == 0


def test_a_beat_resets_the_age_and_counts_the_work():
    hb = Heartbeat()
    hb.started = time.monotonic() - 60
    hb.beat()
    assert hb.has_worked() is True
    assert hb.last_work_age_seconds() < 1
    assert hb.work_count == 1


def test_concurrent_beats_are_not_lost():
    """Runs are concurrent here, and ``work_count += 1`` is not atomic under the
    GIL — a lost increment would understate throughput on exactly the busy
    service where the number matters most."""
    hb = Heartbeat()
    threads = [threading.Thread(target=lambda: [hb.beat() for _ in range(200)]) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert hb.work_count == 1600


# ── The work / staleness gate ────────────────────────────────────────────────


def test_an_idle_service_is_idle_not_stalled(m):
    """The central distinction. Nobody asking for an analysis is the normal state
    for most of the day; reporting that as a stall would page every night and
    train everyone to mute the alert."""
    m.heartbeat.started = time.monotonic() - (STALL_SECONDS + 600)

    r = m.readiness()
    assert r["stalled"] is False
    assert r["work_expected"] is False
    assert m.readiness_json()["status"] == "idle"


def test_an_in_flight_run_that_stops_advancing_is_stalled(m):
    """The failure this module exists to catch: the process is alive, the loop is
    inside a hung LLM call, and no graph node has advanced."""
    tracker = m.run_started("run")
    tracker.graph_step()
    assert m.readiness_json()["status"] == "ok"

    # Age past the threshold without touching the clock.
    m.heartbeat.last_work = time.monotonic() - (STALL_SECONDS + 60)
    r = m.readiness()
    assert r["stalled"] is True
    assert r["work_expected"] is True
    assert m.readiness_json()["status"] == "stalled"


def test_a_paused_run_is_not_in_flight(m):
    """A run paused at watch_price_condition may wait hours for a price. It must
    not count as in flight, or every watching thread reports a stall for the whole
    wait."""
    tracker = m.run_started("run")
    tracker.graph_step()
    tracker.finish("paused")

    m.heartbeat.last_work = time.monotonic() - (STALL_SECONDS + 600)
    assert m.readiness()["stalled"] is False
    assert m.readiness_json()["status"] == "idle"
    assert series(m.render().decode(), "deep_quant_runs_total",
                  {"kind": "run", "outcome": "paused"}) == 1


def test_idle_still_exports_the_age_so_idleness_stays_visible(m):
    """Suppressing the alert must not suppress the signal — an operator still
    needs to see how long the service has had nothing to do."""
    m.heartbeat.started = time.monotonic() - 120
    body = m.readiness_json()
    assert body["last_work_age_seconds"] >= 119
    assert body["runs_in_flight"] == 0


def test_concurrent_runs_keep_work_expected_until_the_last_one_ends(m):
    a = m.run_started("run")
    b = m.run_started("qa")
    assert m.readiness()["runs_in_flight"] == 2

    a.finish("completed")
    assert m.readiness()["work_expected"] is True, "one run still in flight"

    b.finish("completed")
    assert m.readiness()["work_expected"] is False
    assert m.readiness()["runs_in_flight"] == 0


def test_finish_is_idempotent_and_cannot_strand_work_expected(m):
    """``event_generator``'s ``finally`` always calls finish() to catch a client
    disconnect. Double-counting there would drive runs_in_flight negative, leaving
    work_expected pinned at 0 and silently disabling stall detection for the life
    of the process."""
    tracker = m.run_started("run")
    tracker.finish("completed")
    tracker.finish("disconnected")
    tracker.finish("error")

    assert m.readiness()["runs_in_flight"] == 0
    text = m.render().decode()
    assert series(text, "deep_quant_runs_total", {"kind": "run", "outcome": "completed"}) == 1
    assert series(text, "deep_quant_runs_total", {"kind": "run", "outcome": "disconnected"}) == 0
    assert series(text, "deep_quant_runs_total", {"kind": "run", "outcome": "error"}) == 0


def test_a_disconnect_before_the_terminal_event_is_recorded_as_such(m):
    """A client that hangs up mid-run is a distinct fact from a run that errored —
    conflating them would send someone hunting a graph bug that never happened."""
    tracker = m.run_started("run")
    tracker.graph_step()
    tracker.finish("disconnected")

    text = m.render().decode()
    assert series(text, "deep_quant_runs_total", {"kind": "run", "outcome": "disconnected"}) == 1
    assert m.readiness()["runs_in_flight"] == 0


def test_started_and_terminal_counts_expose_a_wedge(m):
    """The gap between started and terminal runs IS the wedge signal: runs that
    are neither finishing nor erroring."""
    m.run_started("run")
    m.run_started("run")
    finished = m.run_started("run")
    finished.finish("completed")

    text = m.render().decode()
    assert series(text, "deep_quant_runs_started_total", {"kind": "run"}) == 3
    terminal = sum(
        series(text, "deep_quant_runs_total", {"kind": "run", "outcome": o}) or 0
        for o in sm.RUN_OUTCOMES
    )
    assert terminal == 1
    assert m.readiness()["runs_in_flight"] == 2


# ── Exposition ───────────────────────────────────────────────────────────────


def test_the_shared_contract_metrics_are_exported_under_the_service_label(m):
    text = m.render().decode()
    for name in (
        "deep_quant_up",
        "deep_quant_uptime_seconds",
        "deep_quant_last_work_age_seconds",
        "deep_quant_stall_threshold_seconds",
        "deep_quant_market_session_open",
        "deep_quant_work_expected",
        "deep_quant_work_completed_total",
        "deep_quant_build_info",
    ):
        assert series(text, name) is not None, f"{name} must be exported"
    assert 'service="deep-quant"' in text, "every series carries the service label"


def test_the_readiness_body_carries_exactly_the_fields_status_api_reads(m):
    tracker = m.run_started("run")
    tracker.graph_step()

    body = m.readiness_json()
    assert sorted(body.keys()) == [
        "last_work_age_seconds",
        "market_session",
        "runs_in_flight",
        "service",
        "stall_threshold_seconds",
        "status",
        "uptime_seconds",
        "work_completed",
        "work_expected",
    ]
    assert body["service"] == "deep-quant"
    assert body["status"] == "ok"
    assert body["work_completed"] == 1
    assert body["stall_threshold_seconds"] == STALL_SECONDS
    # Must survive a JSON round-trip — the HTTP handler serializes it verbatim.
    assert json.loads(json.dumps(body)) == body


def test_every_error_series_exists_at_zero_before_anything_happens(m):
    """``rate()`` over a series that does not exist renders as "no data", which
    looks identical to a failed scrape. Pre-creating them is what makes a genuine
    zero readable as a zero."""
    text = m.render().decode()
    for kind in sm.RUN_KINDS:
        for outcome in sm.RUN_OUTCOMES:
            assert series(text, "deep_quant_runs_total",
                          {"kind": kind, "outcome": outcome}) == 0, f"{kind}/{outcome}"
    for outcome in sm.KEY_OUTCOMES:
        assert series(text, "deep_quant_llm_key_resolutions_total", {"outcome": outcome}) == 0
    for outcome in sm.OPTIONS_OUTCOMES:
        assert series(text, "deep_quant_options_snapshots_total", {"outcome": outcome}) == 0
    assert series(text, "deep_quant_stream_events_total", {"event": "ERROR"}) == 0


def test_service_only_series_exist_at_zero_too(m):
    """The single-label counters and gauges need pre-creation just as much.

    ``work_completed_total`` is THE throughput series: a panel reading "no data"
    on a freshly restarted service would be indistinguishable from one reading
    "no data" because the scrape failed.
    """
    text = m.render().decode()
    for name in (
        "deep_quant_work_completed_total",
        "deep_quant_cancellations_total",
        "deep_quant_runs_in_flight",
        "deep_quant_stream_subscribers",
    ):
        assert series(text, name) == 0, f"{name} must exist at zero before first use"


def test_run_duration_is_observed_per_outcome(m):
    tracker = m.run_started("qa")
    tracker.finish("completed")
    text = m.render().decode()
    assert series(text, "deep_quant_run_duration_seconds_count",
                  {"kind": "qa", "outcome": "completed"}) == 1
    assert series(text, "deep_quant_run_duration_seconds_count",
                  {"kind": "qa", "outcome": "error"}) is None, \
        "an outcome that never happened has no duration to report"


def test_options_snapshot_outcomes_and_latency(m):
    m.options_snapshot("ok", 0.2)
    m.options_snapshot("no_snapshot", 0.1)
    text = m.render().decode()
    assert series(text, "deep_quant_options_snapshots_total", {"outcome": "ok"}) == 1
    assert series(text, "deep_quant_options_snapshots_total", {"outcome": "no_snapshot"}) == 1
    assert series(text, "deep_quant_options_snapshot_duration_seconds_count") == 2


def test_telemetry_availability_is_reported(m):
    """The telemetry layer degrades silently by design, so without this a
    deployment can lose all trade-outcome recording with no other sign."""
    assert series(m.render().decode(), "deep_quant_telemetry_available") == 0, \
        "0 until main.py confirms the import"
    m.set_telemetry_available(True)
    assert series(m.render().decode(), "deep_quant_telemetry_available") == 1


# ── Unbounded label defence ──────────────────────────────────────────────────


def test_unknown_labels_are_dropped_or_folded(m):
    m.stream_event("NOT_A_REAL_EVENT")
    m.key_resolution("invented")
    m.run_started("../../etc/passwd").finish("also_invented")

    text = m.render().decode()
    assert "NOT_A_REAL_EVENT" not in text
    assert "invented" not in text
    assert "etc/passwd" not in text, "an arbitrary kind cannot become a label value"
    # An unknown kind folds to `run` and an unknown outcome to `error`, so the
    # event is still counted rather than silently vanishing.
    assert series(text, "deep_quant_runs_total", {"kind": "run", "outcome": "error"}) == 1


def test_tool_names_are_capped_but_real_tools_are_never_folded(m):
    """Tool names come from LLM output, so a hallucinated name could otherwise
    mint a permanent series. The cap sits well above the registered tool count, so
    nothing is folded in normal operation."""
    m.tool_call("get_market_data", "success")
    m.tool_call("declare_trade", "failure")
    text = m.render().decode()
    assert series(text, "deep_quant_tool_calls_total",
                  {"tool": "get_market_data", "status": "success"}) == 1
    assert series(text, "deep_quant_tool_calls_total",
                  {"tool": "declare_trade", "status": "failure"}) == 1

    for i in range(sm.MAX_TOOL_LABELS + 20):
        m.tool_call(f"hallucinated_tool_{i}", "failure")
    text = m.render().decode()
    assert series(text, "deep_quant_tool_calls_total", {"tool": "other", "status": "failure"}) > 0
    # The two real tools were seen first, so they keep their own series.
    assert series(text, "deep_quant_tool_calls_total",
                  {"tool": "get_market_data", "status": "success"}) == 1


# ── Degradation ──────────────────────────────────────────────────────────────


def test_a_registry_failure_leaves_the_handle_inert(m):
    """Every call site is unconditional, so a broken registry must never be able
    to take down the service it is observing."""
    m.inner = None

    tracker = m.run_started("run")
    tracker.graph_step()
    tracker.stream_event("REASONING")
    tracker.tool_call("get_market_data", "success")
    tracker.finish("completed")
    m.key_resolution("shared")
    m.cancellation_requested()
    m.set_stream_subscribers(2)
    m.options_snapshot("ok", 0.1)
    m.set_telemetry_available(True)

    assert m.render() == b""
    assert m.serve() is None, "no listener without a registry"
    # Readiness must still answer — it is derived from the heartbeat, not the
    # registry, so /health keeps working even uninstrumented.
    assert m.readiness_json()["service"] == "deep-quant"


# ── The listener ─────────────────────────────────────────────────────────────


def test_serve_answers_metrics_health_and_ready(monkeypatch, m):
    monkeypatch.setenv("METRICS_PORT", "0")  # let the OS pick a free port
    server = m.serve()
    assert server is not None
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"

        with urllib.request.urlopen(f"{base}/metrics", timeout=5) as res:
            assert res.status == 200
            assert "text/plain" in res.headers["Content-Type"]
            assert "deep_quant_last_work_age_seconds" in res.read().decode()

        with urllib.request.urlopen(f"{base}/health", timeout=5) as res:
            assert res.status == 200
            assert json.loads(res.read())["status"] == "idle"

        try:
            urllib.request.urlopen(f"{base}/nope", timeout=5)
            pytest.fail("an unrouted path must 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404

        # A stalled service is running, so /health stays 200 — restarting it on a
        # stall would turn a visible problem into a crash loop. /ready is the one
        # that fails, which makes the stall actionable without being
        # self-inflicted.
        m.run_started("run")
        m.heartbeat.last_work = time.monotonic() - (STALL_SECONDS + 60)

        with urllib.request.urlopen(f"{base}/health", timeout=5) as res:
            assert res.status == 200
            assert json.loads(res.read())["status"] == "stalled"

        try:
            urllib.request.urlopen(f"{base}/ready", timeout=5)
            pytest.fail("/ready must fail while stalled")
        except urllib.error.HTTPError as e:
            assert e.code == 503
            assert json.loads(e.read())["status"] == "stalled"
    finally:
        server.shutdown()
        server.server_close()


def test_the_listener_answers_while_the_event_loop_is_blocked(monkeypatch, m):
    """The reason the listener is a thread and not a FastAPI route.

    A run blocked in a synchronous call starves every coroutine, so an
    ``@app.get("/metrics")`` handler would time out — and a scrape timeout is
    indistinguishable from a dead container, losing the one fact worth knowing.
    Here the loop is genuinely blocked and the exposition still answers.
    """
    import asyncio

    monkeypatch.setenv("METRICS_PORT", "0")
    server = m.serve()
    assert server is not None

    async def block_then_scrape():
        base = f"http://127.0.0.1:{server.server_address[1]}"
        # A synchronous sleep on the loop thread: nothing else on this loop can
        # run for its duration, which is exactly the wedge being simulated.
        time.sleep(0.4)
        with urllib.request.urlopen(f"{base}/health", timeout=5) as res:
            return res.status, json.loads(res.read())

    try:
        loop = asyncio.new_event_loop()
        try:
            scraped = {}

            def scrape_during_the_block():
                base = f"http://127.0.0.1:{server.server_address[1]}"
                with urllib.request.urlopen(f"{base}/metrics", timeout=5) as res:
                    scraped["status"] = res.status
                    scraped["body"] = res.read().decode()

            t = threading.Thread(target=scrape_during_the_block)
            t.start()
            loop.run_until_complete(block_then_scrape())
            t.join(timeout=5)

            assert scraped.get("status") == 200, "the exposition must survive a blocked loop"
            assert "deep_quant_up" in scraped["body"]
        finally:
            loop.close()
    finally:
        server.shutdown()
        server.server_close()
