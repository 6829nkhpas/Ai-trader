// quant/radar.rs — Quant Radar: User-Driven Live Market Scanner (FEAT-037).
//
// Refactored from the original fixed 50-symbol background loop into a
// **user-driven** scanner. The set of symbols tracked is owned by a shared
// `RadarRegistry` that the React UI mutates via `set_radar_symbols`, so the
// radar follows exactly the instruments the user adds to their Quant Radar.
//
// When a pattern or institutional strategy fires on a tracked symbol, a
// `radar-alert` Tauri event is emitted. Unlike the old text-only alert, the
// payload now carries the **located** detections (timeframe, candle index,
// timestamp and price geometry) so the front-end can route the chart to the
// symbol *and* draw the pattern / strategy where it formed.
//
// ── Architecture ──────────────────────────────────────────────────────────
//   • Shared `RadarRegistry` (RwLock<Vec<String>>) holds the user's symbols.
//   • A single tokio task loops over the registry every `interval` seconds.
//   • Per symbol it loads candles from QuestDB via the in-process Postgres
//     pool (the same near-native path Deep Quant uses — no HTTP/browser),
//     runs the located `scanner`, and emits enriched `radar-alert` events.
//   • Deduplicates: the same (symbol, detection) pair won't re-fire within
//     the dedup window.
//   • Configurable via environment variables:
//       RADAR_ENABLED         — opt-in master switch (default off)
//       RADAR_INTERVAL_SECS   — scan interval (default 60)
//       RADAR_TREND_THRESHOLD — trend_score threshold for trend alerts
//       RADAR_TIMEFRAME       — timeframe to scan (default "10m")

use std::collections::HashMap;
use std::sync::RwLock;

use log::{debug, error, info, warn};
use serde::Serialize;
use tauri::Manager;

use crate::quant::scanner::{self, LocatedPattern, LocatedStrategy, TimedCandle};

// ── Shared Symbol Registry ──────────────────────────────────────────────

/// Thread-safe set of symbols the radar background worker tracks.
///
/// Registered as Tauri managed state so the `set_radar_symbols` /
/// `get_radar_symbols` commands and the background loop all share one
/// source of truth. Seeded empty — the UI hydrates it from the user's
/// persisted radar list on boot.
#[derive(Default)]
pub struct RadarRegistry {
    symbols: RwLock<Vec<String>>,
}

impl RadarRegistry {
    pub fn new() -> Self {
        Self { symbols: RwLock::new(Vec::new()) }
    }

    /// Replace the tracked set. Upper-cases + de-duplicates, preserving order.
    /// Returns the cleaned list that was stored.
    pub fn set_symbols(&self, incoming: Vec<String>) -> Vec<String> {
        let mut cleaned: Vec<String> = Vec::with_capacity(incoming.len());
        for s in incoming {
            let up = s.trim().to_uppercase();
            if !up.is_empty() && !cleaned.contains(&up) {
                cleaned.push(up);
            }
        }
        if let Ok(mut guard) = self.symbols.write() {
            *guard = cleaned.clone();
        }
        cleaned
    }

    /// Snapshot the current tracked set.
    pub fn symbols(&self) -> Vec<String> {
        self.symbols.read().map(|g| g.clone()).unwrap_or_default()
    }
}

// ── Tuning Constants ────────────────────────────────────────────────────

/// Alert threshold for trend_score (absolute value).
const DEFAULT_TREND_THRESHOLD: i32 = 50;

/// Default scan interval in seconds.
const DEFAULT_INTERVAL_SECS: u64 = 60;

/// Default timeframe scanned by the background worker.
const DEFAULT_TIMEFRAME: &str = "10m";

/// Minimum number of candles required to run a scan.
const MIN_CANDLES: usize = 20;

/// How long (ms) before the same (symbol, detection) alert may re-fire.
const DEDUP_WINDOW_MS: i64 = 15 * 60 * 1_000;

// ── Alert Payload ───────────────────────────────────────────────────────

/// Enriched, located radar alert emitted to the React frontend.
///
/// Carries everything the UI needs to (a) list the alert, (b) route the
/// chart to the symbol/timeframe, and (c) draw the located patterns and
/// strategies on the chart.
#[derive(Debug, Clone, Serialize)]
pub struct RadarAlert {
    pub symbol: String,
    pub timeframe: String,
    pub trigger_reason: String,
    pub trend_score: i32,
    pub momentum: String,
    pub volatility: String,
    pub volume_flow: String,
    pub patterns: Vec<LocatedPattern>,
    pub strategies: Vec<LocatedStrategy>,
    pub timestamp_ms: i64,
    pub severity: String, // "HIGH" | "MEDIUM" | "LOW"
}

