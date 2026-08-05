// src/metrics.rs — alpha-terminal's Prometheus surface (:9103).
//
// WHY THIS EXISTS: this service turns the raw tick stream into 10-minute OHLC
// candles and pushes them to the terminal over :8081. Its failure mode is
// unusually quiet. The consumer can be attached and decoding ticks perfectly
// while no candle ever closes — and because a candle only closes when a tick
// arrives in a *later* window, a feed that dies mid-window leaves the last
// candle open forever with no error anywhere.
//
// The signals that matter here:
//
//   ticks_total          input. Separates "no ticks arriving" from "ticks
//                        arriving but nothing coming out"; those have different
//                        causes and different owners.
//   candles_closed_total output — the service's actual purpose.
//   decode_errors_total  malformed protobuf on market.ticks: a producer/schema
//                        mismatch rather than a transport fault.
//   publish_errors_total closed candles that never reached Kafka. The candle was
//                        computed and then lost; downstream never saw it.
//   ws_clients           attached terminals.
//   tracked_symbols      how many symbols have an open candle. Also drives the
//                        work-expected gate below.
//
// The work Heartbeat beats on each decoded tick, not on each closed candle.
// Candles close at most once per symbol per 10 minutes, so a heartbeat tied to
// them would have to tolerate 10+ minutes of silence before alerting — by which
// point the feed has been dead for ten minutes. Ticks are the fine-grained
// signal; candles_closed_total is what shows the aggregation itself is alive.
//
// Every method is a no-op when instrumentation failed to initialise, so call
// sites stay unconditional and a metrics fault can never take down the service.

use service_metrics::prometheus::{Gauge, IntCounter, Opts};
use service_metrics::{Heartbeat, MetricsConfig, ServiceMetrics};

/// Default port for alpha-terminal's `/metrics`, `/health` and `/ready`
/// endpoints. Overridable with `METRICS_PORT`.
const DEFAULT_METRICS_PORT: u16 = 9103;

/// Ticks arrive continuously during market hours, so 60s of silence means the
/// upstream feed is gone rather than quiet — the same threshold ingestion uses,
/// since this service consumes the very stream ingestion produces.
/// `MarketSession` widens it automatically outside 09:15-15:30 IST.
const IN_SESSION_STALL_SECONDS: f64 = 60.0;

/// Alpha-terminal's metric handles.
///
/// Cheap to clone. Construction never fails: on registry failure the handle is
/// inert and every method does nothing, so instrumentation cannot break the
/// service it observes.
#[derive(Clone)]
pub struct AlphaMetrics {
    inner: Option<Handles>,
}

#[derive(Clone)]
struct Handles {
    base: ServiceMetrics,
    heartbeat: Heartbeat,
    ticks: IntCounter,
    decode_errors: IntCounter,
    candles_closed: IntCounter,
    publish_errors: IntCounter,
    ws_clients: Gauge,
    tracked_symbols: Gauge,
}

impl AlphaMetrics {
    /// Build the metrics, degrading to an inert handle on failure.
    pub fn new() -> Self {
        match Self::try_build() {
            Ok(handles) => Self {
                inner: Some(handles),
            },
            Err(e) => {
                log::error!(
                    "Prometheus metrics unavailable: {e}. Alpha-terminal continues \
                     uninstrumented and will scrape as down — investigate before \
                     relying on monitoring."
                );
                Self { inner: None }
            }
        }
    }

