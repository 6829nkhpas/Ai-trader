// src/metrics.rs — the quant-rag agent's Prometheus surface (:9106).
//
// WHY THIS EXISTS: this agent consumes 10-minute OHLC candles, classifies market
// structure, and on a >=2% move calls an external LLM to produce a MarketInsight
// for `signals.insights` and the terminal WebSocket on :8083. It is the only
// service in the pipeline that depends on a third-party API, so it has a failure
// mode none of the others have: everything local is healthy and the product is
// still broken, because the provider is down, throttling, or returning prose
// where JSON was asked for.
//
// The complication specific to this agent is that its output is rare *by
// design*. Three gates sit between a candle and an insight: the 2% anomaly
// threshold, a 15-second global cooldown, and a 5-minute per-symbol cooldown. A
// calm session can legitimately produce zero insights from open to close. So:
//
//   - The Heartbeat beats on each consumed candle, never on an emitted insight.
//     Beating on output would page through every quiet afternoon.
//   - "No insights" is therefore not alertable, and no threshold on
//     insights_total can be. What IS alertable is candles stopping, the LLM
//     erroring, or anomalies being found and then continuously suppressed.
//
//   candles_total           input — the work point
//   anomalies_total         candles past the 2% threshold
//   llm_suppressed_total    anomalies dropped by a cooldown, by reason
//   llm_latency_seconds     LLM call duration by outcome; _count is call count
//   llm_errors_total        LLM failures, by kind
//   llm_retries_total       429 backoffs inside a single call
//   insights_total          real insights published
//   fallback_insights_total error placeholders published in their place
//   publish_errors_total    insights computed and then lost
//   active_patterns         symbols with a live structural pattern
//   ws_clients              terminals attached to :8083
//   decode_errors_total     malformed protobuf on market.ohlc.10m
//
// insights_total and fallback_insights_total are deliberately separate. On LLM
// failure this agent does not go quiet — it publishes a placeholder carrying the
// error text — so a single combined counter would keep climbing straight through
// a total provider outage and read as perfect health.
//
// Every method is a no-op when instrumentation failed to initialise, so call
// sites stay unconditional and a metrics fault can never take down the service.

use service_metrics::prometheus::{
    Gauge, HistogramOpts, HistogramVec, IntCounter, IntCounterVec, Opts,
};
use service_metrics::{Heartbeat, MetricsConfig, ServiceMetrics};

/// Default port for the quant-rag agent's `/metrics`, `/health` and `/ready`
/// endpoints. Overridable with `METRICS_PORT`.
const DEFAULT_METRICS_PORT: u16 = 9106;

/// Input is one candle per 10-minute bucket, so silence only becomes suspicious
/// at 1.5x that interval — the same reasoning as the predictive agent, which
/// consumes the same topic. `MarketSession` widens this outside market hours.
const IN_SESSION_STALL_SECONDS: f64 = 900.0;

/// Why an anomaly did not reach the LLM. Pre-created so a reason that has never
/// fired reports an explicit 0 rather than being absent from the exposition —
/// `rate()` over a missing series renders as "no data", which is
/// indistinguishable from a broken scrape.
const SUPPRESSION_REASONS: [&str; 2] = ["global_cooldown", "symbol_cooldown"];

/// LLM failure kinds, recorded at the point each is detected rather than
/// inferred from an error string. Pre-created for the same reason.
///
/// The distinction matters operationally: `network` and `http_status` are the
/// provider's fault and usually transient, while `malformed_output` means the
/// model is answering but ignoring the JSON contract — that one does not clear
/// on its own and usually needs a prompt or model change.
const LLM_ERROR_KINDS: [&str; 5] = [
    "network",
    "http_status",
    "invalid_json",
    "missing_content",
    "malformed_output",
];

/// Outcome labels on the latency histogram. Failures are timed too: a provider
/// that hangs until timeout and one that refuses instantly are both errors, but
/// only the timing tells them apart.
const LLM_OUTCOMES: [&str; 2] = ["ok", "error"];

