//! Shared Prometheus instrumentation for the Strat Ai services.
//!
//! # Why this crate exists
//!
//! Before this, "is the service working?" was answered by `docker ps` — which
//! reports the container is running and nothing more. Half of these services are
//! Kafka consumers or WebSocket pumps with no request surface, so a wedged
//! consumer is indistinguishable from a quiet market. This crate makes the
//! distinction explicit:
//!
//! - [`Heartbeat`] — the service asserts each completed unit of real work.
//! - [`MarketSession`] — NSE hours, so "idle at 3 a.m." is not "broken".
//! - [`ServiceMetrics`] — the standard metric set every service exports.
//! - [`serve_metrics`] — a `/metrics` + `/health` + `/ready` listener; one call
//!   to adopt.
//!
//! # Adopting it in a service
//!
//! ```no_run
//! use service_metrics::{MetricsConfig, ServiceMetrics};
//!
//! # async fn example() -> Result<(), Box<dyn std::error::Error>> {
//! let metrics = ServiceMetrics::new(MetricsConfig {
//!     service: "ingestion",
//!     version: env!("CARGO_PKG_VERSION"),
//!     // A healthy tick pipeline works many times a second; 60s of silence
//!     // during market hours means something is wrong.
//!     in_session_stall_seconds: 60.0,
//! })?;
//!
//! let hb = metrics.heartbeat();
//! service_metrics::serve_metrics(9101, metrics.clone());
//!
//! loop {
//!     // ... consume a tick, decode it, write it ...
//!     hb.beat(); // only after the work is genuinely done
//! #   break;
//! }
//! # Ok(())
//! # }
//! ```
//!
//! Services with their own metrics register them on [`ServiceMetrics::registry`]
//! so everything lands on the same `/metrics` endpoint.

mod heartbeat;
mod session;

pub use heartbeat::Heartbeat;
pub use session::MarketSession;

/// Re-exported so services declare their own metrics without adding a direct
/// `prometheus` dependency. That keeps one version across the monorepo and
/// stops a service re-enabling the default features this crate deliberately
/// turns off — the protobuf exposition path (unused) and the procfs collector
/// (Linux-only, which would break Windows dev builds).
pub use prometheus;

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;

use axum::extract::State;
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;
use prometheus::{Encoder, Gauge, IntCounter, IntGaugeVec, Opts, Registry, TextEncoder};

/// How a service describes itself to the metrics layer.
#[derive(Debug, Clone)]
pub struct MetricsConfig {
    /// Prometheus metric prefix and `service` label. Use the service's
    /// directory name (`ingestion`, `aggregator`, `alpha-terminal`); dashes are
    /// converted to underscores for metric names but kept in the label.
    pub service: &'static str,
    /// Reported as a `build_info` label, so a stale container is obvious.
    pub version: &'static str,
    /// Seconds of no completed work that counts as a stall *during* market
    /// hours. [`MarketSession`] widens this off-session automatically — set it
    /// from how often the service works when the market is busiest.
    pub in_session_stall_seconds: f64,
}

/// The standard metric set, plus a registry for service-specific metrics.
///
/// Cheap to clone — all state is shared.
#[derive(Clone)]
pub struct ServiceMetrics {
    inner: Arc<Inner>,
}

struct Inner {
    config: MetricsConfig,
    registry: Registry,
    heartbeat: Heartbeat,

    // Gauges are refreshed on scrape rather than continuously, so nothing on
    // the service's hot path pays for them.
    up: Gauge,
    uptime: Gauge,
    last_work_age: Gauge,
    stall_threshold: Gauge,
    session: Gauge,
    work_total: IntCounter,

    /// How much of `Heartbeat::work_count` has already been folded into
    /// `work_total`. Lets a plain atomic counter drive a monotonic Prometheus
    /// counter without the hot path touching Prometheus at all.
    exported_work: AtomicU64,

    /// Whether the service currently has anything to do at all. See
    /// [`ServiceMetrics::set_work_expected`].
    work_expected: AtomicBool,
    work_expected_gauge: Gauge,
}

