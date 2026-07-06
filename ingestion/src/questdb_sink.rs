// src/questdb_sink.rs — QuestDB Postgres-wire sink (Subphases 16-17)
//
// QuestDB exposes a Postgres-compatible wire protocol on port 8812.
// This module uses sqlx to connect to that endpoint, create the `live_ticks`
// table if it does not exist, and insert individual ticks via parameterised
// queries.
//
// Why two QuestDB writers?
//   - `questdb_writer.rs` (ILP over TCP :9009)  → highest throughput, blind write
//   - `questdb_sink.rs`   (PG wire  over :8812)  → SQL-accessible, auditable
//
// In production we route ticks to BOTH:
//   ILP  → live analytics / charting  (sub-millisecond latency)
//   PG   → tick archive / backtesting  (queryable, relational)
//
// Connection string format (matches QuestDB defaults):
//   postgresql://admin:quest@localhost:8812/qdb
//
// Environment variable: QUESTDB_POSTGRES_URL
//
// Table schema (exactly as specified in the subphase directive):
//   CREATE TABLE IF NOT EXISTS live_ticks (
//       symbol              SYMBOL,
//       timestamp           TIMESTAMP,
//       last_traded_price   DOUBLE,
//       volume              INT,
//       best_bid            DOUBLE,
//       best_ask            DOUBLE
//   ) timestamp(timestamp) PARTITION BY DAY;

use log::{error, info, warn};
use sqlx::PgPool;

use crate::proto::market_data::Tick;

// ── Public API ───────────────────────────────────────────────────────────────

/// Connect to QuestDB's Postgres wire endpoint and return a connection pool.
///
/// Reads the connection string from the `url` argument (typically sourced from
/// the `QUESTDB_POSTGRES_URL` environment variable).
///
/// Pool size is kept small (max 5 connections) because QuestDB's PG wire layer
/// is single-threaded; flooding it with connections does not improve throughput.
///
/// # Errors
/// Returns `sqlx::Error` if the URL is malformed or the server is unreachable.
pub async fn init_pool(url: &str) -> Result<PgPool, sqlx::Error> {
    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(5)
        .connect(url)
        .await?;

    info!("QuestDB PG pool connected → {}", url);
    Ok(pool)
}

/// Create the `live_ticks` table in QuestDB if it does not already exist.
///
/// This is idempotent — safe to call on every service start-up.
///
/// The schema follows the exact DDL specified in the subphase directive:
/// ```sql
/// CREATE TABLE IF NOT EXISTS live_ticks (
///     symbol              SYMBOL,
///     timestamp           TIMESTAMP,
///     last_traded_price   DOUBLE,
///     volume              INT,
///     best_bid            DOUBLE,
///     best_ask            DOUBLE
/// ) timestamp(timestamp) PARTITION BY DAY;
/// ```
///
/// `timestamp(timestamp)` designates the `timestamp` column as the QuestDB
/// ordered timestamp — required for time-series queries and WAL ingestion.
/// `PARTITION BY DAY` creates daily partition files for efficient range scans.
pub async fn create_table_if_not_exists(pool: &PgPool) {
    let ddl = "
        CREATE TABLE IF NOT EXISTS live_ticks (
            symbol              SYMBOL,
            timestamp           TIMESTAMP,
            last_traded_price   DOUBLE,
            volume              INT,
            best_bid            DOUBLE,
            best_ask            DOUBLE
        ) timestamp(timestamp) PARTITION BY DAY;
    ";

    match sqlx::query(ddl).execute(pool).await {
        Ok(_) => info!("QuestDB: live_ticks table ready."),
        Err(e) => error!("QuestDB create_table_if_not_exists failed: {}", e),
    }
}

/// Insert a single `Tick` into the `live_ticks` table.
///
/// Timestamp conversion:
///   Kite delivers `timestamp_ms` as Unix milliseconds (i64).
///   QuestDB TIMESTAMP expects **microseconds** since the Unix epoch.
///   We multiply by 1_000 to convert ms → µs before binding.
///
/// Failures are logged as warnings and the tick is dropped — this is
/// intentional: we prefer slightly lossy archive over blocking the hot path.
pub async fn insert_tick(pool: &PgPool, tick: &Tick) {
    // milliseconds → microseconds for QuestDB TIMESTAMP type
    let ts_micros: i64 = tick.timestamp_ms * 1_000;

    let result = sqlx::query(
        "INSERT INTO live_ticks \
         (symbol, timestamp, last_traded_price, volume, best_bid, best_ask) \
         VALUES ($1, $2, $3, $4, $5, $6)",
    )
    .bind(&tick.symbol)
    .bind(ts_micros)
    .bind(tick.last_traded_price)
    .bind(tick.volume)
    .bind(tick.best_bid)
    .bind(tick.best_ask)
    .execute(pool)
    .await;

    match result {
        Ok(_) => {
            log::trace!(
                "QuestDB PG insert OK — symbol={} ts_µs={}",
                tick.symbol,
                ts_micros
            );
        }
        Err(e) => {
            warn!(
                "QuestDB PG insert failed for {}: {}",
                tick.symbol, e
            );
        }
    }
}
