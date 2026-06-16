// services/option_chain_subscriber.rs — Bounded option-chain subscriber (Phase F1, task 9.1).
//
// This is the Tauri-side driver that decides *which* option instruments the
// ingestion service should follow. It is the integration glue around the pure
// chain-resolution math in `option_chain.rs`:
//
//   1. Reads the single resolved `FnoConfig` once (shared by the subscriber loop
//      and the snapshot interval handed to ingestion — R6.4).
//   2. On a periodic tick, for each configured underlying:
//        - reads the underlying's spot from QuestDB `live_ticks`,
//        - skips + logs the underlying when its spot is unavailable (R4.5),
//        - loads that underlying's NFO ladder from SQLite into `OptionContract`s,
//        - resolves the bounded selection via the pure `build_chain_selection`,
//        - pushes an `option_chain_set` command to the ingestion control port
//          only when the ATM has shifted past the re-center threshold (or there
//          is no prior selection) — `should_recenter` (R4.3).
//   3. Push failures are logged and simply retried on the next iteration (R7.3);
//      the equity subscription path is entirely independent.
//
// The pure decision-making (selection, re-centering) lives in `option_chain.rs`;
// this module only performs I/O (DB reads, the control-port write) and the
// periodic scheduling.

use std::collections::HashMap;

use chrono::Local;
use log::{info, warn};
use sqlx::{PgPool, Row};
use tauri::Manager;
use tokio::io::AsyncWriteExt;

use crate::db::DbState;
use crate::services::fno_config::{resolve_fno_config, FnoConfig};
use crate::services::option_chain::{
    build_chain_selection, should_recenter, ChainSelection, OptionContract, OptionType,
};

/// How often the subscriber re-resolves each underlying's chain selection.
/// This is the resolution cadence (how often we *check* whether the ATM moved),
/// distinct from the snapshot cadence handed to ingestion.
const RESOLUTION_INTERVAL_SECS: u64 = 15;

/// How long to wait for the QuestDB pool to be registered as Tauri state before
/// re-checking, during startup.
const POOL_WAIT_SECS: u64 = 5;

/// Read the latest spot price for `underlying` from QuestDB `live_ticks`. (R4.5)
///
/// Returns the most recent `last_traded_price` for the symbol, or `None` when no
/// tick exists for that symbol or the query fails — the caller treats `None` as
/// "spot unavailable" and skips the underlying for this iteration. This function
/// performs a read only and never mutates QuestDB.
pub async fn read_spot(pool: &PgPool, underlying: &str) -> Option<f64> {
    let query = "SELECT last_traded_price \
                 FROM live_ticks \
                 WHERE symbol = $1 \
                 ORDER BY timestamp DESC \
                 LIMIT 1";

    match sqlx::query(query).bind(underlying).fetch_optional(pool).await {
        Ok(Some(row)) => match row.try_get::<f64, _>("last_traded_price") {
            Ok(price) if price.is_finite() && price > 0.0 => Some(price),
            Ok(_) => None,
            Err(e) => {
                warn!(
                    "[OptionChainSub] spot decode failed for {}: {} — treating as unavailable.",
                    underlying, e
                );
                None
            }
        },
        Ok(None) => None,
        Err(e) => {
            warn!(
                "[OptionChainSub] spot query failed for {}: {} — treating as unavailable.",
                underlying, e
            );
            None
        }
    }
}

/// Map an `OptionType` to the wire string used in the `option_chain_set` command.
fn option_type_str(t: OptionType) -> &'static str {
    match t {
        OptionType::Ce => "CE",
        OptionType::Pe => "PE",
        OptionType::Fut => "FUT",
    }
}

