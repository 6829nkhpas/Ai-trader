// candles.rs — Standalone QuestDB candle loader (no Kite backfill).
//
// A Tauri-free reimplementation of the desktop `load_candles_with_ts` merge
// path. On the droplet the ingestion service continuously populates QuestDB, so
// this loader only READS: it never triggers a proactive Kite backfill (which is
// what coupled the desktop loader to the Tauri AppHandle / instrument DB). It
// unions the three candle sources, dedups on timestamp keeping the
// highest-priority source, and slices to the most recent `limit`.

use quant_core::patterns::Candle;
use sqlx::{PgPool, Row};

/// Typed outcome for a failed candle load (mirrors the desktop contract so the
/// `get_candles` handler can differentiate a graceful shortfall from a fault).
#[derive(Debug, Clone)]
pub enum CandleLoadError {
    /// Insufficient / genuinely empty history — a data-availability outcome.
    Shortfall {
        symbol: String,
        timeframe: String,
        available: usize,
        needed: usize,
        detail: String,
    },
    /// A genuine pool/DB/connection failure — `source` names the failing query.
    Fault { source: String, detail: String },
}

impl std::fmt::Display for CandleLoadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CandleLoadError::Shortfall { detail, .. } => write!(f, "{}", detail),
            CandleLoadError::Fault { source, detail } => {
                write!(f, "candle store fault: {}: {}", source, detail)
            }
        }
    }
}

fn is_infrastructure_error(e: &sqlx::Error) -> bool {
    matches!(
        e,
        sqlx::Error::PoolClosed
            | sqlx::Error::PoolTimedOut
            | sqlx::Error::Io(_)
            | sqlx::Error::Tls(_)
            | sqlx::Error::Protocol(_)
            | sqlx::Error::WorkerCrashed
            | sqlx::Error::Database(_)
    )
}

/// A "table does not exist" QuestDB error is NOT an infrastructure fault: on a
/// fresh deployment (before any data has been written for a source) the table
/// simply hasn't been created yet. Treating it as an empty source lets an empty
/// union degrade to a graceful Availability_Shortfall instead of a 503 Fault.
fn is_missing_table_error(e: &sqlx::Error) -> bool {
    if let sqlx::Error::Database(db) = e {
        db.message().to_lowercase().contains("does not exist")
    } else {
        false
    }
}

/// Ensure the historical candle tables exist (idempotent). Mirrors the desktop
/// `history_loader::run_migration` DDL exactly so the schema is identical
/// whether the desktop or the tool-server creates them. `live_ticks` is owned
/// by the ingestion service (auto-created via the ILP write path) and is not
/// created here; a missing `live_ticks` is treated as an empty source.
pub async fn migrate(pool: &PgPool) {
    let ddl_daily = "CREATE TABLE IF NOT EXISTS historical_candles (\
        symbol SYMBOL, ts TIMESTAMP, open DOUBLE, high DOUBLE, low DOUBLE, \
        close DOUBLE, volume LONG) timestamp(ts) PARTITION BY YEAR;";
    if let Err(e) = sqlx::query(ddl_daily).execute(pool).await {
        log::error!("[tool-server] migration historical_candles failed: {}", e);
    }
    let _ = sqlx::query("ALTER TABLE historical_candles DEDUP ENABLE UPSERT KEYS(ts, symbol);")
        .execute(pool)
        .await;

    let ddl_intraday = "CREATE TABLE IF NOT EXISTS historical_intraday (\
        symbol SYMBOL, timeframe SYMBOL, ts TIMESTAMP, open DOUBLE, high DOUBLE, \
        low DOUBLE, close DOUBLE, volume LONG) timestamp(ts) PARTITION BY MONTH;";
    if let Err(e) = sqlx::query(ddl_intraday).execute(pool).await {
        log::error!("[tool-server] migration historical_intraday failed: {}", e);
    }
    let _ = sqlx::query(
        "ALTER TABLE historical_intraday DEDUP ENABLE UPSERT KEYS(ts, symbol, timeframe);",
    )
    .execute(pool)
    .await;

    log::info!("[tool-server] QuestDB historical table migration complete.");
}

const PRIO_DAILY: u8 = 1;
const PRIO_INTRADAY: u8 = 2;
const PRIO_LIVE: u8 = 3;

struct PrioCandle {
    ts_millis: i64,
    priority: u8,
    candle: Candle,
}

