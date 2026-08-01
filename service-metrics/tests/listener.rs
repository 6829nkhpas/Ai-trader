//! End-to-end test of the listener that [`service_metrics::serve_metrics`]
//! spawns.
//!
//! The unit tests in `lib.rs` cover rendering and classification, but never
//! bind a socket — so they would pass even if routing, status codes or content
//! types were wrong. Every service depends on this listener behaving, and the
//! compose healthchecks read its status codes, so it is worth exercising over a
//! real TCP connection.
//!
//! Raw TCP rather than an HTTP client: it keeps the dependency graph of a crate
//! that seven services link down to what it already needs.

use std::time::Duration;

use service_metrics::{serve_metrics, MetricsConfig, ServiceMetrics};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

/// Issue a GET and return `(status_line, headers, body)`.
async fn get(port: u16, path: &str) -> (String, String, String) {
    let mut stream = TcpStream::connect(("127.0.0.1", port))
        .await
        .expect("metrics listener should be accepting connections");

    stream
        .write_all(
            format!("GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                .as_bytes(),
        )
        .await
        .expect("write request");

    let mut raw = String::new();
    stream
        .read_to_string(&mut raw)
        .await
        .expect("read response");

    let (head, body) = raw
        .split_once("\r\n\r\n")
        .map(|(h, b)| (h.to_string(), b.to_string()))
        .unwrap_or((raw.clone(), String::new()));
    let status = head.lines().next().unwrap_or_default().to_string();
    (status, head, body)
}

/// Bind an ephemeral port and release it, so the test picks a free port instead
/// of racing other tests on a hardcoded one.
async fn free_port() -> u16 {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind ephemeral");
    listener.local_addr().expect("local addr").port()
}

async fn start(service: &'static str, stall_seconds: f64) -> (u16, ServiceMetrics) {
    let metrics = ServiceMetrics::new(MetricsConfig {
        service,
        version: "0.0.0-test",
        in_session_stall_seconds: stall_seconds,
    })
    .expect("metric set");

    let port = free_port().await;
    serve_metrics(port, metrics.clone());

    // Give the spawned task a moment to bind before the first connect.
    for _ in 0..50 {
        if TcpStream::connect(("127.0.0.1", port)).await.is_ok() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }

    (port, metrics)
}

#[tokio::test]
async fn metrics_endpoint_serves_prometheus_text() {
    let (port, metrics) = start("ingestion", 60.0).await;
    metrics.heartbeat().beat();

    let (status, headers, body) = get(port, "/metrics").await;
    assert!(status.contains("200"), "status was: {status}");
    assert!(
        headers.to_lowercase().contains("text/plain"),
        "Prometheus requires a text content type; headers:\n{headers}"
    );

    // The exposition format Prometheus actually parses: HELP/TYPE then samples.
    assert!(body.contains("# HELP ingestion_last_work_age_seconds"));
    assert!(body.contains("# TYPE ingestion_work_completed_total counter"));
    assert!(body.contains(r#"ingestion_up{service="ingestion"} 1"#));
    assert!(body.contains(r#"ingestion_work_completed_total{service="ingestion"} 1"#));
}

#[tokio::test]
async fn health_reports_200_even_when_stalled() {
    // Liveness must not fail on staleness: an orchestrator restarting a stalled
    // consumer would hide the stall and lose the diagnostic state with it.
    let (port, _metrics) = start("technical", 0.0).await;

    let (status, headers, body) = get(port, "/health").await;
    assert!(status.contains("200"), "status was: {status}");
    assert!(headers.to_lowercase().contains("application/json"));
    assert!(body.contains(r#""service":"technical""#), "body: {body}");
}

#[tokio::test]
async fn ready_returns_503_when_work_has_stalled() {
    // A 0s in-session threshold; if the suite runs off-session the threshold
    // widens, so assert the status code against what /ready itself reports
    // rather than assuming the wall clock.
    let (port, metrics) = start("predictive", 0.0).await;
    let expected_stalled = metrics.readiness().stalled;

    let (status, _headers, body) = get(port, "/ready").await;
    if expected_stalled {
        assert!(status.contains("503"), "status was: {status}");
        assert!(body.contains(r#""status":"stalled""#), "body: {body}");
    } else {
        assert!(status.contains("200"), "status was: {status}");
        assert!(body.contains(r#""status":"ok""#), "body: {body}");
    }
}

#[tokio::test]
async fn ready_returns_200_while_work_is_flowing() {
    // A generous threshold, so this holds whatever the session.
    let (port, metrics) = start("aggregator", 86_400.0).await;
    metrics.heartbeat().beat();

    let (status, _headers, body) = get(port, "/ready").await;
    assert!(status.contains("200"), "status was: {status}");
    assert!(body.contains(r#""status":"ok""#), "body: {body}");
}

#[tokio::test]
async fn unknown_paths_404_rather_than_erroring() {
    let (port, _metrics) = start("ohlc", 60.0).await;
    let (status, _headers, _body) = get(port, "/does-not-exist").await;
    assert!(status.contains("404"), "status was: {status}");
}

#[tokio::test]
async fn a_bind_failure_does_not_panic_the_service() {
    // Instrumentation must never take down what it observes. Occupy a port,
    // point serve_metrics at it, and confirm the process survives.
    let occupied = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let port = occupied.local_addr().unwrap().port();

    let metrics = ServiceMetrics::new(MetricsConfig {
        service: "sentiment",
        version: "0.0.0-test",
        in_session_stall_seconds: 60.0,
    })
    .expect("metric set");

    serve_metrics(port, metrics.clone());
    tokio::time::sleep(Duration::from_millis(200)).await;

    // Still usable: the service carries on without instrumentation.
    metrics.heartbeat().beat();
    assert_eq!(metrics.readiness().work_completed, 1);
}