/// Build the `option_chain_set` JSON payload for a resolved selection. (R4.1, R6.4)
///
/// This is the PURE core of `push_chain_set`: it performs no I/O and is a total,
/// deterministic function of the selection and the snapshot interval, so it is
/// directly unit-testable. The JSON shape matches the control-channel contract
/// (design §6):
/// ```jsonc
/// { "underlying", "snapshot_interval_secs",
///   "tokens": [ { "token", "tradingsymbol", "expiry", "strike", "type" } ] }
/// ```
/// `expiry` is serialized as an ISO date string (`YYYY-MM-DD`) and `type` as
/// `CE` / `PE` / `FUT`. The `snapshot_interval_secs` carried here is exactly the
/// value the caller threads through from the single resolved `FnoConfig`, so the
/// interval used for selection and the interval handed to ingestion are one and
/// the same (R6.4).
fn build_chain_set_payload(selection: &ChainSelection, interval_secs: u64) -> serde_json::Value {
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

/// Push the resolved selection to the ingestion control port as a single
/// newline-delimited `option_chain_set:{json}` command. (R4.1)
///
/// The payload is built by the pure `build_chain_set_payload`; this function adds
/// only the I/O. A connection or write failure is logged and surfaced as `Err`
/// so the caller can retry on the next iteration (R7.3); it never panics.
pub async fn push_chain_set(selection: &ChainSelection, interval_secs: u64) -> Result<(), String> {
    let control_port =
        std::env::var("INGESTION_CONTROL_PORT").unwrap_or_else(|_| "8085".to_string());

    let payload = build_chain_set_payload(selection, interval_secs);

    let cmd = format!("option_chain_set:{}\n", payload);

    let addr = format!("127.0.0.1:{}", control_port);
    let mut stream = tokio::net::TcpStream::connect(&addr)
        .await
        .map_err(|e| format!("cannot reach ingestion control :{} — {}", control_port, e))?;

    stream
        .write_all(cmd.as_bytes())
        .await
        .map_err(|e| format!("control write error: {}", e))?;

    Ok(())
}

/// Load one underlying's NFO option ladder from SQLite into `OptionContract`s.
///
/// Reads CE/PE/FUT contracts for `underlying` from `nfo_instruments`. Expiry is
/// stored as an ISO date string; rows whose expiry fails to parse into a
/// `NaiveDate`, or whose `instrument_type` is not CE/PE/FUT, are skipped (robust
/// against malformed rows). Returns an empty vector when the underlying has no
/// rows or the DB is unavailable — `build_chain_selection` handles an empty
/// ladder gracefully.
fn load_ladder(db_state: &DbState, underlying: &str) -> Vec<OptionContract> {
    let conn = match db_state.conn.lock() {
        Ok(c) => c,
        Err(e) => {
            warn!("[OptionChainSub] DB lock failed loading ladder for {}: {}", underlying, e);
            return Vec::new();
        }
    };

    let mut stmt = match conn.prepare(
        "SELECT instrument_token, tradingsymbol, instrument_type, strike, expiry \
         FROM nfo_instruments \
         WHERE underlying = ?1",
    ) {
        Ok(s) => s,
        Err(e) => {
            warn!("[OptionChainSub] prepare failed for {}: {}", underlying, e);
            return Vec::new();
        }
    };

    let rows = stmt.query_map([underlying], |row| {
        let token: i64 = row.get(0)?;
        let tradingsymbol: String = row.get(1)?;
        let instrument_type: String = row.get(2)?;
        let strike: f64 = row.get(3)?;
        let expiry: String = row.get(4)?;
        Ok((token, tradingsymbol, instrument_type, strike, expiry))
    });

    let rows = match rows {
        Ok(r) => r,
        Err(e) => {
            warn!("[OptionChainSub] query failed for {}: {}", underlying, e);
            return Vec::new();
        }
    };

    let mut ladder = Vec::new();
    for row in rows.flatten() {
        let (token, tradingsymbol, instrument_type, strike, expiry_str) = row;

        let option_type = match instrument_type.trim().to_uppercase().as_str() {
            "CE" => OptionType::Ce,
            "PE" => OptionType::Pe,
            "FUT" => OptionType::Fut,
            _ => continue, // unknown type — skip
        };

        // Robust expiry parse: skip rows whose ISO date string is unparseable.
        let expiry = match chrono::NaiveDate::parse_from_str(expiry_str.trim(), "%Y-%m-%d") {
            Ok(d) => d,
            Err(_) => continue,
        };

        ladder.push(OptionContract {
            token: token as u32,
            tradingsymbol,
            underlying: underlying.to_string(),
            option_type,
            strike,
            expiry,
        });
    }

    ladder
}

/// The outcome of evaluating one underlying for a push, as a PURE decision over
/// already-gathered inputs. Factoring this out of the I/O in `resolve_once` makes
/// the spot-unavailable branch (R4.5) and the re-center gate (R4.3) unit-testable
/// without a live feed, a DB, or the control port.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum PushDecision {
    /// Spot unavailable → skip the underlying and log; push nothing (R4.5).
    SkipSpotUnavailable,
    /// The resolved selection is empty → nothing to push.
    SkipEmptySelection,
    /// The ATM has not moved past the re-center threshold → no push this tick (R4.3).
    SkipNoRecenter,
    /// Push the selection to ingestion.
    Push,
}

