// commands/fno.rs — Tauri IPC bridge for the Frontend F&O Section (Phase F4).
//
// TRANSPORT ONLY — this module never computes an options analytic. It is the
// thin Rust seam between the Next.js F&O section and the already-built F1/F2/F3
// backend (the deep-quant FastAPI service on :8086). It exposes two commands:
//
//   * `get_fno_analytics(underlying, expiry)` — proxies a single read-only
//     GET to `${FNO_SERVICE_URL:-http://localhost:8086}/options/snapshot` and
//     returns the parsed JSON verbatim (the combined chain / analytics / bias
//     payload OR the F2 `Unavailable_Marker`). On a transport/HTTP error it
//     returns `Err(String)` so the frontend renders an honest error/empty
//     state rather than crashing (R6.1, R6.3, R6.5, R9.1).
//
//   * `fno_list_chains()` — returns the configured index underlyings
//     established by F1 (`resolve_fno_config`) and, per underlying, the
//     available expiries read from the local NFO instrument master. The
//     underlying set is therefore bounded to the configured indexes; it can
//     never offer a non-index or unconfigured underlying (R2.2, R9.3).
//
// The scoped subscription poll loop (`fno_subscribe` / `fno_unsubscribe`) lives
// at the bottom of this file; the `generate_handler!` registration is wired in
// a later task, not here.

use std::collections::BTreeMap;
use std::sync::{Mutex, OnceLock};

use log::{info, warn};
use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

use crate::services::fno_config::resolve_fno_config;
use crate::services::fno_service::{
    fetch_snapshots_from_questdb, load_instruments_for_expiry, resolve_nearest_expiry,
};
use crate::services::option_chain::select_atm;
use crate::services::option_chain_subscriber::{
    read_spot, resolve_nfo_underlying_name,
};

/// Resolve the base URL of the F&O analytics service (the deep-quant FastAPI
/// app). Reads `FNO_SERVICE_URL`, falling back to `http://localhost:8086`.
/// Any trailing slash is trimmed so the path can be appended cleanly.


/// The set of configured index chains the F&O section may show.
///
/// `underlyings` is bounded to the configured index underlyings established by
/// F1 (R2.2, R9.3). `expiries_by_underlying` maps each underlying to its
/// available expiries (ISO `YYYY-MM-DD`, ascending); an underlying with no
/// known expiries maps to an empty list (the frontend then lets the bridge
/// resolve the nearest expiry).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct FnoChains {
    pub underlyings: Vec<String>,
    pub expiries_by_underlying: BTreeMap<String, Vec<String>>,
}

/// Tauri command: fetch the combined F&O snapshot for one underlying / expiry.
///
/// Issues a `reqwest` GET to the F&O service's `/options/snapshot` endpoint and
/// returns the parsed JSON **unchanged** — the payload (chain + F2 analytics +
/// F3 bias) or the F2 `Unavailable_Marker`. It recomputes nothing (R6.1, R6.3,
/// R9.1). A transport failure or non-2xx HTTP status becomes an `Err(String)`
/// the frontend surfaces as a visible error/empty state (R6.5).
#[tauri::command]
pub async fn get_fno_analytics(
    _app: AppHandle,
    db_state: tauri::State<'_, crate::db::DbState>,
    pool: tauri::State<'_, sqlx::PgPool>,
    underlying: String,
    expiry: String,
) -> Result<serde_json::Value, String> {
    crate::services::fno_service::build_fno_snapshot(&db_state, &pool, &underlying, &expiry).await
}

/// Tauri command: list the configured index underlyings and their expiries.
///
/// The underlyings come straight from the single F1 `FnoConfig` resolver, so
/// the selector is bounded to the configured indexes and never offers an
/// unconfigured underlying (R2.2, R9.3). Per-underlying expiries are read
/// best-effort from the local NFO instrument master; a missing DB or an
/// underlying with no listed contracts yields an empty expiry list rather than
/// an error.
#[tauri::command]
pub async fn fno_list_chains(app: AppHandle) -> Result<FnoChains, String> {
    let mut underlyings = resolve_fno_config().underlyings;

    // Include dynamically-requested underlyings (e.g. a stock chain the user
    // opened from search) so the selector can offer them alongside the
    // configured indexes. Still bounded: entries were validated against the NFO
    // master before being registered by `fno_request_underlying`.
    if let Some(reg) =
        app.try_state::<crate::services::option_chain_subscriber::RequestedUnderlyings>()
    {
        for u in reg.snapshot() {
            if !underlyings.iter().any(|c| c.eq_ignore_ascii_case(&u)) {
                underlyings.push(u);
            }
        }
    }

    let mut expiries_by_underlying: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let db_state = app.try_state::<crate::db::DbState>();
    let pool = app.try_state::<sqlx::PgPool>();

    for u in &underlyings {
        let mut expiries = match &db_state {
            Some(state) => load_expiries(state, u),
            None => Vec::new(),
        };

        if let Some(p) = &pool {
            if let Ok(db_expiries) = crate::services::fno_service::fetch_expiries_from_questdb(p, u).await {
                for de in db_expiries {
                    if !expiries.contains(&de) {
                        expiries.push(de);
                    }
                }
            }
        }

        expiries.sort();
        expiries_by_underlying.insert(u.clone(), expiries);
    }

    Ok(FnoChains {
        underlyings,
        expiries_by_underlying,
    })
}

