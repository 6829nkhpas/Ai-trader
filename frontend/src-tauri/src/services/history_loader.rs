// src/services/history_loader.rs — Historical Data Ingestion
//
// Fetches daily AND intraday OHLCV candles through the configured market-data
// provider and bulk-inserts them into QuestDB tables via the Postgres wire
// protocol.
//
// ── Where the fetch lives ───────────────────────────────────────────────────
//   Since P14 the HTTP call, the credentials, and the direct-vs-proxy choice all
//   belong to `providers::kite`; this module asks
//   `providers::registry::market_data().historical(token, interval, from, to)`
//   for one provider-sized window at a time. What stays here is everything the
//   provider deliberately does NOT do: chunking, rate pacing, dedup and the
//   QuestDB write.
//
// ── Interval Support ────────────────────────────────────────────────────────
//   Daily:    "day"      → stored in `historical_candles`  (PARTITION BY YEAR)
//   Intraday: "minute", "5minute", "10minute", "15minute",
//             "30minute", "60minute" → stored in `historical_intraday` (PARTITION BY MONTH)
//
// ── Chunking Strategy ───────────────────────────────────────────────────────
//   Kite allows up to 2000 days per request for daily candles.
//   For intraday, limits vary (60 days for 1m, 100 days for others).
//   We chunk into appropriate windows based on the interval.
//
// ── Rate Limiting ───────────────────────────────────────────────────────────
//   Kite rate-limits historical requests to 3/sec. `KiteRatePacer` spaces
//   outbound requests 350ms apart, measured from the START of the previous
//   request — so time already spent fetching and inserting counts toward the
//   interval instead of being paid twice, and no sleep trails the final chunk.
//
// ── Deduplication ───────────────────────────────────────────────────────────
//   Before fetching, we query QuestDB for the existing data range for the
//   given symbol. If data already covers a chunk window, that chunk is skipped
//   entirely — preventing redundant API calls and preserving Kite credits.

use chrono::NaiveDate;
use log::{info, warn, error};
use sqlx::PgPool;

// ── Candle type ─────────────────────────────────────────────────────────────

/// A single parsed candle row, as the market-data provider returns it.
///
/// An alias rather than a distinct struct since P14: the transport (direct Kite
/// vs. the server-side proxy, credentials, retries) now lives in
/// `providers::kite`, and this module's job starts at the point where bars have to
/// be chunked, paced and written to QuestDB. Keeping one type means no conversion
/// layer where a field could quietly be dropped.
///
/// `timestamp` is an ISO-8601 string (e.g. `"2024-01-15T00:00:00+0530"`), which is
/// what `parse_kite_timestamp` below accepts — see [`crate::providers::Candle`].
pub type HistoricalCandle = crate::providers::Candle;

/// Date range of existing data in QuestDB for a given symbol.
#[derive(Debug)]
pub struct ExistingRange {
    pub min_ts: Option<NaiveDate>,
    pub max_ts: Option<NaiveDate>,
}

// ── Timeframe Mapping ───────────────────────────────────────────────────────

/// Configuration for a Kite intraday interval.
#[derive(Debug, Clone)]
pub struct KiteIntervalConfig {
    /// Kite API interval string (e.g., "minute", "5minute", "60minute")
    pub kite_interval: &'static str,
    /// How many days of history to fetch from Kite
    pub lookback_days: i64,
    /// Max days per chunk request (Kite's per-request limit for this interval)
    pub chunk_days: i64,
}