impl ServiceMetrics {
    /// Build the standard metric set for a service.
    ///
    /// Fails only if a metric name collides, which would mean two
    /// `ServiceMetrics` for the same service in one process.
    pub fn new(config: MetricsConfig) -> Result<Self, prometheus::Error> {
        let registry = Registry::new();
        let p = config.service.replace('-', "_");
        let svc = config.service;

        let g = |name: String, help: &str| -> Result<Gauge, prometheus::Error> {
            let g = Gauge::with_opts(Opts::new(name, help).const_label("service", svc))?;
            registry.register(Box::new(g.clone()))?;
            Ok(g)
        };

        let up = g(
            format!("{p}_up"),
            "1 while the service's metrics listener is serving. Absence of the \
             metric (a scrape failure) is the real down signal.",
        )?;
        up.set(1.0);

        let uptime = g(
            format!("{p}_uptime_seconds"),
            "Seconds since the process started. A resetting value means crash-looping.",
        )?;

        let last_work_age = g(
            format!("{p}_last_work_age_seconds"),
            "Seconds since the service last completed a unit of real work. The \
             primary working-vs-failing signal; compare against \
             *_stall_threshold_seconds.",
        )?;

        let stall_threshold = g(
            format!("{p}_stall_threshold_seconds"),
            "Age above which this service is considered stalled, already widened \
             for the current market session. Exported so alert rules compare two \
             metrics instead of reimplementing NSE hours in PromQL.",
        )?;

        let session = g(
            format!("{p}_market_session_open"),
            "1 during NSE trading hours (09:15-15:30 IST, weekdays), else 0.",
        )?;

        let work_expected_gauge = g(
            format!("{p}_work_expected"),
            "1 when the service has something to do (subscriptions, an upstream \
             connection), 0 when legitimately idle. Staleness is only a stall \
             while this is 1.",
        )?;
        work_expected_gauge.set(1.0);

        let work_total = IntCounter::with_opts(
            Opts::new(
                format!("{p}_work_completed_total"),
                "Total units of real work completed since start. Its rate is the \
                 throughput signal; a flat line during session hours is a stall.",
            )
            .const_label("service", svc),
        )?;
        registry.register(Box::new(work_total.clone()))?;

        let build_info = IntGaugeVec::new(
            Opts::new(
                format!("{p}_build_info"),
                "Always 1; the labels carry the build identity.",
            )
            .const_label("service", svc),
            &["version"],
        )?;
        build_info.with_label_values(&[config.version]).set(1);
        registry.register(Box::new(build_info))?;

        Ok(Self {
            inner: Arc::new(Inner {
                config,
                registry,
                heartbeat: Heartbeat::new(),
                up,
                uptime,
                last_work_age,
                stall_threshold,
                session,
                work_total,
                exported_work: AtomicU64::new(0),
                work_expected: AtomicBool::new(true),
                work_expected_gauge,
            }),
        })
    }

    /// The heartbeat to `beat()` on each completed unit of work.
    pub fn heartbeat(&self) -> Heartbeat {
        self.inner.heartbeat.clone()
    }

    /// Declare whether the service currently has anything to do.
    ///
    /// Defaults to `true`. Set it to `false` when the service is legitimately
    /// idle *by design* rather than broken — `ingestion` boots with zero
    /// subscriptions and waits for control-port commands, so without this it
    /// would report a stall that grows forever while behaving exactly as
    /// intended. While `false`, staleness is not treated as a stall; the age is
    /// still exported, so the idleness itself remains visible.
    ///
    /// Set it back to `true` as soon as real work is expected again, otherwise
    /// a genuine failure would be masked.
    pub fn set_work_expected(&self, expected: bool) {
        self.inner.work_expected.store(expected, Ordering::Relaxed);
    }

    /// The registry, for a service to add its own metrics to this endpoint.
    pub fn registry(&self) -> &Registry {
        &self.inner.registry
    }

