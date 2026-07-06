// services/fno_config.rs — Env-driven F&O configuration resolution (Phase F1, task 5.1).
//
// A single resolver reads the F&O scope from environment variables once and is
// shared by both the option-chain subscriber and the snapshot interval handed to
// the ingestion service (R6.4). It is TOTAL: any unset, empty, or invalid value
// falls back to its documented default, and the function never panics
// (R6.1–R6.3).
//
// To keep the resolution logic unit- and property-testable without mutating the
// real process environment, the env-reading is separated from a pure resolution
// function: `resolve_fno_config_with` takes a closure that looks up a variable by
// name, and `resolve_fno_config` simply supplies `std::env::var` to it.
//
//   Env var                       Meaning                         Default
//   ----------------------------  ------------------------------  -----------------------
//   FNO_UNDERLYINGS               comma-separated underlyings     "NIFTY 50,BANKNIFTY"
//   FNO_NEAREST_EXPIRIES          N nearest expiries              2
//   FNO_STRIKE_BAND_HALF_WIDTH    M (strikes each side of ATM)    10
//   FNO_ATM_RECENTER_THRESHOLD    min ATM move to re-subscribe    1
//   FNO_SNAPSHOT_INTERVAL_SECS    chain snapshot cadence (secs)   60

use crate::services::option_chain::ChainConfig;

/// Documented defaults (kept in one place so tests and code agree).
pub const DEFAULT_UNDERLYINGS: [&str; 2] = ["NIFTY 50", "BANKNIFTY"];
pub const DEFAULT_NEAREST_EXPIRIES: usize = 2;
pub const DEFAULT_STRIKE_BAND_HALF_WIDTH: usize = 10;
pub const DEFAULT_ATM_RECENTER_THRESHOLD: f64 = 1.0;
pub const DEFAULT_SNAPSHOT_INTERVAL_SECS: u64 = 60;

/// Resolved F&O configuration: the underlyings to follow plus the bounded
/// chain-resolution parameters. Guaranteed by construction to have a non-empty
/// `underlyings` list and positive counts/intervals.
#[derive(Clone, PartialEq, Debug)]
pub struct FnoConfig {
    /// FNO_UNDERLYINGS — never empty.
    pub underlyings: Vec<String>,
    /// Bounded chain-resolution parameters (expiries / band / recenter / snapshot).
    pub chain: ChainConfig,
}

/// Read the F&O configuration from the real process environment, applying
/// documented defaults for any unset/empty/invalid value. Total; never panics.
/// (R6.1–R6.3)
pub fn resolve_fno_config() -> FnoConfig {
    resolve_fno_config_with(|key| std::env::var(key).ok())
}

/// Pure resolution: build a fully-valid `FnoConfig` from an arbitrary env lookup.
///
/// `get` returns the raw value of a variable, or `None` when it is unset. Empty
/// or invalid raw values are treated exactly like unset — the documented default
/// is applied — so the result always has a non-empty underlyings list and
/// strictly positive counts/intervals. This function performs no I/O and never
/// panics, which makes it directly property-testable with arbitrary env maps
/// (task 5.2).
pub fn resolve_fno_config_with<F>(get: F) -> FnoConfig
where
    F: Fn(&str) -> Option<String>,
{
    let underlyings = parse_underlyings(get("FNO_UNDERLYINGS").as_deref());
    let nearest_expiries = parse_positive_usize(
        get("FNO_NEAREST_EXPIRIES").as_deref(),
        DEFAULT_NEAREST_EXPIRIES,
    );
    let strike_band_half_width = parse_band_half_width(
        get("FNO_STRIKE_BAND_HALF_WIDTH").as_deref(),
        DEFAULT_STRIKE_BAND_HALF_WIDTH,
    );
    let recenter_threshold = parse_positive_f64(
        get("FNO_ATM_RECENTER_THRESHOLD").as_deref(),
        DEFAULT_ATM_RECENTER_THRESHOLD,
    );
    let snapshot_interval_secs = parse_positive_u64(
        get("FNO_SNAPSHOT_INTERVAL_SECS").as_deref(),
        DEFAULT_SNAPSHOT_INTERVAL_SECS,
    );

    FnoConfig {
        underlyings,
        chain: ChainConfig {
            nearest_expiries,
            strike_band_half_width,
            recenter_threshold,
            snapshot_interval_secs,
        },
    }
}

/// Parse a comma-separated underlyings list. Entries are trimmed and empty
/// entries dropped; if nothing usable remains (unset, empty, or all-blank), the
/// documented default list is returned. The result is always non-empty.
fn parse_underlyings(raw: Option<&str>) -> Vec<String> {
    let parsed: Vec<String> = raw
        .unwrap_or("")
        .split(',')
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .collect();

    if parsed.is_empty() {
        DEFAULT_UNDERLYINGS.iter().map(|s| s.to_string()).collect()
    } else {
        parsed
    }
}

