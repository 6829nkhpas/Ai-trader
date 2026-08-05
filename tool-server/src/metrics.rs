// src/metrics.rs — the tool-server's Prometheus surface (:9107).
//
// WHY THIS EXISTS: this service answers the /tools/* calls the deep-quant
// LangGraph agent makes while analysing a trade, backed by QuestDB over the PG
// wire. Every other instrumented service in this pipeline is a stream loop; this
// one is request/response, and that difference changes what monitoring can
// honestly claim.
//
// THE CENTRAL DIFFERENCE: this service is *idle by design*. It does nothing at
// all until a user runs an analysis, so hours of silence during market hours are
// completely normal. `work_expected` is therefore left permanently false and the
// stall detector never arms — an alert on "no tool calls" would fire on any quiet
// afternoon and mean nothing. The heartbeat still beats per request, because
// `work_completed_total` and `last_work_age_seconds` remain useful as *facts*
// (throughput, time since last use) for the status API to report. They are
// simply not alertable here.
//
// WHAT IS WORTH ALERTING ON is the failure this service is uniquely prone to.
// Its data-availability contract answers HTTP 200 with `{"unavailable": true}`
// when the candle store has no history — deliberately, so the agent treats a
// missing input as a missing input rather than an error. The consequence is that
// a completely empty QuestDB produces a service where every request succeeds,
// every status code is 200, and every answer is empty. From the outside it looks
// perfect. `tool_unavailable_total` is the metric that makes that state visible,
// and it is the most valuable series this module exports.
//
//   tool_calls_total{tool,outcome}   requests by tool and 2xx/4xx/5xx class
//   tool_duration_seconds{tool}      handler latency
//   tool_unavailable_total{tool}     200 OK carrying an "unavailable" marker
//   db_errors_total{source}          QuestDB faults, by query site
//   db_pool_connections{state}       pool occupancy: in_use vs idle
//   active_watchers                  registered price-level watchers
//   watcher_triggers_total           watchers whose condition fired
//   resume_failures_total            triggers that never reached deep-quant
//
// Every method is a no-op when instrumentation failed to initialise, so call
// sites stay unconditional and a metrics fault can never take down the service.

use service_metrics::prometheus::{
    Gauge, GaugeVec, HistogramOpts, HistogramVec, IntCounter, IntCounterVec, Opts,
};
use service_metrics::{Heartbeat, MetricsConfig, ServiceMetrics};

/// Default port for the tool-server's `/metrics`, `/health` and `/ready`
/// endpoints. Overridable with `METRICS_PORT`.
const DEFAULT_METRICS_PORT: u16 = 9107;

/// Nominal stall threshold. Recorded for completeness and exported by the shared
/// crate, but inert in practice: `work_expected` is never set true here, so
/// `stalled` cannot become true whatever this value is. See the module header.
const IN_SESSION_STALL_SECONDS: f64 = 300.0;

/// The nine tool routes, pre-created so every tool reports an explicit 0 before
/// its first call. `rate()` over an absent series renders as "no data", which is
/// indistinguishable from a broken scrape — and a tool the agent has not called
/// yet is exactly the series that would be missing.
const TOOLS: [&str; 9] = [
    "get_candles",
    "get_consensus",
    "get_support_resistance",
    "get_chart_patterns",
    "get_multi_tf_trend",
    "get_prediction",
    "get_news_context",
    "declare_trade",
    "watch_condition",
];

/// Response classes. Kept to the status class rather than the exact code: the
/// operational question is "did the agent get an answer", and per-code series
/// would multiply cardinality across nine tools for no added signal.
const OUTCOMES: [&str; 3] = ["success", "client_error", "server_error"];

/// Query sites that can report a QuestDB fault, so a failure can be attributed
/// without reading logs.
const DB_ERROR_SOURCES: [&str; 2] = ["candle_load", "migrate"];

/// Latency buckets in seconds. Tool calls are QuestDB reads plus quant maths —
/// tens of milliseconds when warm — but `get_news_context` reaches out to the
/// sentiment service and Google News with a 10s timeout, so the range has to
/// span both without putting either end in `+Inf`.
const DURATION_BUCKETS: [f64; 10] = [
    0.005, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
];

