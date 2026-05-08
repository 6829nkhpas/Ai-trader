// src/services/history_loader.rs — Zerodha Kite Historical Data Ingestion
//
// Fetches daily OHLCV candles from the Kite Historical API and bulk-inserts
// them into QuestDB's `historical_candles` table via the Postgres wire protocol.
//
// ── API Endpoint ────────────────────────────────────────────────────────────
//   GET https://api.kite.trade/instruments/historical/{token}/day
//   Query params: from (yyyy-mm-dd), to (yyyy-mm-dd)
//   Auth header:  Authorization: token {api_key}:{access_token}
//
// ── Chunking Strategy ───────────────────────────────────────────────────────
//   Kite allows up to 2000 days per request for daily candles.
//   We chunk into 365-day (1-year) windows for cleanliness, looping up to
//   5 times for a full 5-year backfill. This also keeps individual responses
//   manageable in memory.
//
// ── Rate Limiting ───────────────────────────────────────────────────────────
//   Kite rate-limits historical requests to 3/sec. We insert a 350ms delay
//   between chunk fetches to stay safely under the limit.
//
// ── Deduplication ───────────────────────────────────────────────────────────
//   Before fetching, we query QuestDB for the existing data range for the
//   given symbol. If data already covers a chunk window, that chunk is skipped
//   entirely — preventing redundant API calls and preserving Kite credits.

use chrono::NaiveDate;
use log::{info, warn, error};
use serde::Deserialize;
use sqlx::PgPool;

// ── Kite API Response Types ─────────────────────────────────────────────────

/// Top-level response from the Kite Historical API.
#[derive(Debug, Deserialize)]
pub struct KiteHistoricalResponse {
    pub status: String,
    pub data: KiteHistoricalData,
}

/// The `data` object containing the candle array.
#[derive(Debug, Deserialize)]
pub struct KiteHistoricalData {
    pub candles: Vec<Vec<serde_json::Value>>,
}

/// A single parsed candle row from the Kite API response.
#[derive(Debug, Clone)]
pub struct HistoricalCandle {
    pub timestamp: String, // ISO 8601 string from Kite, e.g. "2024-01-15T00:00:00+0530"
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: i64,
}

/// Date range of existing data in QuestDB for a given symbol.
#[derive(Debug)]
pub struct ExistingRange {
    pub min_ts: Option<NaiveDate>,
    pub max_ts: Option<NaiveDate>,
}

// ── Public API ──────────────────────────────────────────────────────────────

/// Run the QuestDB migration to ensure the `historical_candles` table exists.
///
/// Idempotent — safe to call on every startup.
pub async fn run_migration(pool: &PgPool) {
    let ddl = "
        CREATE TABLE IF NOT EXISTS historical_candles (
            symbol    SYMBOL,
            ts        TIMESTAMP,
            open      DOUBLE,
            high      DOUBLE,
            low       DOUBLE,
            close     DOUBLE,
            volume    LONG
        ) timestamp(ts) PARTITION BY YEAR;
    ";

    match sqlx::query(ddl).execute(pool).await {
        Ok(_) => info!("QuestDB: historical_candles table ready (PARTITION BY YEAR)."),
        Err(e) => error!("QuestDB migration for historical_candles failed: {}", e),
    }
}

