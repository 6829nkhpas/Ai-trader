//! The registry of services we expect to exist, and the pure classifier that
//! turns raw numbers into a [`Health`].
//!
//! Everything here is a pure function over plain data, which is the point: the
//! decision "is this service working" is the one part of this crate that must be
//! right, and it is testable without a live Prometheus or a live fleet.

use crate::model::{Health, ServiceStatus};

/// Every service the panel should see, as `(name, tier, metrics port)`.
///
/// Hardcoded rather than discovered from Prometheus's `up` series on purpose: a
/// service missing from the scrape config would simply vanish from a discovered
/// list, and a service that silently disappears from a status page is the exact
/// failure this whole stack exists to prevent. Listed here, it surfaces as
/// `unknown` instead.
///
/// The ports mirror `infra/prometheus/prometheus.yml` and are used only by the
/// direct-probe fallback. Keep the two in sync — the tier strings too.
pub const SERVICES: &[(&str, &str, u16)] = &[
    ("ingestion", "data-plane", 9101),
    ("aggregator", "data-plane", 9102),
    ("alpha-terminal", "data-plane", 9103),
    ("technical", "agents", 9104),
    ("predictive", "agents", 9105),
    ("quant-rag", "agents", 9106),
    ("tool-server", "reasoning", 9107),
    ("sentiment", "agents", 9108),
    ("deep-quant", "reasoning", 9109),
];

pub fn tier_of(name: &str) -> &'static str {
    SERVICES
        .iter()
        .find(|(n, _, _)| *n == name)
        .map(|(_, t, _)| *t)
        .unwrap_or("unknown")
}

/// What we managed to learn about one service in a single collection pass.
///
/// `None` means "no such series", which is genuinely different from `Some(0.0)`
/// — the first is blindness, the second is a measurement.
#[derive(Debug, Clone, Default)]
pub struct Sample {
    /// Prometheus `up`: 1 scraped, 0 scrape failed, `None` no series at all.
    pub up: Option<f64>,
    pub last_work_age_s: Option<f64>,
    /// The service's own threshold, already widened for the current session by
    /// `MarketSession::stall_threshold_seconds` on the service side. That is why
    /// this classifier contains no session logic: comparing two numbers the
    /// service publishes beats reimplementing NSE hours in a second place.
    pub stall_threshold_s: Option<f64>,
    pub work_expected: Option<f64>,
    pub work_rate_per_s: Option<f64>,
    pub uptime_s: Option<f64>,
    pub last_seen: Option<String>,
    pub detail: serde_json::Map<String, serde_json::Value>,
}

/// The whole decision, in one place.
pub fn classify(name: &str, sample: &Sample) -> ServiceStatus {
    let tier = tier_of(name);

    let (status, reason) = match sample.up {
        // No `up` series at all. Either the target was never configured or
        // Prometheus has no memory of it. We do not know anything.
        None => (
            Health::Unknown,
            Some("no data — target not configured or never scraped".to_string()),
        ),
        Some(u) if u == 0.0 => (Health::Down, Some("scrape failed".to_string())),
        Some(_) => match (
            sample.last_work_age_s,
            sample.stall_threshold_s,
            sample.work_expected,
        ) {
            // The case the whole stack exists for: process answering, work not
            // happening. Only counts when the service says work was expected —
            // otherwise it is idle, which is healthy.
            (Some(age), Some(threshold), Some(expected))
                if expected == 1.0 && age > threshold =>
            {
                (
                    Health::Degraded,
                    Some(format!(
                        "stalled — no work for {age:.0}s (threshold {threshold:.0}s)"
                    )),
                )
            }
            _ => (Health::Up, None),
        },
    };

    ServiceStatus {
        name: name.to_string(),
        status,
        tier: tier.to_string(),
        reason,
        uptime_s: sample.uptime_s,
        last_work_age_s: sample.last_work_age_s,
        stall_threshold_s: sample.stall_threshold_s,
        work_expected: sample.work_expected.map(|w| w == 1.0),
        work_rate_per_s: sample.work_rate_per_s,
        // Only meaningful when the service is not currently answering.
        last_seen: if status == Health::Up {
            None
        } else {
            sample.last_seen.clone()
        },
        detail: sample.detail.clone(),
    }
}

/// Fold per-service healths into the report's `overall`.
///
/// An empty fleet is `Unknown`, not `Up` — "we found no services" must never
/// render as a green dashboard.
pub fn overall(services: &[ServiceStatus]) -> Health {
    services
        .iter()
        .map(|s| s.status)
        .reduce(Health::worst)
        .unwrap_or(Health::Unknown)
}

