// option_chain_selector.rs — decides WHICH option contracts get ingested.
//
// This is the half of the desktop `option_chain_subscriber.rs` that had to survive
// the Tauri retirement. The other half already lived server-side: the desktop
// never wrote QuestDB itself, it resolved a bounded strike band and pushed it to
// the ingestion service's TCP control port, and `ingestion/src/option_sink.rs`
// did the writing. So only the *selection* was homeless.
//
// It landed in the aggregator because the aggregator already owns both inputs:
//   * the NFO instrument cache (`KiteApiState::instruments_for`) — the strike
//     ladder, refreshed daily, shared with the search endpoint;
//   * an authenticated Kite quote path (`last_price_for`) — the spot price that
//     decides where ATM sits.
// `ingestion` has neither (it only ever receives tokens), so putting it there
// would have meant teaching it to download and parse the instrument master.
//
// Why this matters: with nothing pushing a selection, ingestion subscribes to no
// option instruments, `option_chain_snapshots` stops receiving rows, and the
// website's F&O workspace — the option chain, PCR, max pain, OI walls, and the
// `fno_*` contract resolution in `lib/bridge/webAdapters.ts` — goes empty. Not
// with an error; just silently blank, because an empty table is indistinguishable
// from a market with no open interest.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use chrono::{NaiveDate, Utc};
use log::{debug, error, info, warn};
use tokio::io::AsyncWriteExt;

use crate::kite_api::{Instrument, KiteApiState};
use crate::option_chain::{
    build_chain_selection, should_recenter, ChainConfig, ChainSelection, OptionType,
};

// ── Configuration ────────────────────────────────────────────────────────────
//
// Same `FNO_*` names and same defaults as the retired
// `frontend/src-tauri/src/services/fno_config.rs`, so an existing deployment's
// environment keeps working unchanged.

const DEFAULT_UNDERLYINGS: [&str; 2] = ["NIFTY", "BANKNIFTY"];
const DEFAULT_NEAREST_EXPIRIES: usize = 2;
const DEFAULT_STRIKE_BAND_HALF_WIDTH: usize = 10;
const DEFAULT_ATM_RECENTER_THRESHOLD: f64 = 1.0;
const DEFAULT_SNAPSHOT_INTERVAL_SECS: u64 = 60;

/// How often the selection is re-evaluated. Independent of the snapshot cadence:
/// re-centering only matters when spot has moved a strike, which is far slower
/// than the snapshot interval.
const SELECTION_CYCLE: Duration = Duration::from_secs(60);

/// Delay before the first cycle, so the instrument cache and the ingestion
/// control port have a chance to come up. A failed cycle is retried, not fatal,
/// so this is politeness rather than correctness.
const STARTUP_GRACE: Duration = Duration::from_secs(15);

/// Resolved selector configuration.
pub struct SelectorConfig {
    pub underlyings: Vec<String>,
    pub chain: ChainConfig,
}

fn env_underlyings() -> Vec<String> {
    let raw = std::env::var("FNO_UNDERLYINGS").unwrap_or_default();
    let parsed: Vec<String> = raw
        .split(',')
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_uppercase())
        .collect();
    if parsed.is_empty() {
        DEFAULT_UNDERLYINGS.iter().map(|s| s.to_string()).collect()
    } else {
        parsed
    }
}

/// Parse a positive value from the environment, falling back on absent, empty,
/// unparseable, or non-positive input.
///
/// Non-positive is rejected rather than accepted because a zero band or zero
/// interval would silently select nothing — a misconfiguration that reads as
/// "the market has no options" instead of as an error.
fn env_positive<T>(key: &str, default: T) -> T
where
    T: std::str::FromStr + PartialOrd + Default + Copy,
{
    match std::env::var(key).ok().and_then(|v| v.trim().parse::<T>().ok()) {
        Some(v) if v > T::default() => v,
        _ => default,
    }
}

pub fn resolve_config() -> SelectorConfig {
    SelectorConfig {
        underlyings: env_underlyings(),
        chain: ChainConfig {
            nearest_expiries: env_positive("FNO_NEAREST_EXPIRIES", DEFAULT_NEAREST_EXPIRIES),
            strike_band_half_width: env_positive(
                "FNO_STRIKE_BAND_HALF_WIDTH",
                DEFAULT_STRIKE_BAND_HALF_WIDTH,
            ),
            recenter_threshold: env_positive(
                "FNO_ATM_RECENTER_THRESHOLD",
                DEFAULT_ATM_RECENTER_THRESHOLD,
            ),
            snapshot_interval_secs: env_positive(
                "FNO_SNAPSHOT_INTERVAL_SECS",
                DEFAULT_SNAPSHOT_INTERVAL_SECS,
            ),
        },
    }
}

// ── Name reconciliation ──────────────────────────────────────────────────────

