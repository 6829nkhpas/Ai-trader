// src/option_sink.rs — QuestDB option sinks (Options Data Foundation, Phase F1)
//
// This module is *additive*: it adds the option-specific QuestDB tables and
// write paths for the F&O program. It NEVER touches the `live_ticks` or
// `historical_candles` DDL or write paths (Requirement 5.6) — those remain in
// `questdb_sink.rs` / `questdb_writer.rs` exactly as they were.
//
// Two tables are created here, both mirroring the existing `live_ticks`
// time-partitioning convention (`timestamp(...) PARTITION BY DAY`) so they are
// efficient for time-range scans (Requirement 5.4):
//
//   option_ticks            — every parsed option tick, tagged with its
//                             underlying / expiry / strike / type (R5.1, R5.5)
//   option_chain_snapshots  — periodic per-instrument OI/price snapshots so OI
//                             change can be tracked over time (R5.2, R5.5)
//
// Open interest is stored as a nullable LONG: NULL when the tick carried no OI
// (R2.4) — never a fabricated zero.
//
// Design note — purity for testability:
//   The row-building logic is split out into PURE functions
//   (`build_option_tick_row`, `build_snapshot_row`) that map a tick + its
//   selection metadata into a fully-tagged row struct. These functions perform
//   no I/O and are deterministic, so the property test (task 7.2) can target
//   them directly without a live feed or a database. The `insert_*` / `write_*`
//   functions are thin wrappers that build the pure row and bind it.

// Wired into the tick router and snapshot timer in task 8.1; until then these
// public sinks are intentionally unreferenced.
#![allow(dead_code)]

use log::{error, info, warn};
use sqlx::PgPool;

use crate::proto::market_data::Tick;

// ── Metadata ───────────────────────────────────────────────────────────────

/// Per-instrument selection metadata used to tag every stored option row so a
/// chain can be reconstructed by underlying + expiry (Requirement 5.5).
#[derive(Debug, Clone, PartialEq)]
pub struct OptionMeta {
    /// Underlying name (e.g. "NIFTY 50", "BANKNIFTY").
    pub underlying: String,
    /// Expiry as an ISO date string (e.g. "2024-12-26").
    pub expiry: String,
    /// Strike price (0.0 for FUT).
    pub strike: f64,
    /// Instrument type: "CE" / "PE" / "FUT".
    pub option_type: String,
}

// ── Pure row builders ────────────────────────────────────────────────────────

/// A fully-built `option_ticks` row, ready to bind. Produced by the PURE
/// `build_option_tick_row` so the tagging logic is testable without a database.
#[derive(Debug, Clone, PartialEq)]
pub struct OptionTickRow {
    pub symbol: String,
    pub underlying: String,
    pub expiry: String,
    pub strike: f64,
    pub option_type: String,
    /// QuestDB TIMESTAMP value in microseconds since the Unix epoch.
    pub timestamp_micros: i64,
    pub last_traded_price: f64,
    pub volume: i64,
    /// Open interest — `None` is persisted as SQL NULL (never coerced to 0).
    pub open_interest: Option<i64>,
    pub best_bid: f64,
    pub best_ask: f64,
}

/// A fully-built `option_chain_snapshots` row. Produced by the PURE
/// `build_snapshot_row`.
#[derive(Debug, Clone, PartialEq)]
pub struct SnapshotRow {
    pub underlying: String,
    pub expiry: String,
    pub strike: f64,
    pub option_type: String,
    pub symbol: String,
    pub last_price: f64,
    /// Open interest — `None` is persisted as SQL NULL.
    pub open_interest: Option<i64>,
    /// QuestDB TIMESTAMP value in microseconds since the Unix epoch.
    pub snapshot_ts_micros: i64,
}