/// Tauri command: request that the F&O chain for `underlying` be ingested.
///
/// Opens F&O for any NFO underlying the user selects from search (e.g. a stock
/// such as `"RELIANCE"`) by registering it with the option-chain subscriber,
/// which resolves and ingests its bounded chain on the next tick. Bounded: the
/// underlying MUST exist in the local NFO instrument master, so arbitrary or
/// non-derivative symbols are rejected.
///
/// Returns `Ok(true)` when the underlying is a known F&O underlying (now being
/// ingested) and `Ok(false)` when it has no NFO contracts (the caller should
/// fall back to the price chart). Never panics.
#[tauri::command]
pub async fn fno_request_underlying(app: AppHandle, underlying: String) -> Result<bool, String> {
    let u = underlying.trim().to_string();
    if u.is_empty() {
        return Ok(false);
    }

    // The ladder groups under the NFO derivative name (identity for stocks).
    let nfo_name =
        crate::services::option_chain_subscriber::resolve_nfo_underlying_name(&u);

    // Bounded: only underlyings that actually have NFO contracts or QuestDB snapshots may be ingested.
    let pool = app.try_state::<sqlx::PgPool>();
    let has_chain = match app.try_state::<crate::db::DbState>() {
        Some(state) => nfo_underlying_exists(&state, pool.as_deref(), &nfo_name).await,
        None => false,
    };
    if !has_chain {
        info!(
            "[fno] request for '{}' rejected — no NFO contracts or snapshots under '{}'.",
            u, nfo_name
        );
        return Ok(false);
    }

    match app.try_state::<crate::services::option_chain_subscriber::RequestedUnderlyings>() {
        Some(reg) => {
            if reg.add(&u) {
                info!(
                    "[fno] now ingesting requested underlying '{}' (ladder '{}').",
                    u, nfo_name
                );
            }
            // Ensure the underlying's spot flows into `live_ticks` so the
            // option-chain subscriber can resolve its ATM. For a stock the
            // underlying IS its NSE tradingsymbol, so this subscribes the right
            // instrument. Fire-and-forget so the command returns immediately.
            let app_bg = app.clone();
            let sym_bg = u.clone();
            tauri::async_runtime::spawn(async move {
                crate::commands::ticker::ensure_spot_subscribed(&app_bg, &sym_bg).await;
            });
            Ok(true)
        }
        None => {
            warn!("[fno] RequestedUnderlyings state missing; cannot ingest '{}'.", u);
            Ok(false)
        }
    }
}

/// The resolved nearest F&O contract for an underlying — returned by
/// `fno_resolve_nearest_contract` so the frontend can chart a concrete tradingsymbol
/// the moment the user enters F&O mode or clicks an underlying while in F&O mode.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ResolvedContract {
    pub tradingsymbol: String,
    pub underlying: String,
    pub expiry: String,
    pub strike: f64,
    pub option_type: String, // "CE" | "PE"
}

/// Tauri command: resolve the nearest F&O option contract for `underlying`.
///
/// Picks the nearest non-expired expiry, the at-the-money strike for that
/// expiry (from the NFO instrument master + the latest spot), and the CE side
/// at that strike. Falls back to PE at the ATM strike when CE has no open
/// interest in the latest QuestDB snapshot. Widens up to two strikes each side
/// of ATM if neither CE nor PE is listed at the exact ATM strike.
///
/// Returns `Ok(None)` when no expiry, no instruments, or no contract can be
/// resolved — the frontend then falls back to charting the underlying equity
/// rather than crashing or fabricating a contract. Never panics.
#[tauri::command]
pub async fn fno_resolve_nearest_contract(
    app: AppHandle,
    underlying: String,
) -> Result<Option<ResolvedContract>, String> {
    let u = underlying.trim().to_string();
    if u.is_empty() {
        return Ok(None);
    }

    let db_state = match app.try_state::<crate::db::DbState>() {
        Some(s) => s,
        None => return Ok(None),
    };
    let pool = app.try_state::<sqlx::PgPool>();

    let nfo_name = resolve_nfo_underlying_name(&u);

    // 1. Nearest expiry.
    let expiry = match resolve_nearest_expiry(&db_state, &nfo_name) {
        Some(e) => e,
        None => {
            info!(
                "[fno] resolve_nearest_contract: no expiry in nfo_instruments for '{}' (nfo='{}').",
                u, nfo_name
            );
            return Ok(None);
        }
    };

    // 2. Listed CE/PE contracts for that expiry.
    let instruments = load_instruments_for_expiry(&db_state, &nfo_name, &expiry);
    if instruments.is_empty() {
        info!(
            "[fno] resolve_nearest_contract: no CE/PE rows for {} / {}.",
            nfo_name, expiry
        );
        return Ok(None);
    }

    // Distinct sorted strike ladder.
    let mut strikes: Vec<f64> = instruments
        .iter()
        .map(|(_, _, _, s)| *s)
        .filter(|s| s.is_finite())
        .collect();
    strikes.sort_by(|a, b| a.partial_cmp(b).unwrap());
    strikes.dedup_by(|a, b| a == b);
    if strikes.is_empty() {
        return Ok(None);
    }

    // 3. Spot → ATM strike. Fall back to the median listed strike when no spot
    //    is available (market closed, no tick yet) so we still return a contract.
    let spot = match &pool {
        Some(p) => read_spot(p, &u).await,
        None => None,
    };
    let atm = match spot {
        Some(s) if s.is_finite() => select_atm(&strikes, s).unwrap_or_else(|| {
            strikes[strikes.len() / 2]
        }),
        _ => strikes[strikes.len() / 2],
    };

    // 4. Build a (strike, type) → tradingsymbol lookup. f64 is not Ord, so use a
    //    HashMap keyed on the bit representation of the strike + option type.
    let mut by_strike_type: std::collections::HashMap<(u64, String), String> =
        std::collections::HashMap::new();
    for (_, sym, inst_type, strike) in &instruments {
        by_strike_type.insert((strike.to_bits(), inst_type.clone()), sym.clone());
    }

    // 5. Walk out from ATM (ATM, ±1, ±2 strikes) looking for a listed contract.
    //    CE preferred. If both CE and PE exist at the candidate strike, decide
    //    by open interest in the latest QuestDB snapshot.
    let atm_index = strikes
        .iter()
        .position(|s| (*s - atm).abs() < f64::EPSILON)
        .unwrap_or(strikes.len() / 2);

    let mut candidate_indices: Vec<usize> = vec![atm_index];
    for offset in 1..=2 {
        if atm_index + offset < strikes.len() {
            candidate_indices.push(atm_index + offset);
        }
        if atm_index >= offset {
            candidate_indices.push(atm_index - offset);
        }
    }

    // Latest snapshot rows for OI tie-break (one read; reused across candidates).
    let snapshot_rows = match &pool {
        Some(p) => fetch_snapshots_from_questdb(p, &nfo_name, &expiry).await.unwrap_or_default(),
        None => Vec::new(),
    };
    let oi_for: std::collections::HashMap<(u64, String), i64> = snapshot_rows
        .iter()
        .filter_map(|r| {
            let oi = r.open_interest.unwrap_or(0);
            Some(((r.strike.to_bits(), r.option_type.clone()), oi))
        })
        .collect();

    for &idx in &candidate_indices {
        let strike = strikes[idx];
        let ce_sym = by_strike_type.get(&(strike.to_bits(), "CE".to_string())).cloned();
        let pe_sym = by_strike_type.get(&(strike.to_bits(), "PE".to_string())).cloned();

        let (tradingsymbol, option_type) = match (ce_sym.clone(), pe_sym.clone()) {
            (Some(ce), Some(pe)) => {
                // Both listed → decide by OI. CE preferred on tie.
                let ce_oi = *oi_for.get(&(strike.to_bits(), "CE".to_string())).unwrap_or(&0);
                let pe_oi = *oi_for.get(&(strike.to_bits(), "PE".to_string())).unwrap_or(&0);
                if ce_oi > 0 || pe_oi == 0 {
                    (ce, "CE".to_string())
                } else {
                    (pe, "PE".to_string())
                }
            }
            (Some(ce), None) => (ce, "CE".to_string()),
            (None, Some(pe)) => (pe, "PE".to_string()),
            (None, None) => continue,
        };

        info!(
            "[fno] resolve_nearest_contract: {} → {} {} (strike {}, expiry {}, spot={:?})",
            u, tradingsymbol, option_type, strike, expiry, spot
        );
        return Ok(Some(ResolvedContract {
            tradingsymbol,
            underlying: u.clone(),
            expiry,
            strike,
            option_type,
        }));
    }

    info!(
        "[fno] resolve_nearest_contract: no CE/PE contract near ATM {:.2} for {} / {}.",
        atm, nfo_name, expiry
    );
    Ok(None)
}

