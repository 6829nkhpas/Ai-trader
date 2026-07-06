// src/commands/radar.rs — Quant Radar IPC Bridge (FEAT-037).
//
// Native, desktop-first Tauri commands that power the user-driven Quant
// Radar. All heavy work — candle fetching and pattern/strategy detection —
// runs in-process in Rust against the QuestDB Postgres-wire pool, the same
// near-native path Deep Quant uses. The frontend never touches the network
// for radar work; it just invokes a command and renders the result.
//
//   • scan_radar_symbol — fetch + locate in one native call. Given a symbol
//     and timeframe, loads candles from QuestDB (with proactive Kite backfill
//     when credentials exist) and returns a `RadarScan` of located patterns +
//     strategies (candle index, timestamp, timeframe, price geometry) for
//     on-chart visualization.
//
//   • scan_quant_radar — pure CPU scan over a candle series the caller
//     already holds (e.g. the chart's in-memory candles). Zero I/O.
//
//   • set_radar_symbols / get_radar_symbols — manage the background worker's
//     shared symbol registry so live alerts track the user's chosen symbols.

use tauri::{AppHandle, Manager};

use crate::quant::radar::RadarRegistry;
use crate::quant::scanner::{self, RadarScan, TimedCandle};

/// Minimum candles before a scan is worthwhile. Below this we still return a
/// (mostly empty) scan so the UI can show "insufficient data" gracefully.
const MIN_SCAN_CANDLES: usize = 5;

/// Default number of candles to pull from QuestDB per scan.
const SCAN_CANDLE_LIMIT: i64 = 300;

fn empty_scan(symbol: String, timeframe: String, candle_count: usize, last_close: f64, last_time: i64) -> RadarScan {
    RadarScan {
        symbol,
        timeframe,
        candle_count,
        last_close,
        last_time,
        trend_score: 0,
        momentum_state: "NEUTRAL".into(),
        volatility_state: "NORMAL".into(),
        volume_flow_state: "NEUTRAL".into(),
        patterns: vec![],
        strategies: vec![],
    }
}

/// Fetch candles natively from QuestDB and scan them for located patterns
/// and strategies — the primary desktop path (no browser fetch involved).
///
/// # Frontend invocation
/// ```ts
/// const scan = await invoke<RadarScan>("scan_radar_symbol", {
///   symbol: "RELIANCE",
///   timeframe: "10m",
///   lookback: 60,   // optional
/// });
/// ```
#[tauri::command]
pub async fn scan_radar_symbol(
    app: AppHandle,
    symbol: String,
    timeframe: String,
    lookback: Option<usize>,
) -> Result<RadarScan, String> {
    let pool = app
        .try_state::<sqlx::PgPool>()
        .ok_or_else(|| "QuestDB pool not ready yet — try again in a moment.".to_string())?;

    let rows = crate::commands::deep_quant::load_candles_with_ts(
        Some(&app),
        pool.inner(),
        &symbol,
        &timeframe,
        SCAN_CANDLE_LIMIT,
        // Radar floor of 0: return whatever candles exist (the loader still
        // errors only when ALL sources are empty). The located scanner is
        // safe on any length and degrades its consensus summary gracefully,
        // so patterns/strategies still surface on freshly-cached timeframes.
        0,
    )
    .await?;

    let candles: Vec<TimedCandle> = rows
        .into_iter()
        .map(|(ts_millis, c)| TimedCandle {
            time: ts_millis / 1000, // ms → seconds (lightweight-charts convention)
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: c.volume,
        })
        .collect();

    let lb = lookback.unwrap_or(scanner::DEFAULT_LOOKBACK);
    Ok(scanner::scan(&symbol, &candles, &timeframe, lb))
}

/// Scan a caller-supplied candle series for located patterns and strategies.
///
/// Pure CPU work (no I/O) — used when the caller already has candles in
/// memory (e.g. the active chart) and wants a zero-latency rescan.
#[tauri::command]
pub fn scan_quant_radar(
    symbol: String,
    timeframe: String,
    candles: Vec<TimedCandle>,
    lookback: Option<usize>,
) -> Result<RadarScan, String> {
    if candles.len() < MIN_SCAN_CANDLES {
        let last_close = candles.last().map(|c| c.close).unwrap_or(0.0);
        let last_time = candles.last().map(|c| c.time).unwrap_or(0);
        return Ok(empty_scan(symbol, timeframe, candles.len(), last_close, last_time));
    }

    let lb = lookback.unwrap_or(scanner::DEFAULT_LOOKBACK);
    Ok(scanner::scan(&symbol, &candles, &timeframe, lb))
}

/// Replace the background radar worker's tracked symbol set.
///
/// Symbols are upper-cased and de-duplicated. Passing an empty list pauses
/// live background scanning (the worker simply finds nothing to do).
#[tauri::command]
pub fn set_radar_symbols(
    registry: tauri::State<'_, RadarRegistry>,
    symbols: Vec<String>,
) -> Result<Vec<String>, String> {
    let cleaned = registry.set_symbols(symbols);
    Ok(cleaned)
}

/// Read the radar worker's current tracked symbol set.
#[tauri::command]
pub fn get_radar_symbols(
    registry: tauri::State<'_, RadarRegistry>,
) -> Result<Vec<String>, String> {
    Ok(registry.symbols())
}
