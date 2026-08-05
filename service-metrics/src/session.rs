// service-metrics/src/session.rs — NSE trading-session awareness.
//
// WHY THIS EXISTS: every staleness check in this stack has to answer "is zero
// throughput a failure?" — and the honest answer depends on the clock. The NSE
// equity session runs 09:15–15:30 IST on weekdays. Outside it, a tick pipeline
// producing nothing is behaving perfectly. Without this distinction the
// WorkStalled alert would fire every single night at 15:31 and stay lit until
// 09:15, which trains everyone to ignore it — and an alert nobody reads is
// worse than no alert, because it looks like coverage.
//
// Deliberately NOT handled here: NSE trading holidays. That needs a maintained
// calendar, and getting it wrong in the "market is open" direction would
// suppress real alerts. A holiday simply looks like an in-session quiet day —
// noisier, but it fails toward reporting rather than hiding. See
// `is_trading_holiday` in the follow-up work noted in docs/MONITORING.md.

use chrono::{DateTime, Datelike, FixedOffset, TimeZone, Timelike, Utc, Weekday};

/// IST is UTC+05:30 year-round — India observes no daylight saving, so a fixed
/// offset is exact here rather than an approximation.
const IST_OFFSET_SECONDS: i32 = 5 * 3600 + 30 * 60;

/// Session open, 09:15 IST, as minutes from midnight.
const SESSION_OPEN_MIN: u32 = 9 * 60 + 15;

/// Session close, 15:30 IST, as minutes from midnight.
const SESSION_CLOSE_MIN: u32 = 15 * 60 + 30;

/// Where "now" falls relative to the NSE equity session.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MarketSession {
    /// Weekday, 09:15–15:30 IST. Silence from a data service is suspicious.
    Open,
    /// Weekday outside session hours. Reduced activity is expected.
    Closed,
    /// Saturday or Sunday. Near-total silence is expected.
    Weekend,
}

impl MarketSession {
    /// Classify the current instant.
    pub fn now() -> Self {
        Self::at(Utc::now())
    }

    /// Classify an arbitrary instant. Split from [`now`] so the logic is
    /// testable without mocking the clock.
    pub fn at(instant: DateTime<Utc>) -> Self {
        let ist = FixedOffset::east_opt(IST_OFFSET_SECONDS)
            .expect("IST offset is a valid fixed offset")
            .from_utc_datetime(&instant.naive_utc());

        match ist.weekday() {
            Weekday::Sat | Weekday::Sun => MarketSession::Weekend,
            _ => {
                let minutes = ist.hour() * 60 + ist.minute();
                // Inclusive of both bounds: 09:15:00 is open, and 15:30:00 is
                // still within the session (the closing auction lands on it).
                if (SESSION_OPEN_MIN..=SESSION_CLOSE_MIN).contains(&minutes) {
                    MarketSession::Open
                } else {
                    MarketSession::Closed
                }
            }
        }
    }

    /// True only during live trading hours.
    pub fn is_open(self) -> bool {
        matches!(self, MarketSession::Open)
    }