    fn try_build() -> Result<Handles, service_metrics::prometheus::Error> {
        let base = ServiceMetrics::new(MetricsConfig {
            service: "alpha-terminal",
            version: env!("CARGO_PKG_VERSION"),
            in_session_stall_seconds: IN_SESSION_STALL_SECONDS,
        })?;
        let registry = base.registry();

        let ticks = IntCounter::with_opts(Opts::new(
            "alpha_terminal_ticks_total",
            "Ticks consumed and decoded from market.ticks. Read alongside \
             candles_closed_total: input without output means the aggregation \
             has wedged, no input at all means the fault is upstream.",
        ))?;
        registry.register(Box::new(ticks.clone()))?;

        let decode_errors = IntCounter::with_opts(Opts::new(
            "alpha_terminal_decode_errors_total",
            "Payloads that failed protobuf decoding. A nonzero rate means a \
             producer/schema mismatch, not a transport problem.",
        ))?;
        registry.register(Box::new(decode_errors.clone()))?;

        let candles_closed = IntCounter::with_opts(Opts::new(
            "alpha_terminal_candles_closed_total",
            "Completed 10-minute OHLC candles. The service's output. A candle \
             only closes when a tick arrives in a later window, so this stops \
             advancing the moment the feed dies — with no error logged anywhere.",
        ))?;
        registry.register(Box::new(candles_closed.clone()))?;

        let publish_errors = IntCounter::with_opts(Opts::new(
            "alpha_terminal_publish_errors_total",
            "Closed candles that failed to publish to Kafka. The candle was \
             computed and then lost, so downstream consumers never saw it.",
        ))?;
        registry.register(Box::new(publish_errors.clone()))?;

        let ws_clients = Gauge::with_opts(Opts::new(
            "alpha_terminal_ws_clients",
            "WebSocket clients currently attached to the candle stream. 0 is \
             normal when no terminal is open.",
        ))?;
        registry.register(Box::new(ws_clients.clone()))?;

        let tracked_symbols = Gauge::with_opts(Opts::new(
            "alpha_terminal_tracked_symbols",
            "Symbols with an open candle in the aggregation engine. 0 at boot is \
             normal — the engine is populated by the tick stream itself.",
        ))?;
        registry.register(Box::new(tracked_symbols.clone()))?;

        let heartbeat = base.heartbeat();

        // No symbols are tracked until the first tick arrives, so nothing is due
        // yet. Without this a freshly started service — or one started outside
        // market hours — would report an ever-growing stall while behaving
        // exactly as designed.
        base.set_work_expected(false);

        Ok(Handles {
            base,
            heartbeat,
            ticks,
            decode_errors,
            candles_closed,
            publish_errors,
            ws_clients,
            tracked_symbols,
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

    /// A tick was consumed and decoded.
    ///
    /// This is the work point. Candles close at most once per symbol per 10
    /// minutes, so beating on candles instead would mean tolerating 10+ minutes
    /// of silence before a dead feed could possibly register as a stall.
    pub fn tick_decoded(&self) {
        if let Some(h) = &self.inner {
            h.ticks.inc();
            h.heartbeat.beat();
        }
    }

    /// A payload arrived but could not be decoded. Deliberately not work: a
    /// stream of undecodable messages is a failure, not progress.
    pub fn decode_failed(&self) {
        if let Some(h) = &self.inner {
            h.decode_errors.inc();
        }
    }

    /// A 10-minute candle closed.
    pub fn candle_closed(&self) {
        if let Some(h) = &self.inner {
            h.candles_closed.inc();
        }
    }

    /// A closed candle failed to reach Kafka and was lost.
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

    /// Report how many symbols the engine is tracking.
    ///
    /// Also drives the work-expected gate: with no symbols tracked the service
    /// has seen no ticks at all and has nothing to do, so staleness is idleness.
    /// Once a symbol is tracked, ticks are due during market hours and silence
    /// is a stall.
    pub fn set_tracked_symbols(&self, count: usize) {
        if let Some(h) = &self.inner {
            h.tracked_symbols.set(count as f64);
            h.base.set_work_expected(count > 0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Each test builds its own registry — the metric names are fixed, so two
    /// live instances would collide if they shared one.
    fn metrics() -> AlphaMetrics {
        let m = AlphaMetrics::new();
        assert!(
            m.inner.is_some(),
            "metrics must build cleanly; an inert handle would make every \
             assertion below vacuous"
        );
        m
    }

    fn render(m: &AlphaMetrics) -> String {
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
    fn decoding_a_tick_counts_and_beats() {
        let m = metrics();
        m.set_tracked_symbols(1);
        m.tick_decoded();
        m.tick_decoded();

        let out = render(&m);
        assert_eq!(sample(&out, "alpha_terminal_ticks_total"), Some(2.0));
        assert_eq!(
            sample(&out, "alpha_terminal_work_completed_total"),
            Some(2.0),
            "ticks must beat the shared heartbeat"
        );
        assert!(!m.inner.as_ref().unwrap().base.readiness().stalled);
    }

    #[test]
    fn ticks_beat_rather_than_candles() {
        // The reason the work point is the tick and not the candle: a candle
        // closes at most once per symbol per 10 minutes. If closing were the
        // only beat, a feed that died right after a close would look healthy
        // for the whole next window.
        let m = metrics();
        m.set_tracked_symbols(1);
        m.tick_decoded();

        let out = render(&m);
        assert_eq!(sample(&out, "alpha_terminal_candles_closed_total"), Some(0.0));
        assert_eq!(
            sample(&out, "alpha_terminal_work_completed_total"),
            Some(1.0),
            "a tick with no candle close must still register as work"
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
        assert_eq!(sample(&out, "alpha_terminal_decode_errors_total"), Some(2.0));
        assert_eq!(
            sample(&out, "alpha_terminal_work_completed_total"),
            Some(0.0),
            "decode failures must not count as work"
        );
    }

    #[test]
    fn input_without_output_is_visible_as_a_distinct_state() {
        // The failure this pair exists to expose: ticks arriving, no candle ever
        // closing. Both counters are needed — either alone is ambiguous.
        let m = metrics();
        m.set_tracked_symbols(2);
        for _ in 0..20 {
            m.tick_decoded();
        }

        let out = render(&m);
        assert_eq!(sample(&out, "alpha_terminal_ticks_total"), Some(20.0));
        assert_eq!(
            sample(&out, "alpha_terminal_candles_closed_total"),
            Some(0.0),
            "consuming ticks must not imply closing candles"
        );
    }

    #[test]
    fn closed_candles_and_publish_failures_are_tracked_separately() {
        // A candle that closed but never reached Kafka is a real loss, and it
        // must not be hidden by the fact that the close itself succeeded.
        let m = metrics();
        m.candle_closed();
        m.candle_closed();
        m.publish_failed();

        let out = render(&m);
        assert_eq!(sample(&out, "alpha_terminal_candles_closed_total"), Some(2.0));
        assert_eq!(sample(&out, "alpha_terminal_publish_errors_total"), Some(1.0));
    }

    #[test]
    fn ws_client_count_tracks_connect_and_disconnect() {
        let m = metrics();
        m.ws_client_connected();
        m.ws_client_connected();
        assert_eq!(sample(&render(&m), "alpha_terminal_ws_clients"), Some(2.0));

        m.ws_client_disconnected();
        assert_eq!(sample(&render(&m), "alpha_terminal_ws_clients"), Some(1.0));

        // Back to zero is a normal state (no terminal open), not an error.
        m.ws_client_disconnected();
        assert_eq!(sample(&render(&m), "alpha_terminal_ws_clients"), Some(0.0));
    }

    #[test]
    fn no_tracked_symbols_means_idle_not_stalled() {
        // The real boot state: the engine is empty until the first tick arrives.
        // Started overnight, that is indistinguishable from a dead feed unless
        // the work-expected gate is disarmed.
        let m = metrics();
        let r = m.inner.as_ref().unwrap().base.readiness();
        assert!(!r.work_expected);
        assert!(!r.stalled);

        let out = render(&m);
        assert_eq!(sample(&out, "alpha_terminal_tracked_symbols"), Some(0.0));
        assert_eq!(sample(&out, "alpha_terminal_work_expected"), Some(0.0));
    }

    #[test]
    fn tracking_a_symbol_arms_stall_detection() {
        let m = metrics();
        m.set_tracked_symbols(3);

        assert!(
            m.inner.as_ref().unwrap().base.readiness().work_expected,
            "with symbols tracked, ticks are due and silence is a stall"
        );
        let out = render(&m);
        assert_eq!(sample(&out, "alpha_terminal_tracked_symbols"), Some(3.0));
        assert_eq!(sample(&out, "alpha_terminal_work_expected"), Some(1.0));
    }

    #[test]
    fn an_inert_handle_is_safe_to_call() {
        // The degraded path: if the registry failed to build, every call site in
        // the hot loop still runs unconditionally and must simply do nothing.
        let m = AlphaMetrics { inner: None };
        m.serve();
        m.tick_decoded();
        m.decode_failed();
        m.candle_closed();
        m.publish_failed();
        m.ws_client_connected();
        m.ws_client_disconnected();
        m.set_tracked_symbols(3);
    }
}
