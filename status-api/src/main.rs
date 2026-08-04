//! status-api — the one endpoint the admin panel calls.
//!
//! Reads Prometheus, falls back to probing each service's own `/ready` when
//! Prometheus is unreachable, and reduces both to the [`model`] contract. The
//! panel never learns PromQL and never learns that each service prefixes its
//! metrics differently.
//!
//! Two design points worth keeping:
//!
//! 1. **The fallback exists so monitoring fails independently of what it
//!    monitors.** If Prometheus OOMs on this tight box, the panel still gets
//!    real per-service readiness — just without history or rates.
//! 2. **A background refresh, not per-request fetching.** The panel polls; the
//!    refresh loop is what actually talks to Prometheus. That bounds load
//!    regardless of how many browser tabs are open, and it gives this service a
//!    real unit of work to heartbeat on, so it is covered by the same
//!    WorkStalled alert as everything else.

mod classify;
mod model;

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::extract::{Path, State};
use axum::http::{HeaderValue, Method, StatusCode};
use axum::response::IntoResponse;
use axum::routing::get;
use axum::{Json, Router};
use chrono::{DateTime, SecondsFormat, Utc};
use serde::Deserialize;
use service_metrics::{MarketSession, MetricsConfig, ServiceMetrics};
use tower_http::cors::CorsLayer;

use classify::{classify, counts, overall, tier_of, Sample, SERVICES};
use model::{Alert, HistoryPoint, ServiceDetail, ServiceStatus, Source, StatusReport};

const SERVICE: &str = "status-api";

/// Seconds of no successful collection that counts as a stall in-session.
/// Generous relative to the refresh interval: one failed round-trip to
/// Prometheus is not an incident, a minute of them is.
const IN_SESSION_STALL_SECONDS: f64 = 90.0;

struct AppState {
    http: reqwest::Client,
    prometheus_url: String,
    /// Last completed report. Served to every caller; refreshed by one task.
    cache: Mutex<StatusReport>,
    metrics: ServiceMetrics,
}

#[tokio::main]
async fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let port: u16 = env_u16("STATUS_API_PORT").or_else(|| env_u16("METRICS_PORT")).unwrap_or(9110);
    let prometheus_url = std::env::var("PROMETHEUS_URL")
        .unwrap_or_else(|_| "http://prometheus:9090".to_string())
        .trim_end_matches('/')
        .to_string();
    let refresh = Duration::from_secs(
        std::env::var("STATUS_API_REFRESH_SECONDS")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(15),
    );

    let metrics = ServiceMetrics::new(MetricsConfig {
        service: SERVICE,
        version: env!("CARGO_PKG_VERSION"),
        in_session_stall_seconds: IN_SESSION_STALL_SECONDS,
    })
    .expect("service metrics registry");
    // This service always has something to do: the refresh loop runs regardless
    // of whether anyone is looking.
    metrics.set_work_expected(true);

    let state = Arc::new(AppState {
        // Short timeouts throughout. A monitoring endpoint that hangs is worse
        // than one that answers "unknown" — the panel would show a spinner
        // where it should show a red row.
        http: reqwest::Client::builder()
            .timeout(Duration::from_secs(5))
            .build()
            .expect("http client"),
        prometheus_url,
        cache: Mutex::new(empty_report("collecting")),
        metrics: metrics.clone(),
    });

    // Collect once before binding so the first caller gets real data rather than
    // a placeholder.
    refresh_once(&state).await;

    {
        let state = state.clone();
        tokio::spawn(async move {
            let mut ticker = tokio::time::interval(refresh);
            ticker.tick().await; // fires immediately; already collected above
            loop {
                ticker.tick().await;
                refresh_once(&state).await;
            }
        });
    }

    let app = Router::new()
        .route("/api/status", get(get_status))
        .route("/api/status/:service", get(get_service))
        .route("/api/alerts", get(get_alerts))
        // This service's own liveness, and its own metrics — :9110 is both the
        // API port and its Prometheus slot, so /metrics rides the same router.
        .route("/health", get(get_health))
        .route("/ready", get(get_health))
        .route("/metrics", get(get_metrics))
        .layer(cors_layer())
        .with_state(state);

    let addr = format!("0.0.0.0:{port}");
    log::info!("status-api listening on {addr}");
    let listener = tokio::net::TcpListener::bind(&addr)
        .await
        .unwrap_or_else(|e| panic!("bind {addr}: {e}"));
    axum::serve(listener, app).await.expect("serve");
}