/// Decide whether one underlying's resolved selection should be pushed. (R4.3, R4.5)
///
/// PURE and total — a deterministic function of the spot-availability result, the
/// resolved selection, the last pushed ATM (if any), and the re-center threshold:
///   - `spot == None`  → `SkipSpotUnavailable` (the spot source is unavailable;
///     the caller skips + logs and pushes nothing, never an unbounded/mis-centered
///     band — R4.5). This is checked first, before the selection is read.
///   - empty selection → `SkipEmptySelection`.
///   - no prior ATM    → `Push` (first selection for this underlying).
///   - prior ATM       → `Push` iff `should_recenter(prev, atm, threshold)` (R4.3),
///     else `SkipNoRecenter`.
fn decide_push(
    spot: Option<f64>,
    selection: &ChainSelection,
    last_atm: Option<f64>,
    recenter_threshold: f64,
) -> PushDecision {
    // R4.5: spot unavailable → push nothing. Checked before touching the
    // selection so a None spot can never yield a push.
    if spot.is_none() {
        return PushDecision::SkipSpotUnavailable;
    }

    if selection.entries.is_empty() {
        return PushDecision::SkipEmptySelection;
    }

    match last_atm {
        None => PushDecision::Push,
        Some(prev) => {
            if should_recenter(prev, selection.atm_strike, recenter_threshold) {
                PushDecision::Push
            } else {
                PushDecision::SkipNoRecenter
            }
        }
    }
}

/// An empty selection placeholder for an underlying (used when the spot is
/// unavailable, so no chain is resolved). Keeps `decide_push` total.
fn empty_selection(underlying: &str) -> ChainSelection {
    ChainSelection {
        underlying: underlying.to_string(),
        atm_strike: 0.0,
        entries: Vec::new(),
    }
}

