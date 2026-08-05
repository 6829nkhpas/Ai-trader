//! The wire contract consumed by the admin panel at `dashboard.stratai.live`.
//!
//! This module is the whole reason `status-api` exists as a service rather than
//! as a Grafana link: the panel gets one stable JSON shape and never learns
//! PromQL, never hard-codes a metric name, and never has to know that each
//! service prefixes its metrics differently.
//!
//! Because it is a published contract, the field names here are load-bearing.
//! Renaming one is a breaking change for a deployment that lives in a different
//! repository and ships on its own schedule — so the names are pinned by tests
//! in [`crate::classify`], the same way the metric names are pinned in each
//! service.

use serde::{Deserialize, Serialize};

/// Health of a single service, or of the fleet as a whole.
///
/// The ordering of the variants is the severity ordering used by
/// [`Health::worst`], which is how `overall` is derived.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Health {
    /// Scraping, and doing work within its own stall threshold — or legitimately
    /// idle with nothing to do.
    Up,
    /// Reachable but not working: stalled past its threshold while work was
    /// expected, or reporting errors on a dependency.
    Degraded,
    /// Not answering at all.
    Down,
    /// We could not find out.
    ///
    /// Deliberately the MOST severe variant, and deliberately never collapsed
    /// into `up`. If Prometheus is unreachable and the direct probe also fails,
    /// the honest answer is "no idea" — reporting that as healthy is how a
    /// monitoring system convinces someone that a dead platform is fine. It
    /// sorts above `down` so that `overall` degrades rather than improves when
    /// visibility is lost.
    Unknown,
}

impl Health {
    /// The more severe of two healths. `Unknown` beats everything.
    pub fn worst(self, other: Health) -> Health {
        if other > self {
            other
        } else {
            self
        }
    }
}

/// Where the numbers in a report came from.
///
/// Exposed to the panel so a human reading a green dashboard can tell whether
/// it is backed by the full metric set or by a fallback that only knows
/// reachability. A `direct_probe` report with everything `up` is much weaker
/// evidence than a `prometheus` one, and hiding that distinction would be a lie
/// of omission.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Source {
    /// Full metric set from Prometheus.
    Prometheus,
    /// Prometheus was unreachable; each service's own `/ready` was probed.
    /// Reachability and per-service readiness only — no rates, no history.
    DirectProbe,
    /// Neither worked.
    None,
}

/// One service's entry in the report.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServiceStatus {
    pub name: String,
    pub status: Health,
    /// Which layer of the platform this sits in (`data-plane`, `agents`,
    /// `reasoning`, `monitoring`). Lets the panel group without a second
    /// mapping of its own.
    pub tier: String,

    /// Why it is not `up`. Always present for degraded/down/unknown, always
    /// absent for `up` — a reason on a healthy service reads as a warning.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,

    #[serde(skip_serializing_if = "Option::is_none")]
    pub uptime_s: Option<f64>,

    /// Seconds since this service last completed a unit of real work.
    ///
    /// The central number in the whole contract. Five of these services are
    /// Kafka/WS loops that can wedge on a poll and answer health checks forever
    /// while processing nothing; this is what separates that state from health.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_work_age_s: Option<f64>,

    /// The service's OWN threshold for "too long", not a constant chosen here.
    /// Sent so the panel can render a proportion rather than an unlabelled
    /// number — and so the Rust services' automatic off-session widening is
    /// visible instead of looking like a bug.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stall_threshold_s: Option<f64>,

    /// Whether the service had anything to do at all. A stale service with
    /// `work_expected: false` is idle, not failing.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub work_expected: Option<bool>,

    /// Completed units of work per second, averaged over 5m.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub work_rate_per_s: Option<f64>,

    /// Last time this service was observed answering, RFC3339. Only populated
    /// for services that are not currently up — for a healthy service the
    /// answer is "now", and sending it invites the panel to render a
    /// meaningless timestamp.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_seen: Option<String>,

    /// Service-specific extras (`kite_ws`, `ws_clients`, `runs_in_flight`, ...).
    /// Free-form by design: the panel renders these as labelled rows without
    /// needing a schema change here every time a service adds a gauge.
    #[serde(default, skip_serializing_if = "serde_json::Map::is_empty")]
    pub detail: serde_json::Map<String, serde_json::Value>,
}

impl ServiceStatus {
    /// A service we know should exist but have no information about.
    pub fn unknown(name: &str, tier: &str, reason: impl Into<String>) -> Self {
        Self {
            name: name.to_string(),
            status: Health::Unknown,
            tier: tier.to_string(),
            reason: Some(reason.into()),
            uptime_s: None,
            last_work_age_s: None,
            stall_threshold_s: None,
            work_expected: None,
            work_rate_per_s: None,
            last_seen: None,
            detail: serde_json::Map::new(),
        }
    }
}

/// A firing alert, passed through from Prometheus.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Alert {
    pub name: String,
    /// `firing` or `pending`. Pending alerts are included because they are the
    /// early warning — an alert with `for: 5m` spends five minutes pending, and
    /// hiding that is throwing away the only lead time available.
    pub state: String,
    pub severity: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub service: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub summary: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub runbook: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub active_since: Option<String>,
}