/// PURE: build a fully-tagged `option_ticks` row from a parsed `Tick` and its
/// selection metadata. No I/O, deterministic, leaves its inputs unchanged.
///
/// The row carries the exact underlying / expiry / strike / type of the
/// instrument (Requirement 5.5) and preserves open-interest presence: a tick
/// with `open_interest = None` yields a `None` row value (SQL NULL), never a
/// fabricated zero (Requirement 2.4).
///
/// Timestamp conversion mirrors the equity sink: Kite delivers `timestamp_ms`
/// as Unix milliseconds; QuestDB TIMESTAMP expects microseconds, so we scale by
/// 1_000 (saturating to avoid overflow on degenerate input).
pub fn build_option_tick_row(tick: &Tick, meta: &OptionMeta) -> OptionTickRow {
    OptionTickRow {
        symbol: tick.symbol.clone(),
        underlying: meta.underlying.clone(),
        expiry: meta.expiry.clone(),
        strike: meta.strike,
        option_type: meta.option_type.clone(),
        timestamp_micros: tick.timestamp_ms.saturating_mul(1_000),
        last_traded_price: tick.last_traded_price,
        volume: tick.volume as i64,
        open_interest: tick.open_interest.map(|oi| oi as i64),
        best_bid: tick.best_bid,
        best_ask: tick.best_ask,
    }
}

/// PURE: build a fully-tagged `option_chain_snapshots` row from selection
/// metadata plus the latest-known symbol/price/OI for that instrument. No I/O,
/// deterministic, leaves its inputs unchanged.
///
/// `snapshot_ts_ms` is the snapshot time in Unix milliseconds; it is converted
/// to microseconds for QuestDB. Open-interest presence is preserved (NULL when
/// absent).
pub fn build_snapshot_row(
    meta: &OptionMeta,
    symbol: &str,
    last_price: f64,
    open_interest: Option<u64>,
    snapshot_ts_ms: i64,
) -> SnapshotRow {
    SnapshotRow {
        underlying: meta.underlying.clone(),
        expiry: meta.expiry.clone(),
        strike: meta.strike,
        option_type: meta.option_type.clone(),
        symbol: symbol.to_string(),
        last_price,
        open_interest: open_interest.map(|oi| oi as i64),
        snapshot_ts_micros: snapshot_ts_ms.saturating_mul(1_000),
    }
}

// ── DDL ──────────────────────────────────────────────────────────────────────

/// Create the option tables in QuestDB if they do not already exist.
///
/// Idempotent — safe to call on every service start-up. This is additive and
/// does not touch the `live_ticks` / `historical_candles` tables (R5.6).
///
/// Both tables use `timestamp(...) PARTITION BY DAY`, consistent with the
/// existing historical tables (R5.4), and carry the underlying / expiry /
/// strike / type tags so a chain reconstructs by underlying + expiry (R5.5).
/// `open_interest` is a nullable LONG (NULL when absent, R2.4).
pub async fn create_option_tables(pool: &PgPool) {
    let option_ticks_ddl = "
        CREATE TABLE IF NOT EXISTS option_ticks (
            symbol            SYMBOL,
            underlying        SYMBOL,
            expiry            SYMBOL,
            strike            DOUBLE,
            option_type       SYMBOL,
            timestamp         TIMESTAMP,
            last_traded_price DOUBLE,
            volume            LONG,
            open_interest     LONG,
            best_bid          DOUBLE,
            best_ask          DOUBLE
        ) timestamp(timestamp) PARTITION BY DAY;
    ";

    let snapshots_ddl = "
        CREATE TABLE IF NOT EXISTS option_chain_snapshots (
            underlying    SYMBOL,
            expiry        SYMBOL,
            strike        DOUBLE,
            option_type   SYMBOL,
            symbol        SYMBOL,
            last_price    DOUBLE,
            open_interest LONG,
            snapshot_ts   TIMESTAMP
        ) timestamp(snapshot_ts) PARTITION BY DAY;
    ";

    match sqlx::query(option_ticks_ddl).execute(pool).await {
        Ok(_) => info!("QuestDB: option_ticks table ready."),
        Err(e) => error!("QuestDB create option_ticks failed: {}", e),
    }

    match sqlx::query(snapshots_ddl).execute(pool).await {
        Ok(_) => info!("QuestDB: option_chain_snapshots table ready."),
        Err(e) => error!("QuestDB create option_chain_snapshots failed: {}", e),
    }
}

// ── Writes ───────────────────────────────────────────────────────────────────