/// The tool-server's metric handles.
///
/// Cheap to clone. Construction never fails: on registry failure the handle is
/// inert and every method does nothing, so instrumentation cannot break the
/// service it observes.
#[derive(Clone)]
pub struct ToolServerMetrics {
    inner: Option<Handles>,
}

#[derive(Clone)]
struct Handles {
    base: ServiceMetrics,
    heartbeat: Heartbeat,
    calls: IntCounterVec,
    duration: HistogramVec,
    unavailable: IntCounterVec,
    db_errors: IntCounterVec,
    pool: GaugeVec,
    active_watchers: Gauge,
    watcher_triggers: IntCounter,
    resume_failures: IntCounter,
}

impl ToolServerMetrics {
    /// Build the metrics, degrading to an inert handle on failure.
    pub fn new() -> Self {
        match Self::try_build() {
            Ok(handles) => Self {
                inner: Some(handles),
            },
            Err(e) => {
                log::error!(
                    "Prometheus metrics unavailable: {e}. The tool-server continues \
                     uninstrumented and will scrape as down — investigate before \
                     relying on monitoring."
                );
                Self { inner: None }
            }
        }
    }

    fn try_build() -> Result<Handles, service_metrics::prometheus::Error> {
        let base = ServiceMetrics::new(MetricsConfig {
            service: "tool-server",
            version: env!("CARGO_PKG_VERSION"),
            in_session_stall_seconds: IN_SESSION_STALL_SECONDS,
        })?;
        let registry = base.registry();

        let calls = IntCounterVec::new(
            Opts::new(
                "tool_server_tool_calls_total",
                "Tool invocations by tool and response class. Zero across all \
                 tools is normal — nothing runs until a user starts an analysis \
                 — so this is a throughput and error-ratio signal, not a \
                 liveness one.",
            ),
            &["tool", "outcome"],
        )?;
        registry.register(Box::new(calls.clone()))?;

        let duration = HistogramVec::new(
            HistogramOpts::new(
                "tool_server_tool_duration_seconds",
                "Handler latency per tool. The agent runs these calls inside a \
                 user-visible analysis, so a QuestDB slowdown surfaces here as a \
                 slow product before it surfaces as an error anywhere.",
            )
            .buckets(DURATION_BUCKETS.to_vec()),
            &["tool"],
        )?;
        registry.register(Box::new(duration.clone()))?;

        let unavailable = IntCounterVec::new(
            Opts::new(
                "tool_server_tool_unavailable_total",
                "Calls answered HTTP 200 with an `unavailable` marker because the \
                 data was not there. This is the service's most important series: \
                 the availability contract means an empty candle store yields all \
                 200s and all empty answers, so status codes alone report perfect \
                 health while the agent is reasoning from nothing. Read as a ratio \
                 against tool_calls_total.",
            ),
            &["tool"],
        )?;
        registry.register(Box::new(unavailable.clone()))?;

        let db_errors = IntCounterVec::new(
            Opts::new(
                "tool_server_db_errors_total",
                "QuestDB query faults by site. Distinct from the unavailable \
                 marker above: that one means the store answered and had no rows, \
                 this means the store could not be reached or the query failed.",
            ),
            &["source"],
        )?;
        registry.register(Box::new(db_errors.clone()))?;

        let pool = GaugeVec::new(
            Opts::new(
                "tool_server_db_pool_connections",
                "QuestDB PG pool occupancy. in_use at the 10-connection ceiling \
                 with idle at 0 means requests are queueing for a connection, \
                 which presents to the user as a slow analysis rather than an \
                 error and is invisible in every other metric here.",
            ),
            &["state"],
        )?;
        registry.register(Box::new(pool.clone()))?;

        let active_watchers = Gauge::with_opts(Opts::new(
            "tool_server_active_watchers",
            "Registered price-level watchers, each holding a QuestDB-polling \
             task. Ratcheting upward across days means watchers are being \
             registered and never resolving — a slow leak of both memory and \
             poll load that nothing else reports.",
        ))?;
        registry.register(Box::new(active_watchers.clone()))?;

        let watcher_triggers = IntCounter::with_opts(Opts::new(
            "tool_server_watcher_triggers_total",
            "Watchers whose price/volume condition fired.",
        ))?;
        registry.register(Box::new(watcher_triggers.clone()))?;

        let resume_failures = IntCounter::with_opts(Opts::new(
            "tool_server_resume_failures_total",
            "Triggered watchers whose /resume POST to deep-quant failed. The \
             worst failure this service has: the condition the user was waiting \
             for happened, and the notification was dropped. The watcher is \
             deregistered either way, so nothing retries and nothing else logs a \
             fault.",
        ))?;
        registry.register(Box::new(resume_failures.clone()))?;

        // Instantiate every label series at 0 — see TOOLS.
        for tool in TOOLS {
            duration.with_label_values(&[tool]);
            unavailable.with_label_values(&[tool]);
            for outcome in OUTCOMES {
                calls.with_label_values(&[tool, outcome]);
            }
        }
        for source in DB_ERROR_SOURCES {
            db_errors.with_label_values(&[source]);
        }
        for state in ["in_use", "idle"] {
            pool.with_label_values(&[state]);
        }

        let heartbeat = base.heartbeat();

        // Left false for the process lifetime, and deliberately never set true.
        // This service has nothing to do between analyses, so an armed stall
        // detector would report an outage on every quiet afternoon. See the
        // module header.
        base.set_work_expected(false);

        Ok(Handles {
            base,
            heartbeat,
            calls,
            duration,
            unavailable,
            db_errors,
            pool,
            active_watchers,
            watcher_triggers,
            resume_failures,
        })
    }

