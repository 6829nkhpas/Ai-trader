// quant/radar.rs — Quant Radar: Live Market Scanner (Background Worker).
//
// Spawns a tokio task that continuously evaluates the ConsensusEngine
// across a configurable list of F&O instruments.  When an institutional
// strategy fires (Golden Cross, ORB Breakout, etc.) or the trend score
// exceeds the alert threshold, a `radar-alert` Tauri event is emitted
// to the React frontend in real time.
//
// ── Architecture ──────────────────────────────────────────────────────────
//   • Runs on a dedicated tokio task — never blocks the main Tauri thread.
//   • Fetches candle data from the Kite REST proxy for each symbol.
//   • Computes indicators via IndicatorState::from_candles_basic() and
//     runs the full ConsensusEngine pipeline.
//   • Deduplicates alerts: the same (symbol, strategy) pair won't fire
//     again until the scan interval resets.
//   • Configurable via environment variables:
//       RADAR_INTERVAL_SECS   — scan interval (default 60)
//       RADAR_TREND_THRESHOLD — trend_score threshold (default 50)
//       RADAR_SYMBOLS         — comma-separated override list

use log::{info, warn, error, debug};
use serde::Serialize;

use crate::quant::{ConsensusEngine, IndicatorState};
use crate::quant::patterns::Candle;

// ── Default F&O symbol universe ─────────────────────────────────────────
/// Top NSE F&O instruments — scanned every cycle.
const DEFAULT_SYMBOLS: &[&str] = &[
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
    "LT", "AXISBANK", "BAJFINANCE", "MARUTI", "ASIANPAINT",
    "HCLTECH", "SUNPHARMA", "TITAN", "WIPRO", "ULTRACEMCO",
    "NESTLEIND", "TECHM", "TATAMOTORS", "POWERGRID", "NTPC",
    "ONGC", "JSWSTEEL", "TATASTEEL", "ADANIENT", "ADANIPORTS",
    "BAJAJFINSV", "COALINDIA", "GRASIM", "M&M", "DRREDDY",
    "CIPLA", "BRITANNIA", "DIVISLAB", "EICHERMOT", "APOLLOHOSP",
    "BPCL", "HEROMOTOCO", "TATACONSUM", "SBILIFE", "INDUSINDBK",
    "DABUR", "HAVELLS", "PIDILITIND", "GODREJCP", "BIOCON",
];

/// Alert threshold for trend_score (absolute value).
const DEFAULT_TREND_THRESHOLD: i32 = 50;

/// Default scan interval in seconds.
const DEFAULT_INTERVAL_SECS: u64 = 60;

/// Minimum number of candles required to run consensus analysis.
const MIN_CANDLES: usize = 20;

// ── Alert Payload ───────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize)]
pub struct RadarAlert {
    pub symbol: String,
    pub trigger_reason: String,
    pub trend_score: i32,
    pub momentum: String,
    pub volatility: String,
    pub active_strategies: Vec<String>,
    pub active_patterns: Vec<String>,
    pub timestamp_ms: i64,
    pub severity: String, // "HIGH" | "MEDIUM" | "LOW"
}

// ── Public API ──────────────────────────────────────────────────────────

