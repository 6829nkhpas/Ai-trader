// src/metrics.rs — the technical agent's Prometheus surface (:9104).
//
// WHY THIS EXISTS: this agent consumes market.ticks, maintains per-symbol RSI
// and VWAP, and publishes TechSignal to `technical_signals`. The aggregator's
// entire decision path is fed from that topic, so when this agent goes quiet the
// aggregator looks idle rather than starved and an operator starts debugging the
// wrong service.
//
// The complication specific to this agent: RSI needs a 14-tick warm-up per
// symbol before a signal can be emitted at all. So "ticks arriving, no signals
// leaving" is the *correct* state for the first 14 ticks of every symbol, and
// indistinguishable from a wedged pipeline unless the warm-up is exposed. That
// is what warmed_up_symbols is for — without it, every restart looks like an
// outage for the first few seconds and a genuine wedge looks like a restart.
//
//   ticks_total          input, counted where the tick is actually processed
//   signals_total        output — the service's purpose
//   decode_errors_total  malformed protobuf on market.ticks
//   publish_errors_total signals computed and then lost
//   tracked_symbols      symbols with indicator state
//   warmed_up_symbols    symbols past RSI warm-up, i.e. able to emit at all
//
// The work Heartbeat beats on each processed tick, not on each published
// signal. Beating on signals would report a stall during warm-up, and would
// alert on this service whenever the upstream feed narrowed to symbols that had
// not yet warmed up.
//
// Every method is a no-op when instrumentation failed to initialise, so call
// sites stay unconditional and a metrics fault can never take down the service.

use service_metrics::prometheus::{Gauge, IntCounter, Opts};
use service_metrics::{Heartbeat, MetricsConfig, ServiceMetrics};

/// Default port for the technical agent's `/metrics`, `/health` and `/ready`
/// endpoints. Overridable with `METRICS_PORT`.
const DEFAULT_METRICS_PORT: u16 = 9104;

/// Ticks arrive continuously during market hours, so 60s of silence means the
/// upstream feed is gone rather than quiet. `MarketSession` widens this
/// automatically outside 09:15-15:30 IST.
const IN_SESSION_STALL_SECONDS: f64 = 60.0;

/// The technical agent's metric handles.
///
/// Cheap to clone. Construction never fails: on registry failure the handle is
/// inert and every method does nothing, so instrumentation cannot break the
/// service it observes.
#[derive(Clone)]
pub struct TechnicalMetrics {
    inner: Option<Handles>,
}

#[derive(Clone)]
struct Handles {
    base: ServiceMetrics,
    heartbeat: Heartbeat,
    ticks: IntCounter,
    decode_errors: IntCounter,
    signals: IntCounter,
    publish_errors: IntCounter,
    tracked_symbols: Gauge,
    warmed_up_symbols: Gauge,
}

impl TechnicalMetrics {
    /// Build the metrics, degrading to an inert handle on failure.
    pub fn new() -> Self {
        match Self::try_build() {
            Ok(handles) => Self {
                inner: Some(handles),
            },
            Err(e) => {
                log::error!(
                    "Prometheus metrics unavailable: {e}. The technical agent \
                     continues uninstrumented and will scrape as down — \
                     investigate before relying on monitoring."
                );
                Self { inner: None }
            }
        }
    }