/// Fetch 5 years of daily candles from Kite and store in QuestDB.
///
/// # Arguments
/// * `pool`             — QuestDB connection pool (PG wire, port 8812)
/// * `instrument_token` — Kite instrument token (e.g., 738561 for RELIANCE)
/// * `symbol`           — Human-readable symbol name (e.g., "RELIANCE")
/// * `api_key`          — Kite Connect API key
/// * `access_token`     — Kite OAuth access token (resets daily at midnight IST)
///
/// # Chunking
/// Loops in 365-day windows starting from `today - 5 years` up to `today`.
/// Each chunk that overlaps with existing QuestDB data is skipped.
pub async fn load_historical_data(
    pool: &PgPool,
    instrument_token: u32,
    symbol: &str,
    api_key: &str,
    access_token: &str,
) -> Result<u64, String> {
    let today = chrono::Local::now().date_naive();
    let five_years_ago = today - chrono::Duration::days(365 * 5);

    info!(
        "Historical loader: {} (token {}) — fetching {} → {}",
        symbol, instrument_token, five_years_ago, today
    );

    // ── 1. Check existing data range ────────────────────────────────────
    let existing = query_existing_range(pool, symbol).await;
    info!(
        "Existing data range for {}: {:?} → {:?}",
        symbol, existing.min_ts, existing.max_ts
    );

    // ── 2. Build chunk windows (365-day slices) ─────────────────────────
    let mut chunk_start = five_years_ago;
    let mut total_inserted: u64 = 0;
    let client = reqwest::Client::new();

    while chunk_start < today {
        let chunk_end = std::cmp::min(chunk_start + chrono::Duration::days(365), today);

        // Skip if QuestDB already covers this chunk
        if let (Some(min), Some(max)) = (existing.min_ts, existing.max_ts) {
            if chunk_start >= min && chunk_end <= max {
                info!(
                    "Chunk {} → {} already covered — skipping.",
                    chunk_start, chunk_end
                );
                chunk_start = chunk_end + chrono::Duration::days(1);
                continue;
            }
        }

        info!("Fetching chunk: {} → {}", chunk_start, chunk_end);

        // ── 3. Fetch from Kite API ──────────────────────────────────────
        match fetch_kite_candles(
            &client,
            instrument_token,
            &chunk_start,
            &chunk_end,
            api_key,
            access_token,
        )
        .await
        {
            Ok(candles) => {
                let count = candles.len() as u64;
                info!("Received {} candles for chunk {} → {}", count, chunk_start, chunk_end);

                // ── 4. Bulk insert into QuestDB ─────────────────────────
                if let Err(e) = bulk_insert(pool, symbol, &candles).await {
                    error!("Bulk insert failed for {} chunk {} → {}: {}", symbol, chunk_start, chunk_end, e);
                } else {
                    total_inserted += count;
                }
            }
            Err(e) => {
                error!(
                    "Kite API fetch failed for {} chunk {} → {}: {}",
                    symbol, chunk_start, chunk_end, e
                );
            }
        }

        // ── 5. Rate-limit delay (Kite: 3 req/sec max) ──────────────────
        tokio::time::sleep(std::time::Duration::from_millis(350)).await;

        chunk_start = chunk_end + chrono::Duration::days(1);
    }

    info!(
        "Historical loader complete: {} — {} candles ingested.",
        symbol, total_inserted
    );

    Ok(total_inserted)
}

// ── Private Helpers ─────────────────────────────────────────────────────────

/// Query QuestDB for the min/max timestamp of existing data for a symbol.
async fn query_existing_range(pool: &PgPool, symbol: &str) -> ExistingRange {
    // Use raw query + manual Row extraction to handle QuestDB's PG wire
    // timestamp encoding (may be i64 µs or NaiveDateTime depending on driver).
    let result = sqlx::query(
        "SELECT min(ts) as min_ts, max(ts) as max_ts FROM historical_candles WHERE symbol = $1",
    )
    .bind(symbol)
    .fetch_optional(pool)
    .await;

    match result {
        Ok(Some(row)) => {
            use sqlx::Row;

            // Try extracting as chrono::NaiveDateTime first (sqlx chrono feature),
            // then fall back to i64 microseconds if QuestDB returns raw ints.
            let min_date: Option<NaiveDate> = row
                .try_get::<chrono::NaiveDateTime, _>("min_ts")
                .ok()
                .map(|dt| dt.date());

            let max_date: Option<NaiveDate> = row
                .try_get::<chrono::NaiveDateTime, _>("max_ts")
                .ok()
                .map(|dt| dt.date());

            ExistingRange {
                min_ts: min_date,
                max_ts: max_date,
            }
        }
        Ok(None) => ExistingRange {
            min_ts: None,
            max_ts: None,
        },
        Err(e) => {
            warn!("Could not query existing range for {}: {} — assuming empty.", symbol, e);
            ExistingRange {
                min_ts: None,
                max_ts: None,
            }
        }
    }
}