    /// The service's name, as configured.
    pub fn service(&self) -> &'static str {
        self.inner.config.service
    }

    /// Refresh the sampled gauges and encode the registry in Prometheus text
    /// format.
    pub fn render(&self) -> Result<String, prometheus::Error> {
        self.refresh();
        let encoder = TextEncoder::new();
        let mut buf = Vec::new();
        encoder.encode(&self.inner.registry.gather(), &mut buf)?;
        String::from_utf8(buf)
            .map_err(|e| prometheus::Error::Msg(format!("metrics were not valid UTF-8: {e}")))
    }

    /// Whether the service has gone longer without work than the current
    /// session allows. Returns the age and threshold alongside so callers can
    /// report *why*.
    pub fn readiness(&self) -> Readiness {
        self.refresh();
        let session = MarketSession::now();
        let age = self.inner.heartbeat.last_work_age_seconds();
        let threshold = session.stall_threshold_seconds(self.inner.config.in_session_stall_seconds);
        let work_expected = self.inner.work_expected.load(Ordering::Relaxed);
        Readiness {
            // Only a service that is *supposed* to be working can stall. An
            // ingestion process with no subscriptions is idle, not broken.
            stalled: work_expected && age > threshold,
            work_expected,
            age_seconds: age,
            threshold_seconds: threshold,
            session,
            work_completed: self.inner.heartbeat.work_count(),
            uptime_seconds: self.inner.heartbeat.uptime_seconds(),
        }
    }

    /// Pull current values out of the heartbeat and clock into the gauges.
    fn refresh(&self) {
        let i = &self.inner;
        let session = MarketSession::now();

        i.up.set(1.0);
        i.uptime.set(i.heartbeat.uptime_seconds());
        i.last_work_age.set(i.heartbeat.last_work_age_seconds());
        i.stall_threshold
            .set(session.stall_threshold_seconds(i.config.in_session_stall_seconds));
        i.session.set(if session.is_open() { 1.0 } else { 0.0 });
        i.work_expected_gauge.set(
            if i.work_expected.load(Ordering::Relaxed) {
                1.0
            } else {
                0.0
            },
        );

        // Fold any work completed since the last refresh into the counter.
        //
        // fetch_update rather than swap: /metrics, /health and /ready each call
        // refresh() and can overlap. A plain swap lets a slower caller move
        // `exported` *backwards* to its own stale reading, after which the next
        // refresh re-adds work already counted — every throughput graph drifts
        // upward. Only advancing the watermark makes concurrent refreshes at
        // worst a no-op instead of a double count.
        let observed = i.heartbeat.work_count();
        let previous = i
            .exported_work
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |exported| {
                (observed > exported).then_some(observed)
            });
        if let Ok(exported) = previous {
            i.work_total.inc_by(observed - exported);
        }
    }
}

/// The answer to "is this service actually working right now?".
#[derive(Debug, Clone)]
pub struct Readiness {
    /// True when work was expected and `age_seconds` exceeds
    /// `threshold_seconds`.
    pub stalled: bool,
    /// Whether the service had anything to do — see
    /// [`ServiceMetrics::set_work_expected`]. A stale-but-not-expected service
    /// reports `idle`, not `stalled`.
    pub work_expected: bool,
    pub age_seconds: f64,
    pub threshold_seconds: f64,
    pub session: MarketSession,
    pub work_completed: u64,
    pub uptime_seconds: f64,
}

impl Readiness {
    /// JSON body shared by `/health` and `/ready`.
    ///
    /// `status` is one of:
    /// - `ok`      — working within its threshold.
    /// - `stalled` — work was expected and none has happened in time.
    /// - `idle`    — nothing to do (no subscriptions / no upstream demand).
    ///   Reported distinctly so an operator is not sent chasing a stall that
    ///   is really "nobody has asked for anything yet".
    ///
    /// Hand-rolled rather than via serde: every field is a number, a bool or a
    /// name this crate controls, so there is nothing to escape — and it saves
    /// pulling serde into the dependency graph of a crate that seven services
    /// link.
    pub fn to_json(&self, service: &str) -> String {
        let status = match (self.stalled, self.work_expected) {
            (true, _) => "stalled",
            (false, true) => "ok",
            (false, false) => "idle",
        };
        format!(
            concat!(
                r#"{{"service":"{}","status":"{}","market_session":"{}","#,
                r#""work_expected":{},"last_work_age_seconds":{:.1},"#,
                r#""stall_threshold_seconds":{:.1},"work_completed":{},"#,
                r#""uptime_seconds":{:.1}}}"#
            ),
            service,
            status,
            self.session.as_str(),
            self.work_expected,
            self.age_seconds,
            self.threshold_seconds,
            self.work_completed,
            self.uptime_seconds,
        )
    }
}