/// Run one resolution pass over every configured underlying, pushing updated
/// selections where the ATM has shifted. `last_atm` carries the last pushed ATM
/// per underlying across iterations so re-centering is only triggered on a real
/// move (R4.3) or when there is no prior selection.
async fn resolve_once(
    pool: &PgPool,
    db_state: &DbState,
    cfg: &FnoConfig,
    last_atm: &mut HashMap<String, f64>,
) {
    let today = Local::now().date_naive();

    for underlying in &cfg.underlyings {
        let spot = read_spot(pool, underlying).await;

        // Resolve the selection only when a spot is available; otherwise an empty
        // placeholder (the spot-unavailable decision is taken before it is read,
        // so no ladder load happens for an unavailable underlying).
        let selection = match spot {
            Some(s) => {
                let ladder = load_ladder(db_state, underlying);
                build_chain_selection(&ladder, underlying, s, today, &cfg.chain)
            }
            None => empty_selection(underlying),
        };

        match decide_push(
            spot,
            &selection,
            last_atm.get(underlying).copied(),
            cfg.chain.recenter_threshold,
        ) {
            // R4.5: spot unavailable → skip + log, never push.
            PushDecision::SkipSpotUnavailable => {
                info!(
                    "[OptionChainSub] spot unavailable for {} — skipping chain subscription this tick.",
                    underlying
                );
            }
            PushDecision::SkipEmptySelection => {
                info!(
                    "[OptionChainSub] empty selection for {} — nothing to push.",
                    underlying
                );
            }
            // R4.3: ATM has not moved enough → leave the existing subscription as-is.
            PushDecision::SkipNoRecenter => {}
            PushDecision::Push => {
                // R6.4: the snapshot interval handed to ingestion is the SAME value
                // resolved once in `cfg` that drives selection here.
                match push_chain_set(&selection, cfg.chain.snapshot_interval_secs).await {
                    Ok(()) => {
                        last_atm.insert(underlying.clone(), selection.atm_strike);
                        info!(
                            "[OptionChainSub] ✓ pushed {} tokens for {} (ATM {:.2}).",
                            selection.entries.len(),
                            underlying,
                            selection.atm_strike,
                        );
                    }
                    Err(e) => {
                        // R7.3: log and retry on the next iteration; do not update
                        // last_atm so the same selection is re-attempted next tick.
                        warn!(
                            "[OptionChainSub] push failed for {}: {} — will retry next iteration.",
                            underlying, e
                        );
                    }
                }
            }
        }
    }
}

/// Periodic option-chain subscriber loop. Spawned at startup alongside the
/// instrument sync.
///
/// Reads the single resolved `FnoConfig` once (R6.4 — the same `chain` config
/// drives both selection here and the snapshot interval handed to ingestion via
/// `push_chain_set`). Waits for the QuestDB pool and SQLite DB to be registered
/// as Tauri state, then ticks every `RESOLUTION_INTERVAL_SECS`, resolving and
/// (when warranted) pushing each underlying's bounded selection.
pub async fn run_option_chain_subscriber(app: tauri::AppHandle) {
    // R6.4: resolve the F&O config ONCE; this single value feeds both the
    // subscriber's selection math and the snapshot interval pushed to ingestion.
    let cfg = resolve_fno_config();

    info!(
        "[OptionChainSub] starting — underlyings={:?}, N={}, M={}, recenter={}, snapshot={}s, tick={}s",
        cfg.underlyings,
        cfg.chain.nearest_expiries,
        cfg.chain.strike_band_half_width,
        cfg.chain.recenter_threshold,
        cfg.chain.snapshot_interval_secs,
        RESOLUTION_INTERVAL_SECS,
    );

    let mut last_atm: HashMap<String, f64> = HashMap::new();

    loop {
        // The QuestDB pool is registered asynchronously in lib.rs; wait for it
        // (and the SQLite DB) before attempting a resolution pass.
        let pool = match app.try_state::<PgPool>() {
            Some(p) => p.inner().clone(),
            None => {
                tokio::time::sleep(std::time::Duration::from_secs(POOL_WAIT_SECS)).await;
                continue;
            }
        };

        if let Some(db_state) = app.try_state::<DbState>() {
            resolve_once(&pool, &db_state, &cfg, &mut last_atm).await;
        } else {
            warn!("[OptionChainSub] SQLite DbState not available yet — retrying.");
        }

        tokio::time::sleep(std::time::Duration::from_secs(RESOLUTION_INTERVAL_SECS)).await;
    }
}

#[cfg(test)]
mod tests {
    // Unit tests for the option-chain subscriber (task 9.2).
    //
    // The subscriber itself is mostly I/O (QuestDB, SQLite, the TCP control port,
    // the tauri AppHandle), which is not unit-testable without live infra. These
    // tests target the PURE seams extracted from that I/O:
    //   - `decide_push`             — the spot-unavailable / re-center decision (R4.5, R4.3)
    //   - `build_chain_set_payload` — the control-channel JSON (R6.4 + CE/PE/FUT + ISO expiry)
    // together with the single resolved `FnoConfig` that feeds both selection and
    // the snapshot interval handed to ingestion (R6.4).