/// Latency buckets in seconds, sized for LLM calls rather than the sub-second
/// defaults — an entirely sub-100ms default bucket set would put every real
/// call in `+Inf` and measure nothing.
const LLM_LATENCY_BUCKETS: [f64; 9] = [0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0];

/// The quant-rag agent's metric handles.
///
/// Cheap to clone. Construction never fails: on registry failure the handle is
/// inert and every method does nothing, so instrumentation cannot break the
/// service it observes.
#[derive(Clone)]
pub struct QuantRagMetrics {
    inner: Option<Handles>,
}

#[derive(Clone)]
struct Handles {
    base: ServiceMetrics,
    heartbeat: Heartbeat,
    candles: IntCounter,
    decode_errors: IntCounter,
    anomalies: IntCounter,
    suppressed: IntCounterVec,
    llm_latency: HistogramVec,
    llm_errors: IntCounterVec,
    llm_retries: IntCounter,
    insights: IntCounter,
    fallback_insights: IntCounter,
    publish_errors: IntCounter,
    active_patterns: Gauge,
    ws_clients: Gauge,
}

impl QuantRagMetrics {
    /// Build the metrics, degrading to an inert handle on failure.
    pub fn new() -> Self {
        match Self::try_build() {
            Ok(handles) => Self {
                inner: Some(handles),
            },
            Err(e) => {
                log::error!(
                    "Prometheus metrics unavailable: {e}. Quant-RAG continues \
                     uninstrumented and will scrape as down — investigate before \
                     relying on monitoring."
                );
                Self { inner: None }
            }
        }
    }