/// Insert a single option `Tick`, tagged with its selection metadata, into the
/// `option_ticks` table (R5.1, R5.5).
///
/// Failures are logged as warnings and the tick is dropped — consistent with
/// the equity sink's lossy-but-non-blocking policy, and so an option-side
/// insert error never disturbs the equity path (R7.1).
pub async fn insert_option_tick(pool: &PgPool, tick: &Tick, meta: &OptionMeta) {
    let row = build_option_tick_row(tick, meta);

    let result = sqlx::query(
        "INSERT INTO option_ticks \
         (symbol, underlying, expiry, strike, option_type, timestamp, \
          last_traded_price, volume, open_interest, best_bid, best_ask) \
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
    )
    .bind(&row.symbol)
    .bind(&row.underlying)
    .bind(&row.expiry)
    .bind(row.strike)
    .bind(&row.option_type)
    .bind(row.timestamp_micros)
    .bind(row.last_traded_price)
    .bind(row.volume)
    .bind(row.open_interest)
    .bind(row.best_bid)
    .bind(row.best_ask)
    .execute(pool)
    .await;

    match result {
        Ok(_) => log::trace!(
            "QuestDB option_ticks insert OK — symbol={} ts_µs={}",
            row.symbol,
            row.timestamp_micros
        ),
        Err(e) => warn!("QuestDB option_ticks insert failed for {}: {}", row.symbol, e),
    }
}