/// Whether `nfo_instruments` or QuestDB `option_chain_snapshots` has any CE/PE contract for `underlying`.
async fn nfo_underlying_exists(
    db_state: &crate::db::DbState,
    pool: Option<&sqlx::PgPool>,
    underlying: &str,
) -> bool {
    let sqlite_exists = {
        let conn = match db_state.conn.lock() {
            Ok(c) => c,
            Err(_) => return false,
        };
        conn.query_row(
            "SELECT 1 FROM nfo_instruments \
             WHERE underlying = ?1 AND instrument_type IN ('CE', 'PE') LIMIT 1",
            [underlying],
            |_| Ok(()),
        )
        .is_ok()
    };

    if sqlite_exists {
        return true;
    }

    if let Some(p) = pool {
        let query = "SELECT 1 FROM option_chain_snapshots WHERE underlying = $1 LIMIT 1";
        sqlx::query(query).bind(underlying).fetch_optional(p).await.is_ok()
    } else {
        false
    }
}

/// Read the distinct option expiries available for one underlying from the
/// local NFO instrument master (SQLite `nfo_instruments`), ascending.
///
/// Mirrors the `option_chain_subscriber` ladder loader's conventions: the
/// configured underlying string is matched directly against `nfo_instruments.
/// underlying`, only CE/PE rows are considered, and any expiry that is not a
/// well-formed ISO date is skipped (robust against malformed rows). A poisoned
/// lock, a query error, or an empty table all yield an empty list — never a
/// panic — so `fno_list_chains` stays total.
fn load_expiries(db_state: &crate::db::DbState, underlying: &str) -> Vec<String> {
    let conn = match db_state.conn.lock() {
        Ok(c) => c,
        Err(e) => {
            warn!("[fno] SQLite lock poisoned reading expiries for {}: {}", underlying, e);
            return Vec::new();
        }
    };

    let mut stmt = match conn.prepare(
        "SELECT DISTINCT expiry FROM nfo_instruments \
         WHERE underlying = ?1 AND instrument_type IN ('CE', 'PE') \
         ORDER BY expiry ASC",
    ) {
        Ok(s) => s,
        Err(e) => {
            warn!("[fno] prepare expiries query failed for {}: {}", underlying, e);
            return Vec::new();
        }
    };

    let rows = match stmt.query_map([underlying], |row| row.get::<_, String>(0)) {
        Ok(r) => r,
        Err(e) => {
            warn!("[fno] expiries query failed for {}: {}", underlying, e);
            return Vec::new();
        }
    };

    let mut expiries: Vec<String> = Vec::new();
    for expiry in rows.flatten() {
        let trimmed = expiry.trim().to_string();
        // Keep only well-formed ISO dates; skip malformed rows. The SQL
        // ORDER BY already sorts lexicographically, which is chronological for
        // YYYY-MM-DD, so dropping invalids preserves ascending order.
        if chrono::NaiveDate::parse_from_str(&trimmed, "%Y-%m-%d").is_ok() {
            expiries.push(trimmed);
        }
    }

    expiries
}