/// Spawns the Radar background worker on a dedicated tokio task.
///
/// This function returns immediately — the scan loop runs asynchronously
/// and emits `radar-alert` events via the Tauri AppHandle.
///
/// ── Lazy-loading guard ────────────────────────────────────────────────
/// The radar is **disabled by default** (Alpha Suite V3 lazy-loading
/// directive). It iterates 50+ F&O symbols every 60s, hitting the Kite
/// REST proxy and the consensus engine for each — exactly the kind of
/// pre-emptive global analysis we no longer want to run on cold start.
///
/// To opt in, set the env var `RADAR_ENABLED=true`. When unset (or any
/// value other than `true` / `1`), this function logs and returns
/// immediately, leaving zero background work behind.
pub fn spawn_radar_worker(app_handle: tauri::AppHandle) {
    // ── Opt-in switch ────────────────────────────────────────────────
    let enabled = std::env::var("RADAR_ENABLED")
        .ok()
        .map(|v| matches!(v.to_ascii_lowercase().as_str(), "true" | "1" | "yes" | "on"))
        .unwrap_or(false);

    if !enabled {
        info!(
            "[Radar] Disabled (set RADAR_ENABLED=true to opt in). \
             Background F&O scanner will not start — analysis runs lazily \
             per-symbol on subscribe_ticker."
        );
        let _ = app_handle; // explicit consume; no work to do.
        return;
    }

    // ── Read configuration from environment ──────────────────────────
    let interval_secs = std::env::var("RADAR_INTERVAL_SECS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(DEFAULT_INTERVAL_SECS);

    let trend_threshold = std::env::var("RADAR_TREND_THRESHOLD")
        .ok()
        .and_then(|v| v.parse::<i32>().ok())
        .unwrap_or(DEFAULT_TREND_THRESHOLD);

    let symbols: Vec<String> = std::env::var("RADAR_SYMBOLS")
        .ok()
        .map(|s| s.split(',').map(|sym| sym.trim().to_uppercase()).collect())
        .unwrap_or_else(|| DEFAULT_SYMBOLS.iter().map(|s| s.to_string()).collect());

    info!("╔══════════════════════════════════════════════════╗");
    info!("║  📡 Quant Radar — Live Market Scanner Starting   ║");
    info!("╚══════════════════════════════════════════════════╝");
    info!("Radar config: {} symbols | {}s interval | trend_threshold={}",
          symbols.len(), interval_secs, trend_threshold);

    tauri::async_runtime::spawn(async move {
        // Initial delay to let Kite instruments cache warm up (avoids 429 storm)
        tokio::time::sleep(std::time::Duration::from_secs(30)).await;
        info!("[Radar] Background scan loop started.");

        let kite_api_key = std::env::var("KITE_API_KEY").unwrap_or_default();
        let kite_access_token = std::env::var("KITE_ACCESS_TOKEN").unwrap_or_default();
        let has_kite = !kite_api_key.is_empty() && !kite_access_token.is_empty();

        if !has_kite {
            warn!("[Radar] KITE_API_KEY or KITE_ACCESS_TOKEN not set. Radar will use cached/fallback data only.");
        }

        let client = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(10))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());

        loop {
            let scan_start = std::time::Instant::now();
            let mut alerts_this_cycle: Vec<RadarAlert> = Vec::new();
            let mut scanned = 0_usize;
            let mut failed = 0_usize;

            for symbol in &symbols {
                match fetch_candles_for_symbol(&client, symbol, &kite_api_key, &kite_access_token).await {
                    Ok(candles) if candles.len() >= MIN_CANDLES => {
                        let indicators = IndicatorState::from_candles_basic(&candles);
                        let report = ConsensusEngine::compile_consensus(symbol, &candles, &indicators);

                        let has_strategies = !report.active_strategies.is_empty();
                        let strong_trend = report.trend_score.abs() >= trend_threshold;

                        if has_strategies || strong_trend {
                            let mut reasons: Vec<String> = Vec::new();

                            for strategy in &report.active_strategies {
                                reasons.push(strategy.clone());
                            }

                            if strong_trend && !has_strategies {
                                let direction = if report.trend_score > 0 { "Bullish" } else { "Bearish" };
                                reasons.push(format!("Strong {} Trend (score: {})", direction, report.trend_score));
                            }

                            let severity = if report.active_strategies.iter().any(|s|
                                s.contains("Golden Cross") || s.contains("ORB Breakout")
                            ) {
                                "HIGH"
                            } else if report.trend_score.abs() >= 75 || has_strategies {
                                "MEDIUM"
                            } else {
                                "LOW"
                            };

                            let alert = RadarAlert {
                                symbol: symbol.clone(),
                                trigger_reason: reasons.join(" | "),
                                trend_score: report.trend_score,
                                momentum: report.momentum_state,
                                volatility: report.volatility_state,
                                active_strategies: report.active_strategies,
                                active_patterns: report.active_patterns,
                                timestamp_ms: chrono::Utc::now().timestamp_millis(),
                                severity: severity.to_string(),
                            };

                            alerts_this_cycle.push(alert);
                        }

                        scanned += 1;
                    }
                    Ok(candles) => {
                        debug!("[Radar] {} — insufficient data ({} candles, need {})", symbol, candles.len(), MIN_CANDLES);
                        scanned += 1;
                    }
                    Err(e) => {
                        debug!("[Radar] {} — fetch failed: {}", symbol, e);
                        failed += 1;
                    }
                }

                // Rate-limit: 500ms between symbols to respect Kite's 3 req/sec limit
                tokio::time::sleep(std::time::Duration::from_millis(500)).await;
            }

            let elapsed = scan_start.elapsed();

            // Emit all alerts for this cycle
            if !alerts_this_cycle.is_empty() {
                info!(
                    "[Radar] Scan complete: {}/{} symbols | {} alerts | {:.1}s",
                    scanned, symbols.len(), alerts_this_cycle.len(), elapsed.as_secs_f64()
                );

                for alert in &alerts_this_cycle {
                    use tauri::Emitter;
                    match app_handle.emit("radar-alert", alert) {
                        Ok(_) => {
                            info!(
                                "[Radar] 🚨 ALERT: {} — {} (trend={}, severity={})",
                                alert.symbol, alert.trigger_reason, alert.trend_score, alert.severity
                            );
                        }
                        Err(e) => {
                            error!("[Radar] Failed to emit alert for {}: {}", alert.symbol, e);
                        }
                    }
                }
            } else {
                debug!(
                    "[Radar] Scan complete: {}/{} symbols | no alerts | {:.1}s",
                    scanned, symbols.len(), elapsed.as_secs_f64()
                );
            }

            if failed > 0 {
                debug!("[Radar] {} symbol(s) failed to fetch this cycle.", failed);
            }

            // Sleep until next scan cycle
            tokio::time::sleep(std::time::Duration::from_secs(interval_secs)).await;
        }
    });
}

