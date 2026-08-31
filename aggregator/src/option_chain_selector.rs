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

use std::collections::{HashMap, HashSet};
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

/// Underlyings whose chains are ingested when `FNO_UNDERLYINGS` is unset.
///
/// This was `["NIFTY", "BANKNIFTY"]`, which is why F&O worked for the two indices
/// and was silently dead for every stock. `spot_subscriber::DEFAULT_SPOT_SYMBOLS`
/// already streams spot for these seven names — they are the watchlist the
/// terminal ships with — so the F&O workspace offered them as underlyings while
/// nothing ever selected a single contract for them. What the user saw was the
/// residue of a retired producer: a chain frozen on an expiry that had since
/// lapsed, and a chart that could never load because the exchange had already
/// delisted the contract.
///
/// Size: `underlyings × nearest_expiries × (2 × band + 1) × 2` bounds this at
/// 10 × 7 × 21 × 2 = 2940 option tokens, against Kite's 3000-instrument WebSocket
/// ceiling. Snapshot volume scales with it (~1 row per token per minute), which is
/// the real cost of this list growing — keep that in mind before extending it, and
/// see `DEFAULT_NEAREST_EXPIRIES` for the other half of the arithmetic. That
/// ceiling is now within 60 tokens of the limit, so ANOTHER underlying has to be
/// paid for by lowering `FNO_NEAREST_EXPIRIES` or `FNO_STRIKE_BAND_HALF_WIDTH`.
/// This is why BANKEX is absent despite being BFO-listed like SENSEX: 11 × 7 × 21
/// × 2 = 3234 would breach it. `run` warns rather than letting it fail silently.
///
/// Every name here must be F&O-listed. `INDIA VIX` and `NIFTY IT` are in the spot
/// list but have no option chain, so they are deliberately absent: a non-derivative
/// underlying costs one "no contracts selected" warning per minute.
///
/// SENSEX is not an NSE name. Its options are listed on **BFO**, so it is resolved
/// against a different instrument master than the other nine — see
/// `derivative_exchange`.
const DEFAULT_UNDERLYINGS: [&str; 10] = [
    "NIFTY",
    "BANKNIFTY",
    "SENSEX",
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "INFY",
    "ICICIBANK",
    "SBIN",
    "ITC",
];
/// How many of an underlying's non-expired expiries get ingested.
///
/// This was 2, and that number — not the UI — is why the F&O expiry dropdown
/// offered two dates for every instrument. The dropdown reads
/// `SELECT DISTINCT expiry FROM option_chain_snapshots`, so it can only ever show
/// what the selector chose to follow. NIFTY lists 21 expiries (four weeklies,
/// three monthlies, quarterlies, then LEAPS out to 2031); every broker's chain
/// shows the near end of that ladder, and we showed the first two of it.
///
/// 7 covers what is actually tradable and matches what brokers display. Measured
/// against the live NFO master: NIFTY's nearest 7 are exactly its four weeklies
/// plus the Sep/Oct/Nov monthlies, stopping before the quarterlies; BANKNIFTY
/// lists 6 in total (3 monthly + 3 quarterly) and single stocks list 3 monthlies,
/// so those take their whole ladder and 7 costs nothing there.
///
/// Token cost, which is the reason this is not simply "all of them": the ceiling
/// is `underlyings × nearest_expiries × (2 × band + 1) × 2` = 9 × 7 × 21 × 2 =
/// 2646, against Kite's 3000-instrument WebSocket limit. The real figure is about
/// 1430 because only NIFTY has 7 expiries to take. Following the whole NIFTY
/// ladder instead would spend 882 tokens on LEAPS nobody charts. `run` warns at
/// startup when the configured ceiling breaches the limit, because exceeding it
/// costs live ticks silently rather than loudly.
const DEFAULT_NEAREST_EXPIRIES: usize = 7;

/// Kite's per-connection instrument subscription limit. Only used to warn.
const KITE_WS_INSTRUMENT_LIMIT: usize = 3000;
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
///
/// SENSEX and BANKEX quote under **BSE**, not NSE. `NSE:SENSEX` is not an
/// instrument, so the default `NSE:{other}` arm would return no spot and the
/// selector would skip the underlying every cycle with "no spot price yet".
pub fn spot_quote_key(underlying: &str) -> String {
    match underlying.trim().to_uppercase().as_str() {
        "NIFTY" | "NIFTY 50" => "NSE:NIFTY 50".to_string(),
        "BANKNIFTY" | "NIFTY BANK" => "NSE:NIFTY BANK".to_string(),
        "FINNIFTY" | "NIFTY FIN SERVICE" => "NSE:NIFTY FIN SERVICE".to_string(),
        "MIDCPNIFTY" | "NIFTY MIDCAP SELECT" => "NSE:NIFTY MIDCAP SELECT".to_string(),
        "SENSEX" => "BSE:SENSEX".to_string(),
        "BANKEX" => "BSE:BANKEX".to_string(),
        other => format!("NSE:{other}"),
    }
}