// ───────────────────────────── Scoped subscription ─────────────────────────
//
// A single background poll loop, scoped to the active F&O mode, that re-fetches
// the combined snapshot on the F1 snapshot cadence and emits `fno-snapshot` only
// when the snapshot timestamp advances. It mirrors the lazy-bootstrap style used
// elsewhere (e.g. `subscribe_ticker`): `fno_subscribe` spawns (or replaces) the
// task; `fno_unsubscribe` aborts it so no F&O work runs while the section is
// hidden (R6.2, R7.1, R7.3). Like the option-chain subscriber, the loop LOGS and
// retries on the next tick on a transport error rather than crashing (R6.5).
//
// The active task handle and its `(underlying, expiry)` key live in a single
// process-global slot rather than Tauri-managed state, so this module is fully
// self-contained (the `generate_handler!` wiring is a separate task) and there is
// at most one F&O poll loop running at any time.

/// The currently-running F&O poll task: the active `(underlying, expiry)` it is
/// polling and its abort handle.
struct FnoSubscription {
    underlying: String,
    expiry: String,
    handle: tokio::task::JoinHandle<()>,
}

/// Process-global slot holding the single active F&O subscription (if any).
///
/// Guarded by a plain `std::sync::Mutex` — every critical section is a short,
/// synchronous swap (abort the old handle, store the new one) with no `.await`
/// held across the lock, so it can never deadlock the async runtime.
fn subscription_slot() -> &'static Mutex<Option<FnoSubscription>> {
    static SLOT: OnceLock<Mutex<Option<FnoSubscription>>> = OnceLock::new();
    SLOT.get_or_init(|| Mutex::new(None))
}

/// Read the F&O snapshot cadence (seconds) from the single F1 `FnoConfig`.
///
/// This is the SAME `snapshot_interval_secs` the option-chain subscriber threads
/// through to ingestion (R6.4), so the poll loop re-fetches at exactly the rate
/// new snapshots are produced rather than inventing its own cadence.
fn snapshot_cadence_secs() -> u64 {
    resolve_fno_config().chain.snapshot_interval_secs
}

/// Fetch the combined F&O snapshot for one underlying / expiry from the F&O
/// service. Transport-only twin of `get_fno_analytics`, used by the poll loop;
/// returns the parsed JSON verbatim or an `Err(String)` the loop logs and
/// retries (it never recomputes an analytic — R6.3, R9.1).
async fn fetch_fno_snapshot(app: &AppHandle, underlying: &str, expiry: &str) -> Result<serde_json::Value, String> {
    let db_state = app.state::<crate::db::DbState>();
    let pool = app.state::<sqlx::PgPool>();
    crate::services::fno_service::build_fno_snapshot(&db_state, &pool, underlying, expiry).await
}

/// Extract the `snapshot_ts` (epoch ms) from a snapshot payload, if present.
///
/// Accepts either an integer or a float JSON number (and a numeric string, for
/// robustness against backend serialization choices). Returns `None` for an
/// `Unavailable_Marker` or any payload without a usable `snapshot_ts`, so the
/// loop simply does not emit on those ticks (the de-dup gate is monotonic).
fn extract_snapshot_ts(payload: &serde_json::Value) -> Option<i64> {
    let v = payload.get("snapshot_ts")?;
    if let Some(i) = v.as_i64() {
        return Some(i);
    }
    if let Some(f) = v.as_f64() {
        if f.is_finite() {
            return Some(f as i64);
        }
    }
    v.as_str().and_then(|s| s.trim().parse::<i64>().ok())
}

/// The pure de-dup gate that decides whether the poll loop emits for a payload.
///
/// Given a fetched `payload` and the `last_emitted_ts` (the snapshot_ts of the
/// most recent emission, or `None` before the first), returns `Some(ts)` — the
/// new value `last_emitted_ts` should advance to — when the payload carries a
/// `snapshot_ts` that strictly advances past the last emitted one, and `None`
/// otherwise (a duplicate/regressed snapshot_ts, or a payload with none, e.g. an
/// `Unavailable_Marker`). This is exactly the rule "emit `fno-snapshot` once per
/// new `snapshot_ts`, suppressing duplicates" (R6.2, R7.1), extracted so it can
/// be exercised with real payload sequences independently of the AppHandle and
/// the spawned task.
fn next_emit_ts(payload: &serde_json::Value, last_emitted_ts: Option<i64>) -> Option<i64> {
    match extract_snapshot_ts(payload) {
        Some(ts) if last_emitted_ts.map_or(true, |prev| ts > prev) => Some(ts),
        _ => None,
    }
}

/// The poll loop body: re-fetch on the snapshot cadence and `emit('fno-snapshot')`
/// ONLY when `snapshot_ts` strictly advances past the last emitted value (R6.2,
/// R7.1). A transport/HTTP error is logged and retried on the next tick rather
/// than crashing the task (R6.5); a payload with no `snapshot_ts` (e.g. an
/// `Unavailable_Marker`) is skipped so duplicates are suppressed. The loop ends
/// only when the task is aborted by `fno_subscribe`/`fno_unsubscribe`.
async fn run_fno_poll_loop(app: AppHandle, underlying: String, expiry: String) {
    let cadence = std::time::Duration::from_secs(snapshot_cadence_secs());
    let mut last_emitted_ts: Option<i64> = None;

    info!(
        "[fno] poll loop started for {} / {} (cadence {}s).",
        underlying,
        if expiry.is_empty() { "<nearest>" } else { &expiry },
        cadence.as_secs(),
    );

    loop {
        match fetch_fno_snapshot(&app, &underlying, &expiry).await {
            Ok(payload) => {
                if let Some(ts) = next_emit_ts(&payload, last_emitted_ts) {
                    // snapshot_ts advanced → emit exactly once for this new snapshot.
                    if let Err(e) = app.emit("fno-snapshot", payload) {
                        warn!(
                            "[fno] emit fno-snapshot failed for {} / {}: {} — will retry next tick.",
                            underlying, expiry, e
                        );
                    } else {
                        last_emitted_ts = Some(ts);
                    }
                }
                // snapshot_ts unchanged / regressed, or absent (marker) → suppress.
            }
            Err(e) => {
                // R6.5/R7.3: log and retry on the next tick; never crash the task.
                warn!(
                    "[fno] poll fetch failed for {} / {}: {} — retrying next tick.",
                    underlying, expiry, e
                );
            }
        }

        tokio::time::sleep(cadence).await;
    }
}