    fn try_build() -> Result<Handles, service_metrics::prometheus::Error> {
        let base = ServiceMetrics::new(MetricsConfig {
            service: "technical",
            version: env!("CARGO_PKG_VERSION"),
            in_session_stall_seconds: IN_SESSION_STALL_SECONDS,
        })?;
        let registry = base.registry();

        let ticks = IntCounter::with_opts(Opts::new(
            "technical_ticks_total",
            "Ticks processed through the indicator pipeline. Counted where the \
             tick is actually consumed, not where it is decoded, so a wedged \
             event loop stops this counter even while the Kafka listener keeps \
             draining the topic.",
        ))?;
        registry.register(Box::new(ticks.clone()))?;

        let decode_errors = IntCounter::with_opts(Opts::new(
            "technical_decode_errors_total",
            "Payloads on market.ticks that failed protobuf decoding. A nonzero \
             rate means a producer/schema mismatch, not a transport problem.",
        ))?;
        registry.register(Box::new(decode_errors.clone()))?;

        let signals = IntCounter::with_opts(Opts::new(
            "technical_signals_total",
            "TechSignal messages published to technical_signals. The aggregator's \
             decision path is fed entirely from this; when it flatlines the \
             aggregator looks idle rather than starved.",
        ))?;
        registry.register(Box::new(signals.clone()))?;

        let publish_errors = IntCounter::with_opts(Opts::new(
            "technical_publish_errors_total",
            "Signals that failed to publish to Kafka. The signal was computed \
             and then lost, so the aggregator never saw it.",
        ))?;
        registry.register(Box::new(publish_errors.clone()))?;

        let tracked_symbols = Gauge::with_opts(Opts::new(
            "technical_tracked_symbols",
            "Symbols with live indicator state. 0 at boot is normal — state is \
             created by the tick stream itself.",
        ))?;
        registry.register(Box::new(tracked_symbols.clone()))?;

        let warmed_up_symbols = Gauge::with_opts(Opts::new(
            "technical_warmed_up_symbols",
            "Symbols past the 14-tick RSI warm-up and therefore able to emit \
             signals at all. Read against tracked_symbols: while this is below \
             it, ticks arriving with no signals leaving is correct behaviour, \
             not a wedge.",
        ))?;
        registry.register(Box::new(warmed_up_symbols.clone()))?;

        let heartbeat = base.heartbeat();

        // No symbols are tracked until the first tick arrives, so no work is due
        // yet. Without this a service started overnight would report an
        // ever-growing stall while behaving exactly as designed.
        base.set_work_expected(false);

        Ok(Handles {
            base,
            heartbeat,
            ticks,
            decode_errors,
            signals,
            publish_errors,
            tracked_symbols,
            warmed_up_symbols,
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

    /// A tick was processed through the indicator pipeline.
    ///
    /// This is the work point rather than signal emission: RSI needs 14 ticks
    /// per symbol before anything can be published, so beating on signals would
    /// report a stall during every warm-up.
    pub fn tick_processed(&self) {
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

    /// A TechSignal was computed and handed to the producer.
    pub fn signal_emitted(&self) {
        if let Some(h) = &self.inner {
            h.signals.inc();
        }
    }

    /// A signal failed to reach Kafka and was lost.
    pub fn publish_failed(&self) {
        if let Some(h) = &self.inner {
            h.publish_errors.inc();
        }
    }

    /// Report how many symbols have state and how many are past RSI warm-up.
    ///
    /// Reported as a pair because either number alone is ambiguous: `tracked`
    /// without `warmed` cannot distinguish warm-up from a wedge, and `warmed`
    /// without `tracked` cannot show how much of the feed is still warming.
    ///
    /// Also drives the work-expected gate — an empty state map has seen no ticks
    /// at all, so staleness there is idleness rather than failure.
    pub fn set_symbol_counts(&self, tracked: usize, warmed_up: usize) {
        if let Some(h) = &self.inner {
            h.tracked_symbols.set(tracked as f64);
            h.warmed_up_symbols.set(warmed_up as f64);
            h.base.set_work_expected(tracked > 0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Each test builds its own registry — the metric names are fixed, so two
    /// live instances would collide if they shared one.
    fn metrics() -> TechnicalMetrics {
        let m = TechnicalMetrics::new();
        assert!(
            m.inner.is_some(),
            "metrics must build cleanly; an inert handle would make every \
             assertion below vacuous"
        );
        m
    }

    fn render(m: &TechnicalMetrics) -> String {
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
    fn processing_a_tick_counts_and_beats() {
        let m = metrics();
        m.set_symbol_counts(1, 0);
        m.tick_processed();
        m.tick_processed();

        let out = render(&m);
        assert_eq!(sample(&out, "technical_ticks_total"), Some(2.0));
        assert_eq!(
            sample(&out, "technical_work_completed_total"),
            Some(2.0),
            "ticks must beat the shared heartbeat"
        );
        assert!(!m.inner.as_ref().unwrap().base.readiness().stalled);
    }

    #[test]
    fn warming_up_is_not_a_stall() {
        // The failure this exists to prevent: for the first 14 ticks of every
        // symbol, no signal can be emitted. If the heartbeat beat on signals,
        // every restart would page as an outage.
        let m = metrics();
        m.set_symbol_counts(5, 0);
        for _ in 0..13 {
            m.tick_processed();
        }

        let out = render(&m);
        assert_eq!(sample(&out, "technical_signals_total"), Some(0.0));
        assert_eq!(
            sample(&out, "technical_work_completed_total"),
            Some(13.0),
            "ticks during warm-up must still register as work"
        );
        assert!(
            !m.inner.as_ref().unwrap().base.readiness().stalled,
            "a warming-up agent is working, not stalled"
        );
    }

    #[test]
    fn warm_up_progress_is_readable_against_tracked_symbols() {
        // The pair is what makes "ticks in, no signals out" interpretable.
        // Neither gauge alone distinguishes warm-up from a wedged pipeline.
        let m = metrics();
        m.set_symbol_counts(10, 4);

        let out = render(&m);
        assert_eq!(sample(&out, "technical_tracked_symbols"), Some(10.0));
        assert_eq!(sample(&out, "technical_warmed_up_symbols"), Some(4.0));
    }

    #[test]
    fn a_fully_warm_agent_emitting_nothing_is_visible() {
        // Once every tracked symbol is warm, ticks arriving with no signals
        // leaving is no longer explainable by warm-up — the two gauges being
        // equal is what makes that inference possible.
        let m = metrics();
        m.set_symbol_counts(3, 3);
        for _ in 0..50 {
            m.tick_processed();
        }

        let out = render(&m);
        assert_eq!(sample(&out, "technical_tracked_symbols"), Some(3.0));
        assert_eq!(sample(&out, "technical_warmed_up_symbols"), Some(3.0));
        assert_eq!(sample(&out, "technical_ticks_total"), Some(50.0));
        assert_eq!(
            sample(&out, "technical_signals_total"),
            Some(0.0),
            "50 ticks across 3 fully-warm symbols with no output is a real fault"
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
        assert_eq!(sample(&out, "technical_decode_errors_total"), Some(2.0));
        assert_eq!(
            sample(&out, "technical_work_completed_total"),
            Some(0.0),
            "decode failures must not count as work"
        );
    }

    #[test]
    fn emitted_signals_and_publish_failures_are_tracked_separately() {
        // A signal that was computed and then lost in transit is a real failure,
        // and it must not be hidden by the fact that the computation succeeded.
        let m = metrics();
        m.signal_emitted();
        m.signal_emitted();
        m.signal_emitted();
        m.publish_failed();

        let out = render(&m);
        assert_eq!(sample(&out, "technical_signals_total"), Some(3.0));
        assert_eq!(sample(&out, "technical_publish_errors_total"), Some(1.0));
    }

    #[test]
    fn no_tracked_symbols_means_idle_not_stalled() {
        // The real boot state: state is empty until the first tick arrives.
        // Started overnight, that is indistinguishable from a dead feed unless
        // the work-expected gate is disarmed.
        let m = metrics();
        let r = m.inner.as_ref().unwrap().base.readiness();
        assert!(!r.work_expected);
        assert!(!r.stalled);

        let out = render(&m);
        assert_eq!(sample(&out, "technical_tracked_symbols"), Some(0.0));
        assert_eq!(sample(&out, "technical_work_expected"), Some(0.0));
    }

    #[test]
    fn tracking_a_symbol_arms_stall_detection() {
        let m = metrics();
        m.set_symbol_counts(2, 0);

        assert!(
            m.inner.as_ref().unwrap().base.readiness().work_expected,
            "with symbols tracked, ticks are due and silence is a stall"
        );
        assert_eq!(sample(&render(&m), "technical_work_expected"), Some(1.0));
    }

    #[test]
    fn an_inert_handle_is_safe_to_call() {
        // The degraded path: if the registry failed to build, every call site in
        // the hot loop still runs unconditionally and must simply do nothing.
        let m = TechnicalMetrics { inner: None };
        m.serve();
        m.tick_processed();
        m.decode_failed();
        m.signal_emitted();
        m.publish_failed();
        m.set_symbol_counts(4, 2);
    }
}