/// Map a UI timeframe string to the Kite Historical API interval and fetch config.
///
/// Returns `None` for daily/weekly/monthly timeframes (those use the existing daily loader).
///
/// **Base-interval caching**: Derived timeframes map to their base Kite interval
/// so that multiple UI timeframes share the same cached data in QuestDB.
/// The frontend's `aggregateCandles()` re-buckets into the exact UI timeframe.
///
/// # Kite Historical API Interval Reference
/// | UI Timeframe(s)    | Kite Interval | Lookback | Notes                    |
/// |--------------------|---------------|----------|--------------------------|
/// | 1m, 2m, 4m         | "minute"      | 7 days   | 2m/4m aggregate from 1m  |
/// | 3m                  | "3minute"     | 30 days  | Kite native              |
/// | 5m                  | "5minute"     | 30 days  |                          |
/// | 10m                 | "10minute"    | 30 days  |                          |
/// | 15m, 75m, 125m      | "15minute"    | 60 days  | 75m/125m aggregate       |
/// | 30m                 | "30minute"    | 60 days  |                          |
/// | 1H, 2H, 3H, 4H     | "60minute"    | 60 days  | 2H+ aggregate from 1H   |
pub fn map_timeframe_to_kite_interval(timeframe: &str) -> Option<KiteIntervalConfig> {
    match timeframe.to_uppercase().as_str() {
        // ── Minute-based (Kite: "minute") ───────────────────────────
        // 1m is the base; 2m and 4m are derived (frontend aggregates)
        "1M"  | "1MIN"
        | "2M" | "2MIN"
        | "4M" | "4MIN"  => Some(KiteIntervalConfig {
            kite_interval: "minute",
            lookback_days: 7,
            chunk_days: 7,      // 1m data is very dense; small chunks
        }),
        // ── 3-minute (Kite native: "3minute") ───────────────────────
        "3M"  | "3MIN"  => Some(KiteIntervalConfig {
            kite_interval: "3minute",
            lookback_days: 30,
            chunk_days: 30,
        }),
        // ── 5-minute ────────────────────────────────────────────────
        "5M"  | "5MIN"  => Some(KiteIntervalConfig {
            kite_interval: "5minute",
            lookback_days: 30,
            chunk_days: 30,
        }),
        // ── 10-minute ───────────────────────────────────────────────
        "10M" | "10MIN" => Some(KiteIntervalConfig {
            kite_interval: "10minute",
            lookback_days: 30,
            chunk_days: 30,
        }),
        // ── 15-minute based (Kite: "15minute") ─────────────────────
        // 15m is the base; 75m and 125m are derived (frontend aggregates)
        "15M" | "15MIN"
        | "75M" | "75MIN"
        | "125M" | "125MIN" => Some(KiteIntervalConfig {
            kite_interval: "15minute",
            lookback_days: 60,
            chunk_days: 60,
        }),
        // ── 30-minute ───────────────────────────────────────────────
        "30M" | "30MIN" => Some(KiteIntervalConfig {
            kite_interval: "30minute",
            lookback_days: 60,
            chunk_days: 60,
        }),
        // ── Hourly-based (Kite: "60minute") ─────────────────────────
        // 1H is the base; 2H, 3H, 4H are derived (frontend aggregates)
        "1H"  | "60M"
        | "2H" | "120M"
        | "3H" | "180M"
        | "4H" | "240M"  => Some(KiteIntervalConfig {
            kite_interval: "60minute",
            lookback_days: 60,
            chunk_days: 60,
        }),
        // Daily / weekly / monthly — handled by the existing daily loader
        _ => None,
    }
}

// ── Depth-Gap Fetch Planning ────────────────────────────────────────────────

/// A single chunk window `[start, end]` the intraday backfill should request
/// from Kite. `start`/`end` map directly to the `from`/`to` query params.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FetchWindow {
    pub start: NaiveDate,
    pub end: NaiveDate,
}

/// Compute the set of chunk windows the intraday backfill should fetch, healing
/// depth behind a short cached window rather than only topping up recency.
///
/// The returned set contains:
///   * the **forward top-up** from `existing_max_ts` toward `today` (as today —
///     starting at `existing_max_ts − 1 day` when a max exists, else the full
///     `[lookback_start, today]` window when `existing` is `None`), AND
///   * the **backward Depth_Gap** `[lookback_start, existing_min_ts)` — included
///     **only** when `existing_min_ts > lookback_start` (there is an older gap to
///     heal).
///
/// It returns an empty `Vec` (a no-op) when the cached window already spans the
/// full Lookback_Window, because every candidate chunk is already covered by the
/// existing `[min_ts, max_ts]` range. The same "chunk already covered" skip guard
/// used by the fetch loops is applied here so no covered chunk is ever emitted.
///
/// This is a **pure** function (no I/O) so the depth-gap planning can be unit
/// tested in isolation (see Property 1).
pub fn intraday_fetch_windows(
    lookback_start: NaiveDate,
    existing_min_ts: Option<NaiveDate>,
    existing_max_ts: Option<NaiveDate>,
    today: NaiveDate,
    chunk_days: i64,
) -> Vec<FetchWindow> {
    let mut windows = Vec::new();

    // ── Backward Depth_Gap: [lookback_start, existing_min_ts) ───────────
    // Only when the cached window's earliest candle is more recent than
    // `lookback_start` (an older gap exists). When `existing_min_ts` is None
    // the forward path below already fetches the full window, so depth is
    // covered there.
    if let Some(min) = existing_min_ts {
        if min > lookback_start {
            chunk_range(
                lookback_start,
                min,
                chunk_days,
                existing_min_ts,
                existing_max_ts,
                &mut windows,
            );
        }
    }

    // ── Forward top-up from existing_max_ts (full window when None) ─────
    // Mirrors the existing loop: start at `lookback_start`, but jump forward
    // to `existing_max_ts − 1 day` when that is later, then chunk to `today`.
    let mut forward_start = lookback_start;
    if let Some(max) = existing_max_ts {
        let max_start = max - chrono::Duration::days(1);
        if max_start > forward_start {
            forward_start = max_start;
        }
    }
    chunk_range(
        forward_start,
        today,
        chunk_days,
        existing_min_ts,
        existing_max_ts,
        &mut windows,
    );

    windows
}