/// The derivative exchange an underlying's option chain is listed on.
///
/// India's two exchanges run separate derivative segments: NSE's is `NFO`, BSE's is
/// `BFO`, and SENSEX / BANKEX contracts exist only in the latter
/// (`SENSEX2690376900CE`, segment `BFO-OPT`, lot 20). A single NFO master therefore
/// selects nothing at all for SENSEX — no error, just an empty chain forever, which
/// is indistinguishable from an underlying with no open interest.
pub fn derivative_exchange(nfo_name: &str) -> &'static str {
    match nfo_name.trim().to_uppercase().as_str() {
        "SENSEX" | "BANKEX" | "SENSEX50" => "BFO",
        _ => "NFO",
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
    // Shared with `spot_subscriber` so the two cannot disagree about where
    // ingestion lives. This used to default the host to `127.0.0.1`, which inside
    // the aggregator container is the aggregator — so every push failed with
    // `Connection refused (os error 111)` and no option chain was ever ingested.
    let addr = crate::spot_subscriber::ingestion_control_addr();

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

    // Overshooting Kite's subscription limit does not fail loudly — the WS accepts
    // the connection and simply stops delivering, which reads as a market with no
    // ticks. Say so at startup instead, where the arithmetic is still visible.
    let ceiling = cfg.underlyings.len()
        * cfg.chain.nearest_expiries
        * (2 * cfg.chain.strike_band_half_width + 1)
        * 2;
    if ceiling > KITE_WS_INSTRUMENT_LIMIT {
        warn!(
            "[chain-selector] configured ceiling is {ceiling} option tokens \
             ({} underlyings × {} expiries × {} strikes × 2), above Kite's \
             {KITE_WS_INSTRUMENT_LIMIT}-instrument WebSocket limit — the excess \
             will silently receive no ticks. Lower FNO_NEAREST_EXPIRIES, \
             FNO_STRIKE_BAND_HALF_WIDTH, or FNO_UNDERLYINGS.",
            cfg.underlyings.len(),
            cfg.chain.nearest_expiries,
            2 * cfg.chain.strike_band_half_width + 1,
        );
    }

    tokio::time::sleep(STARTUP_GRACE).await;

    // Last pushed ATM per underlying, so an unchanged chain is not re-pushed
    // every minute. Ingestion treats each command as a full replacement, so a
    // redundant push would churn its WS subscription for no reason.
    let mut last_atm: HashMap<String, f64> = HashMap::new();
    let mut ticker = tokio::time::interval(SELECTION_CYCLE);

    loop {
        ticker.tick().await;

        // One instrument master per derivative exchange the configured underlyings
        // actually need. This used to be a single NFO fetch, which silently
        // guaranteed an empty chain for SENSEX: its contracts are listed on BFO, so
        // no NFO row ever matched the underlying and the selection came back empty
        // every cycle. Both masters are cached with a 24h TTL, so this is one extra
        // fetch per day rather than per cycle.
        let mut masters: HashMap<&'static str, Vec<crate::option_chain::OptionContract>> =
            HashMap::new();
        let needed: HashSet<&'static str> = cfg
            .underlyings
            .iter()
            .map(|u| derivative_exchange(&nfo_name(u)))
            .collect();
        for exchange in needed {
            match state.instruments_for(exchange).await {
                Ok(rows) => {
                    let contracts = to_option_contracts(&rows);
                    if contracts.is_empty() {
                        warn!(
                            "[chain-selector] {exchange} instrument master parsed to zero option contracts"
                        );
                    }
                    masters.insert(exchange, contracts);
                }
                // Per-exchange, so a BFO outage cannot stop the nine NFO
                // underlyings from being selected.
                Err(e) => warn!(
                    "[chain-selector] {exchange} instruments unavailable, retrying next cycle: {e}"
                ),
            }
        }
        if masters.values().all(|c| c.is_empty()) {
            continue;
        }

        // One date for the whole cycle, so every underlying is resolved against
        // the same "today" even if the cycle straddles midnight.
        let today = Utc::now().date_naive();

        // One quote request for every underlying's spot, not one per underlying:
        // Kite's REST limit is per request, and the per-underlying loop below used
        // to issue a separate call each cycle.
        let quote_keys: Vec<String> = cfg.underlyings.iter().map(|u| spot_quote_key(u)).collect();
        let spots = match state.last_prices_for(&quote_keys).await {
            Ok(map) => map,
            Err(e) => {
                warn!("[chain-selector] spot quote fetch failed, retrying next cycle: {e}");
                continue;
            }
        };

        for configured in &cfg.underlyings {
            let name = nfo_name(configured);
            let exchange = derivative_exchange(&name);
            let instruments = match masters.get(exchange) {
                Some(rows) if !rows.is_empty() => rows,
                // That exchange's master failed this cycle; the others still run.
                _ => continue,
            };

            let spot = match spots.get(&spot_quote_key(configured)) {
                Some(&p) => p,
                None => {
                    debug!("[chain-selector] {configured}: no spot price yet, skipping cycle");
                    continue;
                }
            };

            let selection =
                build_chain_selection(instruments, &name, spot, today, &cfg.chain);
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
    fn shipped_defaults_stay_inside_kites_subscription_limit() {
        // The F&O expiry dropdown shows only the expiries this selector follows,
        // so `DEFAULT_NEAREST_EXPIRIES` is the knob that widens it — and the only
        // thing stopping it from being "all of them" is this ceiling. Exceeding
        // Kite's limit does not error: the excess instruments just never tick, so
        // the chain looks like a market with no open interest. Pin the arithmetic
        // here rather than trusting the comment that states it.
        let ceiling = DEFAULT_UNDERLYINGS.len()
            * DEFAULT_NEAREST_EXPIRIES
            * (2 * DEFAULT_STRIKE_BAND_HALF_WIDTH + 1)
            * 2;
        assert!(
            ceiling <= KITE_WS_INSTRUMENT_LIMIT,
            "shipped defaults would subscribe up to {ceiling} option tokens, over \
             Kite's {KITE_WS_INSTRUMENT_LIMIT}-instrument limit",
        );
        // Enough to reach past the weeklies into the monthlies — two was not.
        assert!(DEFAULT_NEAREST_EXPIRIES >= 5);
    }

    #[test]
    fn follows_the_near_ladder_and_stops_before_the_leaps() {
        // NIFTY's real listed ladder: four weeklies, three monthlies, then
        // quarterlies and LEAPS out to 2031. Brokers show the near end; the
        // default must select exactly that and not spend tokens on 2031.
        let mut rows = Vec::new();
        let listed = [
            "2026-09-01", "2026-09-08", "2026-09-15", "2026-09-22", // weeklies
            "2026-09-29", "2026-10-27", "2026-11-23", // monthlies
            "2026-12-29", "2027-03-30", "2031-06-24", // quarterlies + LEAPS
        ];
        for (i, expiry) in listed.iter().enumerate() {
            let token = 2000 + i as u64;
            rows.push(inst(token, "NIFTYxxxCE", "NIFTY", "CE", 24000.0, expiry));
        }
        let expiries = crate::option_chain::select_nearest_expiries(
            &to_option_contracts(&rows),
            "NIFTY",
            NaiveDate::from_ymd_opt(2026, 8, 31).unwrap(),
            DEFAULT_NEAREST_EXPIRIES,
        );
        let iso: Vec<String> = expiries.iter().map(|d| d.to_string()).collect();
        assert_eq!(iso, &listed[..7]);
    }

    #[test]
    fn sensex_is_resolved_against_bse_and_bfo_not_nse_and_nfo() {
        // SENSEX quotes on BSE and its options are listed on BFO. Getting either
        // wrong produces silence rather than an error: the wrong quote key means
        // "no spot price yet" every cycle, and the wrong instrument master means a
        // selection with no entries — both indistinguishable from a dead market.
        assert_eq!(spot_quote_key("SENSEX"), "BSE:SENSEX");
        assert_eq!(derivative_exchange("SENSEX"), "BFO");
        assert_eq!(derivative_exchange("BANKEX"), "BFO");
        // Everything NSE-listed keeps its existing routing.
        assert_eq!(derivative_exchange("NIFTY"), "NFO");
        assert_eq!(derivative_exchange("BANKNIFTY"), "NFO");
        assert_eq!(derivative_exchange("RELIANCE"), "NFO");
        assert_eq!(spot_quote_key("NIFTY"), "NSE:NIFTY 50");
        // `nfo_name` is identity for SENSEX, so the two agree on the key.
        assert_eq!(derivative_exchange(&nfo_name("SENSEX")), "BFO");
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