/// The `GET /api/status` body.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StatusReport {
    /// Worst health across all services. See [`Health::worst`] — losing
    /// visibility makes this worse, never better.
    pub overall: Health,
    pub generated_at: String,
    /// `open` / `closed` / `weekend`, from `MarketSession::now()` — the same
    /// function the services themselves use, so it agrees with them by
    /// construction and still answers when Prometheus is down. The panel needs
    /// it to explain itself: an idle pipeline at 02:00 IST is correct, and a UI
    /// that cannot say so gets read as an outage every night.
    pub market_session: String,

    /// Counts, so the panel can render a summary line without walking the array
    /// and without deciding for itself whether `unknown` counts as degraded.
    pub up_count: usize,
    pub degraded_count: usize,
    pub down_count: usize,
    pub unknown_count: usize,

    /// Where these numbers came from. See [`Source`].
    pub source: Source,

    /// Present when the report is degraded as a REPORT — Prometheus unreachable,
    /// a query that failed — as opposed to the fleet being unhealthy. The panel
    /// should surface this differently: it means "do not trust the rest of this
    /// object as much as usual".
    #[serde(skip_serializing_if = "Option::is_none")]
    pub monitoring_warning: Option<String>,

    pub services: Vec<ServiceStatus>,
}

/// The `GET /api/status/:service` body: current state plus a short history.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServiceDetail {
    pub generated_at: String,
    pub market_session: String,
    pub source: Source,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub monitoring_warning: Option<String>,
    pub service: ServiceStatus,
    /// Alerts naming this service.
    pub alerts: Vec<Alert>,
    /// Recent samples, oldest first. Empty when Prometheus is unavailable —
    /// history is the one thing a direct probe fundamentally cannot supply.
    pub history: Vec<HistoryPoint>,
}

/// One point in a service's recent history.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HistoryPoint {
    pub at: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub last_work_age_s: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub work_rate_per_s: Option<f64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unknown_is_the_most_severe_health() {
        // The ordering is not cosmetic: `overall` is a fold of `worst` over
        // every service, so if Unknown sorted below Down (or worse, below Up) a
        // fleet we cannot see would report as healthier than one we can.
        assert_eq!(Health::Up.worst(Health::Degraded), Health::Degraded);
        assert_eq!(Health::Degraded.worst(Health::Down), Health::Down);
        assert_eq!(Health::Down.worst(Health::Unknown), Health::Unknown);
        assert_eq!(Health::Unknown.worst(Health::Up), Health::Unknown);
    }

    #[test]
    fn worst_is_commutative_and_idempotent() {
        let all = [Health::Up, Health::Degraded, Health::Down, Health::Unknown];
        for a in all {
            assert_eq!(a.worst(a), a, "{a:?} folded with itself must not change");
            for b in all {
                assert_eq!(a.worst(b), b.worst(a), "worst({a:?}, {b:?}) must commute");
            }
        }
    }

    #[test]
    fn health_serializes_as_the_lowercase_strings_the_panel_expects() {
        // These four strings are a published contract with a deployment in
        // another repository. Changing them is a breaking change, so they are
        // asserted literally rather than derived.
        for (h, want) in [
            (Health::Up, "\"up\""),
            (Health::Degraded, "\"degraded\""),
            (Health::Down, "\"down\""),
            (Health::Unknown, "\"unknown\""),
        ] {
            assert_eq!(serde_json::to_string(&h).unwrap(), want);
        }
    }

    #[test]
    fn a_healthy_service_omits_reason_and_last_seen() {
        // A `reason` on an `up` service renders as a warning in the panel, and
        // a `last_seen` on a live service is a meaningless timestamp.
        let s = ServiceStatus {
            name: "ingestion".into(),
            status: Health::Up,
            tier: "data-plane".into(),
            reason: None,
            uptime_s: Some(120.0),
            last_work_age_s: Some(0.4),
            stall_threshold_s: Some(45.0),
            work_expected: Some(true),
            work_rate_per_s: Some(1840.0),
            last_seen: None,
            detail: serde_json::Map::new(),
        };
        let json = serde_json::to_string(&s).unwrap();
        assert!(!json.contains("reason"), "{json}");
        assert!(!json.contains("last_seen"), "{json}");
        assert!(!json.contains("detail"), "empty detail should be omitted: {json}");
        assert!(json.contains("\"status\":\"up\""), "{json}");
    }

    #[test]
    fn unknown_constructor_always_carries_a_reason() {
        // An unknown with no explanation is indistinguishable from a bug in
        // this service, so the constructor makes the reason non-optional.
        let s = ServiceStatus::unknown("technical", "agents", "prometheus unreachable");
        assert_eq!(s.status, Health::Unknown);
        assert_eq!(s.reason.as_deref(), Some("prometheus unreachable"));
        assert!(s.last_work_age_s.is_none());
    }

    #[test]
    fn the_report_round_trips() {
        let report = StatusReport {
            overall: Health::Degraded,
            generated_at: "2026-08-04T09:14:22Z".into(),
            market_session: "open".into(),
            up_count: 8,
            degraded_count: 1,
            down_count: 0,
            unknown_count: 0,
            source: Source::Prometheus,
            monitoring_warning: None,
            services: vec![ServiceStatus::unknown("x", "agents", "no data")],
        };
        let back: StatusReport =
            serde_json::from_str(&serde_json::to_string(&report).unwrap()).unwrap();
        assert_eq!(back.overall, Health::Degraded);
        assert_eq!(back.market_session, "open");
        assert_eq!(back.source, Source::Prometheus);
        assert_eq!(back.services.len(), 1);
    }

    #[test]
    fn source_serializes_in_snake_case() {
        assert_eq!(
            serde_json::to_string(&Source::DirectProbe).unwrap(),
            "\"direct_probe\""
        );
        assert_eq!(
            serde_json::to_string(&Source::Prometheus).unwrap(),
            "\"prometheus\""
        );
    }
}
