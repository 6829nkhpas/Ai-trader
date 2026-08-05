// src/metrics.rs — ingestion's Prometheus surface (:9101).
//
// WHY THIS EXISTS: ingestion is the head of the whole pipeline. Everything
// downstream — technical, predictive, aggregator, the terminal — is fed from the
// Kite WebSocket this service holds. So when it silently stops receiving, every
// downstream service looks idle rather than broken, and the real cause is one
// hop upstream from wherever an operator starts looking.
//
// The signals that matter here, none of which `docker ps` shows:
//
//   kite_ws_connected     the upstream WS is live. The single most valuable bit
//                         in the stack: 0 means no market data is entering the
//                         system at all, whatever the containers say.
//   ticks_total           throughput. A connected socket delivering nothing
//                         during market hours is still a failure.
//   write_errors_total    the sinks. questdb_sink and option_sink deliberately
//                         log-and-drop on failure (see the doc comment on
//                         questdb_sink::insert_tick) so a database outage never
//                         blocks the hot path. That is the right trade, but it
//                         leaves a total QuestDB failure invisible outside the
//                         log stream. This counter makes it a metric.
//
// The work Heartbeat beats once per decoded tick — where a tick has been parsed
// out of a binary frame and is real, not merely where a frame arrived.
//
// Every method is a no-op when instrumentation failed to initialise, so call
// sites in the hot path stay unconditional and a metrics fault can never take
// down ingestion.

use service_metrics::prometheus::{Gauge, IntCounter, IntCounterVec, Opts};
use service_metrics::{Heartbeat, MetricsConfig, ServiceMetrics};

/// Default port for ingestion's `/metrics`, `/health` and `/ready` endpoints.
/// Overridable with `METRICS_PORT`; 9101 is ingestion's slot in the 9101-9110
/// range documented in docs/MONITORING.md.
const DEFAULT_METRICS_PORT: u16 = 9101;

/// A healthy tick pipeline works many times a second. 60s of complete silence
/// during market hours means the feed is gone, not quiet. `MarketSession`
/// widens this automatically outside 09:15-15:30 IST.
const IN_SESSION_STALL_SECONDS: f64 = 60.0;

/// Sink labels for `ingestion_write_errors_total`. Pre-created so a sink that
/// has never failed reports an explicit 0.
const SINKS: [&str; 5] = ["questdb_pg", "questdb_ilp", "kafka", "option_pg", "snapshot"];

/// Ingestion's metric handles.
///
/// Cheap to clone. Construction never fails: if the registry cannot be built
/// the handle is inert and every method below does nothing, so instrumentation
/// cannot break the service it observes.
#[derive(Clone)]
pub struct IngestionMetrics {
    inner: Option<Handles>,
}

#[derive(Clone)]
struct Handles {
    base: ServiceMetrics,
    heartbeat: Heartbeat,
    kite_ws_connected: Gauge,
    kite_ws_reconnects: IntCounter,
    ticks: IntCounter,
    write_errors: IntCounterVec,
    subscribed_instruments: Gauge,
}

impl IngestionMetrics {
    /// Build ingestion's metrics, logging and degrading to inert on failure.
    pub fn new() -> Self {
        match Self::try_build() {
            Ok(handles) => Self {
                inner: Some(handles),
            },
            Err(e) => {
                log::error!(
                    "Prometheus metrics unavailable: {e}. Ingestion continues \
                     uninstrumented and will scrape as down — investigate before \
                     relying on monitoring."
                );
                Self { inner: None }
            }
        }
    }