/// Slice `[range_start, range_end)` into `chunk_days`-sized windows, appending
/// each window not already covered by the existing `[min, max]` range to `out`.
///
/// Replicates the "chunk already covered" skip guard used by both fetch loops so
/// the planned windows match what the loops would actually request.
fn chunk_range(
    range_start: NaiveDate,
    range_end: NaiveDate,
    chunk_days: i64,
    existing_min: Option<NaiveDate>,
    existing_max: Option<NaiveDate>,
    out: &mut Vec<FetchWindow>,
) {
    let mut chunk_start = range_start;
    while chunk_start < range_end {
        let chunk_end = std::cmp::min(
            chunk_start + chrono::Duration::days(chunk_days),
            range_end,
        );

        // Skip if QuestDB already covers this chunk (same guard as the loops).
        let covered = matches!(
            (existing_min, existing_max),
            (Some(min), Some(max)) if chunk_start >= min && chunk_end <= max
        );
        if !covered {
            out.push(FetchWindow {
                start: chunk_start,
                end: chunk_end,
            });
        }

        chunk_start = chunk_end + chrono::Duration::days(1);
    }
}

// ── Public API ──────────────────────────────────────────────────────────────

/// Run QuestDB migrations to ensure both `historical_candles` (daily) and
/// `historical_intraday` tables exist.
///
/// Idempotent — safe to call on every startup.
pub async fn run_migration(pool: &PgPool) {
    // ── Daily archive table ─────────────────────────────────────────────
    let ddl_daily = "
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

    match sqlx::query(ddl_daily).execute(pool).await {
        Ok(_) => info!("QuestDB: historical_candles table ready (PARTITION BY YEAR)."),
        Err(e) => error!("QuestDB migration for historical_candles failed: {}", e),
    }

    // Make daily inserts idempotent. `bulk_insert` / re-backfills re-fetch
    // overlapping ranges and blindly INSERT, so without a dedup key QuestDB
    // accumulates duplicate rows for the same (symbol, ts). A duplicate-inflated
    // table starves the reader: the read's `ORDER BY ts DESC LIMIT n` counts RAW
    // rows (dominated by dupes), and the merge's timestamp-dedup then collapses
    // them to far fewer distinct bars. Enabling DEDUP makes a matching insert
    // overwrite instead of appending. Idempotent; non-destructive (no rows are
    // removed). Requires a WAL table (QuestDB default for partitioned tables);
    // logged and ignored if unsupported so startup never fails.
    match sqlx::query(
        "ALTER TABLE historical_candles DEDUP ENABLE UPSERT KEYS(ts, symbol);",
    )
    .execute(pool)
    .await
    {
        Ok(_) => info!("QuestDB: historical_candles DEDUP enabled on (ts, symbol)."),
        Err(e) => warn!(
            "QuestDB: could not enable DEDUP on historical_candles (continuing): {}",
            e
        ),
    }

    // ── Intraday table ──────────────────────────────────────────────────
    let ddl_intraday = "
        CREATE TABLE IF NOT EXISTS historical_intraday (
            symbol    SYMBOL,
            timeframe SYMBOL,
            ts        TIMESTAMP,
            open      DOUBLE,
            high      DOUBLE,
            low       DOUBLE,
            close     DOUBLE,
            volume    LONG
        ) timestamp(ts) PARTITION BY MONTH;
    ";

    match sqlx::query(ddl_intraday).execute(pool).await {
        Ok(_) => info!("QuestDB: historical_intraday table ready (PARTITION BY MONTH)."),
        Err(e) => error!("QuestDB migration for historical_intraday failed: {}", e),
    }

    // Same idempotency fix for intraday (see historical_candles above). The
    // forward top-up re-fetches `[max_ts − 1 day, today]` on EVERY run and
    // re-inserts those bars, so duplicates accumulate fastest here — this is the
    // root cause of the "insufficient data: 75/114" starvation on a liquid
    // symbol whose store is actually deep. DEDUP on (ts, symbol, timeframe)
    // makes re-inserting a bar overwrite instead of duplicate. Non-destructive;
    // logged and ignored if the table is not WAL.
    match sqlx::query(
        "ALTER TABLE historical_intraday DEDUP ENABLE UPSERT KEYS(ts, symbol, timeframe);",
    )
    .execute(pool)
    .await
    {
        Ok(_) => info!(
            "QuestDB: historical_intraday DEDUP enabled on (ts, symbol, timeframe)."
        ),
        Err(e) => warn!(
            "QuestDB: could not enable DEDUP on historical_intraday (continuing): {}",
            e
        ),
    }
}