    /// Start the `/metrics`, `/health` and `/ready` listener.
    pub fn serve(&self) {
        let Some(h) = &self.inner else { return };
        let port = std::env::var("METRICS_PORT")
            .ok()
            .and_then(|p| p.parse::<u16>().ok())
            .unwrap_or(DEFAULT_METRICS_PORT);
        service_metrics::serve_metrics(port, h.base.clone());
    }

    /// A tool call completed. `status` is the HTTP status the handler returned.
    ///
    /// The heartbeat beats here so `work_completed_total` counts requests and
    /// `last_work_age_seconds` reports time since last use. Neither can raise a
    /// stall, because `work_expected` is never armed — see the module header.
    pub fn tool_call_completed(&self, tool: &str, status: u16, seconds: f64) {
        if let Some(h) = &self.inner {
            let outcome = match status {
                200..=399 => "success",
                400..=499 => "client_error",
                _ => "server_error",
            };
            h.calls.with_label_values(&[tool, outcome]).inc();
            h.duration.with_label_values(&[tool]).observe(seconds);
            h.heartbeat.beat();
        }
    }

    /// A call succeeded but carried an `unavailable` marker instead of data.
    ///
    /// Counted separately from the call itself, which is also recorded as a
    /// success — because it *was* one at the HTTP layer. The pair is the point:
    /// the ratio is what distinguishes a working service from one answering
    /// every request with nothing.
    pub fn tool_unavailable(&self, tool: &str) {
        if let Some(h) = &self.inner {
            h.unavailable.with_label_values(&[tool]).inc();
        }
    }

    /// A QuestDB query failed. `source` should be one of [`DB_ERROR_SOURCES`].
    pub fn db_error(&self, source: &str) {
        if let Some(h) = &self.inner {
            h.db_errors.with_label_values(&[source]).inc();
        }
    }

    /// Sample the connection pool. Called on a timer rather than per request,
    /// because exhaustion is most interesting exactly when requests are blocked
    /// waiting for a connection and no handler is reaching this code.
    pub fn set_pool_state(&self, in_use: u32, idle: usize) {
        if let Some(h) = &self.inner {
            h.pool.with_label_values(&["in_use"]).set(in_use as f64);
            h.pool.with_label_values(&["idle"]).set(idle as f64);
        }
    }

    /// Report the number of currently registered watchers.
    pub fn set_active_watchers(&self, count: usize) {
        if let Some(h) = &self.inner {
            h.active_watchers.set(count as f64);
        }
    }

    /// A watcher's condition fired.
    pub fn watcher_triggered(&self) {
        if let Some(h) = &self.inner {
            h.watcher_triggers.inc();
        }
    }

    /// A triggered watcher failed to notify deep-quant.
    pub fn resume_failed(&self) {
        if let Some(h) = &self.inner {
            h.resume_failures.inc();
        }
    }
}