// ── Public API ──────────────────────────────────────────────────────────

/// Spawns the Radar background worker on a dedicated tokio task.
///
/// Returns immediately — the scan loop runs asynchronously and emits
/// `radar-alert` events via the Tauri AppHandle. The worker reads its
/// symbol set live from the shared `RadarRegistry`, so the user adding /
/// removing symbols in the UI takes effect on the next cycle without a
/// restart.
///
/// ── Lazy-loading guard ────────────────────────────────────────────────
/// Disabled by default (`RADAR_ENABLED=true` to opt in). Even when enabled,
/// the worker does zero work until the user has added symbols to their
/// radar, so cold start stays cheap.
pub fn spawn_radar_worker(app_handle: tauri::AppHandle) {
    let enabled = std::env::var("RADAR_ENABLED")
        .ok()
        .map(|v| matches!(v.to_ascii_lowercase().as_str(), "true" | "1" | "yes" | "on"))
        .unwrap_or(false);

    if !enabled {
        info!(
            "[Radar] Background worker disabled (set RADAR_ENABLED=true to opt in). \
             On-demand scanning via scan_quant_radar still works."
        );
        let _ = app_handle;
        return;
    }

    let interval_secs = std::env::var("RADAR_INTERVAL_SECS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(DEFAULT_INTERVAL_SECS);

    let trend_threshold = std::env::var("RADAR_TREND_THRESHOLD")
        .ok()
        .and_then(|v| v.parse::<i32>().ok())
        .unwrap_or(DEFAULT_TREND_THRESHOLD);

    let timeframe = std::env::var("RADAR_TIMEFRAME").unwrap_or_else(|_| DEFAULT_TIMEFRAME.to_string());

    info!("╔══════════════════════════════════════════════════╗");
    info!("║  📡 Quant Radar — User-Driven Scanner Starting   ║");
    info!("╚══════════════════════════════════════════════════╝");
    info!(
        "[Radar] config: {}s interval | trend_threshold={} | timeframe={}",
        interval_secs, trend_threshold, timeframe
    );

    tauri::async_runtime::spawn(async move {
        // Initial delay to let the QuestDB pool register + instrument cache warm.
        tokio::time::sleep(std::time::Duration::from_secs(30)).await;
        info!("[Radar] Background scan loop started (native QuestDB path).");

        let kite_api_key = std::env::var("KITE_API_KEY").unwrap_or_default();
        let kite_access_token = std::env::var("KITE_ACCESS_TOKEN").unwrap_or_default();
        if kite_api_key.is_empty() || kite_access_token.is_empty() {
            warn!("[Radar] KITE credentials not set — relying on QuestDB cache only.");
        }

        // (symbol, detection_name) -> last emit time, for dedup.
        let mut last_fired: HashMap<(String, String), i64> = HashMap::new();

        loop {
            let registry = app_handle.state::<RadarRegistry>();
            let symbols = registry.symbols();

            if symbols.is_empty() {
                debug!("[Radar] No symbols in registry — idle this cycle.");
                tokio::time::sleep(std::time::Duration::from_secs(interval_secs)).await;
                continue;
            }

            // The QuestDB pool is registered asynchronously in lib.rs; it may
            // not be ready on the first cycles. Skip gracefully until it is.
            let pool = match app_handle.try_state::<sqlx::PgPool>() {
                Some(p) => p,
                None => {
                    debug!("[Radar] QuestDB pool not ready — waiting.");
                    tokio::time::sleep(std::time::Duration::from_secs(interval_secs)).await;
                    continue;
                }
            };

            let scan_start = std::time::Instant::now();
            let mut scanned = 0usize;
            let mut emitted = 0usize;

            for symbol in &symbols {
                match fetch_candles_for_symbol(&app_handle, pool.inner(), symbol, &timeframe).await {
                    Ok(candles) if candles.len() >= MIN_CANDLES => {
                        let report = scanner::scan(symbol, &candles, &timeframe, scanner::DEFAULT_LOOKBACK);
                        scanned += 1;

                        let has_detections =
                            !report.patterns.is_empty() || !report.strategies.is_empty();
                        let strong_trend = report.trend_score.abs() >= trend_threshold;
                        if !has_detections && !strong_trend {
                            continue;
                        }

                        // Build a stable dedup key from the freshest detection names.
                        let now_ms = chrono::Utc::now().timestamp_millis();
                        let mut reasons: Vec<String> = Vec::new();
                        let mut fresh = false;

                        for s in &report.strategies {
                            let key = (symbol.clone(), format!("S:{}", s.name));
                            if now_ms - *last_fired.get(&key).unwrap_or(&0) >= DEDUP_WINDOW_MS {
                                last_fired.insert(key, now_ms);
                                reasons.push(s.name.clone());
                                fresh = true;
                            }
                        }
                        for p in &report.patterns {
                            let key = (symbol.clone(), format!("P:{}", p.name));
                            if now_ms - *last_fired.get(&key).unwrap_or(&0) >= DEDUP_WINDOW_MS {
                                last_fired.insert(key, now_ms);
                                reasons.push(p.name.clone());
                                fresh = true;
                            }
                        }
                        if strong_trend && reasons.is_empty() {
                            let key = (symbol.clone(), "T:trend".to_string());
                            if now_ms - *last_fired.get(&key).unwrap_or(&0) >= DEDUP_WINDOW_MS {
                                last_fired.insert(key, now_ms);
                                let dir = if report.trend_score > 0 { "Bullish" } else { "Bearish" };
                                reasons.push(format!("Strong {} Trend ({})", dir, report.trend_score));
                                fresh = true;
                            }
                        }

                        if !fresh {
                            continue;
                        }

                        let severity = severity_for(&report.strategies, report.trend_score);
                        let alert = RadarAlert {
                            symbol: symbol.clone(),
                            timeframe: timeframe.clone(),
                            trigger_reason: reasons.join(" | "),
                            trend_score: report.trend_score,
                            momentum: report.momentum_state,
                            volatility: report.volatility_state,
                            volume_flow: report.volume_flow_state,
                            patterns: report.patterns,
                            strategies: report.strategies,
                            timestamp_ms: now_ms,
                            severity,
                        };

                        use tauri::Emitter;
                        match app_handle.emit("radar-alert", &alert) {
                            Ok(_) => {
                                emitted += 1;
                                info!(
                                    "[Radar] 🚨 {} [{}] — {} (trend={}, sev={})",
                                    alert.symbol, alert.timeframe, alert.trigger_reason,
                                    alert.trend_score, alert.severity
                                );
                            }
                            Err(e) => error!("[Radar] emit failed for {}: {}", symbol, e),
                        }
                    }
                    Ok(candles) => {
                        debug!("[Radar] {} — insufficient data ({} candles)", symbol, candles.len());
                        scanned += 1;
                    }
                    Err(e) => debug!("[Radar] {} — fetch failed: {}", symbol, e),
                }

                // Small gap between symbols to keep proactive Kite backfill
                // within the broker's rate limits.
                tokio::time::sleep(std::time::Duration::from_millis(300)).await;
            }

            debug!(
                "[Radar] cycle done: {}/{} scanned | {} alerts | {:.1}s",
                scanned, symbols.len(), emitted, scan_start.elapsed().as_secs_f64()
            );

            tokio::time::sleep(std::time::Duration::from_secs(interval_secs)).await;
        }
    });
}

/// Severity ranking for an alert based on its strategies / trend strength.
fn severity_for(strategies: &[LocatedStrategy], trend_score: i32) -> String {
    let high = strategies.iter().any(|s| {
        let n = s.name.to_ascii_lowercase();
        n.contains("golden") || n.contains("death") || n.contains("orb breakout") || n.contains("orb breakdown")
    });
    if high {
        "HIGH".into()
    } else if trend_score.abs() >= 75 || !strategies.is_empty() {
        "MEDIUM".into()
    } else {
        "LOW".into()
    }
}

// ── Candle Fetcher (native QuestDB path) ────────────────────────────────

/// Fetch recent OHLCV candles (with timestamps) for a symbol straight from
/// QuestDB via the in-process Postgres-wire pool — the same near-native path
/// Deep Quant uses. No HTTP, no browser, no reqwest. When Kite credentials
/// are present, `load_candles_with_ts` also proactively backfills the symbol.
async fn fetch_candles_for_symbol(
    app: &tauri::AppHandle,
    pool: &sqlx::PgPool,
    symbol: &str,
    timeframe: &str,
) -> Result<Vec<TimedCandle>, String> {
    let rows = crate::commands::deep_quant::load_candles_with_ts(
        Some(app),
        pool,
        symbol,
        timeframe,
        300,
        0, // radar floor: scan whatever exists; errors only on zero data
    )
    .await?;

    Ok(rows
        .into_iter()
        .map(|(ts_millis, c)| TimedCandle {
            time: ts_millis / 1000, // ms → seconds
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: c.volume,
        })
        .collect())
}

// Removed: legacy plain-candle alias (no longer used after FEAT-037 refactor).
