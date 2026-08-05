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

    // Redact the password from the log — it appears in the connection string as
    // postgresql://user:PASSWORD@host:port/db and must not be echoed to stdout.
    let redacted = redact_url_password(url);
    info!("QuestDB PG pool connected → {}", redacted);
    Ok(pool)
}

/// Redact the password component from a PostgreSQL connection URL for safe logging.
///
/// Replaces `postgresql://user:PASSWORD@host` with `postgresql://user:***@host`
/// so the credential never appears in logs. Anything that isn't shaped like a
/// URL with userinfo is returned verbatim — a malformed URL must never be the
/// reason a password leaks, so the fallback is "return the input unchanged" ONLY
/// when there is provably no password segment to hide.
///
/// Deliberately hand-rolled rather than regex: this crate does not depend on
/// `regex`, and pulling one in for a single log line would add a build
/// dependency to the production ingestion image.
fn redact_url_password(url: &str) -> String {
    // Locate the authority section: everything between `://` and the next `/`
    // (or end of string). Restricting the search to the authority means a `@` or
    // `:` inside a path or query parameter can't be mistaken for userinfo.
    let Some(scheme_end) = url.find("://") else {
        return url.to_string();
    };
    let authority_start = scheme_end + 3;
    let authority_len = url[authority_start..]
        .find('/')
        .unwrap_or(url.len() - authority_start);
    let authority = &url[authority_start..authority_start + authority_len];

    // Userinfo is the part before the LAST `@` — a password may legally contain
    // an `@`, and taking the first one would leave the tail of it in the log.
    let Some(at) = authority.rfind('@') else {
        return url.to_string(); // No userinfo → no password to redact.
    };
    let userinfo = &authority[..at];

    // Split user from password at the FIRST `:` — a password containing `:` must
    // be redacted whole, so the remainder after the first colon is all secret.
    let Some(colon) = userinfo.find(':') else {
        return url.to_string(); // Bare `user@host` → nothing secret present.
    };

    format!(
        "{}{}:***{}",
        &url[..authority_start],
        &userinfo[..colon],
        &url[authority_start + at..]
    )
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
///
/// Returns `true` if the row landed. The tick is still dropped on failure; the
/// bool exists only so the caller can count the loss into
/// `ingestion_write_errors_total`. Without it a total QuestDB outage is visible
/// nowhere but the log stream.
pub async fn insert_tick(pool: &PgPool, tick: &Tick) -> bool {
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
            true
        }
        Err(e) => {
            warn!(
                "QuestDB PG insert failed for {}: {}",
                tick.symbol, e
            );
            false
        }
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_redact_url_password_standard_pg_url() {
        let url = "postgresql://admin:secret123@localhost:8812/qdb";
        let redacted = redact_url_password(url);
        assert_eq!(redacted, "postgresql://admin:***@localhost:8812/qdb");
    }

    #[test]
    fn test_redact_url_password_with_path() {
        let url = "postgresql://user:pass@host:5432/database?sslmode=require";
        let redacted = redact_url_password(url);
        assert_eq!(redacted, "postgresql://user:***@host:5432/database?sslmode=require");
    }

    #[test]
    fn test_redact_url_password_no_userinfo() {
        let url = "postgresql://localhost:8812/qdb";
        let redacted = redact_url_password(url);
        assert_eq!(redacted, "postgresql://localhost:8812/qdb");
    }

    #[test]
    fn test_redact_url_password_user_only_no_password() {
        let url = "postgresql://admin@localhost:8812/qdb";
        let redacted = redact_url_password(url);
        assert_eq!(redacted, "postgresql://admin@localhost:8812/qdb");
    }

    #[test]
    fn test_redact_url_password_complex_password_with_at_sign() {
        // Password "p@ss:w0rd" contains both @ and : — must be fully redacted.
        let url = "postgresql://admin:p@ss:w0rd@localhost:8812/qdb";
        let redacted = redact_url_password(url);
        assert_eq!(redacted, "postgresql://admin:***@localhost:8812/qdb");
    }

    #[test]
    fn test_redact_url_password_not_a_url() {
        let url = "not a url at all";
        let redacted = redact_url_password(url);
        assert_eq!(redacted, "not a url at all");
    }

    #[test]
    fn test_redact_url_password_no_scheme() {
        let url = "admin:password@localhost:8812";
        let redacted = redact_url_password(url);
        // No `://` → no redaction, return verbatim.
        assert_eq!(redacted, "admin:password@localhost:8812");
    }
}
