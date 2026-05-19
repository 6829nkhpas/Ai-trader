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
use tauri::{AppHandle, Emitter, Manager};

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

/// Query QuestDB for historical OHLCV data, dynamically aggregated by timeframe,
/// and return as a bincode buffer.
///
/// # Arguments (from frontend `invoke("get_historical_view", { symbol, timeframe })`)
/// * `symbol`    — Instrument symbol (e.g., "RELIANCE")
/// * `timeframe` — Optional bar size: "1m", "5m", "15m", "30m", "1H", "4H", "1D",
///                 "1W". Defaults to "1D" (daily) when omitted, preserving the
///                 legacy single-arg call shape used by older UI code paths.
///
/// # Routing
/// * Intraday timeframes ("1m" .. "4H") aggregate raw `live_ticks` rows on the
///   fly using `SAMPLE BY <interval> ALIGN TO CALENDAR`. This produces an
///   honest OHLCV bar from `last_traded_price` regardless of how the ticks
///   landed.
/// * Daily and weekly timeframes ("1D", "1W") read pre-aggregated rows from
///   `historical_candles` (5-year archive backfilled on demand by
///   `load_historical`). Weekly bars are produced by sampling the daily
///   archive with `SAMPLE BY 7d`.
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
    timeframe: Option<String>,
) -> Result<Vec<u8>, String> {
    // Normalise timeframe input (accept upper/lower-case from the UI).
    let tf_raw = timeframe.unwrap_or_else(|| "1D".to_string());
    let tf = tf_raw.trim().to_string();

    // Map the UI timeframe → QuestDB SAMPLE BY interval.
    // Intraday timeframes aggregate live_ticks; daily/weekly read the archive.
    let (sample_interval, source) = match tf.to_uppercase().as_str() {
        "1M"  | "1MIN"  => ("1m",  HistorySource::Ticks),
        "5M"  | "5MIN"  => ("5m",  HistorySource::Ticks),
        "15M" | "15MIN" => ("15m", HistorySource::Ticks),
        "30M" | "30MIN" => ("30m", HistorySource::Ticks),
        "1H"  | "60M"   => ("1h",  HistorySource::Ticks),
        "4H"  | "240M"  => ("4h",  HistorySource::Ticks),
        "1D"  | "DAY"   => ("1d",  HistorySource::Daily),
        "1W"  | "WEEK"  => ("7d",  HistorySource::Daily),
        _               => ("1d",  HistorySource::Daily),
    };

    // ── DIAGNOSTIC TRACER — Tauri command boundary (UI → Rust) ──
    println!(
        "🛑 [RUST RECEIVE] Historical Request — Symbol: {}, Timeframe: {} → SAMPLE BY {} (source: {:?})",
        symbol, tf, sample_interval, source
    );

    info!(
        "get_historical_view: querying {} from QuestDB (tf={}, sample_by={}, source={:?})",
        symbol, tf, sample_interval, source
    );

    // Build the dynamic SQL based on the source table.
    //
    // Both branches return the same column set: ts, open, high, low, close, volume
    // so the row-decoder below stays uniform.
    //
    // ── Why string-format the interval but bind the symbol? ─────────────────
    // QuestDB's parser accepts an *identifier* in SAMPLE BY, not a parameter
    // placeholder, so the interval must be inlined. We control the value
    // (it's hard-coded above), so there is no SQL-injection vector. The
    // user-supplied `symbol` remains a parameterised bind ($1).
    let query = match source {
        HistorySource::Ticks => format!(
            "SELECT timestamp AS ts, \
                    first(last_traded_price) AS open, \
                    max(last_traded_price)   AS high, \
                    min(last_traded_price)   AS low, \
                    last(last_traded_price)  AS close, \
                    sum(volume)              AS volume \
             FROM live_ticks \
             WHERE symbol = $1 \
             SAMPLE BY {} ALIGN TO CALENDAR",
            sample_interval
        ),
        HistorySource::Daily if sample_interval == "1d" => {
            // Pre-aggregated daily archive — no resampling needed.
            "SELECT ts, open, high, low, close, volume \
             FROM historical_candles \
             WHERE symbol = $1 \
             ORDER BY ts ASC"
                .to_string()
        }
        HistorySource::Daily => format!(
            // Weekly view: resample daily candles into 7-day buckets.
            "SELECT ts, \
                    first(open)  AS open, \
                    max(high)    AS high, \
                    min(low)     AS low, \
                    last(close)  AS close, \
                    sum(volume)  AS volume \
             FROM historical_candles \
             WHERE symbol = $1 \
             SAMPLE BY {} ALIGN TO CALENDAR",
            sample_interval
        ),
    };

    let rows = sqlx::query(&query)
        .bind(&symbol)
        .fetch_all(pool.inner())
        .await;

    match rows {
        Ok(data) => {
            use sqlx::Row;

            let candles: Vec<BinaryCandle> = data
                .iter()
                .filter_map(|row| {
                    // QuestDB returns ts as TIMESTAMP which sqlx decodes as
                    // chrono::NaiveDateTime, NOT i64. We must extract as
                    // NaiveDateTime and convert to microseconds for bincode.
                    let ts: i64 = row
                        .try_get::<chrono::NaiveDateTime, _>("ts")
                        .ok()
                        .map(|dt| dt.and_utc().timestamp_micros())
                        .or_else(|| {
                            // Fallback: try as raw i64 in case QuestDB returns raw µs
                            row.try_get::<i64, _>("ts").ok()
                        })?;
                    let open: f64 = row.try_get("open").ok()?;
                    let high: f64 = row.try_get("high").ok()?;
                    let low: f64 = row.try_get("low").ok()?;
                    let close: f64 = row.try_get("close").ok()?;
                    let volume: i64 = row
                        .try_get::<i64, _>("volume")
                        .or_else(|_| row.try_get::<i32, _>("volume").map(|v| v as i64))
                        .unwrap_or(0);
                    Some(BinaryCandle { ts, open, high, low, close, volume })
                })
                .collect();

            info!(
                "get_historical_view: {} ({}) — {} candles fetched, serializing with bincode.",
                symbol,
                tf,
                candles.len()
            );

            // ── DIAGNOSTIC TRACER — Final Mile (Rust → bincode boundary) ──
            // Verifies the exact struct values the backend is about to ship
            // to the UI. If `Total Candles fetched: 0`, the SQL query produced
            // no rows; if first/last look corrupt (NaN/0/garbage timestamps),
            // the QuestDB row decoding above is at fault.
            println!(
                "🛑 [RUST EXIT] Symbol: {} | Timeframe: {:?} | Source: {:?} | SAMPLE BY: {} | Total Candles fetched: {}",
                symbol, tf, source, sample_interval, candles.len()
            );
            if let (Some(first), Some(last)) = (candles.first(), candles.last()) {
                println!("🛑 [RUST EXIT] First Candle: {:?}", first);
                println!("🛑 [RUST EXIT] Last  Candle: {:?}", last);
            } else {
                println!(
                    "🛑 [RUST EXIT] ⚠️  EMPTY result set — no candles to serialize for {} ({}).",
                    symbol, tf
                );
            }

            // Serialize to bincode binary buffer
            let binary = bincode::serialize(&candles).map_err(|e| {
                let msg = format!("bincode serialization failed: {}", e);
                error!("{}", msg);
                broadcast_error(&app, &msg);
                msg
            })?;

            info!(
                "get_historical_view: {} ({}) — {} bytes serialized.",
                symbol,
                tf,
                binary.len()
            );

            // ── DIAGNOSTIC TRACER — Bincode payload size out of Rust ──
            // Use this number to confirm React sees the same byte count on the
            // other side of the IPC boundary. A mismatch here ≠ React side
            // means the Tauri channel itself is the suspect.
            println!(
                "🛑 [RUST EXIT] Bincode payload size: {} bytes (going to UI)",
                binary.len()
            );

            Ok(binary)
        }
        Err(e) => {
            let msg = format!("QuestDB query failed for {} ({}): {}", symbol, tf, e);
            error!("{}", msg);
            broadcast_error(&app, &msg);
            Err(msg)
        }
    }
}

/// Source table the historical view should read from for a given timeframe.
#[derive(Debug, Clone, Copy)]
enum HistorySource {
    /// Aggregate raw ticks via SAMPLE BY (intraday).
    Ticks,
    /// Read pre-aggregated daily archive (resampled for weekly).
    Daily,
}

/// Check whether the QuestDB PG pool has been registered as Tauri state.
///
/// The pool is registered asynchronously in lib.rs — the frontend should
/// call this first and wait until it returns `true` before invoking
/// `get_historical_view`. This prevents the "State not found" race condition.
///
/// Uses `AppHandle::try_state()` instead of `Option<State<PgPool>>` because
/// `State<T>` does not implement `Deserialize` in Tauri v2.
#[tauri::command]
pub async fn get_pool_status(app: AppHandle) -> bool {
    app.try_state::<PgPool>().is_some()
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
    // ── DIAGNOSTIC TRACER — Tauri command boundary ──
    println!(
        "🛑 [RUST RECEIVE] Load-Historical Request - Symbol: {}, Token: {}",
        symbol, instrument_token
    );

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
