// src/metrics.rs — the predictive agent's Prometheus surface (:9105).
//
// WHY THIS EXISTS: this agent consumes 10-minute OHLC candles, fits an OLS
// regression over a rolling 14-candle window, and publishes PredictiveSignal to
// `signals.predictive`. It also feeds the Ghost Candle WebSocket on 8082, so a
// wedge here surfaces in the UI as a chart that quietly stops projecting
// forward, with no error raised anywhere.
//
// The complication specific to this agent is its timescale. Input arrives once
// per 10-minute bucket, and the regression needs 14 buckets before it can
// predict at all. A cold agent therefore emits nothing for over two hours, and
// even a warm one is silent for ten minutes at a stretch. Thresholds tuned for a
// tick stream would page continuously here, and a heartbeat tied to published
// predictions would page through the first 140 minutes of every restart.
//
//   candles_total         input — candles fed into the regression window
//   predictions_total     output — the service's purpose
//   decode_errors_total   malformed protobuf on market.ohlc.10m
//   publish_errors_total  predictions computed and then lost
//   ws_clients            Ghost Candle subscribers currently connected
//   window_fill           candles held in the rolling window, 0..14
//   last_confidence       R² of the most recent fit, on the published 1-100 scale
//
// The work Heartbeat beats on each consumed candle rather than on each
// prediction, so warm-up reads as working. `window_fill` is what makes warm-up
// and wedge distinguishable: below 14 no prediction is possible, and "candles
// in, no predictions out" is correct behaviour rather than a fault.
//
// Every method is a no-op when instrumentation failed to initialise, so call
// sites stay unconditional and a metrics fault can never take down the service.

use service_metrics::prometheus::{Gauge, IntCounter, Opts};
use service_metrics::{Heartbeat, MetricsConfig, ServiceMetrics};

/// Default port for the predictive agent's `/metrics`, `/health` and `/ready`
/// endpoints. Overridable with `METRICS_PORT`.
const DEFAULT_METRICS_PORT: u16 = 9105;

/// Input is one candle per 10-minute bucket, so silence only becomes suspicious
/// at 1.5x that interval — anything tighter would alert in the ordinary gap
/// between two candles. `MarketSession` widens this further outside
/// 09:15-15:30 IST.
const IN_SESSION_STALL_SECONDS: f64 = 900.0;

/// Candles required before the regression can produce a prediction. Mirrors
/// `math::WINDOW_SIZE` and is interpolated into `window_fill`'s help text, so an
/// operator reading the metric learns what "full" means without reading the
/// code — and the description cannot drift from the real window size.
const WINDOW_SIZE: usize = 14;

/// The predictive agent's metric handles.
///
/// Cheap to clone. Construction never fails: on registry failure the handle is
/// inert and every method does nothing, so instrumentation cannot break the
/// service it observes.
#[derive(Clone)]
pub struct PredictiveMetrics {
    inner: Option<Handles>,
}

#[derive(Clone)]
struct Handles {
    base: ServiceMetrics,
    heartbeat: Heartbeat,
    candles: IntCounter,
    decode_errors: IntCounter,
    predictions: IntCounter,
    publish_errors: IntCounter,
    ws_clients: Gauge,
    window_fill: Gauge,
    last_confidence: Gauge,
}

impl PredictiveMetrics {
    /// Build the metrics, degrading to an inert handle on failure.
    pub fn new() -> Self {
        match Self::try_build() {
            Ok(handles) => Self {
                inner: Some(handles),
            },
            Err(e) => {
                log::error!(
                    "Prometheus metrics unavailable: {e}. The predictive agent \
                     continues uninstrumented and will scrape as down — \
                     investigate before relying on monitoring."
                );
                Self { inner: None }
            }
        }
    }