/// Fetch 5 years of daily candles from the market-data provider and store in QuestDB.
///
/// # Arguments
/// * `pool`             — QuestDB connection pool (PG wire, port 8812)
/// * `instrument_token` — Kite instrument token (e.g., 738561 for RELIANCE)
/// * `symbol`           — Human-readable symbol name (e.g., "RELIANCE")
///
/// Broker credentials are no longer parameters: since P14 the provider resolves and
/// applies them itself (`providers::kite`), and taking them here would let a caller
/// pass credentials that are silently ignored.
///
/// # Chunking
/// Loops in 365-day windows starting from `today - 5 years` up to `today`.
/// Each chunk that overlaps with existing QuestDB data is skipped.
pub async fn load_historical_data(
    pool: &PgPool,
    instrument_token: u32,
    symbol: &str,
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
    if let Some(max) = existing.max_ts {
        let max_start = max - chrono::Duration::days(1);
        if max_start > chunk_start {
            chunk_start = max_start;
            info!(
                "Optimizing historical load for {}: starting from existing max ({}) - 1 day",
                symbol, max
            );
        }
    }
    let mut total_inserted: u64 = 0;
    let provider = crate::providers::registry::market_data();
    // Spacing is applied before each request, not after (see `KiteRatePacer`),
    // so already-covered chunks cost nothing and the last chunk isn't followed
    // by a pointless sleep.
    let mut pacer = KiteRatePacer::new(KITE_MIN_REQUEST_INTERVAL);

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

        // ── 3. Fetch from the provider (daily interval) ─────────────────────
        // Rate-limit gate: sleeps only the time still owed since the previous
        // request, and only when a request is genuinely about to be sent.
        pacer.acquire().await;

        match provider
            .historical(instrument_token, "day", &chunk_start, &chunk_end)
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

        // ── 5. Next chunk ───────────────────────────────────────────────
        chunk_start = chunk_end + chrono::Duration::days(1);
    }

    info!(
        "Historical loader complete: {} — {} candles ingested.",
        symbol, total_inserted
    );

    Ok(total_inserted)
}