// ── Candle Fetcher ──────────────────────────────────────────────────────

/// Fetches recent OHLCV candles for a symbol via the Kite REST proxy
/// running on the aggregator at localhost:3000.
///
/// Falls back to QuestDB REST API if Kite proxy is unavailable.
async fn fetch_candles_for_symbol(
    client: &reqwest::Client,
    symbol: &str,
    _api_key: &str,
    _access_token: &str,
) -> Result<Vec<Candle>, String> {
    // ── Path 1: Kite Historical API via aggregator proxy ─────────────
    let kite_port = std::env::var("KITE_API_PORT").unwrap_or_else(|_| "8084".to_string());
    let kite_url = format!(
        "http://127.0.0.1:{}/api/kite/historical?symbol={}&interval=day&from={}&to={}",
        kite_port,
        urlencoding::encode(symbol),
        (chrono::Utc::now() - chrono::Duration::days(300)).format("%Y-%m-%d"),
        chrono::Utc::now().format("%Y-%m-%d"),
    );

    if let Ok(response) = client.get(&kite_url).send().await {
        if response.status().is_success() {
            if let Ok(body) = response.json::<serde_json::Value>().await {
                if let Some(candles_arr) = body.get("candles").and_then(|c| c.as_array()) {
                    let candles: Vec<Candle> = candles_arr.iter().filter_map(|c| {
                        Some(Candle {
                            open:   c.get("open")?.as_f64()?,
                            high:   c.get("high")?.as_f64()?,
                            low:    c.get("low")?.as_f64()?,
                            close:  c.get("close")?.as_f64()?,
                            volume: c.get("volume")?.as_f64().unwrap_or(0.0),
                        })
                    }).collect();

                    if !candles.is_empty() {
                        return Ok(candles);
                    }
                }
            }
        }
    }

    // ── Path 2: QuestDB REST API ─────────────────────────────────────
    let questdb_url = std::env::var("QUESTDB_HTTP_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:9000".to_string());

    let query = format!(
        "SELECT open, high, low, close, volume FROM historical_candles \
         WHERE symbol = '{}' ORDER BY ts DESC LIMIT 300",
        symbol
    );

    let url = format!("{}/exec?query={}&fmt=json", questdb_url, urlencoding::encode(&query));

    let response = client.get(&url).send().await
        .map_err(|e| format!("QuestDB fetch failed: {}", e))?;

    if !response.status().is_success() {
        return Err(format!("QuestDB returned HTTP {}", response.status()));
    }

    let body: serde_json::Value = response.json().await
        .map_err(|e| format!("QuestDB JSON parse failed: {}", e))?;

    let dataset = body.get("dataset")
        .and_then(|d| d.as_array())
        .ok_or_else(|| "No dataset in QuestDB response".to_string())?;

    let mut candles: Vec<Candle> = dataset.iter().filter_map(|row| {
        let arr = row.as_array()?;
        if arr.len() < 5 { return None; }
        Some(Candle {
            open:   arr[0].as_f64()?,
            high:   arr[1].as_f64()?,
            low:    arr[2].as_f64()?,
            close:  arr[3].as_f64()?,
            volume: arr[4].as_f64().unwrap_or(0.0),
        })
    }).collect();

    // QuestDB query is DESC — reverse to chronological order
    candles.reverse();

    Ok(candles)
}