    fn try_build() -> Result<Handles, service_metrics::prometheus::Error> {
        let base = ServiceMetrics::new(MetricsConfig {
            service: "predictive",
            version: env!("CARGO_PKG_VERSION"),
            in_session_stall_seconds: IN_SESSION_STALL_SECONDS,
        })?;
        let registry = base.registry();

        let candles = IntCounter::with_opts(Opts::new(
            "predictive_candles_total",
            "OHLC candles consumed from market.ohlc.10m and fed into the \
             regression window. Read against predictions_total: no candles at \
             all means the fault is upstream in alpha-terminal, candles without \
             predictions means either warm-up (see window_fill) or a wedge here.",
        ))?;
        registry.register(Box::new(candles.clone()))?;

        let decode_errors = IntCounter::with_opts(Opts::new(
            "predictive_decode_errors_total",
            "Payloads that failed protobuf decoding. A nonzero rate means a \
             producer/schema mismatch on the OHLC topic, not a transport fault.",
        ))?;
        registry.register(Box::new(decode_errors.clone()))?;

        let predictions = IntCounter::with_opts(Opts::new(
            "predictive_predictions_total",
            "PredictiveSignals produced — the service's output. Stays at zero \
             until window_fill reaches 14, which is correct behaviour and not a \
             fault.",
        ))?;
        registry.register(Box::new(predictions.clone()))?;

        let publish_errors = IntCounter::with_opts(Opts::new(
            "predictive_publish_errors_total",
            "Predictions that failed to reach Kafka. The prediction was \
             computed and then lost, so the aggregator never saw it — \
             predictions_total alone hides this.",
        ))?;
        registry.register(Box::new(publish_errors.clone()))?;

        let ws_clients = Gauge::with_opts(Opts::new(
            "predictive_ws_clients",
            "Ghost Candle WebSocket subscribers currently connected on :8082. \
             0 is normal when no terminal is open.",
        ))?;
        registry.register(Box::new(ws_clients.clone()))?;

        let window_fill = Gauge::with_opts(Opts::new(
            "predictive_window_fill",
            format!(
                "Candles currently held in the rolling regression window, 0 to \
                 {WINDOW_SIZE}. Below {WINDOW_SIZE} no prediction is \
                 mathematically possible, so this is what separates a \
                 warming-up agent from a wedged one."
            ),
        ))?;
        registry.register(Box::new(window_fill.clone()))?;

        let last_confidence = Gauge::with_opts(Opts::new(
            "predictive_last_confidence",
            "R-squared of the most recent fit, on the published 1-100 scale. A \
             collapse toward 1 means the model is still emitting but has stopped \
             explaining the data — output continues while the signal is worthless.",
        ))?;
        registry.register(Box::new(last_confidence.clone()))?;

        let heartbeat = base.heartbeat();

        // Nothing is due until the first candle arrives. Without this, an agent
        // started overnight — or before the first 10-minute bucket of the
        // session closes — reports an ever-growing stall while behaving exactly
        // as designed.
        base.set_work_expected(false);

        Ok(Handles {
            base,
            heartbeat,
            candles,
            decode_errors,
            predictions,
            publish_errors,
            ws_clients,
            window_fill,
            last_confidence,
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

    /// A candle was consumed, decoded, and fed into the regression window.
    ///
    /// This is the work point. Beating on published predictions instead would
    /// report a stall for the entire 140-minute warm-up after every restart,
    /// and consuming a candle is genuine progress whether or not the window is
    /// yet full enough to predict from.
    ///
    /// `window_fill` is passed through here because the two always change
    /// together — a candle is fed and the window advances in the same step, so
    /// splitting them into separate calls would only create the opportunity for
    /// them to disagree.
    pub fn candle_consumed(&self, window_fill: usize) {
        if let Some(h) = &self.inner {
            h.candles.inc();
            h.window_fill.set(window_fill as f64);
            h.heartbeat.beat();
            // The stream is live, so candles are now due and silence is a stall.
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

    /// A prediction was produced, with the fit's confidence on the 1-100 scale.
    pub fn prediction_emitted(&self, confidence: f64) {
        if let Some(h) = &self.inner {
            h.predictions.inc();
            // Guarded because a degenerate fit can yield NaN, and a NaN gauge
            // renders as `NaN` in the exposition format — which Prometheus
            // ingests, and which then poisons every average and alert
            // expression that touches this series.
            if confidence.is_finite() {
                h.last_confidence.set(confidence);
            }
        }
    }

    /// A prediction failed to reach Kafka and was lost.
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
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Each test builds its own registry — the metric names are fixed, so two
    /// live instances would collide if they shared one.
    fn metrics() -> PredictiveMetrics {
        let m = PredictiveMetrics::new();
        assert!(
            m.inner.is_some(),
            "metrics must build cleanly; an inert handle would make every \
             assertion below vacuous"
        );
        m
    }

    fn render(m: &PredictiveMetrics) -> String {
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
        m.candle_consumed(1);
        m.candle_consumed(2);

        let out = render(&m);
        assert_eq!(sample(&out, "predictive_candles_total"), Some(2.0));
        assert_eq!(
            sample(&out, "predictive_work_completed_total"),
            Some(2.0),
            "consumed candles must beat the shared heartbeat"
        );
        assert!(!m.inner.as_ref().unwrap().base.readiness().stalled);
    }

    #[test]
    fn warming_up_is_not_a_stall() {
        // The state this design exists to protect: 13 candles in, no prediction
        // possible, everything working correctly. Beating on predictions would
        // make this indistinguishable from a wedge for over two hours.
        let m = metrics();
        for i in 1..WINDOW_SIZE {
            m.candle_consumed(i);
        }

        let out = render(&m);
        assert_eq!(sample(&out, "predictive_predictions_total"), Some(0.0));
        assert_eq!(
            sample(&out, "predictive_window_fill"),
            Some((WINDOW_SIZE - 1) as f64),
            "window_fill must show how close the agent is to being able to predict"
        );
        assert!(
            !m.inner.as_ref().unwrap().base.readiness().stalled,
            "a warming-up agent is working, not stalled"
        );
    }

    #[test]
    fn a_full_window_emitting_nothing_is_distinguishable_from_warm_up() {
        // The genuine fault: window full, candles arriving, still no output.
        // window_fill == WINDOW_SIZE is what rules out the innocent explanation.
        let m = metrics();
        for _ in 0..30 {
            m.candle_consumed(WINDOW_SIZE);
        }

        let out = render(&m);
        assert_eq!(sample(&out, "predictive_window_fill"), Some(14.0));
        assert_eq!(sample(&out, "predictive_candles_total"), Some(30.0));
        assert_eq!(
            sample(&out, "predictive_predictions_total"),
            Some(0.0),
            "consuming candles must not imply emitting predictions"
        );
    }

    #[test]
    fn decode_failures_are_counted_but_are_not_work() {
        // A stream of undecodable messages is a failure, not progress. If it
        // beat the heartbeat, a schema mismatch would look perfectly healthy.
        let m = metrics();
        m.decode_failed();
        m.decode_failed();

        let out = render(&m);
        assert_eq!(sample(&out, "predictive_decode_errors_total"), Some(2.0));
        assert_eq!(
            sample(&out, "predictive_work_completed_total"),
            Some(0.0),
            "decode failures must not count as work"
        );
    }

    #[test]
    fn predictions_and_publish_failures_are_tracked_separately() {
        // A prediction that was computed and then lost is a real loss, and it
        // must not be hidden by the fact that the computation succeeded.
        let m = metrics();
        m.prediction_emitted(72.5);
        m.prediction_emitted(80.0);
        m.publish_failed();

        let out = render(&m);
        assert_eq!(sample(&out, "predictive_predictions_total"), Some(2.0));
        assert_eq!(sample(&out, "predictive_publish_errors_total"), Some(1.0));
    }

    #[test]
    fn last_confidence_tracks_the_most_recent_fit() {
        let m = metrics();
        m.prediction_emitted(95.0);
        assert_eq!(sample(&render(&m), "predictive_last_confidence"), Some(95.0));

        m.prediction_emitted(12.0);
        assert_eq!(
            sample(&render(&m), "predictive_last_confidence"),
            Some(12.0),
            "a collapsing R-squared must be visible while output continues"
        );
    }

    #[test]
    fn a_non_finite_confidence_is_ignored_rather_than_exported() {
        // A degenerate fit can produce NaN. Exporting it would poison every
        // average and alert expression over this series, so the last good value
        // is kept instead.
        let m = metrics();
        m.prediction_emitted(50.0);
        m.prediction_emitted(f64::NAN);

        let out = render(&m);
        assert_eq!(sample(&out, "predictive_last_confidence"), Some(50.0));
        assert_eq!(
            sample(&out, "predictive_predictions_total"),
            Some(2.0),
            "the prediction still happened and must still be counted"
        );
    }

    #[test]
    fn ws_client_count_tracks_connect_and_disconnect() {
        let m = metrics();
        m.ws_client_connected();
        m.ws_client_connected();
        assert_eq!(sample(&render(&m), "predictive_ws_clients"), Some(2.0));

        m.ws_client_disconnected();
        assert_eq!(sample(&render(&m), "predictive_ws_clients"), Some(1.0));

        // Back to zero is a normal state (no terminal open), not an error.
        m.ws_client_disconnected();
        assert_eq!(sample(&render(&m), "predictive_ws_clients"), Some(0.0));
    }

    #[test]
    fn no_candles_yet_means_idle_not_stalled() {
        // The real boot state: nothing has arrived yet. Started overnight, that
        // is indistinguishable from a dead upstream unless the work-expected
        // gate starts disarmed.
        let m = metrics();
        let r = m.inner.as_ref().unwrap().base.readiness();
        assert!(!r.work_expected);
        assert!(!r.stalled);

        let out = render(&m);
        assert_eq!(sample(&out, "predictive_window_fill"), Some(0.0));
        assert_eq!(sample(&out, "predictive_work_expected"), Some(0.0));
    }

    #[test]
    fn the_first_candle_arms_stall_detection() {
        let m = metrics();
        m.candle_consumed(1);

        assert!(
            m.inner.as_ref().unwrap().base.readiness().work_expected,
            "once the stream is live, further candles are due and silence is a stall"
        );
        assert_eq!(sample(&render(&m), "predictive_work_expected"), Some(1.0));
    }

    #[test]
    fn an_inert_handle_is_safe_to_call() {
        // The degraded path: if the registry failed to build, every call site in
        // the event loop still runs unconditionally and must simply do nothing.
        let m = PredictiveMetrics { inner: None };
        m.serve();
        m.candle_consumed(7);
        m.decode_failed();
        m.prediction_emitted(50.0);
        m.prediction_emitted(f64::NAN);
        m.publish_failed();
        m.ws_client_connected();
        m.ws_client_disconnected();
    }
}