    /// Lowercase label for JSON payloads and Prometheus labels.
    pub fn as_str(self) -> &'static str {
        match self {
            MarketSession::Open => "open",
            MarketSession::Closed => "closed",
            MarketSession::Weekend => "weekend",
        }
    }

    /// How long a service may go without doing work before it is considered
    /// stalled, given the current session.
    ///
    /// `in_session` is the service's own expectation of its busiest cadence
    /// (ingestion sees ticks every few seconds; quant-rag may idle for minutes
    /// between LLM calls). Off-session the allowance widens sharply — the
    /// service is not broken, there is simply nothing arriving.
    pub fn stall_threshold_seconds(self, in_session: f64) -> f64 {
        match self {
            MarketSession::Open => in_session,
            // Wide enough that a normal overnight lull never trips, narrow
            // enough that a service dead for hours is still eventually noticed.
            MarketSession::Closed => (in_session * 20.0).max(1_800.0),
            MarketSession::Weekend => (in_session * 60.0).max(7_200.0),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build a UTC instant from an IST wall-clock time, so the tests read in
    /// the timezone humans actually reason about here.
    fn ist(y: i32, m: u32, d: u32, hh: u32, mm: u32) -> DateTime<Utc> {
        FixedOffset::east_opt(IST_OFFSET_SECONDS)
            .unwrap()
            .with_ymd_and_hms(y, m, d, hh, mm, 0)
            .unwrap()
            .with_timezone(&Utc)
    }

    // 2026-08-03 is a Monday; 2026-08-01 a Saturday; 2026-08-02 a Sunday.

    #[test]
    fn open_during_weekday_session() {
        assert_eq!(MarketSession::at(ist(2026, 8, 3, 10, 0)), MarketSession::Open);
        assert_eq!(MarketSession::at(ist(2026, 8, 3, 12, 30)), MarketSession::Open);
    }

    #[test]
    fn session_boundaries_are_inclusive() {
        // The exact edges — the cases most likely to be off by a minute.
        assert_eq!(MarketSession::at(ist(2026, 8, 3, 9, 15)), MarketSession::Open);
        assert_eq!(MarketSession::at(ist(2026, 8, 3, 15, 30)), MarketSession::Open);
        assert_eq!(MarketSession::at(ist(2026, 8, 3, 9, 14)), MarketSession::Closed);
        assert_eq!(MarketSession::at(ist(2026, 8, 3, 15, 31)), MarketSession::Closed);
    }

    #[test]
    fn closed_outside_weekday_session() {
        assert_eq!(MarketSession::at(ist(2026, 8, 3, 2, 0)), MarketSession::Closed);
        assert_eq!(MarketSession::at(ist(2026, 8, 3, 23, 59)), MarketSession::Closed);
    }

    #[test]
    fn weekend_wins_even_during_session_hours() {
        // 10:00 on a Saturday is NOT Open — the weekday check must come first.
        assert_eq!(MarketSession::at(ist(2026, 8, 1, 10, 0)), MarketSession::Weekend);
        assert_eq!(MarketSession::at(ist(2026, 8, 2, 10, 0)), MarketSession::Weekend);
    }

    #[test]
    fn utc_instants_map_into_the_correct_ist_day() {
        // 04:00 UTC Monday == 09:30 IST Monday → open.
        let utc_morning = Utc.with_ymd_and_hms(2026, 8, 3, 4, 0, 0).unwrap();
        assert_eq!(MarketSession::at(utc_morning), MarketSession::Open);

        // 20:00 UTC Friday == 01:30 IST Saturday → the IST date rolls over into
        // the weekend even though it is still Friday in UTC. This is the bug
        // that a naive UTC weekday check would introduce.
        let utc_friday_night = Utc.with_ymd_and_hms(2026, 7, 31, 20, 0, 0).unwrap();
        assert_eq!(MarketSession::at(utc_friday_night), MarketSession::Weekend);
    }

    #[test]
    fn thresholds_widen_outside_the_session() {
        let in_session = 60.0;
        assert_eq!(MarketSession::Open.stall_threshold_seconds(in_session), 60.0);
        // A 60s in-session expectation must not stay 60s overnight.
        assert!(MarketSession::Closed.stall_threshold_seconds(in_session) >= 1_800.0);
        assert!(
            MarketSession::Weekend.stall_threshold_seconds(in_session)
                > MarketSession::Closed.stall_threshold_seconds(in_session)
        );
    }

    #[test]
    fn thresholds_respect_a_large_in_session_expectation() {
        // A service that legitimately idles 10 minutes in-session must get a
        // proportionally wider off-session allowance, not the 1800s floor.
        let in_session = 600.0;
        assert_eq!(
            MarketSession::Closed.stall_threshold_seconds(in_session),
            12_000.0
        );
    }

    #[test]
    fn is_open_matches_classification() {
        assert!(MarketSession::Open.is_open());
        assert!(!MarketSession::Closed.is_open());
        assert!(!MarketSession::Weekend.is_open());
    }
}