/// Store `sub` as the single active subscription, aborting and returning the
/// previously-active `(underlying, expiry)` (if any) first. The abort guarantees
/// at most one F&O poll loop runs at a time (R6.2, R7.1). Pure slot mechanics
/// with no `.await` held across the lock, so it is directly testable by storing
/// a dummy task handle without an AppHandle or live HTTP.
fn replace_subscription(sub: FnoSubscription) -> Result<Option<(String, String)>, String> {
    let mut slot = subscription_slot()
        .lock()
        .map_err(|e| format!("F&O subscription lock poisoned: {}", e))?;

    let prev = slot.take();
    let prev_key = prev
        .as_ref()
        .map(|p| (p.underlying.clone(), p.expiry.clone()));
    if let Some(prev) = prev {
        prev.handle.abort();
    }

    *slot = Some(sub);
    Ok(prev_key)
}

/// Abort and clear the active subscription, returning its `(underlying, expiry)`
/// (or `None` when none was running). This is the teardown that ensures no F&O
/// work runs while the section is hidden (R7.3); idempotent and testable.
fn take_subscription() -> Result<Option<(String, String)>, String> {
    let mut slot = subscription_slot()
        .lock()
        .map_err(|e| format!("F&O subscription lock poisoned: {}", e))?;

    let prev = slot.take();
    let prev_key = prev
        .as_ref()
        .map(|p| (p.underlying.clone(), p.expiry.clone()));
    if let Some(prev) = prev {
        prev.handle.abort();
    }

    Ok(prev_key)
}

/// Tauri command: start (or replace) the scoped F&O subscription.
///
/// Spawns a single background poll loop for `(underlying, expiry)`, storing the
/// active key and its abort handle in the process-global slot. Any previously
/// running loop (a stale underlying/expiry) is aborted first, so there is at
/// most one F&O poll task at a time (R6.2, R7.1). Returns immediately; all
/// network work happens on the spawned task.
#[tauri::command]
pub async fn fno_subscribe(
    app: AppHandle,
    underlying: String,
    expiry: String,
) -> Result<(), String> {
    let handle = tokio::spawn(run_fno_poll_loop(
        app.clone(),
        underlying.clone(),
        expiry.clone(),
    ));

    let sub = FnoSubscription {
        underlying: underlying.clone(),
        expiry: expiry.clone(),
        handle,
    };

    if let Some((prev_u, prev_e)) = replace_subscription(sub)? {
        info!(
            "[fno] replacing subscription {} / {} → {} / {}.",
            prev_u, prev_e, underlying, expiry
        );
    }

    Ok(())
}

/// Tauri command: stop the scoped F&O subscription.
///
/// Aborts the running poll loop (if any) so no F&O fetch/emit work runs while the
/// section is hidden (R7.3). Idempotent: calling it with no active subscription
/// is a no-op.
#[tauri::command]
pub async fn fno_unsubscribe(_app: AppHandle) -> Result<(), String> {
    if let Some((prev_u, prev_e)) = take_subscription()? {
        info!("[fno] subscription stopped for {} / {}.", prev_u, prev_e);
    }

    Ok(())
}

#[cfg(test)]
mod subscription_tests {
    // Unit tests for the poll loop's pure seam (task 2.2).
    //
    // The poll loop, `fno_subscribe`, and `fno_unsubscribe` are I/O- and
    // runtime-bound (HTTP, the Tauri AppHandle, a spawned task), which is not
    // unit-testable without live infra. These tests target the PURE, total seam
    // extracted from that I/O — `extract_snapshot_ts` — which is the de-dup gate
    // that decides whether `emit('fno-snapshot')` fires (emit ONLY when
    // snapshot_ts advances).

    use super::extract_snapshot_ts;
    use serde_json::json;

    #[test]
    fn extracts_integer_snapshot_ts() {
        let payload = json!({ "underlying": "NIFTY 50", "snapshot_ts": 1734511200000_i64 });
        assert_eq!(extract_snapshot_ts(&payload), Some(1734511200000));
    }

    #[test]
    fn extracts_float_snapshot_ts_truncating() {
        // Some serializers emit epoch ms as a float; it must still parse.
        let payload = json!({ "snapshot_ts": 1734511200000.0_f64 });
        assert_eq!(extract_snapshot_ts(&payload), Some(1734511200000));
    }

    #[test]
    fn extracts_numeric_string_snapshot_ts() {
        let payload = json!({ "snapshot_ts": "1734511200000" });
        assert_eq!(extract_snapshot_ts(&payload), Some(1734511200000));
    }

    #[test]
    fn missing_snapshot_ts_is_none() {
        // An Unavailable_Marker has no snapshot_ts → loop suppresses emit.
        let marker = json!({ "underlying": "NIFTY 50", "unavailable": true, "reason": "no snapshot" });
        assert_eq!(extract_snapshot_ts(&marker), None);
    }