fn parse_rows_with_ts(rows: &[sqlx::postgres::PgRow], priority: u8) -> Vec<PrioCandle> {
    rows.iter()
        .filter_map(|row| {
            let open: f64 = row.try_get("open").ok()?;
            let high: f64 = row.try_get("high").ok()?;
            let low: f64 = row.try_get("low").ok()?;
            let close: f64 = row.try_get("close").ok()?;
            let volume: i64 = row
                .try_get::<i64, _>("volume")
                .or_else(|_| row.try_get::<i32, _>("volume").map(|v| v as i64))
                .unwrap_or(0);
            let ts_dt = row.try_get::<chrono::NaiveDateTime, _>("ts");
            let ts_i64 = row.try_get::<i64, _>("ts");
            let ts_micros = match ts_dt {
                Ok(dt) => dt.and_utc().timestamp_micros(),
                Err(_) => ts_i64.unwrap_or(0),
            };
            Some(PrioCandle {
                ts_millis: ts_micros / 1000,
                priority,
                candle: Candle {
                    open,
                    high,
                    low,
                    close,
                    volume: volume as f64,
                },
            })
        })
        .collect()
}

/// Convenience: load the most recent `limit` candles (OHLCV only) with a 30-bar
/// minimum floor — the shape most handlers want.
pub async fn load_candles(
    pool: &PgPool,
    symbol: &str,
    timeframe: &str,
    limit: i64,
) -> Result<Vec<Candle>, CandleLoadError> {
    let timed = load_candles_with_ts(pool, symbol, timeframe, limit, 30).await?;
    Ok(timed.into_iter().map(|(_, c)| c).collect())
}

/// Timestamp-preserving loader. Returns ascending `(ts_millis, Candle)` pairs.
pub async fn load_candles_with_ts(
    pool: &PgPool,
    symbol: &str,
    timeframe: &str,
    limit: i64,
    min_candles: usize,
) -> Result<Vec<(i64, Candle)>, CandleLoadError> {
    let limit = limit.max(100);

    let is_weekly = timeframe == "1W"
        || timeframe.eq_ignore_ascii_case("1week")
        || timeframe.eq_ignore_ascii_case("week");
    let is_monthly = timeframe == "1M"
        || timeframe.eq_ignore_ascii_case("1month")
        || timeframe.eq_ignore_ascii_case("1mon")
        || timeframe.eq_ignore_ascii_case("month");
    let is_plain_daily =
        timeframe.eq_ignore_ascii_case("1d") || timeframe.eq_ignore_ascii_case("day");
    let is_daily = is_plain_daily || is_weekly || is_monthly;
    let daily_sample: Option<&str> = if is_weekly {
        Some("7d")
    } else if is_monthly {
        Some("30d")
    } else {
        None
    };

    let mut all_candles: Vec<PrioCandle> = Vec::new();
    let mut infra_fault: Option<(String, String)> = None;

    if is_daily {
        let daily_result = if let Some(unit) = daily_sample {
            let agg = format!(
                "SELECT ts, first(open) AS open, max(high) AS high, min(low) AS low, \
                 last(close) AS close, sum(volume) AS volume \
                 FROM historical_candles WHERE symbol = $1 \
                 SAMPLE BY {} ALIGN TO CALENDAR ORDER BY ts DESC LIMIT $2",
                unit
            );
            sqlx::query(&agg).bind(symbol).bind(limit).fetch_all(pool).await
        } else {
            sqlx::query(
                "SELECT ts, last(open) AS open, last(high) AS high, last(low) AS low, \
                 last(close) AS close, last(volume) AS volume \
                 FROM historical_candles WHERE symbol = $1 ORDER BY ts DESC LIMIT $2",
            )
            .bind(symbol)
            .bind(limit)
            .fetch_all(pool)
            .await
        };
        match &daily_result {
            Ok(rows) if !rows.is_empty() => all_candles.extend(parse_rows_with_ts(rows, PRIO_DAILY)),
            Ok(_) => {}
            Err(e) => {
                if infra_fault.is_none() && is_infrastructure_error(e) && !is_missing_table_error(e) {
                    infra_fault = Some(("historical_candles".to_string(), e.to_string()));
                }
            }
        }
    } else {
        let base_tf = base_timeframe(timeframe);
        let is_derived = timeframe.to_lowercase() != base_tf;
        let intraday_result = if is_derived {
            let sample_interval = derived_sample_interval(timeframe);
            let derived = format!(
                "SELECT ts, first(open) AS open, max(high) AS high, min(low) AS low, \
                 last(close) AS close, last(volume) AS volume \
                 FROM historical_intraday WHERE symbol = $1 AND timeframe = $2 \
                 SAMPLE BY {} ALIGN TO CALENDAR ORDER BY ts DESC LIMIT $3",
                sample_interval
            );
            sqlx::query(&derived)
                .bind(symbol)
                .bind(base_tf)
                .bind(limit)
                .fetch_all(pool)
                .await
        } else {
            sqlx::query(
                "SELECT ts, last(open) AS open, last(high) AS high, last(low) AS low, \
                 last(close) AS close, last(volume) AS volume \
                 FROM historical_intraday WHERE symbol = $1 AND timeframe = $2 \
                 ORDER BY ts DESC LIMIT $3",
            )
            .bind(symbol)
            .bind(timeframe)
            .bind(limit)
            .fetch_all(pool)
            .await
        };
        match &intraday_result {
            Ok(rows) if !rows.is_empty() => {
                all_candles.extend(parse_rows_with_ts(rows, PRIO_INTRADAY))
            }
            Ok(_) => {}
            Err(e) => {
                if infra_fault.is_none() && is_infrastructure_error(e) && !is_missing_table_error(e) {
                    infra_fault = Some(("historical_intraday".to_string(), e.to_string()));
                }
            }
        }
    }

    // Live ticks — for intraday + plain-daily only (never weekly/monthly).
    if !is_weekly && !is_monthly {
        let sample_interval = live_sample_interval(timeframe);
        let live = format!(
            "SELECT timestamp AS ts, first(last_traded_price) AS open, \
             max(last_traded_price) AS high, min(last_traded_price) AS low, \
             last(last_traded_price) AS close, (last(volume) - first(volume)) AS volume \
             FROM live_ticks WHERE symbol = $1 \
             SAMPLE BY {} ALIGN TO CALENDAR ORDER BY timestamp DESC LIMIT $2",
            sample_interval
        );
        match &sqlx::query(&live).bind(symbol).bind(limit).fetch_all(pool).await {
            Ok(rows) if !rows.is_empty() => all_candles.extend(parse_rows_with_ts(rows, PRIO_LIVE)),
            Ok(_) => {}
            Err(e) => {
                if infra_fault.is_none() && is_infrastructure_error(e) && !is_missing_table_error(e) {
                    infra_fault = Some(("live_ticks".to_string(), e.to_string()));
                }
            }
        }
    }

    if all_candles.is_empty() {
        if let Some((source, detail)) = infra_fault {
            return Err(CandleLoadError::Fault { source, detail });
        }
        return Err(CandleLoadError::Shortfall {
            symbol: symbol.to_string(),
            timeframe: timeframe.to_string(),
            available: 0,
            needed: min_candles,
            detail: "Insufficient historical data to compute technical indicators.".to_string(),
        });
    }

    // Merge: sort ascending by ts (ties: ascending priority), then dedup keeping
    // the highest-priority source per timestamp.
    all_candles.sort_by(|a, b| a.ts_millis.cmp(&b.ts_millis).then(a.priority.cmp(&b.priority)));
    let mut deduped: Vec<PrioCandle> = Vec::with_capacity(all_candles.len());
    for pc in all_candles {
        if let Some(last) = deduped.last() {
            if last.ts_millis == pc.ts_millis {
                if pc.priority > last.priority {
                    deduped.pop();
                    deduped.push(pc);
                }
                continue;
            }
        }
        deduped.push(pc);
    }

    let total = deduped.len();
    let start = if total > limit as usize { total - limit as usize } else { 0 };
    let final_candles: Vec<(i64, Candle)> = deduped[start..]
        .iter()
        .map(|pc| (pc.ts_millis, pc.candle.clone()))
        .collect();

    if final_candles.len() < min_candles {
        return Err(CandleLoadError::Shortfall {
            symbol: symbol.to_string(),
            timeframe: timeframe.to_string(),
            available: final_candles.len(),
            needed: min_candles,
            detail: format!(
                "Insufficient data for {} [{}]: {} candle(s) available, need {}.",
                symbol,
                timeframe,
                final_candles.len(),
                min_candles
            ),
        });
    }

    Ok(final_candles)
}