#[cfg(test)]
impl ToolServerMetrics {
    /// Render the registry. Test-only, and exposed at module scope rather than
    /// inside `mod tests` so the router tests in `main.rs` can assert on what
    /// this handle actually recorded.
    pub fn render_for_test(&self) -> String {
        self.inner
            .as_ref()
            .expect("inert handle records nothing, so any assertion would be vacuous")
            .base
            .render()
            .expect("registry render")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Each test builds its own registry — the metric names are fixed, so two
    /// live instances would collide if they shared one.
    fn metrics() -> ToolServerMetrics {
        let m = ToolServerMetrics::new();
        assert!(
            m.inner.is_some(),
            "metrics must build cleanly; an inert handle would make every \
             assertion below vacuous"
        );
        m
    }

    fn render(m: &ToolServerMetrics) -> String {
        m.inner.as_ref().unwrap().base.render().unwrap()
    }

    fn sample(rendered: &str, needle: &str) -> Option<f64> {
        rendered
            .lines()
            .find(|l| l.starts_with(needle))
            .and_then(|l| l.rsplit(' ').next())
            .and_then(|v| v.parse().ok())
    }

    #[test]
    fn an_idle_server_is_never_stalled() {
        // The defining property of this service. It does nothing between
        // analyses, so an armed stall detector would report an outage every
        // quiet afternoon. Unlike the stream services, work_expected is never
        // set true — not even after the first request.
        let m = metrics();
        let before = m.inner.as_ref().unwrap().base.readiness();
        assert!(!before.work_expected);
        assert!(!before.stalled);

        m.tool_call_completed("get_candles", 200, 0.02);

        let after = m.inner.as_ref().unwrap().base.readiness();
        assert!(
            !after.work_expected,
            "a served request must not arm stall detection on a request/response \
             service — the next quiet hour is not an outage"
        );
        assert!(!after.stalled);
    }

    #[test]
    fn a_served_call_counts_and_beats() {
        let m = metrics();
        m.tool_call_completed("get_candles", 200, 0.02);
        m.tool_call_completed("get_candles", 200, 0.04);

        let out = render(&m);
        assert!(out.contains(
            r#"tool_server_tool_calls_total{outcome="success",tool="get_candles"} 2"#
        ));
        assert_eq!(
            sample(&out, "tool_server_work_completed_total"),
            Some(2.0),
            "requests still beat the heartbeat: throughput and time-since-last-use \
             stay reportable even though they are not alertable"
        );
    }

    #[test]
    fn status_codes_collapse_to_outcome_classes() {
        let m = metrics();
        m.tool_call_completed("get_prediction", 200, 0.01);
        m.tool_call_completed("get_prediction", 400, 0.01);
        m.tool_call_completed("get_prediction", 503, 0.01);

        let out = render(&m);
        for (outcome, expected) in [("success", 1), ("client_error", 1), ("server_error", 1)] {
            assert!(
                out.contains(&format!(
                    r#"tool_server_tool_calls_total{{outcome="{outcome}",tool="get_prediction"}} {expected}"#
                )),
                "{outcome} must be counted separately"
            );
        }
    }

    #[test]
    fn an_unavailable_answer_is_still_a_successful_call() {
        // The failure mode this module exists for. The availability contract
        // answers 200 with an `unavailable` marker when there is no history, so
        // an empty QuestDB produces all-200s and all-empty answers. Both series
        // must move: the call succeeded at the HTTP layer, and the agent still
        // got nothing.
        let m = metrics();
        for _ in 0..5 {
            m.tool_call_completed("get_candles", 200, 0.01);
            m.tool_unavailable("get_candles");
        }

        let out = render(&m);
        assert!(out.contains(
            r#"tool_server_tool_calls_total{outcome="success",tool="get_candles"} 5"#
        ));
        assert!(
            out.contains(r#"tool_server_tool_unavailable_total{tool="get_candles"} 5"#),
            "a service answering every request with nothing must be \
             distinguishable from one answering with data"
        );
        assert!(
            out.contains(r#"tool_server_tool_calls_total{outcome="server_error",tool="get_candles"} 0"#),
            "an unavailable marker is not an error and must not be counted as one"
        );
    }

    #[test]
    fn db_faults_are_distinct_from_empty_results() {
        // "The store answered and had no rows" and "the store could not be
        // reached" need different responses from an operator, so they cannot
        // share a series.
        let m = metrics();
        m.tool_unavailable("get_consensus");
        m.db_error("candle_load");
        m.db_error("candle_load");

        let out = render(&m);
        assert!(out.contains(r#"tool_server_tool_unavailable_total{tool="get_consensus"} 1"#));
        assert!(out.contains(r#"tool_server_db_errors_total{source="candle_load"} 2"#));
    }

    #[test]
    fn every_label_series_exists_before_it_first_fires() {
        // rate() over an absent series renders as "no data", which on a
        // dashboard is indistinguishable from a broken scrape — and a tool the
        // agent has not called yet is exactly the series that would be missing.
        let out = render(&metrics());
        for tool in TOOLS {
            assert!(
                out.contains(&format!(
                    r#"tool_server_tool_unavailable_total{{tool="{tool}"}} 0"#
                )),
                "tool {tool} must have a pre-created unavailable series"
            );
            for outcome in OUTCOMES {
                assert!(
                    out.contains(&format!(
                        r#"tool_server_tool_calls_total{{outcome="{outcome}",tool="{tool}"}} 0"#
                    )),
                    "tool {tool} must have a pre-created {outcome} series"
                );
            }
        }
        for source in DB_ERROR_SOURCES {
            assert!(
                out.contains(&format!(
                    r#"tool_server_db_errors_total{{source="{source}"}} 0"#
                )),
                "db error source {source} must be pre-created at 0"
            );
        }
    }

    #[test]
    fn pool_exhaustion_is_visible_before_it_becomes_an_error() {
        // The ceiling is 10 connections. All in use with none idle means
        // requests are queueing, which reaches the user as a slow analysis
        // rather than a failure and appears in no other series here.
        let m = metrics();
        m.set_pool_state(10, 0);

        let out = render(&m);
        assert!(out.contains(r#"tool_server_db_pool_connections{state="in_use"} 10"#));
        assert!(out.contains(r#"tool_server_db_pool_connections{state="idle"} 0"#));

        m.set_pool_state(3, 7);
        let out = render(&m);
        assert!(out.contains(r#"tool_server_db_pool_connections{state="in_use"} 3"#));
        assert!(out.contains(r#"tool_server_db_pool_connections{state="idle"} 7"#));
    }

    #[test]
    fn a_dropped_notification_is_counted_separately_from_the_trigger() {
        // The worst failure here: the condition the user waited for happened and
        // the notification was lost. The watcher is deregistered either way, so
        // nothing retries — the trigger count alone would read as success.
        let m = metrics();
        m.watcher_triggered();
        m.watcher_triggered();
        m.resume_failed();

        let out = render(&m);
        assert_eq!(sample(&out, "tool_server_watcher_triggers_total"), Some(2.0));
        assert_eq!(sample(&out, "tool_server_resume_failures_total"), Some(1.0));
    }

    #[test]
    fn watcher_count_tracks_registrations() {
        // Ratcheting upward across days means watchers never resolve — a slow
        // leak of memory and QuestDB poll load.
        let m = metrics();
        m.set_active_watchers(3);
        assert_eq!(sample(&render(&m), "tool_server_active_watchers"), Some(3.0));

        m.set_active_watchers(0);
        assert_eq!(
            sample(&render(&m), "tool_server_active_watchers"),
            Some(0.0),
            "zero watchers is a normal state, not a fault"
        );
    }

    #[test]
    fn latency_is_recorded_per_tool() {
        let m = metrics();
        m.tool_call_completed("get_news_context", 200, 9.5);
        m.tool_call_completed("get_candles", 200, 0.01);

        let out = render(&m);
        assert!(out.contains(r#"tool_server_tool_duration_seconds_count{tool="get_news_context"} 1"#));
        assert!(
            out.contains(r#"tool_server_tool_duration_seconds_sum{tool="get_news_context"} 9.5"#),
            "the slow external-fetch tool must not be averaged in with QuestDB reads"
        );
        assert!(out.contains(r#"tool_server_tool_duration_seconds_count{tool="get_candles"} 1"#));
    }

    #[test]
    fn an_inert_handle_is_safe_to_call() {
        // The degraded path: if the registry failed to build, every call site
        // still runs unconditionally and must simply do nothing.
        let m = ToolServerMetrics { inner: None };
        m.serve();
        m.tool_call_completed("get_candles", 200, 0.01);
        m.tool_unavailable("get_candles");
        m.db_error("candle_load");
        m.set_pool_state(1, 9);
        m.set_active_watchers(2);
        m.watcher_triggered();
        m.resume_failed();
    }
}