    use super::*;
    use crate::services::fno_config::resolve_fno_config_with;
    use crate::services::option_chain::{ChainSelection, OptionType, SelectedOption};
    use chrono::NaiveDate;

    fn sel_entry(token: u32, strike: f64, option_type: OptionType) -> SelectedOption {
        SelectedOption {
            token,
            tradingsymbol: format!("SYM{token}"),
            underlying: "NIFTY".to_string(),
            expiry: NaiveDate::from_ymd_opt(2025, 1, 30).unwrap(),
            strike,
            option_type,
        }
    }

    fn selection_with(entries: Vec<SelectedOption>, atm: f64) -> ChainSelection {
        ChainSelection {
            underlying: "NIFTY".to_string(),
            atm_strike: atm,
            entries,
        }
    }

    // ── R4.5: spot-unavailable branch pushes nothing ────────────────────────

    #[test]
    fn spot_unavailable_pushes_nothing_even_with_a_nonempty_selection() {
        // A perfectly good selection is present, but the spot is unavailable.
        let selection = selection_with(
            vec![
                sel_entry(1, 22000.0, OptionType::Ce),
                sel_entry(2, 22000.0, OptionType::Pe),
            ],
            22000.0,
        );

        // R4.5: spot None → the decision is "skip, push nothing", never a Push.
        let decision = decide_push(None, &selection, None, 1.0);
        assert_eq!(decision, PushDecision::SkipSpotUnavailable);
        assert_ne!(decision, PushDecision::Push);
    }

    #[test]
    fn spot_unavailable_skips_regardless_of_prior_atm() {
        // Even with a prior ATM recorded and a moved selection, a None spot must
        // still skip (the spot guard is evaluated before the re-center gate).
        let selection = selection_with(vec![sel_entry(1, 22500.0, OptionType::Ce)], 22500.0);
        let decision = decide_push(None, &selection, Some(22000.0), 1.0);
        assert_eq!(decision, PushDecision::SkipSpotUnavailable);
    }

    #[test]
    fn empty_placeholder_for_unavailable_spot_is_empty() {
        // The placeholder built when spot is unavailable carries no entries, so
        // nothing can be pushed for it.
        let placeholder = empty_selection("NIFTY");
        assert!(placeholder.entries.is_empty());
    }

    #[test]
    fn available_spot_first_selection_pushes() {
        // Sanity: with a usable spot, a non-empty selection and no prior ATM, the
        // decision is Push (so the skip above is genuinely the spot branch).
        let selection = selection_with(vec![sel_entry(1, 22000.0, OptionType::Ce)], 22000.0);
        assert_eq!(
            decide_push(Some(22000.0), &selection, None, 1.0),
            PushDecision::Push
        );
    }

    #[test]
    fn available_spot_empty_selection_pushes_nothing() {
        // A usable spot but an empty selection still pushes nothing.
        let selection = selection_with(Vec::new(), 0.0);
        assert_eq!(
            decide_push(Some(22000.0), &selection, None, 1.0),
            PushDecision::SkipEmptySelection
        );
    }

    #[test]
    fn available_spot_unmoved_atm_does_not_repush() {
        // Prior ATM equal to the new ATM and a threshold of 1.0 → no re-center.
        let selection = selection_with(vec![sel_entry(1, 22000.0, OptionType::Ce)], 22000.0);
        assert_eq!(
            decide_push(Some(22000.0), &selection, Some(22000.0), 1.0),
            PushDecision::SkipNoRecenter
        );
    }

    // ── R6.4: one FnoConfig feeds both selection and the snapshot interval ──

