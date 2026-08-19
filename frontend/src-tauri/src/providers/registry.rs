// src/providers/registry.rs — which market-data provider this build talks to.
//
// One env value, `MARKET_DATA_PROVIDER`, selects the feed; `kite` is the default
// and today the only implementation. The point of the indirection is that adding a
// second feed is a new file in `providers/` plus a match arm here — not a fifth
// rewrite of the same direct-vs-proxy branch across four services (P14).
//
// Resolution happens once per process, in a `OnceLock`. That is deliberate: a
// provider swap mid-session would mean one backfill's pages arriving from two
// different feeds, with different adjustment conventions and different notions of
// a session boundary, silently merged into the same QuestDB partition.

use std::sync::OnceLock;

use super::kite::KiteProvider;
use super::MarketDataProvider;

/// The env var that selects the feed. Absent or empty ⇒ `kite`.
pub const PROVIDER_ENV: &str = "MARKET_DATA_PROVIDER";

/// The provider used when nothing is configured.
pub const DEFAULT_PROVIDER: &str = "kite";

static MARKET_DATA: OnceLock<Box<dyn MarketDataProvider>> = OnceLock::new();

/// Normalise a raw env value to a provider id.
///
/// Pure and separate from the `OnceLock` so the selection rule is testable without
/// a process-wide side effect. Case- and whitespace-insensitive, because an env
/// value typed by hand into a `.env` is not a machine-generated token.
pub fn resolve_provider_id(raw: Option<&str>) -> String {
    match raw.map(str::trim).filter(|v| !v.is_empty()) {
        Some(value) => value.to_ascii_lowercase(),
        None => DEFAULT_PROVIDER.to_string(),
    }
}

/// Build the provider for an id, or `None` if no implementation matches.
///
/// Returning `Option` rather than falling back internally keeps "unknown value"
/// distinguishable from "asked for kite", so [`market_data`] can log the
/// misconfiguration instead of quietly ignoring it.
fn build(id: &str) -> Option<Box<dyn MarketDataProvider>> {
    match id {
        "kite" | "zerodha" => Some(Box::new(KiteProvider::new())),
        _ => None,
    }
}

/// The process-wide market-data provider.
///
/// An unrecognised `MARKET_DATA_PROVIDER` logs a warning and falls back to Kite
/// rather than panicking: a typo in a deployment env should not take a shipped
/// desktop app's charts down, and the warning is the operator's signal. Falling
/// back is safe *because* every provider here is read-only — the same reasoning
/// would not hold for a broker/order provider, which is one more reason
/// `BrokerProvider` is not registered here.
pub fn market_data() -> &'static dyn MarketDataProvider {
    MARKET_DATA
        .get_or_init(|| {
            let raw = std::env::var(PROVIDER_ENV).ok();
            let id = resolve_provider_id(raw.as_deref());
            match build(&id) {
                Some(provider) => {
                    log::info!("[providers] market data provider: {}", provider.id());
                    provider
                }
                None => {
                    log::warn!(
                        "[providers] unknown {}='{}' — falling back to '{}'",
                        PROVIDER_ENV,
                        id,
                        DEFAULT_PROVIDER
                    );
                    Box::new(KiteProvider::new())
                }
            }
        })
        .as_ref()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn absent_or_blank_selects_the_default() {
        assert_eq!(resolve_provider_id(None), "kite");
        assert_eq!(resolve_provider_id(Some("")), "kite");
        assert_eq!(resolve_provider_id(Some("   ")), "kite");
    }

    #[test]
    fn a_hand_typed_value_is_normalised() {
        assert_eq!(resolve_provider_id(Some(" Kite ")), "kite");
        assert_eq!(resolve_provider_id(Some("ZERODHA")), "zerodha");
    }

    #[test]
    fn known_ids_build_and_unknown_ones_do_not() {
        assert!(build("kite").is_some());
        assert!(build("zerodha").is_some());
        // An unknown id must NOT silently build something — the caller logs it.
        assert!(build("dhan").is_none());
        assert!(build("").is_none());
    }

    #[test]
    fn both_kite_aliases_report_the_same_provider() {
        assert_eq!(build("kite").unwrap().id(), build("zerodha").unwrap().id());
    }
}