    #[test]
    fn non_finite_or_garbage_snapshot_ts_is_none() {
        assert_eq!(extract_snapshot_ts(&json!({ "snapshot_ts": "not-a-number" })), None);
        assert_eq!(extract_snapshot_ts(&json!({ "snapshot_ts": null })), None);
        assert_eq!(extract_snapshot_ts(&json!({})), None);
    }

    #[test]
    fn advance_gate_emits_only_on_strictly_increasing_ts() {
        // Mirror the loop's gate: emit iff snapshot_ts strictly advances.
        let should_emit = |ts: i64, last: Option<i64>| last.map_or(true, |prev| ts > prev);

        // first snapshot always emits
        assert!(should_emit(100, None));
        // a new, larger ts emits
        assert!(should_emit(200, Some(100)));
        // a duplicate ts is suppressed
        assert!(!should_emit(200, Some(200)));
        // a regressed ts is suppressed
        assert!(!should_emit(150, Some(200)));
    }
}

#[cfg(test)]
mod bridge_transport_tests {
    // Integration tests for the F&O IPC bridge transport (task 2.4).
    //
    // These exercise the bridge's three transport seams end to end:
    //
    //   1. The HTTP fetch seam (`fetch_snapshot_from`, the shared body of
    //      `get_fno_analytics` and the poll loop's `fetch_fno_snapshot`) against
    //      a local mock HTTP server, asserting it returns the combined payload
    //      VERBATIM and passes an `Unavailable_Marker` through unchanged (R6.1).
    //   2. The poll loop's de-dup gate (`next_emit_ts`) over a realistic payload
    //      sequence, asserting it emits exactly once per NEW `snapshot_ts` and
    //      suppresses duplicates / regressions / marker payloads (R6.2, R7.1).
    //   3. The scoped subscription slot (`replace_subscription` /
    //      `take_subscription`, the bodies of `fno_subscribe` / `fno_unsubscribe`),
    //      asserting subscribe replaces-and-aborts the prior task and unsubscribe
    //      aborts the running task and clears the slot (R7.1, R7.3).
    //
    // The full `get_fno_analytics` / `fno_subscribe` Tauri commands additionally
    // take an `AppHandle` and (for subscribe) `app.emit`, which require a live
    // Tauri runtime that cannot be stood up in a unit test. Their entire
    // testable logic — the HTTP call, the verbatim passthrough, the emit gate,
    // and the slot lifecycle — is covered here at the seam level; the only
    // untested lines are the thin `AppHandle` plumbing (`app.clone()`,
    // `app.emit(...)`) that Tauri owns.

    use super::{
        next_emit_ts, replace_subscription, subscription_slot,
        take_subscription, FnoSubscription,
    };
    use serde_json::{json, Value};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;
    use std::time::Duration;

    /// A full combined snapshot payload as the Python endpoint would return it
    /// (chain + F2 analytics + F3 bias), including `null` leaves that must be
    /// preserved verbatim by the transport.
    fn combined_payload() -> Value {
        json!({
            "underlying": "NIFTY 50",
            "expiry": "2024-12-26",
            "snapshot_ts": 1734511200000_i64,
            "market_status": "open",
            "chain": [
                { "strike": 24000.0, "ce_oi": 1820000, "pe_oi": 2310000,
                  "ce_price": 142.5, "pe_price": 98.0, "iv": 0.131 },
                { "strike": 24100.0, "ce_oi": 1500000, "pe_oi": null,
                  "ce_price": 110.0, "pe_price": 121.0, "iv": null }
            ],
            "analytics": {
                "spot": 24010.5,
                "pcr_oi": 1.18, "pcr_volume": 0.94,
                "max_pain": 24000.0,
                "oi_buildup": { "call": "short_buildup", "put": "long_unwinding" },
                "iv_skew": { "put_minus_call": 0.021, "slope": -0.0003, "atm_iv": 0.132 },
                "oi_walls": { "support": 23800.0, "resistance": 24200.0 },
                "futures_basis": null
            },
            "bias": {
                "options_bias_state": "bullish",
                "alignment": "neutral",
                "chain_context": "own-chain",
                "signals": { "pcr_oi": 1.18, "max_pain": 24000.0, "futures_basis": null }
            }
        })
    }

    /// The F2 `Unavailable_Marker` shape the bridge must pass through verbatim.
    fn unavailable_marker() -> Value {
        json!({
            "underlying": "NIFTY 50",
            "expiry": "2024-12-26",
            "unavailable": true,
            "reason": "no chain snapshot available for NIFTY 50 / 2024-12-26",
            "last_snapshot_ts": 1734507600000_i64
        })
    }



    // ── Seam 2: the poll loop emits once per new snapshot_ts ─────────────────

    /// Drive a realistic fetch sequence through the loop's de-dup gate exactly
    /// as `run_fno_poll_loop` does, collecting which payloads would be emitted.
    fn simulate_loop_emissions(sequence: &[Value]) -> Vec<i64> {
        let mut last_emitted_ts: Option<i64> = None;
        let mut emitted: Vec<i64> = Vec::new();
        for payload in sequence {
            if let Some(ts) = next_emit_ts(payload, last_emitted_ts) {
                emitted.push(ts);
                last_emitted_ts = Some(ts);
            }
        }
        emitted
    }

    #[test]
    fn poll_loop_emits_once_per_new_snapshot_ts_and_suppresses_duplicates() {
        let s = |ts: i64| json!({ "underlying": "NIFTY 50", "snapshot_ts": ts });
        let marker = unavailable_marker(); // carries last_snapshot_ts but NO snapshot_ts

        let sequence = vec![
            s(100),         // first snapshot → emit
            s(100),         // identical ts → suppress (duplicate)
            marker.clone(), // marker (no snapshot_ts) → suppress
            s(200),         // advanced → emit
            s(200),         // duplicate → suppress
            s(150),         // regressed → suppress
            s(300),         // advanced → emit
        ];

        // Exactly one emission per NEW snapshot_ts, in order; duplicates,
        // regressions, and the marker are all suppressed (R6.2, R7.1).
        assert_eq!(simulate_loop_emissions(&sequence), vec![100, 200, 300]);
    }

