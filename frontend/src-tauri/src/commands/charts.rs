// src/commands/charts.rs — Binary Historical Data Resolver
//
// Tauri IPC command that queries QuestDB for 5 years of daily OHLCV data
// and serializes the result as a raw binary buffer using bincode.
//
// ── Zero-Latency Transfer ───────────────────────────────────────────────────
//   JSON serialization of 5 years (~1250 candles) adds measurable overhead.
//   bincode produces a compact binary representation that the frontend
//   deserializes directly into a TypedArray — eliminating JSON parse time.
//
// ── Error Handling ──────────────────────────────────────────────────────────
//   On database failure, emits a `system-error` event to the frontend
//   console (matching the Phase 1 Error Visibility pattern).

use log::{info, error};
use serde::Serialize;
use sqlx::PgPool;
use tauri::{AppHandle, Emitter};

use crate::services::history_loader;

/// A single OHLCV candle for binary serialization.
///
/// Field order matches the QuestDB query column order.
/// bincode serializes this as a fixed-size struct — no field names,
/// no delimiters, just raw bytes in order.
#[derive(Debug, Clone, Serialize, serde::Deserialize)]
pub struct BinaryCandle {
    /// Microseconds since Unix epoch (matches QuestDB TIMESTAMP)
    pub ts: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
}

/// Query QuestDB for 5 years of daily OHLCV data and return as a bincode buffer.
///
/// # Arguments (from frontend `invoke("get_historical_view", { symbol })`)
/// * `symbol` — Instrument symbol (e.g., "RELIANCE")
///
/// # Returns
/// `Vec<u8>` — bincode-serialized `Vec<BinaryCandle>`. Tauri automatically
/// converts this to a `Uint8Array` on the JavaScript side.
///
/// # Errors
/// Returns a string error AND emits a `system-error` event for the frontend
/// console to display.
#[tauri::command]
pub async fn get_historical_view(
    app: AppHandle,
    pool: tauri::State<'_, PgPool>,
    symbol: String,
) -> Result<Vec<u8>, String> {
    info!("get_historical_view: querying {} from QuestDB", symbol);

    // Query the last 5 years of daily candles, ordered chronologically
    let rows = sqlx::query(
        "SELECT ts, open, high, low, close, volume \
         FROM historical_candles \
         WHERE symbol = $1 \
         ORDER BY ts ASC",
    )
    .bind(&symbol)
    .fetch_all(pool.inner())
    .await;

    match rows {
        Ok(data) => {
            use sqlx::Row;

            let candles: Vec<BinaryCandle> = data
                .iter()
                .filter_map(|row| {
                    let ts: i64 = row.try_get("ts").ok()?;
                    let open: f64 = row.try_get("open").ok()?;
                    let high: f64 = row.try_get("high").ok()?;
                    let low: f64 = row.try_get("low").ok()?;
                    let close: f64 = row.try_get("close").ok()?;
                    let volume: i64 = row.try_get("volume").ok()?;
                    Some(BinaryCandle { ts, open, high, low, close, volume })
                })
                .collect();

            info!(
                "get_historical_view: {} — {} candles fetched, serializing with bincode.",
                symbol,
                candles.len()
            );

            // Serialize to bincode binary buffer
            let binary = bincode::serialize(&candles).map_err(|e| {
                let msg = format!("bincode serialization failed: {}", e);
                error!("{}", msg);
                broadcast_error(&app, &msg);
                msg
            })?;

            info!(
                "get_historical_view: {} — {} bytes serialized.",
                symbol,
                binary.len()
            );

            Ok(binary)
        }
        Err(e) => {
            let msg = format!("QuestDB query failed for {}: {}", symbol, e);
            error!("{}", msg);
            broadcast_error(&app, &msg);
            Err(msg)
        }
    }
}

/// Check whether the QuestDB PG pool has been registered as Tauri state.
///
/// The pool is registered asynchronously in lib.rs — the frontend should
/// call this first and wait until it returns `true` before invoking
/// `get_historical_view`. This prevents the "State not found" race condition.
#[tauri::command]
pub async fn get_pool_status(
    _pool: Option<tauri::State<'_, PgPool>>,
) -> bool {
    _pool.is_some()
}

/// Proxy a QuestDB REST API request through Rust, returning the raw JSON body.
///
/// This bypasses browser/WebView CORS restrictions entirely — the HTTP request
/// is made from the Rust process (no origin header), so QuestDB responds freely.
///
/// # Arguments (from `invoke("fetch_questdb", { query })`)
/// * `query` — SQL string to send to QuestDB REST API (/exec endpoint)
///
/// # Returns
/// Raw JSON string from QuestDB (the `{ dataset: [...] }` response).
#[tauri::command]
pub async fn fetch_questdb(query: String) -> Result<String, String> {
    let questdb_url = std::env::var("QUESTDB_HTTP_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:9000".to_string());

    let url = format!("{}/exec", questdb_url);

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()
        .map_err(|e| format!("HTTP client error: {}", e))?;

    let response = client
        .get(&url)
        .query(&[("query", &query), ("fmt", &"json".to_string())])
        .send()
        .await
        .map_err(|e| format!("QuestDB HTTP request failed: {}", e))?;

    if !response.status().is_success() {
        return Err(format!("QuestDB returned HTTP {}", response.status()));
    }

    let body = response
        .text()
        .await
        .map_err(|e| format!("Failed to read QuestDB response body: {}", e))?;

    Ok(body)
}

/// Trigger historical data ingestion from Kite API for a given symbol.
///
/// # Arguments (from frontend `invoke("load_historical", { symbol, instrumentToken })`)
/// * `symbol`           — Instrument symbol (e.g., "RELIANCE")
/// * `instrument_token` — Kite instrument token (e.g., 738561)
///
/// # Returns
/// Number of candles ingested.
#[tauri::command]
pub async fn load_historical(
    app: AppHandle,
    pool: tauri::State<'_, PgPool>,
    symbol: String,
    instrument_token: u32,
) -> Result<u64, String> {
    info!("load_historical: starting ingestion for {} (token {})", symbol, instrument_token);

    let api_key = std::env::var("KITE_API_KEY")
        .map_err(|_| "KITE_API_KEY not set in .env".to_string())?;
    let access_token = std::env::var("KITE_ACCESS_TOKEN")
        .map_err(|_| "KITE_ACCESS_TOKEN not set in .env".to_string())?;

    match history_loader::load_historical_data(
        pool.inner(),
        instrument_token,
        &symbol,
        &api_key,
        &access_token,
    )
    .await
    {
        Ok(count) => {
            info!("load_historical: {} — {} candles ingested successfully.", symbol, count);

            // Notify frontend of success
            let _ = app.emit("historical-loaded", serde_json::json!({
                "symbol": symbol,
                "count": count,
            }));

            Ok(count)
        }
        Err(e) => {
            let msg = format!("Historical ingestion failed for {}: {}", symbol, e);
            error!("{}", msg);
            broadcast_error(&app, &msg);
            Err(msg)
        }
    }
}

/// Broadcast a system-level error to the frontend console.
///
/// Matches the Phase 1 Error Visibility pattern — the frontend's
/// SystemConsole component listens for `system-error` events and
/// displays them in the diagnostic log viewer.
fn broadcast_error(app: &AppHandle, message: &str) {
    let payload = serde_json::json!({
        "level": "ERROR",
        "source": "HistoricalEngine",
        "message": message,
    });

    if let Err(e) = app.emit("system-error", payload) {
        error!("Failed to emit system-error event: {}", e);
    }
}