/// The name NFO contracts group under, given a configured underlying.
///
/// The two sides of the exchange disagree for indices: spot quotes key on the NSE
/// index symbol (`NIFTY 50`, `NIFTY BANK`) while NFO options group under the
/// shorter derivative name (`NIFTY`, `BANKNIFTY`). A configured `NIFTY 50` would
/// therefore match no NFO rows and the chain would stay permanently empty. For
/// single-stock underlyings the two names coincide, so identity is correct.
///
/// Mirrors the retired `option_chain_subscriber::resolve_nfo_underlying_name`.
pub fn nfo_name(configured: &str) -> String {
    match configured.trim().to_uppercase().as_str() {
        "NIFTY 50" | "NIFTY50" | "NIFTY" => "NIFTY".to_string(),
        "NIFTY BANK" | "BANKNIFTY" => "BANKNIFTY".to_string(),
        "NIFTY FIN SERVICE" | "FINNIFTY" => "FINNIFTY".to_string(),
        "NIFTY MIDCAP SELECT" | "MIDCPNIFTY" => "MIDCPNIFTY".to_string(),
        "NIFTY NEXT 50" | "NIFTYNXT50" => "NIFTYNXT50".to_string(),
        _ => configured.trim().to_string(),
    }
}

/// The Kite quote key for an underlying's spot price.
///
/// Mirrors the retired `fno_service::map_spot_quote_symbol`.
pub fn spot_quote_key(underlying: &str) -> String {
    match underlying.trim().to_uppercase().as_str() {
        "NIFTY" | "NIFTY 50" => "NSE:NIFTY 50".to_string(),
        "BANKNIFTY" | "NIFTY BANK" => "NSE:NIFTY BANK".to_string(),
        "FINNIFTY" | "NIFTY FIN SERVICE" => "NSE:NIFTY FIN SERVICE".to_string(),
        "MIDCPNIFTY" | "NIFTY MIDCAP SELECT" => "NSE:NIFTY MIDCAP SELECT".to_string(),
        other => format!("NSE:{other}"),
    }
}

// ── Instrument mapping ───────────────────────────────────────────────────────

/// Map the aggregator's CSV-derived `Instrument` rows to the pure module's
/// `OptionContract`.
///
/// Rows that cannot form a contract are dropped rather than defaulted: a bad
/// expiry date or an unrecognised instrument type has no safe stand-in, and
/// inventing one would put a non-existent contract into the subscription.
///
/// Pure, so the filtering is testable without a Kite fetch.
pub fn to_option_contracts(rows: &[Instrument]) -> Vec<crate::option_chain::OptionContract> {
    rows.iter()
        .filter_map(|r| {
            let option_type = match r.instrument_type.to_uppercase().as_str() {
                "CE" => OptionType::Ce,
                "PE" => OptionType::Pe,
                "FUT" => OptionType::Fut,
                _ => return None,
            };
            let expiry = NaiveDate::parse_from_str(r.expiry.trim(), "%Y-%m-%d").ok()?;
            // `name` is the NFO underlying grouping key; fall back to the
            // tradingsymbol only when it is absent, matching `derive_underlying`.
            let underlying = if r.name.trim().is_empty() {
                r.tradingsymbol.trim().to_string()
            } else {
                r.name.trim().to_string()
            };
            Some(crate::option_chain::OptionContract {
                // Kite tokens exceed u32 for no NFO instrument, but a truncating
                // `as` cast would silently subscribe to the wrong contract, so a
                // token that does not fit is dropped instead.
                token: u32::try_from(r.instrument_token).ok()?,
                tradingsymbol: r.tradingsymbol.trim().to_string(),
                underlying,
                option_type,
                strike: r.strike,
                expiry,
            })
        })
        .collect()
}

// ── Control-port push ────────────────────────────────────────────────────────

fn option_type_str(t: OptionType) -> &'static str {
    match t {
        OptionType::Ce => "CE",
        OptionType::Pe => "PE",
        OptionType::Fut => "FUT",
    }
}

/// Build the `option_chain_set` JSON body.
///
/// Wire contract, consumed by `ingestion/src/main.rs::OptionChainSetCmd`:
/// ```jsonc
/// { "underlying", "snapshot_interval_secs",
///   "tokens": [ { "token", "tradingsymbol", "expiry", "strike", "type" } ] }
/// ```
/// Pure, so the shape is unit-testable against that struct without a socket.
pub fn build_chain_set_payload(
    selection: &ChainSelection,
    interval_secs: u64,
) -> serde_json::Value {
    let tokens: Vec<serde_json::Value> = selection
        .entries
        .iter()
        .map(|e| {
            serde_json::json!({
                "token": e.token,
                "tradingsymbol": e.tradingsymbol,
                "expiry": e.expiry.format("%Y-%m-%d").to_string(),
                "strike": e.strike,
                "type": option_type_str(e.option_type),
            })
        })
        .collect();

    serde_json::json!({
        "underlying": selection.underlying,
        "snapshot_interval_secs": interval_secs,
        "tokens": tokens,
    })
}

