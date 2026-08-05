# Monitoring — Prometheus + Grafana + Status API

Every service in the Strat Ai fleet reports whether it is **actually working**, not
merely alive. Five services are Kafka/WS loops that can wedge on a poll and look
perfectly healthy while processing nothing — the instrumentation exists to catch
that gap.

Two audiences:

- **Engineers:** Prometheus console + Grafana dashboards, reachable at
  `https://app-api.stratai.live/prometheus` and `/grafana` (basic auth).
- **Admin panel:** one JSON endpoint at `/status/api/status`, so the panel at
  `dashboard.stratai.live` never learns PromQL and never learns that each service
  prefixes its metrics differently.

---

## Architecture

```
each service :91xx/metrics ─┐
cadvisor      :8090         ├─→ prometheus (7d) ─→ grafana  (engineers)
blackbox      :9115         ┘         │
                                      └────────→ status-api ─→ /api/status
                                                    (JSON)      (admin panel)
```

**Ports (all internal to the compose network; none published to 0.0.0.0):**

| Port | Service | Metrics |
|------|---------|---------|
| 9101 | ingestion | `ingestion_*` |
| 9102 | aggregator | `aggregator_*` |
| 9103 | alpha-terminal | `alpha_terminal_*` |
| 9104 | technical | `technical_*` |
| 9105 | predictive | `predictive_*` |
| 9106 | quant-rag | `quant_rag_*` |
| 9107 | tool-server | `tool_server_*` |
| 9108 | sentiment | `sentiment_*` |
| 9109 | deep-quant | `deep_quant_*` |
| 9110 | status-api | `status_api_*` (also the API port) |

Every first-party service exports `/metrics`, `/health`, `/ready` on its slot,
with a const `service=<name>` label baked in at the source. The `service` label
and the `job` name are deliberately set to the same string: when they disagree
you have a misrouted target (e.g., two services accidentally on one port).

**cAdvisor** (`:8080`) supplies what no in-process exporter can: restart counts,
OOM kills, and actual cgroup memory usage against the `mem_limit`. On a box as
tightly committed as the 8 GB droplet, `container_memory_working_set_bytes` vs
the limit is the single most useful capacity signal.

**Blackbox exporter** (`:9115`) probes the surfaces that have no HTTP metrics
endpoint of their own: QuestDB REST, the WebSocket ports, Kafka, Redis, the
QuestDB PG wire. This answers "can it be reached from inside the compose
network", which is a different question from "does the process think it is
healthy" — both matter, and they disagree in exactly the interesting cases.

**status-api** reads Prometheus normally; when Prometheus is unreachable it falls
back to probing each service's `/ready` directly and feeding the raw numbers
through the same decision function, so the panel still gets real per-service
readiness. Monitoring that dies with what it monitors is the failure this split
exists to prevent.

---

## Shared contract (first-party services)

Every instrumented service publishes:

- `<service>_uptime_seconds` — monotonic; resets to 0 on restart.
- `<service>_last_work_age_seconds` — seconds since the last real unit of work.
- `<service>_stall_threshold_seconds` — the service's own opinion of "too long";
  automatically widened off-market-hours (09:15–15:30 IST weekdays).
- `<service>_work_expected` — 1 when work is expected, 0 when idle is normal.
- `<service>_work_completed_total` — counter; increments on each unit of work.

The **heartbeat** is what answers "working vs failing". Each service calls
`hb.beat()` at the site of real work — a decoded Kite tick, a decision emitted,
a candle aggregated, a TechSignal published. `last_work_age_seconds` is
`now() - last_beat_timestamp`. If it exceeds `stall_threshold_seconds` *and*
`work_expected == 1`, the service is **degraded**.

Session-awareness is owned by the services, not by the classifier. Each publishes
its own already-widened threshold via `MarketSession::stall_threshold_seconds()`,
so off-hours staleness is not degraded — it is expected.

Recording rules in `infra/prometheus/alerts.yml` (group
`service_contract_normalization`) fold these per-service names into one series
each (`service:last_work_age_seconds`, `service:stall_threshold_seconds`, etc.),
selected by regex on `__name__`. This is what makes the admin-panel endpoint
possible: one instant query covers the fleet instead of nine.

---

## Instrumenting a new service

1. **Add the dependency** (`service-metrics = { path = "../service-metrics" }`).
2. **Call `serve_metrics`** in `main()`:
   ```rust
   let config = MetricsConfig { service: "your-service", port: 9111 };
   let metrics = ServiceMetrics::new(config)?;
   tokio::spawn(serve_metrics(metrics.registry()));
   ```
3. **Create a heartbeat** and `.beat()` it at the real work site:
   ```rust
   let hb = metrics.heartbeat("your_work", 60.0); // 60s in-session threshold
   // later, in the loop:
   hb.beat();
   ```
4. **Add `EXPOSE 9111`** to the Dockerfile.
5. **Add the scrape job** to `infra/prometheus/prometheus.yml`:
   ```yaml
   - job_name: your-service
     static_configs:
       - targets: ["your-service:9111"]
         labels:
           service: your-service
           tier: data-plane  # or agents, reasoning, monitoring
   ```
6. **Add to both compose files** with `METRICS_PORT: "9111"` pinned explicitly,
   and a healthcheck hitting `/health`.
7. **Bump `ServiceTargetsMissingFromConfig`** in `alerts.yml` by 1.

Verify: `curl http://localhost:9111/metrics` returns Prometheus exposition format.
Under live load, confirm the work counter actually increments.

---

