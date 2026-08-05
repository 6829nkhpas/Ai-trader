// src/metrics.rs — aggregator's Prometheus surface (:9102).
//
// WHY THIS EXISTS: the aggregator is the fusion point. It consumes TechSignal
// and NewsSentiment from Kafka, weighs them against each other, and emits
// AggregatedDecision — the output the terminal and every other consumer of
// `trade_decisions` ultimately reads. It is also two WebSocket servers and a
// Kite REST proxy, so "the container is up" says very little about whether any
// of that is actually working.
//
// The signals that matter here:
//
//   consumed_total{topic}    input. Distinguishes "nothing is arriving" from
//                            "input arrives but no decisions come out". Those
//                            two have entirely different causes, and this pair
//                            is what makes the difference visible.
//   decisions_total{action}  output — the service's actual purpose.
//   ws_clients               how many terminals are attached. A pipeline
//                            broadcasting to zero clients is a different
//                            problem from one that has stopped producing.
//   ws_lagged_total          slow clients skipping messages: silent data loss
//                            at the last hop, today visible only in logs.
//   decode_errors_total      malformed protobuf — a producer/schema mismatch.
//   kite_api_errors_total    the REST proxy the watchlist panel depends on.
//
// The work Heartbeat beats on any successfully processed message from either
// topic, not only on emitted decisions. Beating only on decisions would report
// the aggregator as stalled whenever `technical` goes quiet, pointing the alert
// at the wrong service; consumed_total{topic} is what localises that.
//
// Every method is a no-op when instrumentation failed to initialise, so call
// sites stay unconditional and a metrics fault can never take down the
// aggregator.

use service_metrics::prometheus::{Gauge, IntCounter, IntCounterVec, Opts};
use service_metrics::{Heartbeat, MetricsConfig, ServiceMetrics};

/// Default port for the aggregator's `/metrics`, `/health` and `/ready`
/// endpoints. Overridable with `METRICS_PORT`.
const DEFAULT_METRICS_PORT: u16 = 9102;

/// Technical signals arrive steadily during market hours, so three minutes of
/// total silence across both topics means the upstream pipeline has stopped.
/// More generous than ingestion's 60s because these are computed downstream and
/// can batch. `MarketSession` widens it automatically outside 09:15-15:30 IST,
/// which is what keeps sparse overnight sentiment from tripping it.
const IN_SESSION_STALL_SECONDS: f64 = 180.0;

/// Kafka topics this service consumes. Pre-created so a topic that has never
/// delivered reports an explicit 0 rather than being absent.
const TOPICS: [&str; 2] = ["technical_signals", "sentiment_signals"];

/// Decision actions, pre-created for the same reason.
const ACTIONS: [&str; 4] = ["BUY", "SELL", "HOLD", "UNKNOWN"];

/// The aggregator's metric handles.
///
/// Cheap to clone. Construction never fails: on registry failure the handle is
/// inert and every method does nothing, so instrumentation cannot break the
/// service it observes.
#[derive(Clone)]
pub struct AggregatorMetrics {
    inner: Option<Handles>,
}

#[derive(Clone)]
struct Handles {
    base: ServiceMetrics,
    heartbeat: Heartbeat,
    consumed: IntCounterVec,
    decode_errors: IntCounterVec,
    decisions: IntCounterVec,
    publish_errors: IntCounter,
    ws_clients: Gauge,
    ws_lagged: IntCounter,
    kite_api_errors: IntCounterVec,
    candles: IntCounter,
}

impl AggregatorMetrics {
    /// Build the aggregator's metrics, degrading to an inert handle on failure.
    pub fn new() -> Self {
        match Self::try_build() {
            Ok(handles) => Self {
                inner: Some(handles),
            },
            Err(e) => {
                log::error!(
                    "Prometheus metrics unavailable: {e}. Aggregator continues \
                     uninstrumented and will scrape as down — investigate before \
                     relying on monitoring."
                );
                Self { inner: None }
            }
        }
    }