/// Write a batch of pre-built `SnapshotRow`s to `option_chain_snapshots`
/// (R5.2, R5.5). Each row is one instrument in the chain selection at the
/// snapshot time.
///
/// Rows are inserted individually; a single-row failure is logged and the rest
/// of the batch continues, so a bad row never aborts the whole snapshot.
pub async fn write_chain_snapshot(pool: &PgPool, rows: &[SnapshotRow]) {
    for row in rows {
        let result = sqlx::query(
            "INSERT INTO option_chain_snapshots \
             (underlying, expiry, strike, option_type, symbol, last_price, \
              open_interest, snapshot_ts) \
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        )
        .bind(&row.underlying)
        .bind(&row.expiry)
        .bind(row.strike)
        .bind(&row.option_type)
        .bind(&row.symbol)
        .bind(row.last_price)
        .bind(row.open_interest)
        .bind(row.snapshot_ts_micros)
        .execute(pool)
        .await;

        if let Err(e) = result {
            warn!(
                "QuestDB option_chain_snapshots insert failed for {}: {}",
                row.symbol, e
            );
        }
    }

    log::trace!(
        "QuestDB option_chain_snapshots wrote {} row(s).",
        rows.len()
    );
}

// ── Tests ──────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::proto::market_data::Tick;
    use proptest::prelude::*;
    use std::collections::HashMap;

    /// Build a `Tick` proto with the given symbol / OI; remaining fields are
    /// filled from the generated scalars so the builder sees a realistic packet.
    fn make_tick(
        symbol: String,
        timestamp_ms: i64,
        ltp: f64,
        volume: i32,
        best_bid: f64,
        best_ask: f64,
        instrument_token: u32,
        open_interest: Option<u64>,
    ) -> Tick {
        Tick {
            symbol,
            timestamp_ms,
            last_traded_price: ltp,
            volume,
            best_bid,
            best_ask,
            instrument_token,
            open: 0.0,
            high: 0.0,
            low: 0.0,
            close: 0.0,
            open_interest,
        }
    }

    /// Generator for per-instrument selection metadata. Underlying / expiry /
    /// type are drawn from small fixed sets (mirroring real chains); strike is
    /// any finite non-negative value.
    fn meta_strategy() -> impl Strategy<Value = OptionMeta> {
        (
            prop::sample::select(vec!["NIFTY 50", "BANKNIFTY", "FINNIFTY"]),
            prop::sample::select(vec!["2024-12-26", "2025-01-30", "2025-02-27"]),
            0.0f64..100_000.0,
            prop::sample::select(vec!["CE", "PE", "FUT"]),
        )
            .prop_map(|(underlying, expiry, strike, option_type)| OptionMeta {
                underlying: underlying.to_string(),
                expiry: expiry.to_string(),
                strike,
                option_type: option_type.to_string(),
            })
    }

    /// Generator for a Kite tick. Scalar fields are kept finite/bounded so the
    /// pass-through equality checks are well-defined (no NaN); OI presence is
    /// generated as a true `Option` so both None and Some(v) are exercised.
    fn tick_strategy() -> impl Strategy<Value = Tick> {
        (
            "[A-Z0-9]{1,16}",
            0i64..4_000_000_000_000i64,
            0.0f64..1_000_000.0,
            0i32..1_000_000,
            0.0f64..1_000_000.0,
            0.0f64..1_000_000.0,
            any::<u32>(),
            prop::option::of(any::<u64>()),
        )
            .prop_map(
                |(sym, ts, ltp, vol, bid, ask, token, oi)| {
                    make_tick(sym, ts, ltp, vol, bid, ask, token, oi)
                },
            )
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: options-data-foundation, Property 11: Stored option rows are
        // fully tagged and a chain reconstructs.
        //
        // Validates: Requirements 5.2, 5.5
        //
        // Part A — single-instrument tagging + OI presence:
        // the constructed option_ticks row carries the exact underlying, expiry,
        // strike, and type of that instrument (from OptionMeta), and open-interest
        // presence is preserved (None -> None, Some(v) -> Some(v as i64), never a
        // fabricated zero).
        #[test]
        fn prop_option_tick_row_fully_tagged(tick in tick_strategy(), meta in meta_strategy()) {
            let row = build_option_tick_row(&tick, &meta);

            // Exact metadata tags.
            prop_assert_eq!(&row.underlying, &meta.underlying);
            prop_assert_eq!(&row.expiry, &meta.expiry);
            prop_assert_eq!(row.strike, meta.strike);
            prop_assert_eq!(&row.option_type, &meta.option_type);

            // Identity carried from the tick.
            prop_assert_eq!(&row.symbol, &tick.symbol);

            // Open-interest presence preserved exactly.
            match tick.open_interest {
                None => prop_assert_eq!(row.open_interest, None),
                Some(v) => prop_assert_eq!(row.open_interest, Some(v as i64)),
            }
        }

        // Feature: options-data-foundation, Property 11: Stored option rows are
        // fully tagged and a chain reconstructs.
        //
        // Validates: Requirements 5.2, 5.5
        //
        // Part B — snapshot assembly over a latest-state map of N distinct
        // instruments produces exactly one row per instrument in the selection,
        // each tagged with its own metadata; grouping the rows by
        // (underlying, expiry) reconstructs the same chain grouping as the
        // selection itself.
        #[test]
        fn prop_chain_snapshot_assembles_one_row_per_instrument(
            entries in prop::collection::vec(
                (meta_strategy(), "[A-Z0-9]{1,16}", 0.0f64..1_000_000.0, prop::option::of(any::<u64>()), 0i64..4_000_000_000_000i64),
                1..40,
            )
        ) {
            // Treat `symbol` as the unique instrument key — the latest-state map
            // holds at most one entry per instrument. De-duplicate so we model a
            // map of N *distinct* instruments.
            let mut by_symbol: HashMap<String, (OptionMeta, String, f64, Option<u64>, i64)> = HashMap::new();
            for (meta, symbol, price, oi, ts) in entries {
                by_symbol.insert(symbol.clone(), (meta, symbol, price, oi, ts));
            }
            let selection: Vec<(OptionMeta, String, f64, Option<u64>, i64)> =
                by_symbol.into_values().collect();

            // Assemble the chain snapshot: one row per instrument in the selection.
            let rows: Vec<SnapshotRow> = selection
                .iter()
                .map(|(meta, symbol, price, oi, ts)| {
                    build_snapshot_row(meta, symbol, *price, *oi, *ts)
                })
                .collect();

            // Exactly one row per instrument in the selection.
            prop_assert_eq!(rows.len(), selection.len());

            // Each row is tagged with its own instrument's metadata + state, and
            // OI presence is preserved.
            for ((meta, symbol, price, oi, _ts), row) in selection.iter().zip(rows.iter()) {
                prop_assert_eq!(&row.underlying, &meta.underlying);
                prop_assert_eq!(&row.expiry, &meta.expiry);
                prop_assert_eq!(row.strike, meta.strike);
                prop_assert_eq!(&row.option_type, &meta.option_type);
                prop_assert_eq!(&row.symbol, symbol);
                prop_assert_eq!(row.last_price, *price);
                match oi {
                    None => prop_assert_eq!(row.open_interest, None),
                    Some(v) => prop_assert_eq!(row.open_interest, Some(*v as i64)),
                }
            }

            // Grouping rows by (underlying, expiry) reconstructs the chain: the
            // per-group instrument-symbol sets match those of the selection.
            let mut chain_from_rows: HashMap<(String, String), Vec<String>> = HashMap::new();
            for row in &rows {
                chain_from_rows
                    .entry((row.underlying.clone(), row.expiry.clone()))
                    .or_default()
                    .push(row.symbol.clone());
            }
            let mut chain_from_selection: HashMap<(String, String), Vec<String>> = HashMap::new();
            for (meta, symbol, _p, _oi, _ts) in &selection {
                chain_from_selection
                    .entry((meta.underlying.clone(), meta.expiry.clone()))
                    .or_default()
                    .push(symbol.clone());
            }
            for v in chain_from_rows.values_mut() { v.sort(); }
            for v in chain_from_selection.values_mut() { v.sort(); }
            prop_assert_eq!(chain_from_rows, chain_from_selection);
        }
    }
}