/// Spawn the metrics listener on `port` in the background.
///
/// Binds `0.0.0.0` so Prometheus can reach it across the compose network; these
/// ports are never published past the host by the compose files. Serves:
///
/// - `GET /metrics` — Prometheus text exposition.
/// - `GET /health`  — liveness. 200 whenever the process can answer.
/// - `GET /ready`   — 200 when working, 503 when stalled past the session
///   threshold. This is what compose healthchecks and the status API read.
///
/// A bind failure is logged, not fatal: instrumentation must never take down the
/// service it observes.
pub fn serve_metrics(port: u16, metrics: ServiceMetrics) {
    tokio::spawn(async move {
        let service = metrics.service();
        let app = Router::new()
            .route("/metrics", get(metrics_handler))
            .route("/health", get(health_handler))
            .route("/ready", get(ready_handler))
            .with_state(metrics);

        let addr = format!("0.0.0.0:{port}");
        let listener = match tokio::net::TcpListener::bind(&addr).await {
            Ok(l) => l,
            Err(e) => {
                log::error!(
                    "[{service}] metrics listener could not bind {addr}: {e}. \
                     The service continues without instrumentation; Prometheus \
                     will report it as down."
                );
                return;
            }
        };

        log::info!("[{service}] metrics listening on {addr} (/metrics, /health, /ready)");
        if let Err(e) = axum::serve(listener, app).await {
            log::error!("[{service}] metrics listener stopped: {e}");
        }
    });
}

async fn metrics_handler(State(metrics): State<ServiceMetrics>) -> Response {
    match metrics.render() {
        Ok(body) => (
            StatusCode::OK,
            [(header::CONTENT_TYPE, "text/plain; version=0.0.4")],
            body,
        )
            .into_response(),
        Err(e) => {
            log::error!("[{}] failed to encode metrics: {e}", metrics.service());
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                format!("failed to encode metrics: {e}"),
            )
                .into_response()
        }
    }
}

/// Liveness: the process is up and its runtime is responsive. Deliberately does
/// not consider staleness — a stalled service should be *reported*, not killed
/// and restarted by an orchestrator that cannot fix the cause.
async fn health_handler(State(metrics): State<ServiceMetrics>) -> Response {
    json_ok(metrics.readiness().to_json(metrics.service()))
}

/// Readiness: the service is completing work at the rate its session implies.
async fn ready_handler(State(metrics): State<ServiceMetrics>) -> Response {
    let readiness = metrics.readiness();
    let body = readiness.to_json(metrics.service());
    let code = if readiness.stalled {
        StatusCode::SERVICE_UNAVAILABLE
    } else {
        StatusCode::OK
    };
    (
        code,
        [(header::CONTENT_TYPE, "application/json")],
        body,
    )
        .into_response()
}