/// Fetch intraday historical candles from Kite and store in QuestDB.
///
/// This is the intraday counterpart to `load_historical_data()`. It fetches
/// candles at the requested interval (e.g., "5minute", "15minute") and stores
/// them in the `historical_intraday` table tagged by symbol+timeframe.
///
/// # Arguments
/// * `pool`             — QuestDB connection pool
/// * `instrument_token` — Kite instrument token
/// * `symbol`           — Human-readable symbol name
/// * `timeframe`        — UI timeframe string (e.g., "5m", "15m", "1H")
///
/// Broker credentials are the provider's business since P14 — see
/// [`load_historical_data`].
///
/// # Deduplication
/// Checks existing data range in `historical_intraday` for this symbol+timeframe.
/// Only fetches chunks that aren't already covered.
pub async fn load_intraday_data(
    pool: &PgPool,
    instrument_token: u32,
    symbol: &str,
    timeframe: &str,
) -> Result<u64, String> {
    let config = map_timeframe_to_kite_interval(timeframe)
        .ok_or_else(|| format!("No intraday mapping for timeframe: {}", timeframe))?;

    let today = chrono::Local::now().date_naive();
    let lookback_start = today - chrono::Duration::days(config.lookback_days);

    info!(
        "Intraday loader: {} (token {}) — interval={}, fetching {} → {}",
        symbol, instrument_token, config.kite_interval, lookback_start, today
    );

    // ── 1. Check existing intraday data range ───────────────────────────
    let existing = query_existing_intraday_range(pool, symbol, timeframe).await;
    info!(
        "Existing intraday data for {} [{}]: {:?} → {:?}",
        symbol, timeframe, existing.min_ts, existing.max_ts
    );

    // ── 2. Build chunk windows ──────────────────────────────────────────
    // `intraday_fetch_windows` returns BOTH the forward top-up from
    // `existing.max_ts` (preserving the prior forward-only behavior exactly)
    // AND the backward Depth_Gap `[lookback_start, existing.min_ts)` when the
    // cached window's earliest candle is more recent than `lookback_start`
    // (R1.1). The same "chunk already covered" skip guard is applied inside
    // the helper, so nothing already in QuestDB is re-fetched (R1.2). When the
    // window is already deep — or `existing` is `None` (full-window forward
    // fetch already covers depth) — no extra Depth_Gap window is emitted, so
    // this stays a no-op for symbols that are already deep enough (R5.3).
    let windows = intraday_fetch_windows(
        lookback_start,
        existing.min_ts,
        existing.max_ts,
        today,
        config.chunk_days,
    );

    if existing.min_ts.map_or(false, |min| min > lookback_start) {
        info!(
            "Intraday depth-heal for {} [{}]: cached window starts at {:?} (> lookback_start {}) — \
             planning backward Depth_Gap fetch in addition to forward top-up.",
            symbol, timeframe, existing.min_ts, lookback_start
        );
    }

    let mut total_inserted: u64 = 0;
    let provider = crate::providers::registry::market_data();
    let mut pacer = KiteRatePacer::new(KITE_MIN_REQUEST_INTERVAL);

    // ── 3-5. Fetch each planned window (forward top-up + Depth_Gap) ─────
    // Same KiteIntervalConfig chunk size, same 3 req/s rate limit (now paced
    // before each request instead of slept after each chunk), and same
    // `historical_intraday` write path as the prior forward fetch. Per-chunk
    // errors are logged (never raised into the caller) exactly as before, so
    // an overall backfill error still surfaces to the caller as it does today
    // (R1.3, R1.4).
    for window in windows {
        let chunk_start = window.start;
        let chunk_end = window.end;

        info!(
            "Fetching intraday chunk: {} → {} (interval={})",
            chunk_start, chunk_end, config.kite_interval
        );

        // ── 3. Fetch from the provider ──────────────────────────────────────
        pacer.acquire().await;

        match provider
            .historical(
                instrument_token,
                config.kite_interval,
                &chunk_start,
                &chunk_end,
            )
            .await
        {
            Ok(candles) => {
                let count = candles.len() as u64;
                info!(
                    "Received {} intraday candles for {} [{}] chunk {} → {}",
                    count, symbol, timeframe, chunk_start, chunk_end
                );

                // ── 4. Bulk insert into historical_intraday ─────────────
                if let Err(e) = bulk_insert_intraday(pool, symbol, timeframe, &candles).await {
                    error!(
                        "Intraday bulk insert failed for {} [{}] chunk {} → {}: {}",
                        symbol, timeframe, chunk_start, chunk_end, e
                    );
                } else {
                    total_inserted += count;
                }
            }
            Err(e) => {
                error!(
                    "Kite API fetch failed for {} [{}] chunk {} → {}: {}",
                    symbol, timeframe, chunk_start, chunk_end, e
                );
            }
        }
    }

    info!(
        "Intraday loader complete: {} [{}] — {} candles ingested.",
        symbol, timeframe, total_inserted
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

// NOTE: the two Kite fetch transports that used to live here (direct
// `api.kite.trade` with credentials, and the server-side proxy fallback for a
// shipped thin client) moved to `providers::kite` under P14. This module keeps
// what is genuinely its own: chunking, rate pacing, QuestDB dedup and the write
// path. The provider is reached through `providers::registry::market_data()`.

/// Minimum spacing between two outbound Kite historical requests.
///
/// Kite rate-limits the historical endpoints to 3 requests/second (a 333 ms
/// floor); 350 ms keeps a small margin.
const KITE_MIN_REQUEST_INTERVAL: std::time::Duration = std::time::Duration::from_millis(350);

/// Paces outbound Kite requests so a backfill stays inside the rate limit
/// without paying for time it already spent working.
///
/// The previous code slept a flat 350 ms *after* every chunk. That was wrong in
/// two ways: it ignored the time the fetch and insert had already consumed (a
/// chunk taking 600 ms of real work still paid the full extra sleep, even though
/// the limit was long since satisfied), and it slept after the **final** chunk
/// too — dead time charged directly to the user's first chart render.
///
/// This sleeps only the remainder, `interval - elapsed_since_last_request`, and
/// only immediately before a request that actually needs it. The 3 req/s ceiling
/// is honoured more precisely than before, because spacing is now measured from
/// one request's start to the next rather than from the end of the preceding
/// chunk's database write.
///
/// Uses [`tokio::time::Instant`] rather than [`std::time::Instant`] so tests can
/// drive it with a paused clock.
struct KiteRatePacer {
    interval: std::time::Duration,
    last_request: Option<tokio::time::Instant>,
}

impl KiteRatePacer {
    fn new(interval: std::time::Duration) -> Self {
        Self { interval, last_request: None }
    }

    /// Wait until the next request may be issued, then record it as issued.
    ///
    /// The first call never sleeps. Later calls sleep only if less than
    /// `interval` has passed since the previous request began — `sleep_until`
    /// with a deadline already in the past returns immediately.
    async fn acquire(&mut self) {
        if let Some(last) = self.last_request {
            tokio::time::sleep_until(last + self.interval).await;
        }
        self.last_request = Some(tokio::time::Instant::now());
    }
}

/// Maximum rows per batched INSERT statement.
///
/// Each batch costs exactly one network round trip, so bigger is faster — but
/// every row contributes 7-8 bind parameters and the PG wire protocol caps a
/// single statement at 65535 of them. 500 rows x 8 params = 4000, comfortably
/// inside the limit while cutting round trips by 500x.
const INSERT_BATCH_ROWS: usize = 500;

/// Build a multi-row `VALUES (...),(...)` placeholder list.
///
/// Emits `cols`-wide tuples of 1-indexed `$n` placeholders — e.g. for
/// `rows=2, cols=3`: `($1,$2,$3),($4,$5,$6)`. Callers bind in the same order.
fn values_placeholders(rows: usize, cols: usize) -> String {
    let mut out = String::with_capacity(rows * cols * 5);
    for r in 0..rows {
        if r > 0 {
            out.push(',');
        }
        out.push('(');
        for c in 0..cols {
            if c > 0 {
                out.push(',');
            }
            out.push('$');
            // Placeholders are 1-indexed in the PG wire protocol.
            out.push_str(&(r * cols + c + 1).to_string());
        }
        out.push(')');
    }
    out
}

/// Bulk-insert a batch of candles into QuestDB's `historical_candles` table.
///
/// Rows are sent in multi-row `INSERT ... VALUES (...),(...)` batches of
/// [`INSERT_BATCH_ROWS`]. This matters far more than it looks: the desktop app
/// talks to QuestDB's PG-wire port across the public internet (~80 ms RTT), and
/// the previous one-INSERT-per-candle loop paid that latency per row — a 2250
/// candle backfill spent ~3 minutes purely waiting on round trips. Batching
/// turns that into ~5 round trips.
///
/// (An earlier comment here claimed QuestDB rejects multi-row VALUES. It does
/// not — verified against the deployed instance before this was written.)
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
    const COLS: usize = 7;

    for chunk in candles.chunks(INSERT_BATCH_ROWS) {
        // Parse every timestamp up front so a malformed row fails the batch
        // before any of it is sent, rather than half-way through.
        let mut parsed = Vec::with_capacity(chunk.len());
        for candle in chunk {
            parsed.push((parse_kite_timestamp(&candle.timestamp)?, candle));
        }

        let sql = format!(
            "INSERT INTO historical_candles (symbol, ts, open, high, low, close, volume) VALUES {}",
            values_placeholders(parsed.len(), COLS)
        );

        let mut query = sqlx::query(&sql);
        for (ts_micros, candle) in &parsed {
            query = query
                .bind(symbol)
                .bind(*ts_micros)
                .bind(candle.open)
                .bind(candle.high)
                .bind(candle.low)
                .bind(candle.close)
                .bind(candle.volume);
        }

        query
            .execute(pool)
            .await
            .map_err(|e| format!("Insert failed for {} rows: {}", parsed.len(), e))?;
    }

    Ok(())
}

/// Bulk-insert a batch of intraday candles into QuestDB's `historical_intraday` table.
///
/// Similar to `bulk_insert()` but includes the `timeframe` column to distinguish
/// between different intraday resolutions (e.g., "5m", "15m", "1H") for the
/// same symbol. Batched identically — see `bulk_insert` for why that matters.
async fn bulk_insert_intraday(
    pool: &PgPool,
    symbol: &str,
    timeframe: &str,
    candles: &[HistoricalCandle],
) -> Result<(), String> {
    const COLS: usize = 8;

    for chunk in candles.chunks(INSERT_BATCH_ROWS) {
        let mut parsed = Vec::with_capacity(chunk.len());
        for candle in chunk {
            parsed.push((parse_kite_timestamp(&candle.timestamp)?, candle));
        }

        let sql = format!(
            "INSERT INTO historical_intraday (symbol, timeframe, ts, open, high, low, close, volume) VALUES {}",
            values_placeholders(parsed.len(), COLS)
        );

        let mut query = sqlx::query(&sql);
        for (ts_micros, candle) in &parsed {
            query = query
                .bind(symbol)
                .bind(timeframe)
                .bind(*ts_micros)
                .bind(candle.open)
                .bind(candle.high)
                .bind(candle.low)
                .bind(candle.close)
                .bind(candle.volume);
        }

        query
            .execute(pool)
            .await
            .map_err(|e| format!("Intraday insert failed for {} rows: {}", parsed.len(), e))?;
    }

    Ok(())
}

/// Query QuestDB for the min/max timestamp of existing intraday data
/// for a specific symbol and timeframe combination.
async fn query_existing_intraday_range(
    pool: &PgPool,
    symbol: &str,
    timeframe: &str,
) -> ExistingRange {
    let result = sqlx::query(
        "SELECT min(ts) as min_ts, max(ts) as max_ts \
         FROM historical_intraday \
         WHERE symbol = $1 AND timeframe = $2",
    )
    .bind(symbol)
    .bind(timeframe)
    .fetch_optional(pool)
    .await;

    match result {
        Ok(Some(row)) => {
            use sqlx::Row;

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
            warn!(
                "Could not query existing intraday range for {} [{}]: {} — assuming empty.",
                symbol, timeframe, e
            );
            ExistingRange {
                min_ts: None,
                max_ts: None,
            }
        }
    }
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

// ════════════════════════════════════════════════════════════════════════
// Feature: intraday-candle-availability, Property 1: Depth-gap window is
// computed correctly and only when needed.
//
// `intraday_fetch_windows` is a pure planner, so these unit tests exercise the
// full decision without any I/O: the backward Depth_Gap `[lookback_start,
// existing_min_ts)` is emitted IFF `existing_min_ts > lookback_start`, the
// forward top-up toward `today` is always emitted, both collapse to a no-op
// when the cached window already spans the full Lookback_Window, and the
// `existing = None` case yields a full-window forward fetch with no backward
// gap.
//
// Validates: Requirements 1.1, 1.2, 5.3.
// ════════════════════════════════════════════════════════════════════════
#[cfg(test)]
mod intraday_fetch_windows_tests {
    use super::*;

    /// Build a `NaiveDate`, panicking on an invalid calendar date (test-only).
    fn d(y: i32, m: u32, day: u32) -> NaiveDate {
        NaiveDate::from_ymd_opt(y, m, day).expect("valid test date")
    }

    /// Shorthand for a `FetchWindow`.
    fn w(start: NaiveDate, end: NaiveDate) -> FetchWindow {
        FetchWindow { start, end }
    }

    const CHUNK_DAYS: i64 = 15;

    /// `today` for every case; `lookback_start` sits 30 days earlier.
    fn today() -> NaiveDate {
        d(2024, 1, 31)
    }
    fn lookback_start() -> NaiveDate {
        d(2024, 1, 1)
    }

    /// `existing = None`: the forward path already fetches the full
    /// `[lookback_start, today]` window (depth is covered there), so there is
    /// no separate backward Depth_Gap. (R1.2 / R5.3 — the `None` case.)
    #[test]
    fn none_existing_fetches_full_forward_window_and_no_backward_gap() {
        let windows =
            intraday_fetch_windows(lookback_start(), None, None, today(), CHUNK_DAYS);

        // Full forward window, chunked at CHUNK_DAYS.
        assert_eq!(
            windows,
            vec![
                w(d(2024, 1, 1), d(2024, 1, 16)),
                w(d(2024, 1, 17), d(2024, 1, 31)),
            ],
            "None existing should fetch the full forward window"
        );

        // Spans exactly [lookback_start, today]; nothing reaches before it.
        assert_eq!(windows.first().unwrap().start, lookback_start());
        assert_eq!(windows.last().unwrap().end, today());
        assert!(windows.iter().all(|win| win.start >= lookback_start()));
    }

    /// `existing_min_ts > lookback_start`: the backward Depth_Gap
    /// `[lookback_start, existing_min_ts)` IS included, alongside the forward
    /// top-up toward `today`. (R1.1 — the "57/114" heal.)
    #[test]
    fn backward_gap_included_when_min_ts_after_lookback_start() {
        let min = d(2024, 1, 10);
        let max = d(2024, 1, 15);

        let windows = intraday_fetch_windows(
            lookback_start(),
            Some(min),
            Some(max),
            today(),
            CHUNK_DAYS,
        );

        // Backward Depth_Gap [lookback_start, min_ts) is present.
        assert!(
            windows.contains(&w(lookback_start(), min)),
            "expected backward Depth_Gap window [lookback_start, min_ts), got {:?}",
            windows
        );
        // Forward top-up toward today is present.
        assert!(
            windows.iter().any(|win| win.end == today()),
            "expected forward top-up reaching today, got {:?}",
            windows
        );

        // Exact planned set (backward gap first, then forward chunks).
        assert_eq!(
            windows,
            vec![
                w(d(2024, 1, 1), d(2024, 1, 10)),
                w(d(2024, 1, 14), d(2024, 1, 29)),
                w(d(2024, 1, 30), d(2024, 1, 31)),
            ]
        );
    }

    /// Boundary of the IFF: when `existing_min_ts == lookback_start` the gap is
    /// NOT older than the window, so no backward Depth_Gap is emitted (the test
    /// is a strict `>`), while the forward top-up is still planned. (R1.1/R1.2.)
    #[test]
    fn backward_gap_excluded_when_min_ts_equals_lookback_start() {
        let min = lookback_start(); // == lookback_start → strict `>` is false
        let max = d(2024, 1, 15);

        let windows = intraday_fetch_windows(
            lookback_start(),
            Some(min),
            Some(max),
            today(),
            CHUNK_DAYS,
        );

        // No window reaches back to lookback_start (no Depth_Gap emitted).
        assert!(
            !windows.iter().any(|win| win.start == lookback_start()),
            "no backward Depth_Gap should start at lookback_start when \
             min_ts == lookback_start, got {:?}",
            windows
        );
        // Forward top-up toward today is still present.
        assert!(
            windows.iter().any(|win| win.end == today()),
            "expected forward top-up reaching today, got {:?}",
            windows
        );
    }

    /// Already deep: the cached window already spans the full Lookback_Window
    /// (`min_ts <= lookback_start` and `max_ts >= today`), so BOTH the backward
    /// Depth_Gap and the forward top-up collapse to a no-op — nothing is
    /// re-fetched. (R1.2 / R5.3 — the no-op guarantee.)
    #[test]
    fn already_deep_window_is_noop() {
        let min = d(2023, 12, 1); // older than lookback_start
        let max = d(2024, 2, 5); // beyond today

        let windows = intraday_fetch_windows(
            lookback_start(),
            Some(min),
            Some(max),
            today(),
            CHUNK_DAYS,
        );

        assert!(
            windows.is_empty(),
            "cached window already spans the Lookback_Window; expected a no-op, \
             got {:?}",
            windows
        );
    }
}

#[cfg(test)]
mod values_placeholders_tests {
    use super::*;

    /// The daily shape: 7 columns per row, numbered continuously across rows.
    #[test]
    fn builds_continuous_1_indexed_tuples() {
        assert_eq!(values_placeholders(2, 3), "($1,$2,$3),($4,$5,$6)");
    }

    /// A single row must not emit a leading or trailing comma.
    #[test]
    fn single_row_has_no_separator() {
        assert_eq!(values_placeholders(1, 4), "($1,$2,$3,$4)");
    }

    /// Placeholder numbering must not restart per row — binds are positional
    /// across the whole statement, so a restart would silently misalign columns.
    #[test]
    fn numbering_spans_all_rows() {
        let sql = values_placeholders(3, 8);
        assert!(sql.starts_with("($1,$2,$3,$4,$5,$6,$7,$8),"), "got {sql}");
        assert!(sql.ends_with(",($17,$18,$19,$20,$21,$22,$23,$24)"), "got {sql}");
    }

    /// A full batch must stay inside the PG wire protocol's 65535-parameter
    /// ceiling — this is the invariant that makes INSERT_BATCH_ROWS safe.
    #[test]
    fn full_batch_stays_under_pg_parameter_limit() {
        for cols in [7usize, 8] {
            let params = INSERT_BATCH_ROWS * cols;
            assert!(
                params <= u16::MAX as usize,
                "{INSERT_BATCH_ROWS} rows x {cols} cols = {params} params, over the 65535 limit"
            );
        }
    }

    /// Degenerate input yields an empty list rather than malformed SQL. The
    /// insert helpers never call it this way (`chunks()` yields no empty
    /// slices), but a stray `VALUES` with a dangling comma would be worse.
    #[test]
    fn zero_rows_is_empty() {
        assert_eq!(values_placeholders(0, 7), "");
    }
}

#[cfg(test)]
mod kite_rate_pacer_tests {
    use super::*;
    use std::time::Duration;

    /// The first request must go out immediately — a cold chart load should not
    /// open with a 350ms stall.
    #[tokio::test(start_paused = true)]
    async fn first_acquire_does_not_sleep() {
        let start = tokio::time::Instant::now();
        let mut pacer = KiteRatePacer::new(Duration::from_millis(350));

        pacer.acquire().await;

        assert_eq!(start.elapsed(), Duration::ZERO);
    }

    /// Back-to-back requests (nothing else happening between them) must be
    /// spaced by the full interval to respect Kite's 3 req/s ceiling.
    #[tokio::test(start_paused = true)]
    async fn back_to_back_requests_are_spaced_by_the_interval() {
        let start = tokio::time::Instant::now();
        let mut pacer = KiteRatePacer::new(Duration::from_millis(350));

        pacer.acquire().await;
        pacer.acquire().await;
        pacer.acquire().await;

        // Requests at t=0, 350, 700 — the interval applies between them, not
        // after the last one.
        assert_eq!(start.elapsed(), Duration::from_millis(700));
    }

    /// The point of the change: work already done counts toward the interval.
    /// A chunk whose fetch+insert took 200ms should wait only the remaining
    /// 150ms, not a fresh 350ms.
    #[tokio::test(start_paused = true)]
    async fn elapsed_work_counts_toward_the_interval() {
        let start = tokio::time::Instant::now();
        let mut pacer = KiteRatePacer::new(Duration::from_millis(350));

        pacer.acquire().await;
        tokio::time::sleep(Duration::from_millis(200)).await; // simulated fetch + insert
        pacer.acquire().await;

        assert_eq!(start.elapsed(), Duration::from_millis(350));
    }

    /// When a chunk takes longer than the interval, the limit is already
    /// satisfied and the next request must not be delayed at all.
    #[tokio::test(start_paused = true)]
    async fn slow_chunk_incurs_no_extra_delay() {
        let start = tokio::time::Instant::now();
        let mut pacer = KiteRatePacer::new(Duration::from_millis(350));

        pacer.acquire().await;
        tokio::time::sleep(Duration::from_millis(900)).await; // slow chunk
        pacer.acquire().await;

        assert_eq!(start.elapsed(), Duration::from_millis(900));
    }
}
