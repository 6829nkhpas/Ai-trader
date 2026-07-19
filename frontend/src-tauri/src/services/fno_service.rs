// src/services/fno_service.rs — F&O Snapshot and Analytics Ingestion service (Rust rewrite)
//
// Fetches F&O snapshot and analytics data:
//   1. Tries to pull real-time quotes/OI from Zerodha Kite Connect API.
//   2. Falls back to QuestDB `option_chain_snapshots` table if Kite API is unreachable or mock is active.
//   3. Computes PCR, Max Pain, and Support/Resistance (OI Walls) in Rust.
//   4. Formats a payload compatible with frontend expectations.

use std::collections::HashMap;
use chrono::Datelike;
use serde::{Deserialize, Serialize};
use sqlx::PgPool;
use log::{info, warn, error};

use crate::db::DbState;
use crate::commands::charts::get_kite_credentials;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FnoChainRow {
    pub strike: f64,
    pub ce_oi: Option<u64>,
    pub pe_oi: Option<u64>,
    pub ce_price: Option<f64>,
    pub pe_price: Option<f64>,
    pub iv: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FnoOiBuildup {
    pub call: Option<String>,
    pub put: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FnoIvSkew {
    pub put_minus_call: Option<f64>,
    pub slope: Option<f64>,
    pub atm_iv: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FnoOiWalls {
    pub support: Option<f64>,
    pub resistance: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FnoAnalytics {
    pub spot: Option<f64>,
    pub pcr_oi: Option<f64>,
    pub pcr_volume: Option<f64>,
    pub max_pain: Option<f64>,
    pub oi_buildup: FnoOiBuildup,
    pub iv_skew: FnoIvSkew,
    pub oi_walls: FnoOiWalls,
    pub futures_basis: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FnoBias {
    pub options_bias_state: Option<String>,
    pub alignment: Option<String>,
    pub chain_context: String,
    pub signals: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FnoPayload {
    pub underlying: String,
    pub expiry: String,
    pub snapshot_ts: i64,
    pub market_status: String,
    pub chain: Vec<FnoChainRow>,
    pub analytics: FnoAnalytics,
    pub bias: FnoBias,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FnoUnavailableMarker {
    pub underlying: String,
    pub expiry: String,
    pub unavailable: bool,
    pub reason: String,
    pub reason_code: String,
    pub last_snapshot_ts: Option<i64>,
}

#[derive(Debug, Clone, sqlx::FromRow)]
pub struct DbSnapshotRow {
    pub strike: f64,
    pub option_type: String,
    pub last_price: Option<f64>,
    pub open_interest: Option<i64>,
    pub ts: Option<i64>,
}

// ── Kite API Response Types ──

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
struct KiteQuoteResponse {
    status: String,
    data: HashMap<String, KiteQuoteData>,
}

#[derive(Debug, Deserialize)]
struct KiteQuoteData {
    last_price: f64,
    oi: Option<u64>,
}

// ── Service Helpers ──

pub fn resolve_nearest_expiry(db_state: &DbState, underlying: &str) -> Option<String> {
    let conn = db_state.conn.lock().ok()?;
    let nfo_name = crate::services::option_chain_subscriber::resolve_nfo_underlying_name(underlying);

    let mut stmt = conn.prepare(
        "SELECT DISTINCT expiry FROM nfo_instruments \
         WHERE underlying = ?1 AND instrument_type IN ('CE', 'PE') \
         ORDER BY expiry ASC"
    ).ok()?;

    let rows = stmt.query_map([nfo_name.as_str()], |row| row.get::<_, String>(0)).ok()?;
    let today = chrono::Local::now().date_naive().format("%Y-%m-%d").to_string();

    let mut futures: Vec<String> = Vec::new();
    let mut all: Vec<String> = Vec::new();
    for expiry in rows.flatten() {
        let trimmed = expiry.trim().to_string();
        if trimmed.is_empty() {
            continue;
        }
        all.push(trimmed.clone());
        if trimmed >= today {
            futures.push(trimmed);
        }
    }

    if !futures.is_empty() {
        return futures.first().cloned();
    }
    all.last().cloned()
}

pub fn load_instruments_for_expiry(
    db_state: &DbState,
    underlying: &str,
    expiry: &str,
) -> Vec<(u32, String, String, f64)> {
    let conn = match db_state.conn.lock() {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    
    let nfo_name = crate::services::option_chain_subscriber::resolve_nfo_underlying_name(underlying);
    
    let mut stmt = match conn.prepare(
        "SELECT instrument_token, tradingsymbol, instrument_type, strike \
         FROM nfo_instruments \
         WHERE underlying = ?1 AND expiry = ?2 AND instrument_type IN ('CE', 'PE') \
         ORDER BY strike ASC"
    ) {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    
    let rows = stmt.query_map([nfo_name.as_str(), expiry], |row| {
        let token: i64 = row.get(0)?;
        let symbol: String = row.get(1)?;
        let inst_type: String = row.get(2)?;
        let strike: f64 = row.get(3)?;
        Ok((token as u32, symbol, inst_type, strike))
    });
    
    match rows {
        Ok(r) => r.flatten().collect(),
        Err(_) => Vec::new(),
    }
}

fn map_spot_quote_symbol(underlying: &str) -> String {
    match underlying.to_uppercase().as_str() {
        "NIFTY 50" | "NIFTY" => "NSE:NIFTY 50".to_string(),
        "BANKNIFTY" | "NIFTY BANK" => "NSE:NIFTY BANK".to_string(),
        "FINNIFTY" | "NIFTY FIN SERVICE" => "NSE:NIFTY FIN SERVICE".to_string(),
        "MIDCPNIFTY" | "NIFTY MIDCAP SELECT" => "NSE:NIFTY MIDCAP SELECT".to_string(),
        other => format!("NSE:{}", other),
    }
}

/// Returns the alternative underlying name for QuestDB backward compatibility.
/// Old snapshots may have been stored as "NIFTY 50" while new ones use "NIFTY".
fn underlying_alt_name(underlying: &str) -> String {
    match underlying.to_uppercase().as_str() {
        "NIFTY" => "NIFTY 50".to_string(),
        "NIFTY 50" => "NIFTY".to_string(),
        "BANKNIFTY" => "NIFTY BANK".to_string(),
        "NIFTY BANK" => "BANKNIFTY".to_string(),
        "FINNIFTY" => "NIFTY FIN SERVICE".to_string(),
        "NIFTY FIN SERVICE" => "FINNIFTY".to_string(),
        "MIDCPNIFTY" => "NIFTY MIDCAP SELECT".to_string(),
        "NIFTY MIDCAP SELECT" => "MIDCPNIFTY".to_string(),
        other => other.to_string(),
    }
}

// ── Analytics Computation ──

fn compute_pcr(chain: &[FnoChainRow]) -> Option<f64> {
    let ce_sum: u64 = chain.iter().map(|r| r.ce_oi.unwrap_or(0)).sum();
    let pe_sum: u64 = chain.iter().map(|r| r.pe_oi.unwrap_or(0)).sum();
    if ce_sum == 0 {
        None
    } else {
        Some(pe_sum as f64 / ce_sum as f64)
    }
}

fn compute_max_pain(chain: &[FnoChainRow]) -> Option<f64> {
    if chain.is_empty() {
        return None;
    }
    let mut min_pain = f64::INFINITY;
    let mut max_pain_strike = None;

    for candidate in chain {
        let mut pain = 0.0;
        for row in chain {
            let ce_oi = row.ce_oi.unwrap_or(0) as f64;
            let pe_oi = row.pe_oi.unwrap_or(0) as f64;
            if candidate.strike > row.strike {
                pain += (candidate.strike - row.strike) * ce_oi;
            } else if candidate.strike < row.strike {
                pain += (row.strike - candidate.strike) * pe_oi;
            }
        }
        if pain < min_pain {
            min_pain = pain;
            max_pain_strike = Some(candidate.strike);
        }
    }
    max_pain_strike
}

fn compute_oi_walls(chain: &[FnoChainRow]) -> (Option<f64>, Option<f64>) {
    let mut max_ce = 0;
    let mut resistance = None;
    let mut max_pe = 0;
    let mut support = None;

    for row in chain {
        if let Some(ce) = row.ce_oi {
            if ce > max_ce {
                max_ce = ce;
                resistance = Some(row.strike);
            }
        }
        if let Some(pe) = row.pe_oi {
            if pe > max_pe {
                max_pe = pe;
                support = Some(row.strike);
            }
        }
    }
    (support, resistance)
}

fn get_market_status() -> String {
    // Return "open" during market hours, else "closed"
    let now = chrono::Local::now();
    let weekday = now.weekday();
    if weekday == chrono::Weekday::Sat || weekday == chrono::Weekday::Sun {
        return "closed".to_string();
    }
    let time = now.time();
    let open = chrono::NaiveTime::from_hms_opt(9, 15, 0).unwrap();
    let close = chrono::NaiveTime::from_hms_opt(15, 30, 0).unwrap();
    if time >= open && time <= close {
        "open".to_string()
    } else {
        "closed".to_string()
    }
}

// ── Core Fetching Entry Point ──

pub async fn build_fno_snapshot(
    db_state: &DbState,
    pool: &PgPool,
    underlying: &str,
    expiry_opt: &str,
) -> Result<serde_json::Value, String> {
    let expiry = if expiry_opt.trim().is_empty() {
        match resolve_nearest_expiry(db_state, underlying) {
            Some(e) => e,
            None => match resolve_latest_expiry_from_questdb(pool, underlying).await {
                Some(e) => {
                    info!(
                        "[fno_service] SQLite nfo_instruments had no expiry for {}; \
                         falling back to latest QuestDB expiry {}.",
                        underlying, e
                    );
                    e
                }
                None => {
                    let marker = FnoUnavailableMarker {
                        underlying: underlying.to_string(),
                        expiry: String::new(),
                        unavailable: true,
                        reason: format!(
                            "No expiry found for underlying: {}. Run `run_nfo_sync` or wait for \
                             the NFO instrument master to populate, then retry.",
                            underlying
                        ),
                        reason_code: "no_expiry".to_string(),
                        last_snapshot_ts: None,
                    };
                    return Ok(serde_json::to_value(marker).unwrap());
                }
            },
        }
    } else {
        expiry_opt.trim().to_string()
    };

    // MOCK_BROKER is for profile/auth only — always call Kite API for F&O data
    let (api_key, access_token) = get_kite_credentials();
    
    let mut quote_success = false;
    let mut chain = Vec::new();
    let mut spot = None;
    let now_ms = chrono::Utc::now().timestamp_millis();

    if !api_key.is_empty() && !access_token.is_empty() {
        let instruments = load_instruments_for_expiry(db_state, underlying, &expiry);
        if !instruments.is_empty() {
            let mut instrument_map = HashMap::new();
            let mut symbols_to_query = Vec::new();

            for (_, sym, inst_type, strike) in &instruments {
                let kite_symbol = format!("NFO:{}", sym);
                symbols_to_query.push(kite_symbol.clone());
                instrument_map.insert(kite_symbol, (inst_type.clone(), *strike));
            }

            let spot_symbol = map_spot_quote_symbol(underlying);
            symbols_to_query.push(spot_symbol.clone());

            info!("[fno_service] Querying Kite Quote API for F&O chain ({} symbols)", symbols_to_query.len());

            match fetch_kite_quotes_api(&api_key, &access_token, &symbols_to_query).await {
                Ok(quotes) => {
                    let mut strike_rows: HashMap<String, FnoChainRow> = HashMap::new();
                    
                    // Parse quotes
                    if let Some(spot_data) = quotes.get(&spot_symbol) {
                        spot = Some(spot_data.last_price);
                    }

                    for (kite_symbol, quote) in &quotes {
                        if let Some((inst_type, strike)) = instrument_map.get(kite_symbol) {
                            let strike_key = format!("{:.2}", strike);
                            let entry = strike_rows.entry(strike_key).or_insert_with(|| FnoChainRow {
                                strike: *strike,
                                ce_oi: None,
                                pe_oi: None,
                                ce_price: None,
                                pe_price: None,
                                iv: None,
                            });

                            if inst_type == "CE" {
                                entry.ce_oi = quote.oi;
                                entry.ce_price = Some(quote.last_price);
                            } else if inst_type == "PE" {
                                entry.pe_oi = quote.oi;
                                entry.pe_price = Some(quote.last_price);
                            }
                        }
                    }

                    chain = strike_rows.into_values().collect();
                    chain.sort_by(|a, b| a.strike.partial_cmp(&b.strike).unwrap());
                    quote_success = !chain.is_empty();

                    if quote_success {
                        let pool_clone = pool.clone();
                        let underlying_clone = underlying.to_string();
                        let expiry_clone = expiry.clone();
                        let chain_clone = chain.clone();
                        let instruments_clone = instruments.clone();
                        tauri::async_runtime::spawn(async move {
                            write_snapshot_to_questdb(&pool_clone, &underlying_clone, &expiry_clone, &chain_clone, &instruments_clone).await;
                        });
                    }
                }
                Err(err) => {
                    warn!("[fno_service] Kite Quote API call failed: {}. Falling back to QuestDB.", err);
                }
            }
        }
    }

    if !quote_success {
        info!("[fno_service] Loading snapshot from QuestDB option_chain_snapshots for {} / {}", underlying, expiry);
        match fetch_snapshots_from_questdb(pool, underlying, &expiry).await {
            Ok(rows) if !rows.is_empty() => {
                let mut strike_rows: HashMap<String, FnoChainRow> = HashMap::new();
                for row in rows {
                    let strike_key = format!("{:.2}", row.strike);
                    let entry = strike_rows.entry(strike_key).or_insert_with(|| FnoChainRow {
                        strike: row.strike,
                        ce_oi: None,
                        pe_oi: None,
                        ce_price: None,
                        pe_price: None,
                        iv: None,
                    });

                    if row.option_type == "CE" {
                        entry.ce_oi = row.open_interest.map(|oi| oi as u64);
                        entry.ce_price = row.last_price;
                    } else if row.option_type == "PE" {
                        entry.pe_oi = row.open_interest.map(|oi| oi as u64);
                        entry.pe_price = row.last_price;
                    }
                }

                chain = strike_rows.into_values().collect();
                chain.sort_by(|a, b| a.strike.partial_cmp(&b.strike).unwrap());

                // Fetch spot from live_ticks
                spot = read_spot_from_live_ticks(pool, underlying).await;
            }
            Ok(_) => {
                warn!("[fno_service] No snapshots found in QuestDB option_chain_snapshots.");
            }
            Err(err) => {
                error!("[fno_service] QuestDB fetch error: {}", err);
            }
        }
    }

    let mut final_expiry = expiry;
    if chain.is_empty() {
        if let Some(fallback_expiry) = resolve_latest_expiry_from_questdb(pool, underlying).await {
            info!("[fno_service] Future expiry has no data. Falling back to latest QuestDB expiry: {}", fallback_expiry);
            if let Ok(rows) = fetch_snapshots_from_questdb(pool, underlying, &fallback_expiry).await {
                if !rows.is_empty() {
                    let mut strike_rows: HashMap<String, FnoChainRow> = HashMap::new();
                    for row in rows {
                        let strike_key = format!("{:.2}", row.strike);
                        let entry = strike_rows.entry(strike_key).or_insert_with(|| FnoChainRow {
                            strike: row.strike,
                            ce_oi: None,
                            pe_oi: None,
                            ce_price: None,
                            pe_price: None,
                            iv: None,
                        });

                        if row.option_type == "CE" {
                            entry.ce_oi = row.open_interest.map(|oi| oi as u64);
                            entry.ce_price = row.last_price;
                        } else if row.option_type == "PE" {
                            entry.pe_oi = row.open_interest.map(|oi| oi as u64);
                            entry.pe_price = row.last_price;
                        }
                    }

                    chain = strike_rows.into_values().collect();
                    chain.sort_by(|a, b| a.strike.partial_cmp(&b.strike).unwrap());
                    final_expiry = fallback_expiry;
                    spot = read_spot_from_live_ticks(pool, underlying).await;
                }
            }
        }
    }

    if chain.is_empty() {
        let marker = FnoUnavailableMarker {
            underlying: underlying.to_string(),
            expiry: final_expiry,
            unavailable: true,
            reason: format!("No F&O snapshot or quote data available for {} / {}", underlying, expiry_opt),
            reason_code: "no_data".to_string(),
            last_snapshot_ts: None,
        };
        return Ok(serde_json::to_value(marker).unwrap());
    }

    // Compute analytics
    let (support, resistance) = compute_oi_walls(&chain);
    let pcr = compute_pcr(&chain);
    let max_pain = compute_max_pain(&chain);

    let payload = FnoPayload {
        underlying: underlying.to_string(),
        expiry: final_expiry,
        snapshot_ts: now_ms,
        market_status: get_market_status(),
        chain,
        analytics: FnoAnalytics {
            spot,
            pcr_oi: pcr,
            pcr_volume: None,
            max_pain,
            oi_buildup: FnoOiBuildup { call: None, put: None },
            iv_skew: FnoIvSkew {
                put_minus_call: None,
                slope: None,
                atm_iv: None,
            },
            oi_walls: FnoOiWalls { support, resistance },
            futures_basis: None,
        },
        bias: FnoBias {
            options_bias_state: Some("neutral".to_string()),
            alignment: Some("neutral".to_string()),
            chain_context: "own-chain".to_string(),
            signals: serde_json::json!({}),
        },
    };

    Ok(serde_json::to_value(payload).unwrap())
}

async fn fetch_kite_quotes_api(
    api_key: &str,
    access_token: &str,
    symbols: &[String],
) -> Result<HashMap<String, KiteQuoteData>, String> {
    let client = reqwest::Client::new();
    let url = "https://api.kite.trade/quote";
    
    // Group symbols into chunks of 500
    let mut all_quotes = HashMap::new();
    for chunk in symbols.chunks(500) {
        let mut query = Vec::new();
        for sym in chunk {
            query.push(("i", sym.clone()));
        }

        let resp = client
            .get(url)
            .query(&query)
            .header("Authorization", format!("token {}:{}", api_key, access_token))
            .header("X-Kite-Version", "3")
            .send()
            .await
            .map_err(|e| format!("HTTP request failed: {}", e))?;

        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(format!("Kite API returned status {}: {}", status, text));
        }

        let body: KiteQuoteResponse = resp
            .json()
            .await
            .map_err(|e| format!("JSON parse failed: {}", e))?;

        for (k, v) in body.data {
            all_quotes.insert(k, v);
        }
    }

    Ok(all_quotes)
}

pub async fn fetch_snapshots_from_questdb(
    pool: &PgPool,
    underlying: &str,
    expiry: &str,
) -> Result<Vec<DbSnapshotRow>, String> {
    let alt = underlying_alt_name(underlying);
    let query = "
        SELECT strike, option_type, last_price, open_interest, cast(snapshot_ts AS LONG) as ts \
        FROM option_chain_snapshots \
        WHERE (underlying = $1 OR underlying = $2) AND expiry = $3 AND snapshot_ts = ( \
            SELECT max(snapshot_ts) FROM option_chain_snapshots WHERE (underlying = $1 OR underlying = $2) AND expiry = $3 \
        ) \
        ORDER BY strike ASC
    ";
    
    let rows = sqlx::query_as::<_, DbSnapshotRow>(query)
        .bind(underlying)
        .bind(&alt)
        .bind(expiry)
        .fetch_all(pool)
        .await
        .map_err(|e| format!("Failed to query QuestDB: {}", e))?;
        
    Ok(rows)
}

async fn read_spot_from_live_ticks(pool: &PgPool, underlying: &str) -> Option<f64> {
    // Try multiple symbol name variants since live_ticks may store the
    // Kite-style symbol ("NSE:NIFTY 50") while we receive the NFO name ("NIFTY").
    let mapped = map_spot_quote_symbol(underlying);
    let candidates = [mapped.as_str(), underlying];

    let query = "SELECT last_traded_price \
                 FROM live_ticks \
                 WHERE symbol = $1 \
                 ORDER BY timestamp DESC \
                 LIMIT 1";

    for sym in &candidates {
        if let Ok(Some(row)) = sqlx::query(query).bind(*sym).fetch_optional(pool).await {
            use sqlx::Row;
            if let Ok(price) = row.try_get::<f64, _>("last_traded_price") {
                if price.is_finite() && price > 0.0 {
                    return Some(price);
                }
            }
        }
    }

    // Fallback 1: check historical_intraday
    let query_intra = "SELECT close \
                       FROM historical_intraday \
                       WHERE symbol = $1 \
                       ORDER BY ts DESC \
                       LIMIT 1";
    for sym in &candidates {
        if let Ok(Some(row)) = sqlx::query(query_intra).bind(*sym).fetch_optional(pool).await {
            use sqlx::Row;
            if let Ok(price) = row.try_get::<f64, _>("close") {
                if price.is_finite() && price > 0.0 {
                    return Some(price);
                }
            }
        }
    }

    // Fallback 2: check historical_candles
    let query_candles = "SELECT close \
                         FROM historical_candles \
                         WHERE symbol = $1 \
                         ORDER BY ts DESC \
                         LIMIT 1";
    for sym in &candidates {
        if let Ok(Some(row)) = sqlx::query(query_candles).bind(*sym).fetch_optional(pool).await {
            use sqlx::Row;
            if let Ok(price) = row.try_get::<f64, _>("close") {
                if price.is_finite() && price > 0.0 {
                    return Some(price);
                }
            }
        }
    }

    None
}

pub async fn resolve_latest_expiry_from_questdb(pool: &PgPool, underlying: &str) -> Option<String> {
    let alt = underlying_alt_name(underlying);
    let query = "
        SELECT expiry FROM option_chain_snapshots \
        WHERE (underlying = $1 OR underlying = $2) \
        ORDER BY snapshot_ts DESC \
        LIMIT 1
    ";
    match sqlx::query_scalar::<_, String>(query)
        .bind(underlying)
        .bind(&alt)
        .fetch_optional(pool)
        .await
    {
        Ok(Some(expiry)) => Some(expiry),
        _ => None,
    }
}

pub async fn fetch_expiries_from_questdb(
    pool: &PgPool,
    underlying: &str,
) -> Result<Vec<String>, String> {
    let alt = underlying_alt_name(underlying);
    let query = "
        SELECT DISTINCT expiry FROM option_chain_snapshots \
        WHERE (underlying = $1 OR underlying = $2) \
        ORDER BY expiry ASC
    ";
    
    let rows = sqlx::query_scalar::<_, String>(query)
        .bind(underlying)
        .bind(&alt)
        .fetch_all(pool)
        .await
        .map_err(|e| format!("Failed to query QuestDB expiries: {}", e))?;
        
    Ok(rows)
}

pub async fn write_snapshot_to_questdb(
    pool: &PgPool,
    underlying: &str,
    expiry: &str,
    chain: &[FnoChainRow],
    instruments: &[(u32, String, String, f64)],
) {
    let now_micros = chrono::Utc::now().timestamp_micros();
    
    // Create a map from strike & option_type to tradingsymbol
    let mut symbol_map = HashMap::new();
    for (_, sym, inst_type, strike) in instruments {
        symbol_map.insert(format!("{:.2}:{}", strike, inst_type), sym.clone());
    }

    for row in chain {
        let strike_key_ce = format!("{:.2}:CE", row.strike);
        let ce_symbol = symbol_map.get(&strike_key_ce);
        if let (Some(symbol), Some(price)) = (ce_symbol, row.ce_price) {
            let ce_oi = row.ce_oi.map(|oi| oi as i64);
            let _ = sqlx::query(
                "INSERT INTO option_chain_snapshots \
                 (underlying, expiry, strike, option_type, symbol, last_price, open_interest, snapshot_ts) \
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
            )
            .bind(underlying)
            .bind(expiry)
            .bind(row.strike)
            .bind("CE")
            .bind(symbol)
            .bind(price)
            .bind(ce_oi)
            .bind(now_micros)
            .execute(pool)
            .await;
        }

        let strike_key_pe = format!("{:.2}:PE", row.strike);
        let pe_symbol = symbol_map.get(&strike_key_pe);
        if let (Some(symbol), Some(price)) = (pe_symbol, row.pe_price) {
            let pe_oi = row.pe_oi.map(|oi| oi as i64);
            let _ = sqlx::query(
                "INSERT INTO option_chain_snapshots \
                 (underlying, expiry, strike, option_type, symbol, last_price, open_interest, snapshot_ts) \
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
            )
            .bind(underlying)
            .bind(expiry)
            .bind(row.strike)
            .bind("PE")
            .bind(symbol)
            .bind(price)
            .bind(pe_oi)
            .bind(now_micros)
            .execute(pool)
            .await;
        }
    }
    
    info!("[fno_service] Wrote F&O quote snapshot to QuestDB for {} / {}", underlying, expiry);
}