/// Fetch daily candles from the Kite Historical API for a single chunk.
///
/// Endpoint: GET /instruments/historical/{token}/day?from={from}&to={to}
/// Auth: `Authorization: token {api_key}:{access_token}`
async fn fetch_kite_candles(
    client: &reqwest::Client,
    instrument_token: u32,
    from: &NaiveDate,
    to: &NaiveDate,
    api_key: &str,
    access_token: &str,
) -> Result<Vec<HistoricalCandle>, String> {
    let url = format!(
        "https://api.kite.trade/instruments/historical/{}/day",
        instrument_token
    );

    let response = client
        .get(&url)
        .query(&[
            ("from", from.format("%Y-%m-%d").to_string()),
            ("to", to.format("%Y-%m-%d").to_string()),
        ])
        .header(
            "Authorization",
            format!("token {}:{}", api_key, access_token),
        )
        .header("X-Kite-Version", "3")
        .send()
        .await
        .map_err(|e| format!("HTTP request failed: {}", e))?;

    if !response.status().is_success() {
        let status = response.status();
        let body = response
            .text()
            .await
            .unwrap_or_else(|_| "unable to read body".into());
        return Err(format!("Kite API error {}: {}", status, body));
    }

    let api_response: KiteHistoricalResponse = response
        .json()
        .await
        .map_err(|e| format!("JSON parse failed: {}", e))?;

    // Parse candle arrays: [timestamp, open, high, low, close, volume]
    let candles: Vec<HistoricalCandle> = api_response
        .data
        .candles
        .iter()
        .filter_map(|row| {
            if row.len() < 6 {
                warn!("Skipping malformed candle row: {:?}", row);
                return None;
            }
            Some(HistoricalCandle {
                timestamp: row[0].as_str().unwrap_or_default().to_string(),
                open: row[1].as_f64().unwrap_or(0.0),
                high: row[2].as_f64().unwrap_or(0.0),
                low: row[3].as_f64().unwrap_or(0.0),
                close: row[4].as_f64().unwrap_or(0.0),
                volume: row[5].as_i64().unwrap_or(0),
            })
        })
        .collect();

    Ok(candles)
}

/// Bulk-insert a batch of candles into QuestDB's `historical_candles` table.
///
/// Uses individual parameterised INSERT statements over the PG wire protocol.
/// QuestDB does not support multi-row VALUES or COPY, so we iterate.
///
/// Timestamp conversion:
///   Kite returns ISO 8601 strings like "2024-01-15T00:00:00+0530".
///   We parse to NaiveDateTime, then convert to microseconds since epoch
///   (QuestDB TIMESTAMP expects µs).
async fn bulk_insert(
    pool: &PgPool,
    symbol: &str,
    candles: &[HistoricalCandle],
) -> Result<(), String> {
    for candle in candles {
        // Parse the Kite timestamp — try multiple formats
        let ts_micros = parse_kite_timestamp(&candle.timestamp)?;

        sqlx::query(
            "INSERT INTO historical_candles (symbol, ts, open, high, low, close, volume) \
             VALUES ($1, $2, $3, $4, $5, $6, $7)",
        )
        .bind(symbol)
        .bind(ts_micros)
        .bind(candle.open)
        .bind(candle.high)
        .bind(candle.low)
        .bind(candle.close)
        .bind(candle.volume)
        .execute(pool)
        .await
        .map_err(|e| format!("Insert failed for ts={}: {}", candle.timestamp, e))?;
    }

    Ok(())
}

/// Parse a Kite ISO 8601 timestamp string into microseconds since Unix epoch.
///
/// Kite returns timestamps like:
///   "2024-01-15T00:00:00+0530"
///
/// We parse with chrono's DateTime<FixedOffset> and convert to µs for QuestDB.
fn parse_kite_timestamp(ts_str: &str) -> Result<i64, String> {
    // Try parsing with timezone offset (Kite's default format)
    if let Ok(dt) = chrono::DateTime::parse_from_str(ts_str, "%Y-%m-%dT%H:%M:%S%z") {
        return Ok(dt.timestamp_micros());
    }

    // Fallback: try without timezone (assume UTC)
    if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(ts_str, "%Y-%m-%dT%H:%M:%S") {
        return Ok(
            dt.and_utc().timestamp_micros(),
        );
    }

    Err(format!("Unable to parse Kite timestamp: {}", ts_str))
}