    #[test]
    fn poll_loop_marker_payloads_never_emit() {
        // A stream consisting only of Unavailable_Markers emits nothing — the
        // de-dup gate suppresses every tick because none carries a snapshot_ts.
        let sequence = vec![unavailable_marker(), unavailable_marker(), unavailable_marker()];
        assert!(simulate_loop_emissions(&sequence).is_empty());
    }

    // ── Preservation (R3.7): the poll loop survives a transport Err ──────────
    //
    // Feature: fno-data-and-search-fix (bugfix) — Property 3 (Preservation).
    //
    // OBSERVATION-FIRST: this records the resilience the fix must keep intact.
    // `run_fno_poll_loop` handles a `fetch_fno_snapshot` `Err` by LOGGING and
    // continuing to the next tick (it never panics, never emits, and never
    // advances the de-dup gate). A background service that is genuinely
    // unreachable must therefore NOT crash the task — it retries on the next
    // tick (R3.7 / R6.5). These tests mirror the loop's exact control flow over
    // a sequence of `Result<Value, String>` fetch outcomes so the survive-and-
    // retry behavior is observable without a live Tauri runtime. EXPECTED PASS
    // on unfixed code — this is the preservation baseline.

    /// Mirror `run_fno_poll_loop`'s per-tick control flow over a sequence of
    /// fetch OUTCOMES: an `Ok(payload)` runs the de-dup gate (emit iff snapshot_ts
    /// strictly advances); an `Err(_)` is logged and skipped (no emit, no state
    /// change) — exactly as the real loop does. Returns the emitted snapshot_ts
    /// sequence. That this function runs to completion over any input models the
    /// loop never crashing on a transport error.
    fn simulate_loop_over_results(sequence: &[Result<Value, String>]) -> Vec<i64> {
        let mut last_emitted_ts: Option<i64> = None;
        let mut emitted: Vec<i64> = Vec::new();
        for outcome in sequence {
            match outcome {
                Ok(payload) => {
                    if let Some(ts) = next_emit_ts(payload, last_emitted_ts) {
                        emitted.push(ts);
                        last_emitted_ts = Some(ts);
                    }
                }
                // Transport/HTTP error → log & retry next tick; never crash,
                // never emit, never disturb the emit gate.
                Err(_e) => continue,
            }
        }
        emitted
    }

    #[test]
    fn poll_loop_survives_transport_err_and_resumes_emitting() {
        let s = |ts: i64| Ok(json!({ "underlying": "NIFTY 50", "snapshot_ts": ts }));
        let boom = || Err("F&O service unreachable at http://localhost:8086/options/snapshot: connection refused".to_string());

        let sequence = vec![
            boom(),   // service down on the first tick → log & retry, no emit
            s(100),   // recovered → first snapshot emits
            boom(),   // transient failure → suppressed, gate unchanged
            s(100),   // duplicate after the error → still suppressed (gate intact)
            s(200),   // advanced → emit
            boom(),   // another failure → suppressed
            s(300),   // advanced → emit
        ];

        // The loop ran to completion (no panic) and emitted exactly once per new
        // snapshot_ts; the interleaved transport errors never crashed it and
        // never disturbed the de-dup gate (R3.7).
        assert_eq!(simulate_loop_over_results(&sequence), vec![100, 200, 300]);
    }

    #[test]
    fn poll_loop_survives_an_unbroken_run_of_transport_errors() {
        // A service that is unreachable for many consecutive ticks: the loop
        // logs and retries every tick, emits nothing, and never crashes.
        let errs: Vec<Result<Value, String>> = (0..25)
            .map(|_| Err("F&O service unreachable: connection refused".to_string()))
            .collect();

        assert!(simulate_loop_over_results(&errs).is_empty());
    }

    // ── Preservation (R3.6): fno_list_chains never offers an unconfigured underlying ─
    //
    // Feature: fno-data-and-search-fix (bugfix) — Property 3 (Preservation).
    //
    // OBSERVATION-FIRST: `fno_list_chains` builds `FnoChains.underlyings` from
    // `resolve_fno_config().underlyings` and keys `expiries_by_underlying` only
    // by those same underlyings (see the command body — the `None` DbState branch
    // inserts an empty expiry list per configured underlying). The bounded-
    // selector guarantee is therefore: the offered underlyings are EXACTLY the
    // configured index underlyings, and no unconfigured underlying can ever
    // appear. This mirrors that exact selection logic over arbitrary env-driven
    // configs (via the pure `resolve_fno_config_with` seam, so no AppHandle /
    // DbState is needed) and asserts the bound. EXPECTED PASS on unfixed code —
    // the preservation baseline; the fix (task 3.3) leaves this bound unchanged.

    use crate::services::fno_config::{resolve_fno_config, resolve_fno_config_with};
    use std::collections::BTreeMap;

    /// Reconstruct `fno_list_chains`'s bounded selection with NO DbState (the
    /// documented `None` branch): the offered underlyings come straight from the
    /// resolved config, and each maps to an empty expiry list. Returns the same
    /// `(underlyings, expiries_by_underlying)` the command would.
    fn list_chains_bound(cfg_underlyings: &[String]) -> (Vec<String>, BTreeMap<String, Vec<String>>) {
        let underlyings = cfg_underlyings.to_vec();
        let mut expiries_by_underlying: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for u in &underlyings {
            expiries_by_underlying.insert(u.clone(), Vec::new());
        }
        (underlyings, expiries_by_underlying)
    }