fn env_u16(key: &str) -> Option<u16> {
    std::env::var(key).ok()?.parse().ok()
}

/// CORS restricted to the dashboard origin.
///
/// Deliberately NOT `allow_origin(Any)` — `aggregator/src/kite_api.rs:812` does
/// that, but it is a localhost-only dev proxy. This endpoint is published
/// through Caddy, and a wildcard here would let any page a logged-in operator
/// visits read the fleet's internal topology.
fn cors_layer() -> CorsLayer {
    let raw = std::env::var("STATUS_API_CORS_ORIGINS")
        .unwrap_or_else(|_| "https://dashboard.stratai.live".to_string());
    let origins: Vec<HeaderValue> = raw
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .filter_map(|s| s.parse().ok())
        .collect();
    if origins.is_empty() {
        log::warn!("STATUS_API_CORS_ORIGINS parsed to nothing; browser callers will be refused");
    }
    CorsLayer::new()
        .allow_origin(origins)
        .allow_methods([Method::GET])
}

// ── Routes ───────────────────────────────────────────────────────────────────

async fn get_status(State(state): State<Arc<AppState>>) -> Json<StatusReport> {
    Json(state.cache.lock().expect("cache poisoned").clone())
}

async fn get_service(
    State(state): State<Arc<AppState>>,
    Path(name): Path<String>,
) -> Result<Json<ServiceDetail>, StatusCode> {
    let report = state.cache.lock().expect("cache poisoned").clone();
    let service = report
        .services
        .iter()
        .find(|s| s.name == name)
        .cloned()
        .ok_or(StatusCode::NOT_FOUND)?;

    let alerts = fetch_alerts(&state)
        .await
        .unwrap_or_default()
        .into_iter()
        .filter(|a| a.service.as_deref() == Some(name.as_str()))
        .collect();

    Ok(Json(ServiceDetail {
        generated_at: now_rfc3339(),
        market_session: MarketSession::now().as_str().to_string(),
        source: report.source,
        monitoring_warning: report.monitoring_warning,
        service,
        alerts,
        // Empty when Prometheus is unreachable — history is the one thing a
        // direct probe fundamentally cannot supply.
        history: fetch_history(&state, &name).await.unwrap_or_default(),
    }))
}

async fn get_alerts(State(state): State<Arc<AppState>>) -> Result<Json<Vec<Alert>>, StatusCode> {
    match fetch_alerts(&state).await {
        Ok(alerts) => Ok(Json(alerts)),
        // Not an empty list: "no alerts" and "cannot tell you about alerts" must
        // not look the same to the panel.
        Err(e) => {
            log::warn!("alerts query failed: {e}");
            Err(StatusCode::SERVICE_UNAVAILABLE)
        }
    }
}

async fn get_health(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    let r = state.metrics.readiness();
    let code = if r.stalled {
        StatusCode::SERVICE_UNAVAILABLE
    } else {
        StatusCode::OK
    };
    (
        code,
        [(axum::http::header::CONTENT_TYPE, "application/json")],
        r.to_json(SERVICE),
    )
}

async fn get_metrics(State(state): State<Arc<AppState>>) -> impl IntoResponse {
    match state.metrics.render() {
        Ok(body) => (StatusCode::OK, body),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, format!("# {e}\n")),
    }
}

// ── Collection ───────────────────────────────────────────────────────────────