/// Parse a strictly-positive `usize`. Unset/empty/invalid/zero → `default`.
fn parse_positive_usize(raw: Option<&str>, default: usize) -> usize {
    match raw.map(str::trim) {
        Some(s) if !s.is_empty() => match s.parse::<usize>() {
            Ok(v) if v > 0 => v,
            _ => default,
        },
        _ => default,
    }
}

/// Parse a strictly-positive `u64`. Unset/empty/invalid/zero → `default`.
fn parse_positive_u64(raw: Option<&str>, default: u64) -> u64 {
    match raw.map(str::trim) {
        Some(s) if !s.is_empty() => match s.parse::<u64>() {
            Ok(v) if v > 0 => v,
            _ => default,
        },
        _ => default,
    }
}

/// Parse a non-negative band half-width (`usize`). The band half-width M may be
/// 0 (a single ATM strike), so unlike the other counts it accepts zero; only
/// unset/empty/invalid values fall back to `default`.
fn parse_band_half_width(raw: Option<&str>, default: usize) -> usize {
    match raw.map(str::trim) {
        Some(s) if !s.is_empty() => s.parse::<usize>().unwrap_or(default),
        _ => default,
    }
}

/// Parse a strictly-positive, finite `f64`. Unset/empty/invalid/non-finite/
/// non-positive → `default`.
fn parse_positive_f64(raw: Option<&str>, default: f64) -> f64 {
    match raw.map(str::trim) {
        Some(s) if !s.is_empty() => match s.parse::<f64>() {
            Ok(v) if v.is_finite() && v > 0.0 => v,
            _ => default,
        },
        _ => default,
    }
}

#[cfg(test)]
mod config_resolution_property_tests {
    // Property test for the env-driven F&O config resolver (task 5.2).
    //
    // The resolver `resolve_fno_config_with` must be TOTAL: for ANY env map
    // (any combination of unset / empty / invalid / valid values for each of the
    // five F&O variables) it returns — without panicking — a `FnoConfig` in which
    // every unset/empty/invalid setting takes its documented default and every
    // field is valid (non-empty underlyings; strictly positive nearest_expiries &
    // snapshot_interval; finite positive recenter_threshold; band half-width may
    // be 0). When a valid value is supplied, that value is used.
    //
    // The test never touches the real process environment: it builds a
    // `HashMap`-backed lookup closure from generated values.
    //
    // Validates: Requirements 6.1, 6.2, 6.3

    use super::*;
    use proptest::prelude::*;
    use std::collections::HashMap;

    /// Per-variable generated case: the raw env value to inject (`None` = unset,
    /// `Some(s)` = the variable is set to `s`, which may be empty/invalid) paired
    /// with the value the resolver is expected to produce for that field.
    ///
    /// FNO_UNDERLYINGS — unset / empty / blank / all-separator → default list;
    /// otherwise the comma-separated, trimmed, non-empty entries.
    fn underlyings_case() -> impl Strategy<Value = (Option<String>, Vec<String>)> {
        let default_list: Vec<String> =
            DEFAULT_UNDERLYINGS.iter().map(|s| s.to_string()).collect();
        let d_unset = default_list.clone();
        let d_blank = default_list;

        prop_oneof![
            // Unset → default.
            Just((None, d_unset)),
            // Set but yields nothing usable (empty / whitespace / only separators) → default.
            prop_oneof![
                Just(String::new()),
                Just("   ".to_string()),
                Just(",".to_string()),
                Just(" , ,  , ".to_string()),
            ]
            .prop_map(move |raw| (Some(raw), d_blank.clone())),
            // Valid: 1..=4 clean tokens; joined with ", " to exercise trimming.
            prop::collection::vec("[A-Za-z0-9]{1,6}", 1..=4).prop_map(|tokens| {
                let raw = tokens.join(", ");
                (Some(raw), tokens)
            }),
        ]
    }

    /// A strictly-positive count (usize): unset/empty/invalid/zero → `default`,
    /// a positive integer (optionally whitespace-padded to exercise trimming) is
    /// used as-is.
    fn positive_usize_case(default: usize) -> impl Strategy<Value = (Option<String>, usize)> {
        prop_oneof![
            Just((None, default)),
            prop_oneof![
                Just(String::new()),
                Just("   ".to_string()),
                Just("0".to_string()),
                Just("-5".to_string()),
                Just("1.5".to_string()),
                Just("abc".to_string()),
            ]
            .prop_map(move |raw| (Some(raw), default)),
            (1usize..=10_000, any::<bool>()).prop_map(|(v, pad)| {
                let raw = if pad { format!("  {v}  ") } else { v.to_string() };
                (Some(raw), v)
            }),
        ]
    }

    /// A strictly-positive interval (u64): unset/empty/invalid/zero → `default`.
    fn positive_u64_case(default: u64) -> impl Strategy<Value = (Option<String>, u64)> {
        prop_oneof![
            Just((None, default)),
            prop_oneof![
                Just(String::new()),
                Just("   ".to_string()),
                Just("0".to_string()),
                Just("-1".to_string()),
                Just("xyz".to_string()),
            ]
            .prop_map(move |raw| (Some(raw), default)),
            (1u64..=86_400, any::<bool>()).prop_map(|(v, pad)| {
                let raw = if pad { format!(" {v} ") } else { v.to_string() };
                (Some(raw), v)
            }),
        ]
    }