fn json_ok(body: String) -> Response {
    (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "application/json")],
        body,
    )
        .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_metrics(service: &'static str, stall: f64) -> ServiceMetrics {
        ServiceMetrics::new(MetricsConfig {
            service,
            version: "0.0.0-test",
            in_session_stall_seconds: stall,
        })
        .expect("standard metric set must construct")
    }

    #[test]
    fn exposition_contains_the_standard_metrics() {
        let m = test_metrics("ingestion", 60.0);
        let out = m.render().expect("render");

        for name in [
            "ingestion_up",
            "ingestion_uptime_seconds",
            "ingestion_last_work_age_seconds",
            "ingestion_stall_threshold_seconds",
            "ingestion_market_session_open",
            "ingestion_work_completed_total",
            "ingestion_build_info",
        ] {
            assert!(out.contains(name), "missing {name} in:\n{out}");
        }
        assert!(out.contains(r#"service="ingestion""#));
        assert!(out.contains(r#"version="0.0.0-test""#));
    }

    #[test]
    fn dashes_in_service_names_become_underscores() {
        // `alpha-terminal` is a real service name and a dash is not legal in a
        // Prometheus metric name — it would be rejected at scrape time.
        let m = test_metrics("alpha-terminal", 60.0);
        let out = m.render().expect("render");
        assert!(out.contains("alpha_terminal_up"));
        assert!(!out.contains("alpha-terminal_up"));
        // The label keeps the real name so dashboards match the compose service.
        assert!(out.contains(r#"service="alpha-terminal""#));
    }

    #[test]
    fn work_counter_tracks_heartbeat_beats() {
        let m = test_metrics("aggregator", 60.0);
        let hb = m.heartbeat();
        hb.beat();
        hb.beat();
        hb.beat();

        let out = m.render().expect("render");
        let line = out
            .lines()
            .find(|l| l.starts_with("aggregator_work_completed_total{"))
            .expect("counter line present");
        assert!(line.ends_with(" 3"), "expected 3 beats, got: {line}");
    }

    #[test]
    fn work_counter_does_not_double_count_across_renders() {
        // refresh() runs on every scrape; folding the same beats in twice would
        // inflate every throughput graph.
        let m = test_metrics("technical", 60.0);
        let hb = m.heartbeat();
        hb.beat();
        m.render().expect("first render");
        m.render().expect("second render");
        hb.beat();

        let out = m.render().expect("third render");
        let line = out
            .lines()
            .find(|l| l.starts_with("technical_work_completed_total{"))
            .expect("counter line present");
        assert!(line.ends_with(" 2"), "expected 2 beats, got: {line}");
    }

    #[test]
    fn fresh_beat_is_not_stalled() {
        let m = test_metrics("predictive", 60.0);
        m.heartbeat().beat();
        let r = m.readiness();
        assert!(!r.stalled, "just-beaten service must not read as stalled");
        assert_eq!(r.work_completed, 1);
    }

    #[test]
    fn zero_threshold_makes_any_age_a_stall() {
        // A 0s in-session threshold still widens off-session, so assert against
        // the threshold the readiness itself reports rather than assuming the
        // test runs during market hours.
        let m = test_metrics("quant-rag", 0.0);
        m.heartbeat().beat();
        let r = m.readiness();
        assert_eq!(r.stalled, r.age_seconds > r.threshold_seconds);
    }

    #[test]
    fn readiness_threshold_matches_the_session() {
        let m = test_metrics("ohlc", 45.0);
        let r = m.readiness();
        assert_eq!(r.threshold_seconds, r.session.stall_threshold_seconds(45.0));
        assert!(
            r.threshold_seconds >= 45.0,
            "off-session thresholds widen, never narrow"
        );
    }

    #[test]
    fn exported_threshold_gauge_matches_readiness() {
        // Alert rules compare *_last_work_age_seconds against this gauge, so a
        // mismatch between the gauge and the /ready verdict would mean the
        // dashboard and the healthcheck disagree.
        let m = test_metrics("insight", 30.0);
        let r = m.readiness();
        let out = m.render().expect("render");
        let line = out
            .lines()
            .find(|l| l.starts_with("insight_stall_threshold_seconds{"))
            .expect("threshold line present");
        let value: f64 = line.rsplit(' ').next().unwrap().parse().unwrap();
        assert_eq!(value, r.threshold_seconds);
    }

    #[test]
    fn readiness_json_is_well_formed() {
        let m = test_metrics("ingestion", 60.0);
        m.heartbeat().beat();
        let json = m.readiness().to_json("ingestion");

        assert!(json.starts_with('{') && json.ends_with('}'));
        assert!(!json.contains('\n'), "body must be a single line: {json}");
        assert!(!json.contains('\\'), "no stray escapes: {json}");
        assert!(json.contains(r#""service":"ingestion""#));
        assert!(json.contains(r#""status":"ok""#));
        assert!(json.contains(r#""work_completed":1"#));
        for key in [
            "market_session",
            "last_work_age_seconds",
            "stall_threshold_seconds",
            "uptime_seconds",
        ] {
            assert!(json.contains(&format!(r#""{key}":"#)), "missing {key}");
        }
    }

    #[test]
    fn stalled_status_is_reported_in_json() {
        let m = test_metrics("ingestion", 0.0);
        let mut r = m.readiness();
        r.stalled = true;
        assert!(r.to_json("ingestion").contains(r#""status":"stalled""#));
    }

    #[test]
    fn idle_service_is_not_reported_as_stalled() {
        // ingestion boots with zero subscriptions and waits for control-port
        // commands. Without the work-expected gate its age grows forever and it
        // would page as a stall while behaving exactly as designed.
        let m = test_metrics("ingestion", 0.0);
        m.set_work_expected(false);

        let r = m.readiness();
        assert!(!r.stalled, "an idle service must not read as stalled");
        assert!(!r.work_expected);
        assert!(
            r.to_json("ingestion").contains(r#""status":"idle""#),
            "idle must be distinguishable from ok: {}",
            r.to_json("ingestion")
        );
    }

    #[test]
    fn idle_still_exports_the_age_so_idleness_stays_visible() {
        // Suppressing the *alert* must not suppress the *signal* — an operator
        // still needs to see how long the service has had nothing to do.
        let m = test_metrics("ingestion", 60.0);
        m.set_work_expected(false);
        let r = m.readiness();
        assert!(r.age_seconds >= 0.0);
        assert!(r
            .to_json("ingestion")
            .contains(r#""last_work_age_seconds":"#));
    }

    #[test]
    fn work_expected_gauge_tracks_the_flag() {
        let m = test_metrics("ingestion", 60.0);

        // Defaults to expecting work, so a service that never calls the setter
        // is monitored strictly rather than silently exempted.
        assert!(m.readiness().work_expected);
        assert!(m
            .render()
            .unwrap()
            .contains(r#"ingestion_work_expected{service="ingestion"} 1"#));

        m.set_work_expected(false);
        assert!(m
            .render()
            .unwrap()
            .contains(r#"ingestion_work_expected{service="ingestion"} 0"#));

        // Re-arming must restore strict monitoring, or a genuine failure after
        // an idle spell would stay hidden.
        m.set_work_expected(true);
        assert!(m
            .render()
            .unwrap()
            .contains(r#"ingestion_work_expected{service="ingestion"} 1"#));
    }

    #[test]
    fn re_arming_after_idle_restores_stall_detection() {
        let m = test_metrics("ingestion", 0.0);
        m.set_work_expected(false);
        assert!(!m.readiness().stalled);

        // A subscription arrives: work is expected again, and the stale age now
        // counts against the service.
        m.set_work_expected(true);
        let r = m.readiness();
        assert_eq!(r.stalled, r.age_seconds > r.threshold_seconds);
    }

    #[test]
    fn concurrent_refreshes_never_double_count() {
        // /metrics, /health and /ready each refresh(), and Prometheus scraping
        // while a healthcheck runs makes overlap routine. With a plain swap
        // watermark, an interleaving where a slow reader writes its stale count
        // back re-adds work already exported and the counter overshoots.
        use std::sync::Barrier;
        use std::thread;

        const BEATS: u64 = 500;
        const READERS: usize = 8;

        let m = test_metrics("ingestion", 60.0);
        let hb = m.heartbeat();
        let barrier = Arc::new(Barrier::new(READERS + 1));

        let readers: Vec<_> = (0..READERS)
            .map(|_| {
                let m = m.clone();
                let barrier = Arc::clone(&barrier);
                thread::spawn(move || {
                    barrier.wait();
                    for _ in 0..200 {
                        m.readiness();
                    }
                })
            })
            .collect();

        barrier.wait();
        for _ in 0..BEATS {
            hb.beat();
        }
        for r in readers {
            r.join().unwrap();
        }

        let out = m.render().expect("render");
        let line = out
            .lines()
            .find(|l| l.starts_with("ingestion_work_completed_total{"))
            .expect("counter line present");
        let exported: u64 = line.rsplit(' ').next().unwrap().parse().unwrap();
        assert_eq!(
            exported, BEATS,
            "counter must equal the beats recorded, never exceed them: {line}"
        );
    }

    #[test]
    fn service_metrics_clones_share_the_heartbeat() {
        // serve_metrics() takes a clone while the worker loop keeps another.
        let m = test_metrics("aggregator", 60.0);
        let clone = m.clone();
        m.heartbeat().beat();
        assert_eq!(clone.readiness().work_completed, 1);
    }

    #[test]
    fn service_specific_metrics_land_on_the_same_endpoint() {
        let m = test_metrics("aggregator", 60.0);
        let custom = prometheus::IntCounter::new("kite_requests_total", "test counter").unwrap();
        custom.inc();
        m.registry().register(Box::new(custom)).unwrap();

        let out = m.render().expect("render");
        assert!(out.contains("kite_requests_total 1"), "got:\n{out}");
    }
}