/// Push one selection to the ingestion control port as a newline-delimited
/// `option_chain_set:{json}` command.
///
/// A connect or write failure is returned, not retried here — the caller retries
/// on the next cycle, which is the right granularity: if ingestion is restarting,
/// a tight retry loop would just log faster.
async fn push_chain_set(selection: &ChainSelection, interval_secs: u64) -> Result<(), String> {
    let host = std::env::var("INGESTION_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let port = std::env::var("INGESTION_CONTROL_PORT").unwrap_or_else(|_| "8085".to_string());
    let addr = format!("{host}:{port}");

    let cmd = format!(
        "option_chain_set:{}\n",
        build_chain_set_payload(selection, interval_secs)
    );

    let mut stream = tokio::net::TcpStream::connect(&addr).await.map_err(|e| {
        format!("cannot reach ingestion control port {addr} — is the ingestion service up? ({e})")
    })?;
    stream
        .write_all(cmd.as_bytes())
        .await
        .map_err(|e| format!("control write to {addr} failed: {e}"))?;

    Ok(())
}

// ── The loop ─────────────────────────────────────────────────────────────────

/// Re-evaluate every configured underlying's chain on a timer, pushing a new
/// selection whenever ATM has moved past the re-center threshold.
///
/// Never returns and never panics: every failure is logged and retried on the
/// next cycle. A permanently misconfigured underlying therefore costs one log
/// line per minute rather than taking the aggregator down with it.
pub async fn run(state: Arc<KiteApiState>) {
    let cfg = resolve_config();
    info!(
        "[chain-selector] starting: underlyings={:?} expiries={} band=±{} recenter={} snapshot={}s",
        cfg.underlyings,
        cfg.chain.nearest_expiries,
        cfg.chain.strike_band_half_width,
        cfg.chain.recenter_threshold,
        cfg.chain.snapshot_interval_secs,
    );

    tokio::time::sleep(STARTUP_GRACE).await;

    // Last pushed ATM per underlying, so an unchanged chain is not re-pushed
    // every minute. Ingestion treats each command as a full replacement, so a
    // redundant push would churn its WS subscription for no reason.
    let mut last_atm: HashMap<String, f64> = HashMap::new();
    let mut ticker = tokio::time::interval(SELECTION_CYCLE);

    loop {
        ticker.tick().await;

        let instruments = match state.instruments_for("NFO").await {
            Ok(rows) => to_option_contracts(&rows),
            Err(e) => {
                warn!("[chain-selector] NFO instruments unavailable, retrying next cycle: {e}");
                continue;
            }
        };
        if instruments.is_empty() {
            warn!("[chain-selector] NFO instrument master parsed to zero option contracts");
            continue;
        }

        // One date for the whole cycle, so every underlying is resolved against
        // the same "today" even if the cycle straddles midnight.
        let today = Utc::now().date_naive();

        for configured in &cfg.underlyings {
            let name = nfo_name(configured);

            let spot = match state.last_price_for(&spot_quote_key(configured)).await {
                Ok(Some(p)) => p,
                Ok(None) => {
                    debug!("[chain-selector] {configured}: no spot price yet, skipping cycle");
                    continue;
                }
                Err(e) => {
                    warn!("[chain-selector] {configured}: spot fetch failed: {e}");
                    continue;
                }
            };

            let selection =
                build_chain_selection(&instruments, &name, spot, today, &cfg.chain);
            if selection.entries.is_empty() {
                warn!(
                    "[chain-selector] {configured} (nfo={name}): no contracts selected at spot {spot:.2}"
                );
                continue;
            }

            // Push on the first cycle, then only once ATM has actually moved.
            let should_push = match last_atm.get(&name) {
                None => true,
                Some(&prev) => {
                    should_recenter(prev, selection.atm_strike, cfg.chain.recenter_threshold)
                }
            };
            if !should_push {
                continue;
            }

            match push_chain_set(&selection, cfg.chain.snapshot_interval_secs).await {
                Ok(()) => {
                    info!(
                        "[chain-selector] {} → {} contracts, ATM {} (spot {:.2})",
                        name,
                        selection.entries.len(),
                        selection.atm_strike,
                        spot
                    );
                    last_atm.insert(name, selection.atm_strike);
                }
                // Deliberately do NOT record the ATM on failure, so the next
                // cycle retries this underlying instead of believing it is pushed.
                Err(e) => error!("[chain-selector] {name}: push failed: {e}"),
            }
        }
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn inst(
        token: u64,
        tradingsymbol: &str,
        name: &str,
        instrument_type: &str,
        strike: f64,
        expiry: &str,
    ) -> Instrument {
        Instrument {
            instrument_token: token,
            exchange_token: token / 256,
            tradingsymbol: tradingsymbol.to_string(),
            name: name.to_string(),
            last_price: 0.0,
            expiry: expiry.to_string(),
            strike,
            tick_size: 0.05,
            lot_size: 75,
            instrument_type: instrument_type.to_string(),
            segment: "NFO-OPT".to_string(),
            exchange: "NFO".to_string(),
        }
    }

    #[test]
    fn reconciles_index_names_across_the_spot_and_nfo_sides() {
        // The bug this prevents: a configured "NIFTY 50" matching no NFO rows and
        // leaving the chain permanently empty.
        assert_eq!(nfo_name("NIFTY 50"), "NIFTY");
        assert_eq!(nfo_name("nifty bank"), "BANKNIFTY");
        assert_eq!(spot_quote_key("NIFTY"), "NSE:NIFTY 50");
        assert_eq!(spot_quote_key("BANKNIFTY"), "NSE:NIFTY BANK");
        // Single stocks: identical on both sides.
        assert_eq!(nfo_name("RELIANCE"), "RELIANCE");
        assert_eq!(spot_quote_key("RELIANCE"), "NSE:RELIANCE");
    }

    #[test]
    fn maps_csv_rows_to_contracts_and_drops_unusable_ones() {
        let rows = vec![
            inst(1001, "NIFTY2671424150CE", "NIFTY", "CE", 24150.0, "2026-07-14"),
            inst(1002, "NIFTY2671424150PE", "NIFTY", "PE", 24150.0, "2026-07-14"),
            inst(1003, "NIFTY26JULFUT", "NIFTY", "FUT", 0.0, "2026-07-28"),
            // Unusable: equity row (no option type) and a malformed expiry. Both
            // must vanish rather than become a contract with a defaulted date.
            inst(1004, "RELIANCE", "RELIANCE", "EQ", 0.0, ""),
            inst(1005, "NIFTY2671424200CE", "NIFTY", "CE", 24200.0, "not-a-date"),
        ];
        let out = to_option_contracts(&rows);
        assert_eq!(out.len(), 3);
        assert!(out.iter().all(|c| c.underlying == "NIFTY"));
        assert!(!out.iter().any(|c| c.tradingsymbol == "RELIANCE"));
        assert!(!out.iter().any(|c| c.strike == 24200.0));
    }

    #[test]
    fn payload_matches_the_ingestion_control_contract() {
        // Pinned against ingestion/src/main.rs::OptionChainSetCmd / OptionTokenSpec.
        // A field rename here is silently ignored by serde on the far side, so the
        // chain would go empty with no error — hence asserting the exact keys.
        let contracts = to_option_contracts(&[
            inst(1001, "NIFTY2671424150CE", "NIFTY", "CE", 24150.0, "2026-07-14"),
            inst(1002, "NIFTY2671424150PE", "NIFTY", "PE", 24150.0, "2026-07-14"),
        ]);
        let cfg = ChainConfig {
            nearest_expiries: 1,
            strike_band_half_width: 1,
            recenter_threshold: 1.0,
            snapshot_interval_secs: 60,
        };
        let selection = build_chain_selection(
            &contracts,
            "NIFTY",
            24150.0,
            NaiveDate::from_ymd_opt(2026, 7, 1).unwrap(),
            &cfg,
        );
        assert!(!selection.entries.is_empty(), "expected a non-empty selection");

        let payload = build_chain_set_payload(&selection, 60);
        assert_eq!(payload["underlying"], "NIFTY");
        assert_eq!(payload["snapshot_interval_secs"], 60);
        let first = &payload["tokens"][0];
        assert!(first["token"].is_u64());
        assert!(first["tradingsymbol"].is_string());
        assert_eq!(first["expiry"], "2026-07-14"); // ISO, not a chrono debug form
        assert!(first["strike"].is_f64());
        assert!(
            first["type"] == "CE" || first["type"] == "PE",
            "type must be the CE/PE wire string"
        );
    }

    #[test]
    fn config_defaults_hold_and_reject_nonsense() {
        // Not asserting env-var reads (they are process-global and would race with
        // other tests); asserting the fallback values the parser lands on.
        assert_eq!(env_positive::<usize>("FNO_NO_SUCH_VAR_XYZ", 7), 7);
        assert_eq!(DEFAULT_STRIKE_BAND_HALF_WIDTH, 10);
        assert_eq!(DEFAULT_SNAPSHOT_INTERVAL_SECS, 60);
        assert_eq!(
            env_underlyings().is_empty(),
            false,
            "underlyings must never resolve empty — an empty list ingests no chain at all"
        );
    }
}