fn base_timeframe(tf: &str) -> &'static str {
    match tf.to_lowercase().as_str() {
        "1m" | "1min" | "2m" | "2min" | "4m" | "4min" => "1m",
        "3m" | "3min" => "3m",
        "5m" | "5min" => "5m",
        "10m" | "10min" => "10m",
        "15m" | "15min" | "75m" | "75min" | "125m" | "125min" => "15m",
        "30m" | "30min" => "30m",
        "1h" | "60m" | "2h" | "120m" | "3h" | "180m" | "4h" | "240m" => "1h",
        _ => "10m",
    }
}

fn derived_sample_interval(tf: &str) -> &str {
    match tf.to_lowercase().as_str() {
        "2m" | "2min" => "2m",
        "4m" | "4min" => "4m",
        "75m" | "75min" => "75m",
        "125m" | "125min" => "125m",
        "2h" | "120m" => "2h",
        "3h" | "180m" => "3h",
        "4h" | "240m" => "4h",
        _ => "10m",
    }
}

fn live_sample_interval(tf: &str) -> &'static str {
    match tf.to_lowercase().as_str() {
        "1m" | "1min" => "1m",
        "3m" | "3min" => "3m",
        "5m" | "5min" => "5m",
        "15m" | "15min" => "15m",
        "30m" | "30min" => "30m",
        "1h" | "60m" | "1hour" => "1h",
        "4h" | "240m" | "4hour" => "4h",
        "1d" | "day" => "1d",
        _ => "10m",
    }
}