/// One collection pass: Prometheus first, direct probes if that fails.
async fn refresh_once(state: &Arc<AppState>) {
    let report = match fetch_from_prometheus(state).await {
        Ok(samples) => build_report(samples, Source::Prometheus, None),
        Err(e) => {
            log::warn!("prometheus unreachable ({e}); falling back to direct probes");
            let samples = probe_all(state).await;
            build_report(
                samples,
                Source::DirectProbe,
                Some(format!(
                    "prometheus unreachable ({e}); per-service readiness probed directly — \
                     no rates or history"
                )),
            )
        }
    };

    // Heartbeat on a completed pass, whichever path produced it. A probe-only
    // pass is degraded data but it IS work — beating only on the Prometheus path
    // would make a Prometheus outage look like status-api itself had died.
    state.metrics.heartbeat().beat();
    *state.cache.lock().expect("cache poisoned") = report;
}

fn build_report(
    samples: HashMap<String, Sample>,
    source: Source,
    warning: Option<String>,
) -> StatusReport {
    let services: Vec<ServiceStatus> = SERVICES
        .iter()
        .map(|(name, _, _)| match samples.get(*name) {
            Some(sample) => classify(name, sample),
            // In the registry but absent from the data: the honest answer is
            // that we do not know, never that it is fine.
            None => ServiceStatus::unknown(name, tier_of(name), "not present in monitoring data"),
        })
        .collect();

    let (up, degraded, down, unknown) = counts(&services);
    StatusReport {
        overall: overall(&services),
        generated_at: now_rfc3339(),
        market_session: MarketSession::now().as_str().to_string(),
        up_count: up,
        degraded_count: degraded,
        down_count: down,
        unknown_count: unknown,
        source,
        monitoring_warning: warning,
        services,
    }
}

fn empty_report(reason: &str) -> StatusReport {
    build_report(HashMap::new(), Source::None, Some(reason.to_string()))
}

// ── Prometheus ───────────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct PromEnvelope<T> {
    status: String,
    // `Option` is already "absent means None" to serde; a `#[serde(default)]`
    // here would make the derive demand `T: Default` for no benefit.
    data: Option<T>,
    error: Option<String>,
}

#[derive(Deserialize)]
struct VectorData {
    result: Vec<VectorSample>,
}

#[derive(Deserialize)]
struct VectorSample {
    metric: HashMap<String, String>,
    /// `[unix_seconds, "value"]` — Prometheus sends the value as a string.
    value: (f64, String),
}

#[derive(Deserialize)]
struct MatrixData {
    result: Vec<MatrixSample>,
}

#[derive(Deserialize)]
struct MatrixSample {
    values: Vec<(f64, String)>,
}

#[derive(Deserialize)]
struct AlertsData {
    alerts: Vec<PromAlert>,
}

#[derive(Deserialize)]
struct PromAlert {
    #[serde(default)]
    labels: HashMap<String, String>,
    #[serde(default)]
    annotations: HashMap<String, String>,
    #[serde(default)]
    state: String,
    /// Prometheus spells this `activeAt`.
    #[serde(default, rename = "activeAt")]
    active_at: Option<DateTime<Utc>>,
}

async fn prom_get<T: serde::de::DeserializeOwned>(
    state: &Arc<AppState>,
    path: &str,
    query: &[(&str, String)],
) -> Result<T, String> {
    let url = format!("{}{path}", state.prometheus_url);
    let resp = state
        .http
        .get(&url)
        .query(query)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let envelope: PromEnvelope<T> = resp.json().await.map_err(|e| e.to_string())?;
    if envelope.status != "success" {
        return Err(envelope.error.unwrap_or_else(|| envelope.status.clone()));
    }
    envelope.data.ok_or_else(|| "empty response".to_string())
}