    fn try_build() -> Result<Handles, service_metrics::prometheus::Error> {
        let base = ServiceMetrics::new(MetricsConfig {
            service: "quant-rag",
            version: env!("CARGO_PKG_VERSION"),
            in_session_stall_seconds: IN_SESSION_STALL_SECONDS,
        })?;
        let registry = base.registry();

        let candles = IntCounter::with_opts(Opts::new(
            "quant_rag_candles_total",
            "OHLC candles consumed from market.ohlc.10m. The work signal: this \
             flat during market hours is the only unambiguous sign the agent is \
             stuck, since every downstream counter is legitimately allowed to \
             sit at zero.",
        ))?;
        registry.register(Box::new(candles.clone()))?;

        let decode_errors = IntCounter::with_opts(Opts::new(
            "quant_rag_decode_errors_total",
            "Payloads that failed protobuf decoding. A nonzero rate means a \
             producer/schema mismatch on the OHLC topic, not a transport fault.",
        ))?;
        registry.register(Box::new(decode_errors.clone()))?;

        let anomalies = IntCounter::with_opts(Opts::new(
            "quant_rag_anomalies_total",
            "Candles whose absolute move crossed the anomaly threshold. Read \
             against insights_total: a widening gap means anomalies are being \
             found and then suppressed or failed, which is invisible from \
             either counter alone.",
        ))?;
        registry.register(Box::new(anomalies.clone()))?;

        let suppressed = IntCounterVec::new(
            Opts::new(
                "quant_rag_llm_suppressed_total",
                "Anomalies that did not reach the LLM because a cooldown was \
                 active. This is intended rate-limiting, not an error — but a \
                 sustained high rate means real market events are being dropped \
                 and the cooldowns are mistuned for current volatility.",
            ),
            &["reason"],
        )?;
        registry.register(Box::new(suppressed.clone()))?;

        let llm_latency = HistogramVec::new(
            HistogramOpts::new(
                "quant_rag_llm_latency_seconds",
                "External LLM call duration. Failures are timed as well as \
                 successes: a provider that hangs until timeout and one that \
                 refuses instantly are both errors, and only the timing \
                 separates them. The _count series doubles as the call count.",
            )
            .buckets(LLM_LATENCY_BUCKETS.to_vec()),
            &["outcome"],
        )?;
        registry.register(Box::new(llm_latency.clone()))?;

        let llm_errors = IntCounterVec::new(
            Opts::new(
                "quant_rag_llm_errors_total",
                "LLM call failures by kind, recorded where each is detected \
                 rather than inferred from an error string. network and \
                 http_status are the provider's problem and usually transient; \
                 malformed_output means the model is answering but ignoring the \
                 JSON contract, which does not clear on its own.",
            ),
            &["kind"],
        )?;
        registry.register(Box::new(llm_errors.clone()))?;

        let llm_retries = IntCounter::with_opts(Opts::new(
            "quant_rag_llm_retries_total",
            "HTTP 429 backoff retries inside LLM calls. Rising while \
             llm_errors_total stays flat is the early warning: the agent is \
             absorbing throttling successfully, but is close to the point where \
             it no longer can.",
        ))?;
        registry.register(Box::new(llm_retries.clone()))?;

        let insights = IntCounter::with_opts(Opts::new(
            "quant_rag_insights_total",
            "Genuine LLM-generated insights published. Zero over a whole \
             session is a legitimate outcome in a calm market, so this must \
             never be alerted on by itself.",
        ))?;
        registry.register(Box::new(insights.clone()))?;

        let fallback_insights = IntCounter::with_opts(Opts::new(
            "quant_rag_fallback_insights_total",
            "Error placeholders published when the LLM call failed. Counted \
             separately from insights_total on purpose: the agent does not go \
             quiet on failure, it publishes a placeholder, so a single combined \
             counter would climb straight through a provider outage and read as \
             perfect health.",
        ))?;
        registry.register(Box::new(fallback_insights.clone()))?;

        let publish_errors = IntCounter::with_opts(Opts::new(
            "quant_rag_publish_errors_total",
            "Insights that failed to reach Kafka. The insight was generated — \
             at real LLM cost — and then lost in transit.",
        ))?;
        registry.register(Box::new(publish_errors.clone()))?;

        let active_patterns = Gauge::with_opts(Opts::new(
            "quant_rag_active_patterns",
            "Symbols currently holding a detected structural pattern. Drops to \
             0 when the classifier stops matching anything, which distinguishes \
             a quiet market from a pattern engine that has silently stopped \
             working.",
        ))?;
        registry.register(Box::new(active_patterns.clone()))?;

        let ws_clients = Gauge::with_opts(Opts::new(
            "quant_rag_ws_clients",
            "Insight WebSocket subscribers currently connected on :8083. 0 is \
             normal when no terminal is open.",
        ))?;
        registry.register(Box::new(ws_clients.clone()))?;

        // Instantiate every label series at 0. Prometheus renders `rate()` over
        // an absent series as "no data", which on a dashboard is
        // indistinguishable from a broken scrape — and these series are absent
        // exactly when things are working.
        for reason in SUPPRESSION_REASONS {
            suppressed.with_label_values(&[reason]);
        }
        for kind in LLM_ERROR_KINDS {
            llm_errors.with_label_values(&[kind]);
        }
        for outcome in LLM_OUTCOMES {
            llm_latency.with_label_values(&[outcome]);
        }

        let heartbeat = base.heartbeat();

        // Nothing is due until the first candle arrives. Without this, an agent
        // started overnight reports an ever-growing stall while behaving
        // exactly as designed.
        base.set_work_expected(false);

        Ok(Handles {
            base,
            heartbeat,
            candles,
            decode_errors,
            anomalies,
            suppressed,
            llm_latency,
            llm_errors,
            llm_retries,
            insights,
            fallback_insights,
            publish_errors,
            active_patterns,
            ws_clients,
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

    /// A candle was consumed, decoded, and run through the pattern classifier.
    ///
    /// This is the work point, and it is the *only* one that can be. Everything
    /// downstream — anomalies, LLM calls, insights — is gated by a threshold and
    /// two cooldowns, so all of it is legitimately allowed to sit at zero for an
    /// entire session. Beating anywhere else would page through quiet markets.
    pub fn candle_consumed(&self) {
        if let Some(h) = &self.inner {
            h.candles.inc();
            h.heartbeat.beat();
            // The stream is live, so further candles are due and silence is now
            // a stall rather than idleness.
            h.base.set_work_expected(true);
        }
    }

    /// A payload arrived but could not be decoded. Deliberately not work: a
    /// stream of undecodable messages is a failure, not progress.
    pub fn decode_failed(&self) {
        if let Some(h) = &self.inner {
            h.decode_errors.inc();
        }
    }

    /// A candle crossed the anomaly threshold, before any cooldown is applied.
    pub fn anomaly_detected(&self) {
        if let Some(h) = &self.inner {
            h.anomalies.inc();
        }
    }

    /// An anomaly was dropped by a cooldown. `reason` should be one of
    /// [`SUPPRESSION_REASONS`].
    pub fn llm_suppressed(&self, reason: &str) {
        if let Some(h) = &self.inner {
            h.suppressed.with_label_values(&[reason]).inc();
        }
    }

    /// An LLM call finished. `ok` selects the outcome label; `seconds` is the
    /// wall-clock duration including any 429 backoff sleeps, because that is
    /// the latency the pipeline actually paid.
    pub fn llm_call_completed(&self, ok: bool, seconds: f64) {
        if let Some(h) = &self.inner {
            let outcome = if ok { "ok" } else { "error" };
            h.llm_latency.with_label_values(&[outcome]).observe(seconds);
        }
    }

    /// An LLM call failed. `kind` should be one of [`LLM_ERROR_KINDS`].
    pub fn llm_failed(&self, kind: &str) {
        if let Some(h) = &self.inner {
            h.llm_errors.with_label_values(&[kind]).inc();
        }
    }

    /// A 429 response triggered a backoff retry inside a single LLM call.
    pub fn llm_retried(&self) {
        if let Some(h) = &self.inner {
            h.llm_retries.inc();
        }
    }

    /// A genuine LLM-generated insight was produced.
    pub fn insight_emitted(&self) {
        if let Some(h) = &self.inner {
            h.insights.inc();
        }
    }

    /// An error placeholder was published in place of a real insight.
    pub fn fallback_emitted(&self) {
        if let Some(h) = &self.inner {
            h.fallback_insights.inc();
        }
    }

    /// An insight failed to reach Kafka and was lost.
    pub fn publish_failed(&self) {
        if let Some(h) = &self.inner {
            h.publish_errors.inc();
        }
    }

    /// Report how many symbols currently hold a detected pattern.
    ///
    /// Note this does *not* touch the work-expected gate: patterns come and go
    /// with the market, and an empty classifier is a normal state rather than a
    /// reason to stop expecting candles.
    pub fn set_active_patterns(&self, count: usize) {
        if let Some(h) = &self.inner {
            h.active_patterns.set(count as f64);
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
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Each test builds its own registry — the metric names are fixed, so two
    /// live instances would collide if they shared one.
    fn metrics() -> QuantRagMetrics {
        let m = QuantRagMetrics::new();
        assert!(
            m.inner.is_some(),
            "metrics must build cleanly; an inert handle would make every \
             assertion below vacuous"
        );
        m
    }

    fn render(m: &QuantRagMetrics) -> String {
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
    fn consuming_a_candle_counts_and_beats() {
        let m = metrics();
        m.candle_consumed();
        m.candle_consumed();

        let out = render(&m);
        assert_eq!(sample(&out, "quant_rag_candles_total"), Some(2.0));
        assert_eq!(
            sample(&out, "quant_rag_work_completed_total"),
            Some(2.0),
            "consumed candles must beat the shared heartbeat"
        );
        assert!(!m.inner.as_ref().unwrap().base.readiness().stalled);
    }

    #[test]
    fn a_calm_market_producing_no_insights_is_not_a_stall() {
        // The state this whole design protects: candles flowing, nothing
        // crossing the 2% threshold, zero insights all session. If the
        // heartbeat were tied to insights, this would page every quiet day.
        let m = metrics();
        for _ in 0..40 {
            m.candle_consumed();
        }

        let out = render(&m);
        assert_eq!(sample(&out, "quant_rag_insights_total"), Some(0.0));
        assert_eq!(sample(&out, "quant_rag_anomalies_total"), Some(0.0));
        assert!(
            !m.inner.as_ref().unwrap().base.readiness().stalled,
            "a quiet market is working, not stalled"
        );
    }

    #[test]
    fn suppressed_anomalies_are_visible_against_the_anomaly_count() {
        // Anomalies found and then dropped by cooldowns. Neither counter alone
        // shows this: anomalies_total looks busy, insights_total looks dead.
        let m = metrics();
        for _ in 0..5 {
            m.anomaly_detected();
        }
        m.llm_suppressed("global_cooldown");
        m.llm_suppressed("symbol_cooldown");
        m.llm_suppressed("symbol_cooldown");

        let out = render(&m);
        assert_eq!(sample(&out, "quant_rag_anomalies_total"), Some(5.0));
        assert!(out.contains(r#"quant_rag_llm_suppressed_total{reason="global_cooldown"} 1"#));
        assert!(out.contains(r#"quant_rag_llm_suppressed_total{reason="symbol_cooldown"} 2"#));
    }

    #[test]
    fn fallbacks_are_not_counted_as_insights() {
        // The failure this split exists to expose. On LLM failure the agent
        // publishes a placeholder rather than going quiet, so one combined
        // counter would climb through a total provider outage and read healthy.
        let m = metrics();
        m.insight_emitted();
        for _ in 0..9 {
            m.fallback_emitted();
        }

        let out = render(&m);
        assert_eq!(
            sample(&out, "quant_rag_insights_total"),
            Some(1.0),
            "only genuine insights count here"
        );
        assert_eq!(sample(&out, "quant_rag_fallback_insights_total"), Some(9.0));
    }

    #[test]
    fn llm_error_kinds_are_recorded_separately() {
        // network/http_status are transient provider faults; malformed_output
        // means the model is answering but ignoring the JSON contract, which
        // needs a human. Collapsing them would hide that difference.
        let m = metrics();
        m.llm_failed("network");
        m.llm_failed("malformed_output");
        m.llm_failed("malformed_output");

        let out = render(&m);
        assert!(out.contains(r#"quant_rag_llm_errors_total{kind="network"} 1"#));
        assert!(out.contains(r#"quant_rag_llm_errors_total{kind="malformed_output"} 2"#));
    }

    #[test]
    fn every_label_series_exists_before_it_first_fires() {
        // rate() over an absent series renders as "no data", which on a
        // dashboard is indistinguishable from a broken scrape — and these
        // series are absent exactly when everything is working.
        let out = render(&metrics());
        for reason in SUPPRESSION_REASONS {
            assert!(
                out.contains(&format!(
                    r#"quant_rag_llm_suppressed_total{{reason="{reason}"}} 0"#
                )),
                "suppression reason {reason} must be pre-created at 0"
            );
        }
        for kind in LLM_ERROR_KINDS {
            assert!(
                out.contains(&format!(r#"quant_rag_llm_errors_total{{kind="{kind}"}} 0"#)),
                "error kind {kind} must be pre-created at 0"
            );
        }
        for outcome in LLM_OUTCOMES {
            assert!(
                out.contains(&format!(
                    r#"quant_rag_llm_latency_seconds_count{{outcome="{outcome}"}} 0"#
                )),
                "latency outcome {outcome} must be pre-created at 0"
            );
        }
    }

    #[test]
    fn failed_llm_calls_are_timed_as_well_as_successful_ones() {
        // A provider that hangs until timeout and one that refuses instantly
        // are both errors; only the timing tells them apart.
        let m = metrics();
        m.llm_call_completed(true, 3.0);
        m.llm_call_completed(false, 45.0);

        let out = render(&m);
        assert!(out.contains(r#"quant_rag_llm_latency_seconds_count{outcome="ok"} 1"#));
        assert!(out.contains(r#"quant_rag_llm_latency_seconds_count{outcome="error"} 1"#));
        assert!(
            out.contains(r#"quant_rag_llm_latency_seconds_sum{outcome="error"} 45"#),
            "the duration of a failed call must be preserved, not discarded"
        );
    }

    #[test]
    fn retries_are_counted_independently_of_failures() {
        // Retries climbing while errors stay flat is the early warning: the
        // agent is absorbing throttling, but is near the point where it cannot.
        let m = metrics();
        m.llm_retried();
        m.llm_retried();
        m.llm_call_completed(true, 9.0);

        let out = render(&m);
        assert_eq!(sample(&out, "quant_rag_llm_retries_total"), Some(2.0));
        assert!(out.contains(r#"quant_rag_llm_errors_total{kind="http_status"} 0"#));
    }

    #[test]
    fn decode_failures_are_counted_but_are_not_work() {
        // A stream of undecodable messages is a failure, not progress. If it
        // beat the heartbeat, a schema mismatch would look perfectly healthy.
        let m = metrics();
        m.decode_failed();
        m.decode_failed();

        let out = render(&m);
        assert_eq!(sample(&out, "quant_rag_decode_errors_total"), Some(2.0));
        assert_eq!(
            sample(&out, "quant_rag_work_completed_total"),
            Some(0.0),
            "decode failures must not count as work"
        );
    }

    #[test]
    fn publish_failures_are_separate_from_generation() {
        // An insight that cost a real LLM call and was then lost in transit
        // must not be hidden by the fact that generation succeeded.
        let m = metrics();
        m.insight_emitted();
        m.insight_emitted();
        m.publish_failed();

        let out = render(&m);
        assert_eq!(sample(&out, "quant_rag_insights_total"), Some(2.0));
        assert_eq!(sample(&out, "quant_rag_publish_errors_total"), Some(1.0));
    }

    #[test]
    fn active_patterns_does_not_disarm_stall_detection() {
        // Patterns come and go with the market. An empty classifier is normal
        // and must not be read as "nothing to do" — candles are still due.
        let m = metrics();
        m.candle_consumed();
        m.set_active_patterns(0);

        assert!(
            m.inner.as_ref().unwrap().base.readiness().work_expected,
            "candles remain due even when no pattern is active"
        );
        assert_eq!(sample(&render(&m), "quant_rag_active_patterns"), Some(0.0));

        m.set_active_patterns(4);
        assert_eq!(sample(&render(&m), "quant_rag_active_patterns"), Some(4.0));
    }

    #[test]
    fn ws_client_count_tracks_connect_and_disconnect() {
        let m = metrics();
        m.ws_client_connected();
        m.ws_client_connected();
        assert_eq!(sample(&render(&m), "quant_rag_ws_clients"), Some(2.0));

        m.ws_client_disconnected();
        assert_eq!(sample(&render(&m), "quant_rag_ws_clients"), Some(1.0));

        // Back to zero is a normal state (no terminal open), not an error.
        m.ws_client_disconnected();
        assert_eq!(sample(&render(&m), "quant_rag_ws_clients"), Some(0.0));
    }

    #[test]
    fn no_candles_yet_means_idle_not_stalled() {
        // The real boot state: nothing has arrived. Started overnight, that is
        // indistinguishable from a dead upstream unless the gate starts
        // disarmed.
        let m = metrics();
        let r = m.inner.as_ref().unwrap().base.readiness();
        assert!(!r.work_expected);
        assert!(!r.stalled);
        assert_eq!(sample(&render(&m), "quant_rag_work_expected"), Some(0.0));
    }

    #[test]
    fn an_inert_handle_is_safe_to_call() {
        // The degraded path: if the registry failed to build, every call site
        // still runs unconditionally and must simply do nothing.
        let m = QuantRagMetrics { inner: None };
        m.serve();
        m.candle_consumed();
        m.decode_failed();
        m.anomaly_detected();
        m.llm_suppressed("global_cooldown");
        m.llm_call_completed(true, 1.0);
        m.llm_failed("network");
        m.llm_retried();
        m.insight_emitted();
        m.fallback_emitted();
        m.publish_failed();
        m.set_active_patterns(3);
        m.ws_client_connected();
        m.ws_client_disconnected();
    }
}