// ── Integration tests (gated on a live QuestDB) ──────────────────────────────
//
// Task 7.3: integration tests for option storage. These exercise the *real*
// QuestDB write + read-back path over the Postgres wire protocol, so they need
// a running QuestDB. They are GATED: each test reads the QuestDB PG URL from the
// environment (QUESTDB_TEST_URL, then QUESTDB_POSTGRES_URL, else the QuestDB
// default) and, if the pool cannot connect, prints a skip message and returns —
// the test passes as a no-op rather than failing. This keeps `cargo test` green
// in environments (CI, dev laptops) where QuestDB is not running, while still
// exercising the full round-trip wherever QuestDB is available.
//
// Stability across reruns: every test tags its rows with a unique, run-specific
// suffix (nanosecond clock) embedded in the underlying / symbol values and reads
// back only its own rows by that tag. QuestDB has no convenient row-level DELETE,
// so this "unique-key" approach (rather than cleanup) keeps reruns deterministic
// regardless of leftover rows from prior runs.
//
// Covers:
//   * option_ticks insert + read-back round-trip, including a row with
//     open_interest = NULL (R2.3, R5.1)
//   * option_chain_snapshots write produces one row per selection instrument
//     (R5.2)
//   * DDL smoke: both option tables exist with DAY partitioning; live_ticks /
//     historical_candles remain unchanged (R5.4, R5.6)
#[cfg(test)]
mod integration_tests {
    use super::*;
    use sqlx::postgres::PgPoolOptions;
    use sqlx::Row;
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

    /// Try to connect to a live QuestDB PG-wire endpoint. Returns `None` (and
    /// prints a skip notice) when QuestDB is unavailable, so callers can return
    /// early and let the test pass as a no-op.
    async fn connect_test_pool() -> Option<PgPool> {
        let url = std::env::var("QUESTDB_TEST_URL")
            .or_else(|_| std::env::var("QUESTDB_POSTGRES_URL"))
            .unwrap_or_else(|_| "postgresql://admin:quest@localhost:8812/qdb".to_string());

        match PgPoolOptions::new()
            .max_connections(2)
            .acquire_timeout(Duration::from_secs(3))
            .connect(&url)
            .await
        {
            Ok(pool) => Some(pool),
            Err(e) => {
                eprintln!(
                    "[option_sink integration] SKIP — QuestDB unavailable at {} ({}). \
                     Set QUESTDB_TEST_URL / QUESTDB_POSTGRES_URL to a running QuestDB to run these tests.",
                    url, e
                );
                None
            }
        }
    }