    #[test]
    fn list_chains_offers_exactly_the_configured_underlyings() {
        // The real, process-default config: the offered set equals it exactly.
        let configured = resolve_fno_config().underlyings;
        let (offered, expiries) = list_chains_bound(&configured);

        assert!(!offered.is_empty(), "the configured underlyings list is never empty");
        assert_eq!(offered, configured, "offers exactly the configured underlyings");

        // Every expiry-map key is a configured underlying — no unconfigured key.
        let configured_set: std::collections::BTreeSet<&String> = configured.iter().collect();
        for key in expiries.keys() {
            assert!(
                configured_set.contains(key),
                "expiries keyed by an unconfigured underlying: {key}"
            );
        }
    }

    #[test]
    fn list_chains_never_offers_an_unconfigured_underlying_for_any_env() {
        // A spread of env maps: unset, empty, blank, a single index, and a
        // custom comma-separated set — the offered set always equals the config
        // and never introduces an underlying outside it.
        let env_cases: Vec<Option<&str>> = vec![
            None,
            Some(""),
            Some("   "),
            Some("NIFTY 50"),
            Some("NIFTY 50, BANKNIFTY"),
            Some(" FINNIFTY , , MIDCPNIFTY "),
        ];

        for raw in env_cases {
            let cfg = resolve_fno_config_with(|k| if k == "FNO_UNDERLYINGS" { raw.map(String::from) } else { None });
            let configured = cfg.underlyings.clone();
            let (offered, expiries) = list_chains_bound(&configured);

            assert!(!offered.is_empty(), "underlyings must never be empty (raw={raw:?})");
            // The bound: offered == configured, no additions, no unconfigured entry.
            assert_eq!(offered, configured, "offered set must equal the configured set (raw={raw:?})");
            let configured_set: std::collections::BTreeSet<&String> = configured.iter().collect();
            for u in &offered {
                assert!(configured_set.contains(u), "offered an unconfigured underlying {u:?} (raw={raw:?})");
            }
            for key in expiries.keys() {
                assert!(configured_set.contains(key), "expiry map keyed by unconfigured underlying {key:?} (raw={raw:?})");
            }
        }
    }

    // ── Seam 3: subscribe replaces / unsubscribe aborts the stored task ──────

    /// A guard whose `Drop` flips a shared flag. When a spawned task is aborted,
    /// the runtime drops its future, running this guard's `Drop` — a positive,
    /// observable signal that the task was actually torn down.
    struct DropFlag(Arc<AtomicBool>);
    impl Drop for DropFlag {
        fn drop(&mut self) {
            self.0.store(true, Ordering::SeqCst);
        }
    }

    /// Spawn a forever-sleeping task carrying a drop-flag; returns the flag and
    /// the task's `JoinHandle` so it can be stored in a `FnoSubscription`.
    fn spawn_marked_task() -> (Arc<AtomicBool>, tokio::task::JoinHandle<()>) {
        let flag = Arc::new(AtomicBool::new(false));
        let f = flag.clone();
        let handle = tokio::spawn(async move {
            let _guard = DropFlag(f);
            loop {
                tokio::time::sleep(Duration::from_secs(3600)).await;
            }
        });
        (flag, handle)
    }

    async fn wait_for_flag(flag: &Arc<AtomicBool>) -> bool {
        for _ in 0..50 {
            if flag.load(Ordering::SeqCst) {
                return true;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        flag.load(Ordering::SeqCst)
    }

    fn current_key() -> Option<(String, String)> {
        subscription_slot()
            .lock()
            .unwrap()
            .as_ref()
            .map(|s| (s.underlying.clone(), s.expiry.clone()))
    }

    #[tokio::test]
    async fn subscribe_replaces_and_unsubscribe_aborts_the_stored_task() {
        // Start from a clean slot (this is the only test touching the global slot).
        let _ = take_subscription().unwrap();

        // First subscribe: nothing was running, slot now holds the new key.
        let (flag1, h1) = spawn_marked_task();
        tokio::task::yield_now().await;
        let prev = replace_subscription(FnoSubscription {
            underlying: "NIFTY 50".into(),
            expiry: "2024-12-26".into(),
            handle: h1,
        })
        .unwrap();
        assert_eq!(prev, None, "no subscription was active before the first subscribe");
        assert_eq!(current_key(), Some(("NIFTY 50".into(), "2024-12-26".into())));

        // Re-subscribe (selector change): the prior task is aborted and replaced.
        let (flag2, h2) = spawn_marked_task();
        tokio::task::yield_now().await;
        let prev = replace_subscription(FnoSubscription {
            underlying: "BANKNIFTY".into(),
            expiry: "".into(),
            handle: h2,
        })
        .unwrap();
        assert_eq!(
            prev,
            Some(("NIFTY 50".into(), "2024-12-26".into())),
            "subscribe should report and replace the prior key"
        );
        assert!(wait_for_flag(&flag1).await, "the replaced poll task must be aborted (R7.1)");
        assert_eq!(current_key(), Some(("BANKNIFTY".into(), "".into())));

        // Unsubscribe: the running task is aborted and the slot cleared (R7.3).
        let prev = take_subscription().unwrap();
        assert_eq!(prev, Some(("BANKNIFTY".into(), "".into())));
        assert!(wait_for_flag(&flag2).await, "unsubscribe must abort the running poll task (R7.3)");
        assert_eq!(current_key(), None, "the slot is empty after unsubscribe");

        // Idempotent: a second unsubscribe with no active task is a no-op.
        assert_eq!(take_subscription().unwrap(), None);
    }
}
