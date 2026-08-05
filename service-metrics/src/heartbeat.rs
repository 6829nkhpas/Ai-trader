// service-metrics/src/heartbeat.rs — "is this service actually doing work?"
//
// WHY THIS EXISTS: five of this monorepo's services (technical, predictive,
// quant-rag, ingestion, alpha-terminal) are Kafka consumers or WebSocket loops
// with no request/response surface. For those, every conventional health signal
// lies:
//
//   - the container is running          → says nothing about consumption
//   - the process responds to /health   → the HTTP task is fine while the
//                                         consumer task is wedged on a poll
//   - CPU is non-zero                   → a busy retry loop burns CPU going
//                                         nowhere
//
// The only trustworthy signal is the service asserting "I just completed a unit
// of real work". `Heartbeat::beat()` is that assertion, and
// `*_last_work_age_seconds` is how long it has been since the last one. Paired
// with MarketSession thresholds, that is what separates "idle because the market
// is shut" from "wedged".
//
// Call `beat()` at the point where work is genuinely FINISHED — after the tick
// is decoded, after the signal is published — never merely after a loop
// iteration or a poll that returned nothing. A heartbeat on an empty poll would
// report perfect health for a service consuming an empty topic, which is
// precisely the failure this exists to catch.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

/// Tracks when a service last completed a unit of real work.
///
/// Cheap to clone (`Arc` internally) and safe to `beat()` from any task or
/// thread — a relaxed atomic store on the hot path.
#[derive(Debug, Clone)]
pub struct Heartbeat {
    /// Unix millis of the last completed unit of work. 0 = none yet.
    last_work_ms: Arc<AtomicU64>,
    /// Unix millis at construction, for uptime.
    started_ms: u64,
    /// Total completed units, for rate calculations.
    work_count: Arc<AtomicU64>,
}

impl Heartbeat {
    /// Create a heartbeat that has not yet recorded any work.
    ///
    /// A fresh heartbeat reports `has_worked() == false` rather than pretending
    /// work just happened, so a service that starts up and immediately fails to
    /// consume anything is visible instead of masked by its own start time.
    pub fn new() -> Self {
        Self {
            last_work_ms: Arc::new(AtomicU64::new(0)),
            started_ms: now_ms(),
            work_count: Arc::new(AtomicU64::new(0)),
        }
    }

    /// Record one completed unit of real work.
    ///
    /// Hot path: one relaxed store and one relaxed increment. Safe to call on
    /// every tick.
    pub fn beat(&self) {
        self.last_work_ms.store(now_ms(), Ordering::Relaxed);
        self.work_count.fetch_add(1, Ordering::Relaxed);
    }

    /// Whether any work has been completed since start.
    pub fn has_worked(&self) -> bool {
        self.last_work_ms.load(Ordering::Relaxed) != 0
    }

    /// Total completed units of work since start.
    pub fn work_count(&self) -> u64 {
        self.work_count.load(Ordering::Relaxed)
    }

    /// Seconds since the last completed unit of work.
    ///
    /// Before any work has happened this measures from process start, so a
    /// service that never consumes its first message still ages into a stall
    /// rather than sitting at zero forever.
    pub fn last_work_age_seconds(&self) -> f64 {
        let last = self.last_work_ms.load(Ordering::Relaxed);
        let reference = if last == 0 { self.started_ms } else { last };
        // saturating_sub: a backwards clock step yields 0, never a wrapped
        // enormous age that would fire every stall alert at once.
        now_ms().saturating_sub(reference) as f64 / 1000.0
    }

    /// Seconds since the process started.
    pub fn uptime_seconds(&self) -> f64 {
        now_ms().saturating_sub(self.started_ms) as f64 / 1000.0
    }
}

impl Default for Heartbeat {
    fn default() -> Self {
        Self::new()
    }
}

/// Current unix time in milliseconds. Returns 0 if the clock predates the epoch
/// (unrepresentable in practice) rather than panicking in a metrics path.
fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::thread;
    use std::time::Duration;

    #[test]
    fn starts_with_no_work_recorded() {
        let hb = Heartbeat::new();
        assert!(!hb.has_worked());
        assert_eq!(hb.work_count(), 0);
    }

    #[test]
    fn beat_records_work() {
        let hb = Heartbeat::new();
        hb.beat();
        assert!(hb.has_worked());
        assert_eq!(hb.work_count(), 1);
        hb.beat();
        assert_eq!(hb.work_count(), 2);
    }

    #[test]
    fn age_resets_on_beat() {
        let hb = Heartbeat::new();
        thread::sleep(Duration::from_millis(60));
        let before = hb.last_work_age_seconds();
        assert!(before >= 0.05, "age should accrue, got {before}");
        hb.beat();
        assert!(
            hb.last_work_age_seconds() < before,
            "beat() must reset the age"
        );
    }

    #[test]
    fn age_measures_from_start_before_any_work() {
        // The service-never-consumed-anything case: age must grow from start,
        // not sit at zero, or a permanently wedged service looks healthy.
        let hb = Heartbeat::new();
        thread::sleep(Duration::from_millis(60));
        assert!(hb.last_work_age_seconds() >= 0.05);
        assert!(!hb.has_worked());
    }

    #[test]
    fn clones_share_state() {
        // Services beat from a worker task while /metrics reads from the HTTP
        // task — both hold clones, and they must observe the same counter.
        let hb = Heartbeat::new();
        let clone = hb.clone();
        clone.beat();
        assert_eq!(hb.work_count(), 1);
        assert!(hb.has_worked());
    }

    #[test]
    fn beats_from_many_threads_are_all_counted() {
        let hb = Heartbeat::new();
        let handles: Vec<_> = (0..8)
            .map(|_| {
                let hb = hb.clone();
                thread::spawn(move || {
                    for _ in 0..100 {
                        hb.beat();
                    }
                })
            })
            .collect();
        for h in handles {
            h.join().unwrap();
        }
        assert_eq!(hb.work_count(), 800);
    }

    #[test]
    fn uptime_accrues() {
        let hb = Heartbeat::new();
        thread::sleep(Duration::from_millis(60));
        assert!(hb.uptime_seconds() >= 0.05);
    }
}