/// Everything the fleet report needs, in two instant queries.
///
/// The first matches `up` plus every `service:*` recording rule plus a short
/// list of service-specific gauges, all of which carry a `service` label. The
/// recording rules exist precisely so this works — each service prefixes its own
/// metrics (`ingestion_*`, `deep_quant_*`), so without them there would be no
/// single name to ask for. See `infra/prometheus/alerts.yml`, group
/// `service_contract_normalization`.
///
/// The second answers "when did we last see it", which only matters for a
/// service that is currently down and which no instantaneous series can supply.
async fn fetch_from_prometheus(state: &Arc<AppState>) -> Result<HashMap<String, Sample>, String> {
    let data: VectorData = prom_get(
        state,
        "/api/v1/query",
        &[(
            "query",
            // The trailing alternation picks up the per-service colour the panel
            // shows in `detail` (kite WS state, attached WS clients, in-flight
            // agent runs). Matched by SUFFIX because the prefix differs per
            // service, which is the same reason the recording rules exist.
            r#"{__name__=~"up|service:.+|.+_(kite_ws_connected|ws_clients|runs_in_flight)", service!=""}"#
                .to_string(),
        )],
    )
    .await?;

    let mut out: HashMap<String, Sample> = HashMap::new();
    for row in data.result {
        let Some(service) = row.metric.get("service") else {
            continue;
        };
        let Some(name) = row.metric.get("__name__") else {
            continue;
        };
        let Ok(v) = row.value.1.parse::<f64>() else {
            continue;
        };
        let sample = out.entry(service.clone()).or_default();
        match name.as_str() {
            "up" => sample.up = Some(v),
            "service:last_work_age_seconds" => sample.last_work_age_s = Some(v),
            "service:stall_threshold_seconds" => sample.stall_threshold_s = Some(v),
            "service:work_expected" => sample.work_expected = Some(v),
            "service:work_completed:rate5m" => sample.work_rate_per_s = Some(v),
            "service:uptime_seconds" => sample.uptime_s = Some(v),
            other => {
                if let Some(key) = detail_key(other) {
                    sample.detail.insert(key.to_string(), v.into());
                }
            }
        }
    }

    // Best-effort: a failure here costs a `last_seen` field, not the report.
    match fetch_last_seen(state).await {
        Ok(seen) => {
            for (service, ts) in seen {
                if let Some(sample) = out.get_mut(&service) {
                    sample.last_seen = Some(ts);
                }
            }
        }
        Err(e) => log::debug!("last-seen query failed: {e}"),
    }

    Ok(out)
}

/// The `detail` key for a service-specific gauge, or `None` if it is not one we
/// surface. Keyed on the suffix so `ingestion_kite_ws_connected` and
/// `aggregator_ws_clients` both land on a stable, service-agnostic name.
fn detail_key(metric: &str) -> Option<&'static str> {
    for suffix in ["kite_ws_connected", "ws_clients", "runs_in_flight"] {
        if metric.ends_with(suffix) {
            return Some(suffix);
        }
    }
    None
}

/// When each service was last successfully scraped.
///
/// Only meaningful for a service that is down now — but the query is fleet-wide
/// because asking per-service would mean one round trip per dead service, which
/// is exactly when the box is least healthy. The 5-minute subquery step keeps it
/// cheap; `last_seen` does not need second precision.
async fn fetch_last_seen(state: &Arc<AppState>) -> Result<Vec<(String, String)>, String> {
    let data: VectorData = prom_get(
        state,
        "/api/v1/query",
        &[(
            "query",
            r#"timestamp(last_over_time((up{service!=""} == 1)[6h:5m]))"#.to_string(),
        )],
    )
    .await?;
    Ok(data
        .result
        .into_iter()
        .filter_map(|row| {
            let service = row.metric.get("service")?.clone();
            let ts = row.value.1.parse::<f64>().ok()?;
            let at = DateTime::from_timestamp(ts as i64, 0)?
                .to_rfc3339_opts(SecondsFormat::Secs, true);
            Some((service, at))
        })
        .collect())
}

async fn fetch_alerts(state: &Arc<AppState>) -> Result<Vec<Alert>, String> {
    let data: AlertsData = prom_get(state, "/api/v1/alerts", &[]).await?;
    Ok(data
        .alerts
        .into_iter()
        .map(|a| Alert {
            name: a
                .labels
                .get("alertname")
                .cloned()
                .unwrap_or_else(|| "unknown".into()),
            state: a.state,
            severity: a
                .labels
                .get("severity")
                .cloned()
                .unwrap_or_else(|| "unknown".into()),
            service: a.labels.get("service").cloned(),
            summary: a.annotations.get("summary").cloned(),
            description: a.annotations.get("description").cloned(),
            runbook: a.annotations.get("runbook").cloned(),
            active_since: a
                .active_at
                .map(|t| t.to_rfc3339_opts(SecondsFormat::Secs, true)),
        })
        .collect())
}