    fn try_build() -> Result<Handles, service_metrics::prometheus::Error> {
        let base = ServiceMetrics::new(MetricsConfig {
            service: "ingestion",
            version: env!("CARGO_PKG_VERSION"),
            in_session_stall_seconds: IN_SESSION_STALL_SECONDS,
        })?;
        let registry = base.registry();

        let kite_ws_connected = Gauge::with_opts(Opts::new(
            "ingestion_kite_ws_connected",
            "1 while the upstream Kite WebSocket is connected, 0 while down or \
             reconnecting. 0 means no market data is entering the system, \
             regardless of what container health reports.",
        ))?;
        registry.register(Box::new(kite_ws_connected.clone()))?;

        let kite_ws_reconnects = IntCounter::with_opts(Opts::new(
            "ingestion_kite_ws_reconnects_total",
            "Kite WebSocket connections lost or refused. A climbing rate means a \
             flapping feed even when the gauge happens to read 1 on scrape.",
        ))?;
        registry.register(Box::new(kite_ws_reconnects.clone()))?;

        let ticks = IntCounter::with_opts(Opts::new(
            "ingestion_ticks_total",
            "Ticks decoded from Kite binary frames. Its rate is the pipeline's \
             true input throughput.",
        ))?;
        registry.register(Box::new(ticks.clone()))?;

        let write_errors = IntCounterVec::new(
            Opts::new(
                "ingestion_write_errors_total",
                "Sink writes that failed and were dropped. The sinks log-and-drop \
                 by design so a database outage never blocks the WS reader; this \
                 counter is what makes that silent loss visible.",
            ),
            &["sink"],
        )?;
        registry.register(Box::new(write_errors.clone()))?;

        // Pre-create each series so a sink that has never failed reports 0
        // rather than being absent. rate() over a missing series yields nothing,
        // which a dashboard renders as "no data" — indistinguishable from a
        // broken scrape.
        for sink in SINKS {
            write_errors.with_label_values(&[sink]);
        }

        let subscribed_instruments = Gauge::with_opts(Opts::new(
            "ingestion_subscribed_instruments",
            "Instrument tokens currently subscribed on the Kite WS. 0 is normal \
             at boot — the service waits for control-port commands.",
        ))?;
        registry.register(Box::new(subscribed_instruments.clone()))?;

        let heartbeat = base.heartbeat();

        // Nothing is subscribed at boot, so no ticks are due yet. Without this a
        // freshly started ingestion would report an ever-growing stall while
        // behaving exactly as designed.
        base.set_work_expected(false);

        Ok(Handles {
            base,
            heartbeat,
            kite_ws_connected,
            kite_ws_reconnects,
            ticks,
            write_errors,
            subscribed_instruments,
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

    /// The Kite WebSocket connected.
    pub fn ws_connected(&self) {
        if let Some(h) = &self.inner {
            h.kite_ws_connected.set(1.0);
        }
    }

    /// The Kite WebSocket dropped, or a connect attempt failed.
    pub fn ws_disconnected(&self) {
        if let Some(h) = &self.inner {
            h.kite_ws_connected.set(0.0);
            h.kite_ws_reconnects.inc();
        }
    }

    /// Record `n` decoded ticks from one frame. Counts throughput and beats the
    /// work heartbeat — this is the "real work happened" point for ingestion.
    ///
    /// A zero-tick frame (Kite heartbeat, tokens absent from the symbol map)
    /// deliberately does not beat: counting it as work would mask a feed that
    /// has gone quiet, which is exactly what this instrumentation exists to
    /// catch.
    pub fn ticks_decoded(&self, n: usize) {
        if let Some(h) = &self.inner {
            if n > 0 {
                h.ticks.inc_by(n as u64);
                h.heartbeat.beat();
            }
        }
    }

    /// A sink write failed and the row was dropped. `sink` should be one of
    /// [`SINKS`].
    pub fn write_failed(&self, sink: &str) {
        self.write_failed_n(sink, 1);
    }

    /// Record `n` dropped rows for one sink, for batch writers that are
    /// partially lossy — a snapshot batch can lose some rows and keep the rest.
    pub fn write_failed_n(&self, sink: &str, n: usize) {
        if let Some(h) = &self.inner {
            if n > 0 {
                h.write_errors.with_label_values(&[sink]).inc_by(n as u64);
            }
        }
    }

    /// Report how many instruments are subscribed.
    ///
    /// Also drives the work-expected gate: with nothing subscribed the service
    /// has nothing to do, so staleness is idleness rather than failure. With
    /// even one subscription, ticks are due during market hours and silence is
    /// a stall.
    pub fn set_subscribed(&self, count: usize) {
        if let Some(h) = &self.inner {
            h.subscribed_instruments.set(count as f64);
            h.base.set_work_expected(count > 0);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Each test builds its own registry — the metric names are fixed, so two
    /// live instances would collide if they shared one.
    fn metrics() -> IngestionMetrics {
        let m = IngestionMetrics::new();
        assert!(
            m.inner.is_some(),
            "metrics must build cleanly; an inert handle would make every \
             assertion below vacuous"
        );
        m
    }

    fn render(m: &IngestionMetrics) -> String {
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
    fn ws_connection_state_is_reported() {
        let m = metrics();

        m.ws_connected();
        let out = render(&m);
        assert_eq!(sample(&out, "ingestion_kite_ws_connected"), Some(1.0));
        // A clean first connect must not look like a reconnect.
        assert_eq!(sample(&out, "ingestion_kite_ws_reconnects_total"), Some(0.0));

        m.ws_disconnected();
        let out = render(&m);
        assert_eq!(sample(&out, "ingestion_kite_ws_connected"), Some(0.0));
        assert_eq!(sample(&out, "ingestion_kite_ws_reconnects_total"), Some(1.0));

        // Recovery clears the gauge but leaves the reconnect count standing, so
        // a flapping feed stays visible after it comes back.
        m.ws_connected();
        let out = render(&m);
        assert_eq!(sample(&out, "ingestion_kite_ws_connected"), Some(1.0));
        assert_eq!(sample(&out, "ingestion_kite_ws_reconnects_total"), Some(1.0));
    }

    #[test]
    fn decoding_ticks_counts_and_beats() {
        let m = metrics();
        m.set_subscribed(1);

        m.ticks_decoded(1);
        m.ticks_decoded(1);

        let out = render(&m);
        assert_eq!(sample(&out, "ingestion_ticks_total"), Some(2.0));
        // The shared work counter must move too, or the stall detector sees
        // nothing happening while ticks flow.
        assert_eq!(
            sample(&out, "ingestion_work_completed_total"),
            Some(2.0),
            "ticks_decoded must beat the shared heartbeat"
        );
        assert!(!m.inner.as_ref().unwrap().base.readiness().stalled);
    }

    #[test]
    fn batch_of_ticks_counts_every_tick_but_beats_once() {
        let m = metrics();
        m.set_subscribed(1);
        m.ticks_decoded(7);

        let out = render(&m);
        assert_eq!(sample(&out, "ingestion_ticks_total"), Some(7.0));
        // Throughput counts ticks; the heartbeat only needs to record that work
        // happened, so one beat per frame is enough and keeps the hot path cheap.
        assert_eq!(sample(&out, "ingestion_work_completed_total"), Some(1.0));
    }

    #[test]
    fn an_empty_frame_is_not_treated_as_work() {
        // parse_binary_frame can legitimately yield zero ticks (heartbeat frame,
        // unknown tokens). Counting that as work would mask a feed that has gone
        // quiet — exactly the failure this instrumentation exists to catch.
        let m = metrics();
        m.set_subscribed(1);
        m.ticks_decoded(0);

        let out = render(&m);
        assert_eq!(sample(&out, "ingestion_ticks_total"), Some(0.0));
        assert_eq!(
            sample(&out, "ingestion_work_completed_total"),
            Some(0.0),
            "an empty frame must not beat the heartbeat"
        );
    }

    #[test]
    fn sink_error_counters_start_at_zero_not_absent() {
        let out = render(&metrics());
        for sink in SINKS {
            let needle = format!(r#"ingestion_write_errors_total{{sink="{sink}"}}"#);
            assert_eq!(
                sample(&out, &needle),
                Some(0.0),
                "sink {sink} must be present at 0; got:\n{out}"
            );
        }
    }

    #[test]
    fn write_failures_are_counted_per_sink() {
        let m = metrics();
        m.write_failed("questdb_pg");
        m.write_failed("questdb_pg");
        m.write_failed("kafka");

        let out = render(&m);
        assert_eq!(
            sample(&out, r#"ingestion_write_errors_total{sink="questdb_pg"}"#),
            Some(2.0)
        );
        assert_eq!(
            sample(&out, r#"ingestion_write_errors_total{sink="kafka"}"#),
            Some(1.0)
        );
        // One failing sink must not be attributed to another.
        assert_eq!(
            sample(&out, r#"ingestion_write_errors_total{sink="option_pg"}"#),
            Some(0.0)
        );
    }

    #[test]
    fn batch_write_failures_count_every_lost_row() {
        // A snapshot batch is partially lossy: 3 of N rows failing must read as
        // 3, not as one "the batch had a problem" event.
        let m = metrics();
        m.write_failed_n("snapshot", 3);
        m.write_failed_n("snapshot", 2);
        // A fully successful batch must not touch the counter.
        m.write_failed_n("snapshot", 0);

        let out = render(&m);
        assert_eq!(
            sample(&out, r#"ingestion_write_errors_total{sink="snapshot"}"#),
            Some(5.0)
        );
    }

    #[test]
    fn no_subscriptions_means_idle_not_stalled() {
        // The real boot state: ingestion starts with an empty instrument map and
        // waits for control-port commands. That must not page anyone.
        let m = metrics();
        let r = m.inner.as_ref().unwrap().base.readiness();
        assert!(!r.work_expected);
        assert!(!r.stalled);

        let out = render(&m);
        assert_eq!(sample(&out, "ingestion_subscribed_instruments"), Some(0.0));
        assert_eq!(sample(&out, "ingestion_work_expected"), Some(0.0));
    }

    #[test]
    fn subscribing_arms_stall_detection() {
        let m = metrics();
        m.set_subscribed(3);

        assert!(
            m.inner.as_ref().unwrap().base.readiness().work_expected,
            "with instruments subscribed, ticks are due and silence is a stall"
        );
        let out = render(&m);
        assert_eq!(sample(&out, "ingestion_subscribed_instruments"), Some(3.0));
        assert_eq!(sample(&out, "ingestion_work_expected"), Some(1.0));
    }

    #[test]
    fn unsubscribing_everything_returns_to_idle() {
        // Removing the last subscription puts the service back to legitimately
        // idle rather than leaving it armed and alerting on unavoidable silence.
        let m = metrics();
        m.set_subscribed(2);
        assert!(m.inner.as_ref().unwrap().base.readiness().work_expected);

        m.set_subscribed(0);
        let r = m.inner.as_ref().unwrap().base.readiness();
        assert!(!r.work_expected);
        assert!(!r.stalled);
    }

    #[test]
    fn an_inert_handle_is_safe_to_call() {
        // The degraded path: if the registry failed to build, every call site in
        // the hot loop still runs unconditionally and must simply do nothing.
        let m = IngestionMetrics { inner: None };
        m.serve();
        m.ws_connected();
        m.ws_disconnected();
        m.ticks_decoded(5);
        m.write_failed("questdb_pg");
        m.write_failed_n("snapshot", 3);
        m.set_subscribed(3);
    }
}