    #[test]
    fn single_config_interval_threads_into_the_push_payload() {
        // Resolve ONE FnoConfig (as the subscriber does, once) with an explicit
        // snapshot interval, via the pure env-lookup seam.
        let env: std::collections::HashMap<String, String> =
            [("FNO_SNAPSHOT_INTERVAL_SECS".to_string(), "45".to_string())]
                .into_iter()
                .collect();
        let cfg = resolve_fno_config_with(|k| env.get(k).cloned());
        assert_eq!(cfg.chain.snapshot_interval_secs, 45);

        let selection = selection_with(
            vec![
                sel_entry(101, 22000.0, OptionType::Ce),
                sel_entry(102, 22000.0, OptionType::Pe),
            ],
            22000.0,
        );

        // The interval handed to ingestion (in the payload) is the SAME value from
        // the single resolved config that drives selection — not a separate value.
        let payload = build_chain_set_payload(&selection, cfg.chain.snapshot_interval_secs);
        assert_eq!(
            payload["snapshot_interval_secs"].as_u64(),
            Some(cfg.chain.snapshot_interval_secs)
        );
        assert_eq!(payload["snapshot_interval_secs"].as_u64(), Some(45));
    }

    #[test]
    fn default_config_interval_threads_into_the_push_payload() {
        // With the interval unset, the documented default is resolved and the same
        // default flows into the payload (R6.2/R6.4).
        let cfg = resolve_fno_config_with(|_| None);
        let selection = selection_with(vec![sel_entry(1, 100.0, OptionType::Ce)], 100.0);
        let payload = build_chain_set_payload(&selection, cfg.chain.snapshot_interval_secs);
        assert_eq!(
            payload["snapshot_interval_secs"].as_u64(),
            Some(cfg.chain.snapshot_interval_secs)
        );
    }

    // ── Payload shape: CE/PE/FUT mapping and ISO expiry (supports R4.x) ─────

    #[test]
    fn payload_maps_option_type_and_expiry() {
        let entries = vec![
            SelectedOption {
                token: 10,
                tradingsymbol: "NIFTY25JAN22000CE".to_string(),
                underlying: "NIFTY".to_string(),
                expiry: NaiveDate::from_ymd_opt(2025, 1, 30).unwrap(),
                strike: 22000.0,
                option_type: OptionType::Ce,
            },
            SelectedOption {
                token: 11,
                tradingsymbol: "NIFTY25JAN22000PE".to_string(),
                underlying: "NIFTY".to_string(),
                expiry: NaiveDate::from_ymd_opt(2025, 1, 30).unwrap(),
                strike: 22000.0,
                option_type: OptionType::Pe,
            },
            SelectedOption {
                token: 12,
                tradingsymbol: "NIFTY25JANFUT".to_string(),
                underlying: "NIFTY".to_string(),
                expiry: NaiveDate::from_ymd_opt(2025, 1, 30).unwrap(),
                strike: 0.0,
                option_type: OptionType::Fut,
            },
        ];
        let selection = selection_with(entries, 22000.0);

        let payload = build_chain_set_payload(&selection, 60);

        assert_eq!(payload["underlying"].as_str(), Some("NIFTY"));
        let tokens = payload["tokens"].as_array().expect("tokens array");
        assert_eq!(tokens.len(), 3);

        // type maps to CE / PE / FUT
        assert_eq!(tokens[0]["type"].as_str(), Some("CE"));
        assert_eq!(tokens[1]["type"].as_str(), Some("PE"));
        assert_eq!(tokens[2]["type"].as_str(), Some("FUT"));

        // expiry maps to an ISO date string
        assert_eq!(tokens[0]["expiry"].as_str(), Some("2025-01-30"));
        assert_eq!(tokens[2]["expiry"].as_str(), Some("2025-01-30"));

        // identity fields are preserved
        assert_eq!(tokens[0]["token"].as_u64(), Some(10));
        assert_eq!(tokens[0]["tradingsymbol"].as_str(), Some("NIFTY25JAN22000CE"));
        assert_eq!(tokens[0]["strike"].as_f64(), Some(22000.0));
    }

    #[test]
    fn option_type_str_maps_all_variants() {
        assert_eq!(option_type_str(OptionType::Ce), "CE");
        assert_eq!(option_type_str(OptionType::Pe), "PE");
        assert_eq!(option_type_str(OptionType::Fut), "FUT");
    }
}