pub fn counts(services: &[ServiceStatus]) -> (usize, usize, usize, usize) {
    let n = |h| services.iter().filter(|s| s.status == h).count();
    (
        n(Health::Up),
        n(Health::Degraded),
        n(Health::Down),
        n(Health::Unknown),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A service scraping fine and working — ingestion mid-session.
    fn fresh() -> Sample {
        Sample {
            up: Some(1.0),
            last_work_age_s: Some(0.4),
            stall_threshold_s: Some(60.0),
            work_expected: Some(1.0),
            work_rate_per_s: Some(30.6),
            uptime_s: Some(91_422.0),
            ..Default::default()
        }
    }

    #[test]
    fn fresh_is_up_with_no_reason() {
        let s = classify("ingestion", &fresh());
        assert_eq!(s.status, Health::Up);
        assert!(s.reason.is_none());
        assert_eq!(s.tier, "data-plane");
    }

    #[test]
    fn stale_in_session_is_degraded() {
        // 38s idle against a 30s in-session threshold: the container is up, the
        // Kafka consumer is wedged. This is the case the plan was written for.
        let s = classify(
            "technical",
            &Sample {
                last_work_age_s: Some(38.0),
                stall_threshold_s: Some(30.0),
                ..fresh()
            },
        );
        assert_eq!(s.status, Health::Degraded);
        assert!(s.reason.unwrap().contains("stalled"));
    }

    #[test]
    fn the_same_staleness_off_session_is_up() {
        // Identical age. The only difference is the threshold the service itself
        // published, already widened by MarketSession — which is why nothing in
        // this classifier knows what time it is.
        let s = classify(
            "technical",
            &Sample {
                last_work_age_s: Some(38.0),
                stall_threshold_s: Some(1_800.0),
                ..fresh()
            },
        );
        assert_eq!(s.status, Health::Up);
    }

    #[test]
    fn idle_is_up_even_when_very_stale() {
        // work_expected = 0: nobody has subscribed, so there is nothing to do.
        // Sending an operator after this is how alerts get ignored.
        let s = classify(
            "quant-rag",
            &Sample {
                last_work_age_s: Some(9_000.0),
                stall_threshold_s: Some(300.0),
                work_expected: Some(0.0),
                ..fresh()
            },
        );
        assert_eq!(s.status, Health::Up);
        assert_eq!(s.work_expected, Some(false));
    }

    #[test]
    fn scrape_failed_is_down_and_keeps_last_seen() {
        let s = classify(
            "sentiment",
            &Sample {
                up: Some(0.0),
                last_seen: Some("2026-08-04T08:51:03Z".into()),
                ..fresh()
            },
        );
        assert_eq!(s.status, Health::Down);
        assert_eq!(s.reason.as_deref(), Some("scrape failed"));
        assert_eq!(s.last_seen.as_deref(), Some("2026-08-04T08:51:03Z"));
    }

    #[test]
    fn no_series_is_unknown_never_up() {
        // Prometheus answered but has never heard of this service. The tempting
        // bug is to treat "no stall metric" as "not stalled" and report `up`.
        let s = classify("deep-quant", &Sample::default());
        assert_eq!(s.status, Health::Unknown);
        assert!(s.reason.is_some());
    }

    #[test]
    fn scraping_but_not_publishing_the_contract_is_still_up() {
        // up=1, no heartbeat metrics. It is answering; we just cannot say more.
        // Deliberately not Degraded — a service that predates the contract is
        // not a broken service, and crying wolf here costs more than it buys.
        let s = classify(
            "tool-server",
            &Sample {
                up: Some(1.0),
                ..Default::default()
            },
        );
        assert_eq!(s.status, Health::Up);
    }

    #[test]
    fn prometheus_down_makes_every_service_unknown_and_the_fleet_unknown() {
        // The plan's hardest requirement: losing visibility must not read as
        // health. Not one of these is `up`, and `overall` is not `up` either.
        let services: Vec<_> = SERVICES
            .iter()
            .map(|(n, t, _)| ServiceStatus::unknown(n, t, "prometheus unreachable"))
            .collect();
        assert!(services.iter().all(|s| s.status == Health::Unknown));
        assert_eq!(overall(&services), Health::Unknown);
        let (up, deg, down, unk) = counts(&services);
        assert_eq!((up, deg, down), (0, 0, 0));
        assert_eq!(unk, SERVICES.len());
    }

    #[test]
    fn overall_is_the_worst_service_and_an_empty_fleet_is_unknown() {
        let mk = |h: Health| ServiceStatus {
            status: h,
            ..ServiceStatus::unknown("x", "agents", "r")
        };
        assert_eq!(overall(&[]), Health::Unknown);
        assert_eq!(overall(&[mk(Health::Up), mk(Health::Up)]), Health::Up);
        assert_eq!(
            overall(&[mk(Health::Up), mk(Health::Degraded)]),
            Health::Degraded
        );
        assert_eq!(
            overall(&[mk(Health::Down), mk(Health::Unknown)]),
            Health::Unknown
        );
    }

    #[test]
    fn every_registered_service_has_a_distinct_port_in_the_documented_range() {
        // A duplicated port here means the probe fallback would ask one service
        // about another and report a confident wrong answer.
        let mut ports: Vec<u16> = SERVICES.iter().map(|(_, _, p)| *p).collect();
        ports.sort_unstable();
        ports.dedup();
        assert_eq!(ports.len(), SERVICES.len(), "duplicate metrics port");
        assert!(ports.iter().all(|p| (9101..=9110).contains(p)));
    }
}