/// The last hour of work-age and work-rate for one service, for the detail view.
async fn fetch_history(state: &Arc<AppState>, service: &str) -> Result<Vec<HistoryPoint>, String> {
    let now = Utc::now().timestamp();
    let range = |q: String| {
        [
            ("query", q),
            ("start", (now - 3600).to_string()),
            ("end", now.to_string()),
            ("step", "60".to_string()),
        ]
    };
    let age: MatrixData = prom_get(
        state,
        "/api/v1/query_range",
        &range(format!(r#"service:last_work_age_seconds{{service="{service}"}}"#)),
    )
    .await?;
    let rate: MatrixData = prom_get(
        state,
        "/api/v1/query_range",
        &range(format!(
            r#"service:work_completed:rate5m{{service="{service}"}}"#
        )),
    )
    .await?;

    // Both series share the 60s step grid, so keying on the timestamp lines them
    // up without interpolation.
    let mut by_ts: std::collections::BTreeMap<i64, (Option<f64>, Option<f64>)> = Default::default();
    let ingest = |m: MatrixData,
                  by_ts: &mut std::collections::BTreeMap<i64, (Option<f64>, Option<f64>)>,
                  age_slot: bool| {
        for s in m.result {
            for (ts, v) in s.values {
                let Ok(v) = v.parse::<f64>() else { continue };
                let slot = by_ts.entry(ts as i64).or_insert((None, None));
                if age_slot {
                    slot.0 = Some(v);
                } else {
                    slot.1 = Some(v);
                }
            }
        }
    };
    ingest(age, &mut by_ts, true);
    ingest(rate, &mut by_ts, false);

    Ok(by_ts
        .into_iter()
        .map(|(ts, (age, rate))| HistoryPoint {
            at: DateTime::from_timestamp(ts, 0)
                .unwrap_or_else(Utc::now)
                .to_rfc3339_opts(SecondsFormat::Secs, true),
            last_work_age_s: age,
            work_rate_per_s: rate,
        })
        .collect())
}

// ── Direct probes (the fallback) ──────────────────────────────────────────────

/// The `/ready` body every instrumented service serves — `Readiness::to_json`
/// in the service-metrics crate, and the same shape from the Node and Python
/// services. `status` itself is ignored: the raw numbers go through the same
/// [`classify`] used for the Prometheus path, so there is exactly one place that
/// decides what "working" means.
#[derive(Deserialize)]
struct ReadyBody {
    #[serde(default)]
    work_expected: Option<bool>,
    #[serde(default)]
    last_work_age_seconds: Option<f64>,
    #[serde(default)]
    stall_threshold_seconds: Option<f64>,
    #[serde(default)]
    uptime_seconds: Option<f64>,
}

async fn probe_all(state: &Arc<AppState>) -> HashMap<String, Sample> {
    let probes = SERVICES.iter().map(|(name, _, port)| {
        let state = state.clone();
        async move { ((*name).to_string(), probe_one(&state, name, *port).await) }
    });
    futures_join_all(probes).await.into_iter().collect()
}

/// `futures::future::join_all` without the `futures` dependency — the fleet is
/// nine services, so spawning nine tasks and awaiting them is the whole trick.
async fn futures_join_all<F, T>(futures: impl Iterator<Item = F>) -> Vec<T>
where
    F: std::future::Future<Output = T> + Send + 'static,
    T: Send + 'static,
{
    let handles: Vec<_> = futures.map(tokio::spawn).collect();
    let mut out = Vec::with_capacity(handles.len());
    for h in handles {
        if let Ok(v) = h.await {
            out.push(v);
        }
    }
    out
}

async fn probe_one(state: &Arc<AppState>, name: &str, port: u16) -> Sample {
    let url = format!("http://{name}:{port}/ready");
    let resp = state
        .http
        .get(&url)
        .timeout(Duration::from_secs(2))
        .send()
        .await;

    match resp {
        // 503 is what a stalled service returns from /ready, so a non-2xx status
        // is not "unreachable" — the body is still the answer. Only a transport
        // error means down.
        Ok(r) => match r.json::<ReadyBody>().await {
            Ok(b) => Sample {
                up: Some(1.0),
                last_work_age_s: b.last_work_age_seconds,
                stall_threshold_s: b.stall_threshold_seconds,
                work_expected: b.work_expected.map(|w| if w { 1.0 } else { 0.0 }),
                uptime_s: b.uptime_seconds,
                ..Default::default()
            },
            Err(e) => {
                log::debug!("{name} answered /ready with an unreadable body: {e}");
                Sample {
                    up: Some(1.0),
                    ..Default::default()
                }
            }
        },
        Err(e) => {
            log::debug!("probe {url} failed: {e}");
            Sample {
                up: Some(0.0),
                ..Default::default()
            }
        }
    }
}

fn now_rfc3339() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Secs, true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Health;

    #[test]
    fn a_report_built_from_nothing_is_all_unknown() {
        // The Prometheus-down path before any probe has succeeded. Every service
        // unknown, overall unknown, and a warning saying why.
        let r = empty_report("collecting");
        assert_eq!(r.overall, Health::Unknown);
        assert_eq!(r.unknown_count, SERVICES.len());
        assert_eq!(r.up_count, 0);
        assert_eq!(r.source, Source::None);
        assert!(r.monitoring_warning.is_some());
        assert_eq!(r.services.len(), SERVICES.len());
    }

    #[test]
    fn a_report_names_every_registered_service_even_with_partial_data() {
        // Prometheus knows about one service. The other eight must still appear,
        // as unknown — a service silently missing from the panel is the failure
        // this stack exists to prevent.
        let mut samples = HashMap::new();
        samples.insert(
            "ingestion".to_string(),
            Sample {
                up: Some(1.0),
                last_work_age_s: Some(0.2),
                stall_threshold_s: Some(60.0),
                work_expected: Some(1.0),
                ..Default::default()
            },
        );
        let r = build_report(samples, Source::Prometheus, None);
        assert_eq!(r.services.len(), SERVICES.len());
        assert_eq!(r.up_count, 1);
        assert_eq!(r.unknown_count, SERVICES.len() - 1);
        assert_eq!(r.overall, Health::Unknown);
        assert!(r.monitoring_warning.is_none());
    }

    #[test]
    fn market_session_is_one_of_the_three_documented_strings() {
        let r = empty_report("x");
        assert!(
            ["open", "closed", "weekend"].contains(&r.market_session.as_str()),
            "unexpected session {:?}",
            r.market_session
        );
    }

    #[test]
    fn prometheus_vector_rows_map_onto_the_sample_fields() {
        // Guards the recording-rule names. A rename in alerts.yml that is not
        // mirrored here would silently drop every number to None and report a
        // fleet of `up` services with no work data.
        let body = r#"{
          "status": "success",
          "data": { "resultType": "vector", "result": [
            {"metric":{"__name__":"up","service":"ingestion"},"value":[1,"1"]},
            {"metric":{"__name__":"service:last_work_age_seconds","service":"ingestion"},"value":[1,"0.4"]},
            {"metric":{"__name__":"service:stall_threshold_seconds","service":"ingestion"},"value":[1,"60"]},
            {"metric":{"__name__":"service:work_expected","service":"ingestion"},"value":[1,"1"]},
            {"metric":{"__name__":"service:work_completed:rate5m","service":"ingestion"},"value":[1,"30.6"]},
            {"metric":{"__name__":"service:uptime_seconds","service":"ingestion"},"value":[1,"91422"]}
          ]}
        }"#;
        let env: PromEnvelope<VectorData> = serde_json::from_str(body).unwrap();
        assert_eq!(env.status, "success");
        let rows = env.data.unwrap().result;
        assert_eq!(rows.len(), 6);

        let mut s = Sample::default();
        for row in rows {
            let v: f64 = row.value.1.parse().unwrap();
            match row.metric["__name__"].as_str() {
                "up" => s.up = Some(v),
                "service:last_work_age_seconds" => s.last_work_age_s = Some(v),
                "service:stall_threshold_seconds" => s.stall_threshold_s = Some(v),
                "service:work_expected" => s.work_expected = Some(v),
                "service:work_completed:rate5m" => s.work_rate_per_s = Some(v),
                "service:uptime_seconds" => s.uptime_s = Some(v),
                other => panic!("unhandled {other}"),
            }
        }
        let status = classify("ingestion", &s);
        assert_eq!(status.status, Health::Up);
        assert_eq!(status.work_rate_per_s, Some(30.6));
        assert_eq!(status.uptime_s, Some(91_422.0));
    }

    #[test]
    fn a_ready_body_from_a_stalled_service_classifies_as_degraded() {
        // The fallback path. The service says "stalled"; we ignore its verdict
        // and re-derive it from the numbers, so both paths agree by construction.
        let body = r#"{"service":"technical","status":"stalled","market_session":"open",
            "work_expected":true,"last_work_age_seconds":38.0,
            "stall_threshold_seconds":30.0,"work_completed":118,"uptime_seconds":900.0}"#;
        let b: ReadyBody = serde_json::from_str(body).unwrap();
        let s = Sample {
            up: Some(1.0),
            last_work_age_s: b.last_work_age_seconds,
            stall_threshold_s: b.stall_threshold_seconds,
            work_expected: b.work_expected.map(|w| if w { 1.0 } else { 0.0 }),
            uptime_s: b.uptime_seconds,
            ..Default::default()
        };
        assert_eq!(classify("technical", &s).status, Health::Degraded);
    }

    #[test]
    fn service_specific_gauges_map_onto_stable_detail_keys() {
        // The prefix differs per service, so the suffix is what we key on. These
        // are the actual metric names emitted today (grepped from the sources).
        assert_eq!(detail_key("ingestion_kite_ws_connected"), Some("kite_ws_connected"));
        assert_eq!(detail_key("aggregator_ws_clients"), Some("ws_clients"));
        assert_eq!(detail_key("alpha_terminal_ws_clients"), Some("ws_clients"));
        assert_eq!(detail_key("deep_quant_runs_in_flight"), Some("runs_in_flight"));
        // Anything else stays out of `detail` rather than leaking raw metric
        // names into a published contract.
        assert_eq!(detail_key("ingestion_ticks_total"), None);
        assert_eq!(detail_key("up"), None);
    }

    #[test]
    fn prometheus_error_envelopes_are_errors_not_empty_results() {
        let body = r#"{"status":"error","errorType":"bad_data","error":"parse error"}"#;
        let env: PromEnvelope<VectorData> = serde_json::from_str(body).unwrap();
        assert_eq!(env.status, "error");
        assert_eq!(env.error.as_deref(), Some("parse error"));
        assert!(env.data.is_none());
    }

    #[test]
    fn alerts_deserialize_with_their_runbook_anchor() {
        let body = r#"{"status":"success","data":{"alerts":[
          {"labels":{"alertname":"WorkStalled","severity":"critical","service":"technical"},
           "annotations":{"summary":"technical has done no work","runbook":"docs/MONITORING.md#workstalled"},
           "state":"firing","activeAt":"2026-08-04T09:14:22Z"}
        ]}}"#;
        let env: PromEnvelope<AlertsData> = serde_json::from_str(body).unwrap();
        let a = env.data.unwrap().alerts.into_iter().next().unwrap();
        assert_eq!(a.labels["alertname"], "WorkStalled");
        assert_eq!(a.state, "firing");
        assert!(a.active_at.is_some());
        assert_eq!(
            a.annotations["runbook"],
            "docs/MONITORING.md#workstalled"
        );
    }
}