    /// Unique, run-specific tag (nanoseconds since epoch) used to isolate each
    /// test's rows so reruns are deterministic.
    fn unique_suffix() -> u128 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    }

    /// Run a `count()` query and return the scalar result (0 on error).
    async fn scalar_count(pool: &PgPool, sql: &str) -> i64 {
        sqlx::query_scalar::<_, i64>(sql)
            .fetch_one(pool)
            .await
            .unwrap_or(0)
    }

    /// Poll a count query until it reaches `expected` (QuestDB WAL apply is
    /// asynchronous, so freshly-inserted rows may take a moment to be visible),
    /// up to ~6 seconds. Returns the last observed count.
    async fn wait_for_count(pool: &PgPool, sql: &str, expected: i64) -> i64 {
        let mut last = 0;
        for _ in 0..30 {
            last = scalar_count(pool, sql).await;
            if last >= expected {
                return last;
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
        last
    }

    /// Fetch the QuestDB partitioning of a table via `tables()`. Table names are
    /// hard-coded constants here, so inlining them is safe.
    async fn partition_by(pool: &PgPool, table: &str) -> Option<String> {
        let sql = format!(
            "SELECT partitionBy FROM tables() WHERE table_name = '{}'",
            table
        );
        match sqlx::query(&sql).fetch_optional(pool).await {
            Ok(Some(row)) => row.try_get::<String, _>(0).ok(),
            _ => None,
        }
    }

    /// Whether a table exists in QuestDB.
    async fn table_exists(pool: &PgPool, table: &str) -> bool {
        let sql = format!(
            "SELECT table_name FROM tables() WHERE table_name = '{}'",
            table
        );
        matches!(sqlx::query(&sql).fetch_optional(pool).await, Ok(Some(_)))
    }

    // R2.3, R5.1: option_ticks insert + read-back round-trip, including a row
    // whose open_interest is NULL (absent OI must read back as SQL NULL, never a
    // fabricated zero).
    #[tokio::test]
    async fn integration_option_ticks_roundtrip_with_null_oi() {
        let Some(pool) = connect_test_pool().await else {
            return;
        };
        create_option_tables(&pool).await;

        let suffix = unique_suffix();
        let underlying = format!("ITUND_{}", suffix);
        let sym_with_oi = format!("ITOI_{}", suffix);
        let sym_null_oi = format!("ITNULL_{}", suffix);
        let ts_ms: i64 = 1_700_000_000_000;

        let meta = OptionMeta {
            underlying: underlying.clone(),
            expiry: "2024-12-26".to_string(),
            strike: 24_000.0,
            option_type: "CE".to_string(),
        };

        // Row 1: open interest present.
        let tick_with_oi = Tick {
            symbol: sym_with_oi.clone(),
            timestamp_ms: ts_ms,
            last_traded_price: 123.5,
            volume: 4_200,
            best_bid: 123.0,
            best_ask: 124.0,
            instrument_token: 1,
            open: 0.0,
            high: 0.0,
            low: 0.0,
            close: 0.0,
            open_interest: Some(987_654),
        };

        // Row 2: open interest absent → must persist as NULL.
        let tick_null_oi = Tick {
            symbol: sym_null_oi.clone(),
            timestamp_ms: ts_ms,
            last_traded_price: 55.25,
            volume: 17,
            best_bid: 55.0,
            best_ask: 55.5,
            instrument_token: 2,
            open: 0.0,
            high: 0.0,
            low: 0.0,
            close: 0.0,
            open_interest: None,
        };

        insert_option_tick(&pool, &tick_with_oi, &meta).await;
        insert_option_tick(&pool, &tick_null_oi, &meta).await;

        let count_sql = format!(
            "SELECT count() FROM option_ticks WHERE underlying = '{}'",
            underlying
        );
        let count = wait_for_count(&pool, &count_sql, 2).await;
        assert_eq!(count, 2, "expected both option ticks to be persisted");

        // Read back the OI-bearing row and verify every tagged field round-trips.
        let row = sqlx::query(
            "SELECT symbol, underlying, expiry, strike, option_type, \
                    last_traded_price, volume, open_interest, best_bid, best_ask \
             FROM option_ticks WHERE symbol = $1",
        )
        .bind(&sym_with_oi)
        .fetch_one(&pool)
        .await
        .expect("OI row should be readable");

        assert_eq!(row.get::<String, _>(0), sym_with_oi);
        assert_eq!(row.get::<String, _>(1), underlying);
        assert_eq!(row.get::<String, _>(2), "2024-12-26");
        assert_eq!(row.get::<f64, _>(3), 24_000.0);
        assert_eq!(row.get::<String, _>(4), "CE");
        assert_eq!(row.get::<f64, _>(5), 123.5);
        assert_eq!(row.get::<i64, _>(6), 4_200);
        assert_eq!(row.try_get::<Option<i64>, _>(7).unwrap(), Some(987_654));
        assert_eq!(row.get::<f64, _>(8), 123.0);
        assert_eq!(row.get::<f64, _>(9), 124.0);

        // Read back the NULL-OI row and verify open_interest is SQL NULL.
        let row_null = sqlx::query(
            "SELECT symbol, open_interest FROM option_ticks WHERE symbol = $1",
        )
        .bind(&sym_null_oi)
        .fetch_one(&pool)
        .await
        .expect("NULL-OI row should be readable");

        assert_eq!(row_null.get::<String, _>(0), sym_null_oi);
        assert!(
            row_null.try_get::<Option<i64>, _>(1).unwrap().is_none(),
            "absent open interest must read back as NULL, not a fabricated zero"
        );
    }

    // R5.2: a periodic chain-snapshot write produces exactly one row per
    // instrument in the selection.
    #[tokio::test]
    async fn integration_chain_snapshot_one_row_per_instrument() {
        let Some(pool) = connect_test_pool().await else {
            return;
        };
        create_option_tables(&pool).await;

        let suffix = unique_suffix();
        let underlying = format!("SNAPUND_{}", suffix);
        let snapshot_ts_ms: i64 = 1_700_000_000_000;
        const N: usize = 5;

        // Build N distinct instruments (one selection), as the snapshot timer
        // would from its in-memory latest-state map.
        let mut rows = Vec::with_capacity(N);
        for i in 0..N {
            let meta = OptionMeta {
                underlying: underlying.clone(),
                expiry: "2025-01-30".to_string(),
                strike: 24_000.0 + (i as f64) * 50.0,
                option_type: if i % 2 == 0 { "CE" } else { "PE" }.to_string(),
            };
            let symbol = format!("SNAP_{}_{}", suffix, i);
            rows.push(build_snapshot_row(
                &meta,
                &symbol,
                100.0 + i as f64,
                Some(1_000 * i as u64),
                snapshot_ts_ms,
            ));
        }

        write_chain_snapshot(&pool, &rows).await;

        let count_sql = format!(
            "SELECT count() FROM option_chain_snapshots WHERE underlying = '{}'",
            underlying
        );
        let count = wait_for_count(&pool, &count_sql, N as i64).await;
        assert_eq!(
            count, N as i64,
            "snapshot must write exactly one row per selection instrument"
        );

        // And those rows must be N *distinct* instruments (one per symbol).
        let distinct_sql = format!(
            "SELECT count_distinct(symbol) FROM option_chain_snapshots WHERE underlying = '{}'",
            underlying
        );
        let distinct = scalar_count(&pool, &distinct_sql).await;
        assert_eq!(
            distinct, N as i64,
            "each selection instrument should contribute one distinct snapshot row"
        );
    }

    // R5.4, R5.6: DDL smoke. Both option tables exist with DAY partitioning, and
    // the existing live_ticks / historical_candles tables are left unchanged.
    #[tokio::test]
    async fn integration_ddl_smoke_partitioning_and_unchanged_tables() {
        let Some(pool) = connect_test_pool().await else {
            return;
        };
        // Additive option DDL.
        create_option_tables(&pool).await;
        // Ensure the existing equity table is present (its real creator).
        crate::questdb_sink::create_table_if_not_exists(&pool).await;

        // Both option tables exist and are DAY-partitioned (R5.4).
        assert_eq!(
            partition_by(&pool, "option_ticks").await.as_deref(),
            Some("DAY"),
            "option_ticks must be DAY-partitioned"
        );
        assert_eq!(
            partition_by(&pool, "option_chain_snapshots").await.as_deref(),
            Some("DAY"),
            "option_chain_snapshots must be DAY-partitioned"
        );

        // live_ticks is unchanged (R5.6): still DAY-partitioned, still carries
        // its original columns, and was NOT altered to carry option columns.
        assert_eq!(
            partition_by(&pool, "live_ticks").await.as_deref(),
            Some("DAY"),
            "live_ticks partitioning must remain DAY"
        );
        assert!(
            sqlx::query(
                "SELECT symbol, timestamp, last_traded_price, volume, best_bid, best_ask \
                 FROM live_ticks LIMIT 0"
            )
            .fetch_all(&pool)
            .await
            .is_ok(),
            "live_ticks must retain its original columns"
        );
        assert!(
            sqlx::query("SELECT underlying FROM live_ticks LIMIT 0")
                .fetch_all(&pool)
                .await
                .is_err(),
            "live_ticks must NOT have been altered to carry an option `underlying` column"
        );

        // historical_candles is owned elsewhere and untouched by this crate; if
        // it exists, confirm it keeps a DAY partitioning (i.e. we didn't alter it).
        if table_exists(&pool, "historical_candles").await {
            assert_eq!(
                partition_by(&pool, "historical_candles").await.as_deref(),
                Some("DAY"),
                "historical_candles partitioning must remain DAY"
            );
        }
    }
}