    fn try_build() -> Result<Handles, service_metrics::prometheus::Error> {
        let base = ServiceMetrics::new(MetricsConfig {
            service: "aggregator",
            version: env!("CARGO_PKG_VERSION"),
            in_session_stall_seconds: IN_SESSION_STALL_SECONDS,
        })?;
        let registry = base.registry();

        let consumed = IntCounterVec::new(
            Opts::new(
                "aggregator_consumed_total",
                "Kafka messages consumed, by topic. Read alongside \
                 decisions_total: input without output localises the fault here, \
                 no input at all localises it upstream.",
            ),
            &["topic"],
        )?;
        registry.register(Box::new(consumed.clone()))?;

        let decode_errors = IntCounterVec::new(
            Opts::new(
                "aggregator_decode_errors_total",
                "Payloads that failed protobuf decoding, by topic. A nonzero rate \
                 means a producer/schema mismatch, not a transport problem.",
            ),
            &["topic"],
        )?;
        registry.register(Box::new(decode_errors.clone()))?;

        let decisions = IntCounterVec::new(
            Opts::new(
                "aggregator_decisions_total",
                "AggregatedDecisions emitted, by action. The service's output; \
                 the action split also shows whether weighting has collapsed to \
                 a single verdict.",
            ),
            &["action"],
        )?;
        registry.register(Box::new(decisions.clone()))?;

        let publish_errors = IntCounter::with_opts(Opts::new(
            "aggregator_publish_errors_total",
            "Decisions that failed to publish to Kafka. The decision was computed \
             and then lost, so downstream consumers never saw it.",
        ))?;
        registry.register(Box::new(publish_errors.clone()))?;

        let ws_clients = Gauge::with_opts(Opts::new(
            "aggregator_ws_clients",
            "WebSocket clients currently attached to the decision broadcast. 0 is \
             normal when no terminal is open.",
        ))?;
        registry.register(Box::new(ws_clients.clone()))?;

        let ws_lagged = IntCounter::with_opts(Opts::new(
            "aggregator_ws_lagged_total",
            "Broadcast messages skipped because a client could not keep up. \
             Silent data loss at the last hop before the UI.",
        ))?;
        registry.register(Box::new(ws_lagged.clone()))?;

        let kite_api_errors = IntCounterVec::new(
            Opts::new(
                "aggregator_kite_api_errors_total",
                "Failed calls to the upstream Kite REST API, by endpoint. The \
                 watchlist and quote panels fail when this climbs.",
            ),
            &["endpoint"],
        )?;
        registry.register(Box::new(kite_api_errors.clone()))?;

        let candles = IntCounter::with_opts(Opts::new(
            "aggregator_candles_total",
            "OHLC candles emitted by the secondary market.ticks pipeline. \
             Independent of the decision path: one can stop while the other runs.",
        ))?;
        registry.register(Box::new(candles.clone()))?;

        // Pre-create every series so an idle topic or unused action reports 0
        // rather than being absent — rate() over a missing series yields
        // nothing, which a dashboard renders as "no data" and an operator cannot
        // tell apart from a broken scrape.
        for topic in TOPICS {
            consumed.with_label_values(&[topic]);
            decode_errors.with_label_values(&[topic]);
        }
        for action in ACTIONS {
            decisions.with_label_values(&[action]);
        }

        let heartbeat = base.heartbeat();

        Ok(Handles {
            base,
            heartbeat,
            consumed,
            decode_errors,
            decisions,
            publish_errors,
            ws_clients,
            ws_lagged,
            kite_api_errors,
            candles,
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

    /// A message was consumed and successfully processed.
    ///
    /// This is the work point: it beats for either topic, so the aggregator is
    /// not reported as stalled merely because `technical` went quiet while
    /// sentiment still flows.
    pub fn message_processed(&self, topic: &str) {
        if let Some(h) = &self.inner {
            h.consumed.with_label_values(&[topic]).inc();
            h.heartbeat.beat();
        }
    }

    /// A payload arrived but could not be decoded. Deliberately not work: a
    /// stream of undecodable messages is a failure, not progress.
    pub fn decode_failed(&self, topic: &str) {
        if let Some(h) = &self.inner {
            h.decode_errors.with_label_values(&[topic]).inc();
        }
    }

    /// A decision was emitted. `action` should be one of [`ACTIONS`].
    pub fn decision_emitted(&self, action: &str) {
        if let Some(h) = &self.inner {
            h.decisions.with_label_values(&[action]).inc();
        }
    }

    /// A computed decision failed to reach Kafka and was lost.
    pub fn publish_failed(&self) {
        if let Some(h) = &self.inner {
            h.publish_errors.inc();
        }
    }

    /// A WebSocket client completed its handshake.
    pub fn ws_client_connected(&self) {
        if let Some(h) = &self.inner {
            h.ws_clients.inc();
        }
    }

    /// A WebSocket client disconnected.
    pub fn ws_client_disconnected(&self) {
        if let Some(h) = &self.inner {
            h.ws_clients.dec();
        }
    }

    /// A client fell behind and `skipped` broadcast messages were dropped.
    pub fn ws_client_lagged(&self, skipped: u64) {
        if let Some(h) = &self.inner {
            if skipped > 0 {
                h.ws_lagged.inc_by(skipped);
            }
        }
    }

    /// A call to the upstream Kite REST API failed.
    pub fn kite_api_failed(&self, endpoint: &str) {
        if let Some(h) = &self.inner {
            h.kite_api_errors.with_label_values(&[endpoint]).inc();
        }
    }

    /// An OHLC candle was emitted by the secondary ticks pipeline.
    ///
    /// Counts throughput but does not beat the heartbeat: the aggregator's
    /// readiness tracks the decision pipeline, and a live candle stream must not
    /// mask a decision path that has stopped.
    pub fn candle_emitted(&self) {
        if let Some(h) = &self.inner {
            h.candles.inc();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Each test builds its own registry — the metric names are fixed, so two
    /// live instances would collide if they shared one.
    fn metrics() -> AggregatorMetrics {
        let m = AggregatorMetrics::new();
        assert!(
            m.inner.is_some(),
            "metrics must build cleanly; an inert handle would make every \
             assertion below vacuous"
        );
        m
    }

    fn render(m: &AggregatorMetrics) -> String {
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
    fn topic_and_action_series_start_at_zero_not_absent() {
        let out = render(&metrics());
        for topic in TOPICS {
            assert_eq!(
                sample(&out, &format!(r#"aggregator_consumed_total{{topic="{topic}"}}"#)),
                Some(0.0),
                "topic {topic} must be present at 0; got:\n{out}"
            );
            assert_eq!(
                sample(
                    &out,
                    &format!(r#"aggregator_decode_errors_total{{topic="{topic}"}}"#)
                ),
                Some(0.0)
            );
        }
        for action in ACTIONS {
            assert_eq!(
                sample(
                    &out,
                    &format!(r#"aggregator_decisions_total{{action="{action}"}}"#)
                ),
                Some(0.0),
                "action {action} must be present at 0"
            );
        }
    }

    #[test]
    fn processing_a_message_counts_per_topic_and_beats() {
        let m = metrics();
        m.message_processed("technical_signals");
        m.message_processed("technical_signals");
        m.message_processed("sentiment_signals");

        let out = render(&m);
        assert_eq!(
            sample(&out, r#"aggregator_consumed_total{topic="technical_signals"}"#),
            Some(2.0)
        );
        assert_eq!(
            sample(&out, r#"aggregator_consumed_total{topic="sentiment_signals"}"#),
            Some(1.0)
        );
        assert_eq!(
            sample(&out, "aggregator_work_completed_total"),
            Some(3.0),
            "every processed message must beat the shared heartbeat"
        );
        assert!(!m.inner.as_ref().unwrap().base.readiness().stalled);
    }

    #[test]
    fn sentiment_traffic_alone_keeps_the_service_ready() {
        // The aggregator must not be reported as stalled just because
        // `technical` went quiet — that alert would point at the wrong service.
        // Only total silence across both topics is this service's problem.
        let m = metrics();
        m.message_processed("sentiment_signals");

        let r = m.inner.as_ref().unwrap().base.readiness();
        assert!(!r.stalled);
        assert!(r.work_expected);
    }

    #[test]
    fn decode_failures_are_counted_but_are_not_work() {
        // A stream of undecodable messages is a failure, not progress. If it
        // beat the heartbeat, a schema mismatch would look perfectly healthy.
        let m = metrics();
        m.decode_failed("technical_signals");
        m.decode_failed("technical_signals");

        let out = render(&m);
        assert_eq!(
            sample(
                &out,
                r#"aggregator_decode_errors_total{topic="technical_signals"}"#
            ),
            Some(2.0)
        );
        assert_eq!(
            sample(&out, "aggregator_work_completed_total"),
            Some(0.0),
            "decode failures must not count as work"
        );
    }

    #[test]
    fn decisions_are_counted_per_action() {
        let m = metrics();
        m.decision_emitted("BUY");
        m.decision_emitted("HOLD");
        m.decision_emitted("HOLD");

        let out = render(&m);
        assert_eq!(
            sample(&out, r#"aggregator_decisions_total{action="BUY"}"#),
            Some(1.0)
        );
        assert_eq!(
            sample(&out, r#"aggregator_decisions_total{action="HOLD"}"#),
            Some(2.0)
        );
        assert_eq!(
            sample(&out, r#"aggregator_decisions_total{action="SELL"}"#),
            Some(0.0)
        );
    }

    #[test]
    fn input_without_output_is_visible_as_a_distinct_state() {
        // The failure this pair exists to expose: signals arriving, no decisions
        // emitted. Both counters are needed — either alone is ambiguous.
        let m = metrics();
        for _ in 0..5 {
            m.message_processed("technical_signals");
        }

        let out = render(&m);
        assert_eq!(
            sample(&out, r#"aggregator_consumed_total{topic="technical_signals"}"#),
            Some(5.0)
        );
        let emitted: f64 = ACTIONS
            .iter()
            .filter_map(|a| {
                sample(
                    &out,
                    &format!(r#"aggregator_decisions_total{{action="{a}"}}"#),
                )
            })
            .sum();
        assert_eq!(emitted, 0.0, "consuming must not imply emitting");
    }

    #[test]
    fn ws_client_count_tracks_connect_and_disconnect() {
        let m = metrics();
        m.ws_client_connected();
        m.ws_client_connected();
        assert_eq!(sample(&render(&m), "aggregator_ws_clients"), Some(2.0));

        m.ws_client_disconnected();
        assert_eq!(sample(&render(&m), "aggregator_ws_clients"), Some(1.0));

        // Back to zero is a normal state (no terminal open), not an error.
        m.ws_client_disconnected();
        assert_eq!(sample(&render(&m), "aggregator_ws_clients"), Some(0.0));
    }

    #[test]
    fn lagged_clients_record_every_skipped_message() {
        let m = metrics();
        m.ws_client_lagged(7);
        m.ws_client_lagged(3);
        // A client that skipped nothing must not register.
        m.ws_client_lagged(0);

        assert_eq!(
            sample(&render(&m), "aggregator_ws_lagged_total"),
            Some(10.0)
        );
    }

    #[test]
    fn publish_and_kite_errors_are_counted_separately() {
        let m = metrics();
        m.publish_failed();
        m.kite_api_failed("quote");
        m.kite_api_failed("quote");
        m.kite_api_failed("historical");

        let out = render(&m);
        assert_eq!(sample(&out, "aggregator_publish_errors_total"), Some(1.0));
        assert_eq!(
            sample(&out, r#"aggregator_kite_api_errors_total{endpoint="quote"}"#),
            Some(2.0)
        );
        assert_eq!(
            sample(
                &out,
                r#"aggregator_kite_api_errors_total{endpoint="historical"}"#
            ),
            Some(1.0)
        );
    }

    #[test]
    fn candles_count_but_do_not_mask_a_stalled_decision_path() {
        // The OHLC pipeline is independent of the decision path. If candles beat
        // the heartbeat, a dead decision pipeline would still report ready.
        let m = metrics();
        m.candle_emitted();
        m.candle_emitted();

        let out = render(&m);
        assert_eq!(sample(&out, "aggregator_candles_total"), Some(2.0));
        assert_eq!(
            sample(&out, "aggregator_work_completed_total"),
            Some(0.0),
            "candles must not beat the decision-path heartbeat"
        );
    }

    #[test]
    fn an_inert_handle_is_safe_to_call() {
        // The degraded path: if the registry failed to build, every call site
        // still runs unconditionally and must simply do nothing.
        let m = AggregatorMetrics { inner: None };
        m.serve();
        m.message_processed("technical_signals");
        m.decode_failed("technical_signals");
        m.decision_emitted("BUY");
        m.publish_failed();
        m.ws_client_connected();
        m.ws_client_disconnected();
        m.ws_client_lagged(4);
        m.kite_api_failed("quote");
        m.candle_emitted();
    }
}