    /// A band half-width (usize) that ACCEPTS 0: unset/empty/invalid → `default`,
    /// any parsed non-negative integer (including 0) is used.
    fn band_half_width_case(default: usize) -> impl Strategy<Value = (Option<String>, usize)> {
        prop_oneof![
            Just((None, default)),
            prop_oneof![
                Just(String::new()),
                Just("   ".to_string()),
                Just("-3".to_string()),
                Just("2.5".to_string()),
                Just("abc".to_string()),
            ]
            .prop_map(move |raw| (Some(raw), default)),
            (0usize..=1_000, any::<bool>()).prop_map(|(v, pad)| {
                let raw = if pad { format!("  {v} ") } else { v.to_string() };
                (Some(raw), v)
            }),
        ]
    }

    /// A strictly-positive, finite threshold (f64): unset/empty/invalid/zero/
    /// negative/non-finite → `default`; a positive finite float is used. The float
    /// is rendered with `Display`, which round-trips exactly back to the same f64.
    fn positive_f64_case(default: f64) -> impl Strategy<Value = (Option<String>, f64)> {
        prop_oneof![
            Just((None, default)),
            prop_oneof![
                Just(String::new()),
                Just("   ".to_string()),
                Just("0".to_string()),
                Just("0.0".to_string()),
                Just("-1.5".to_string()),
                Just("nan".to_string()),
                Just("inf".to_string()),
                Just("notanumber".to_string()),
            ]
            .prop_map(move |raw| (Some(raw), default)),
            (0.0001f64..=100_000.0).prop_map(|v| (Some(format!("{v}")), v)),
        ]
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: options-data-foundation, Property 12
        #[test]
        fn config_resolution_is_total_and_defaults_safely(
            (u_raw, u_exp) in underlyings_case(),
            (ne_raw, ne_exp) in positive_usize_case(DEFAULT_NEAREST_EXPIRIES),
            (bw_raw, bw_exp) in band_half_width_case(DEFAULT_STRIKE_BAND_HALF_WIDTH),
            (rt_raw, rt_exp) in positive_f64_case(DEFAULT_ATM_RECENTER_THRESHOLD),
            (si_raw, si_exp) in positive_u64_case(DEFAULT_SNAPSHOT_INTERVAL_SECS),
        ) {
            // Build a HashMap-backed env lookup; only "set" variables are present.
            let mut env: HashMap<String, String> = HashMap::new();
            if let Some(v) = &u_raw {
                env.insert("FNO_UNDERLYINGS".to_string(), v.clone());
            }
            if let Some(v) = &ne_raw {
                env.insert("FNO_NEAREST_EXPIRIES".to_string(), v.clone());
            }
            if let Some(v) = &bw_raw {
                env.insert("FNO_STRIKE_BAND_HALF_WIDTH".to_string(), v.clone());
            }
            if let Some(v) = &rt_raw {
                env.insert("FNO_ATM_RECENTER_THRESHOLD".to_string(), v.clone());
            }
            if let Some(v) = &si_raw {
                env.insert("FNO_SNAPSHOT_INTERVAL_SECS".to_string(), v.clone());
            }

            // Totality: pure resolution over an arbitrary env map never panics (R6.1).
            let cfg = resolve_fno_config_with(|key| env.get(key).cloned());

            // Every field is valid by construction (R6.1, R6.3):
            prop_assert!(
                !cfg.underlyings.is_empty(),
                "underlyings must never be empty"
            );
            prop_assert!(
                cfg.chain.nearest_expiries > 0,
                "nearest_expiries must be strictly positive, got {}",
                cfg.chain.nearest_expiries
            );
            prop_assert!(
                cfg.chain.snapshot_interval_secs > 0,
                "snapshot_interval_secs must be strictly positive, got {}",
                cfg.chain.snapshot_interval_secs
            );
            prop_assert!(
                cfg.chain.recenter_threshold.is_finite() && cfg.chain.recenter_threshold > 0.0,
                "recenter_threshold must be finite and positive, got {}",
                cfg.chain.recenter_threshold
            );

            // Unset/empty/invalid → documented default; valid → the supplied value
            // (R6.1, R6.2).
            prop_assert_eq!(&cfg.underlyings, &u_exp);
            prop_assert_eq!(cfg.chain.nearest_expiries, ne_exp);
            prop_assert_eq!(cfg.chain.strike_band_half_width, bw_exp);
            prop_assert_eq!(cfg.chain.snapshot_interval_secs, si_exp);
            // recenter_threshold round-trips exactly through Display, but compare
            // with a tiny tolerance for robustness.
            prop_assert!(
                (cfg.chain.recenter_threshold - rt_exp).abs()
                    <= 1e-9_f64.max(rt_exp.abs() * 1e-12),
                "recenter_threshold mismatch: got {}, expected {}",
                cfg.chain.recenter_threshold,
                rt_exp
            );
        }
    }
}
