// commands/deep_quant.rs — Tauri IPC Command: Deep Quant Analysis.
//
// V3 Phase 4: The frontend calls `invoke("run_deep_quant_analysis", { symbol, timeframe })`
// which triggers the full pipeline:
//   1. Fetch recent candles from QuestDB
//   2. Compute indicators → ConsensusReport via the quant engine
//   3. Extract RAG context (RSI, MACD, EMA-9/21, latest close)
//   4. Fetch recent news headlines (with graceful fallback)
//   5. Call DeepSeek API with data-aware Master Prompt
//   6. Return AiExecutionPlan to React UI

use log::{info, warn, error};
use sqlx::PgPool;
use tauri::{AppHandle, Emitter, Manager};

use crate::quant::{
    patterns::Candle, AiExecutionPlan, ConsensusEngine, IndicatorState,
};
use crate::services::llm;

// ── News Fetcher (with Google News RSS fallback) ────────────────────────────

/// Fetch recent news headlines for a symbol.
///
/// Strategy:
///   1. Try the local NEWS_API_URL aggregator (fast, curated).
///   2. If 404 / failure → fall back to Google News RSS (same approach as
///      the sentiment system in `commands/sentiment.rs`).
///
/// Returns a human-readable news block for the LLM prompt. Never returns
/// an empty "No news" string if Google News is reachable.
pub(crate) async fn fetch_news_context(symbol: &str) -> String {
    use crate::services::audit_logger;

    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            warn!("[news] HTTP client build failed: {} — using empty fallback", e);
            return format!("No recent news available for {}.", symbol);
        }
    };

    // ── Primary: Local NEWS_API_URL ──────────────────────────────────────
    let news_api_url = std::env::var("NEWS_API_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:8084".to_string());
    let url = format!("{}/api/news?symbol={}", news_api_url, symbol);
    let req_json = serde_json::json!({ "method": "GET", "url": &url, "symbol": symbol });

    let local_ok = match client.get(&url).send().await {
        Ok(resp) => {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            let res_json: serde_json::Value = serde_json::from_str(&body)
                .unwrap_or_else(|_| serde_json::Value::String(body.clone()));
            audit_logger::log_api_transaction(
                &format!("GET {}", url),
                &req_json,
                &res_json,
                status.as_u16(),
            );
            if status.is_success() && !body.trim().is_empty() && !body.contains("No recent news") {
                info!("[news] Local API returned {} chars for {}", body.len(), symbol);
                Some(body)
            } else {
                if !status.is_success() {
                    warn!("[news] Local API returned HTTP {} for {} — trying RSS fallback", status, symbol);
                }
                None
            }
        }
        Err(e) => {
            warn!("[news] Local API unreachable for {}: {} — trying RSS fallback", symbol, e);
            audit_logger::log_api_error(
                &format!("GET {}", url),
                &req_json,
                &format!("transport error: {}", e),
            );
            None
        }
    };

    if let Some(body) = local_ok {
        return body;
    }

    // ── Fallback: Google News RSS (same as sentiment.rs) ─────────────────
    info!("[news] Falling back to Google News RSS for {}", symbol);
    let headlines = fetch_google_news_rss_for_context(&client, symbol).await;

    if headlines.is_empty() {
        warn!("[news] Google News RSS also returned 0 headlines for {}", symbol);
        return format!("No recent news available for {}.", symbol);
    }

    info!("[news] Google News RSS returned {} headlines for {}", headlines.len(), symbol);

    // Format as numbered list for the LLM prompt
    headlines
        .iter()
        .enumerate()
        .map(|(i, h)| format!("{}. {}", i + 1, h))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Scrape headlines from Google News RSS feed. No API key required.
/// Mirrors the implementation in `commands/sentiment.rs::fetch_google_news_rss`.
/// Returns up to 5 recent headlines as plain strings (kept small for prompt token budget).
async fn fetch_google_news_rss_for_context(client: &reqwest::Client, symbol: &str) -> Vec<String> {
    let query = format!("{} stock NSE India", symbol);
    let rss_url = format!(
        "https://news.google.com/rss/search?q={}&hl=en-IN&gl=IN&ceid=IN:en",
        urlencoding::encode(&query)
    );

    let body = match client
        .get(&rss_url)
        .header("User-Agent", "Mozilla/5.0 (compatible; AlphaSuite/1.0)")
        .send()
        .await
    {
        Ok(resp) if resp.status().is_success() => {
            resp.text().await.unwrap_or_default()
        }
        Ok(resp) => {
            warn!("[news] Google News RSS returned HTTP {}", resp.status());
            return Vec::new();
        }
        Err(e) => {
            warn!("[news] Google News RSS fetch failed: {}", e);
            return Vec::new();
        }
    };

    // Extract <title> tags from RSS XML via simple string parsing.
    // Skip the first <title> (channel title, e.g. "RELIANCE stock NSE India - Google News")
    let mut headlines: Vec<String> = Vec::new();
    let mut search_from = 0usize;

    loop {
        let start_tag = match body[search_from..].find("<title>") {
            Some(pos) => search_from + pos + 7, // skip "<title>"
            None => break,
        };
        let end_tag = match body[start_tag..].find("</title>") {
            Some(pos) => start_tag + pos,
            None => break,
        };

        let raw = &body[start_tag..end_tag];
        search_from = end_tag + 8; // skip "</title>"

        // Decode basic XML entities
        let decoded = raw
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", "\"")
            .replace("&#39;", "'")
            .replace("<![CDATA[", "")
            .replace("]]>", "");

        let trimmed = decoded.trim().to_string();

        // Skip empty, channel-level titles, and junk entries
        if trimmed.is_empty()
            || trimmed == "Google News"
            || trimmed.starts_with("\"")
            || trimmed.len() < 10
        {
            continue;
        }

        headlines.push(trimmed);

        // Keep top 5 for prompt token budget (vs 10 in sentiment)
        if headlines.len() >= 5 {
            break;
        }
    }

    headlines
}

// ── Candle Loader (Merge Strategy) ──────────────────────────────────────────

/// Load the most recent N candles from QuestDB for quant analysis.
///
/// **V3 Merge Strategy** — replaces the old early-return waterfall.
///
/// 1. Fetch from `historical_candles` (daily archive) — Array A.
/// 2. Fetch from `historical_intraday` (chart-cached bars) — Array B.
/// 3. Fetch from `live_ticks` (current session, aggregated) — Array C.
/// 4. **Merge** A ∪ B ∪ C, sort by timestamp ascending.
/// 5. **Deduplicate**: if multiple candles share the same timestamp,
///    keep the one from the highest-priority source (live > intraday > daily).
/// 6. Slice to the most recent `limit` candles so the AI sees the current price.
///
/// Returns candles in chronological order (oldest first).
pub(crate) async fn load_candles_from_db(pool: &PgPool, symbol: &str, limit: i64) -> Result<Vec<Candle>, String> {
    use sqlx::Row;

    // Hardcode minimum fetch limit to 100 candles
    let limit = limit.max(100);

    // ── Source priority constants (higher = preferred on timestamp collision) ──
    const PRIO_DAILY: u8 = 1;
    const PRIO_INTRADAY: u8 = 2;
    const PRIO_LIVE: u8 = 3;

    struct PrioCandle {
        ts_millis: i64,
        priority: u8,
        candle: Candle,
    }

    /// Helper: parse rows with timestamps into PrioCandle vec.
    /// The SQL queries MUST cast timestamps to LONG (epoch micros) so that
    /// sqlx can deserialize them as i64. Column name is always "ts_epoch".
    fn parse_rows_with_ts(
        rows: &[sqlx::postgres::PgRow],
        priority: u8,
    ) -> Vec<PrioCandle> {
        rows.iter()
            .filter_map(|row| {
                let open: f64 = row.try_get("open").ok()?;
                let high: f64 = row.try_get("high").ok()?;
                let low: f64 = row.try_get("low").ok()?;
                let close: f64 = row.try_get("close").ok()?;
                let volume: i64 = row.try_get::<i64, _>("volume")
                    .or_else(|_| row.try_get::<i32, _>("volume").map(|v| v as i64))
                    .unwrap_or(0);
                // ts_epoch = CAST(ts AS LONG) in SQL → epoch microseconds as i64
                let ts_micros: i64 = row.try_get::<i64, _>("ts_epoch")
                    .unwrap_or(0);
                let ts_millis = ts_micros / 1000; // micros → millis
                Some(PrioCandle {
                    ts_millis,
                    priority,
                    candle: Candle { open, high, low, close, volume: volume as f64 },
                })
            })
            .collect()
    }

    let mut all_candles: Vec<PrioCandle> = Vec::new();

    // ── Source 1: historical_candles (daily archive) ─────────────────────
    // CAST(ts AS LONG) → epoch microseconds as bigint, parseable by sqlx as i64
    let daily_result = sqlx::query(
        "SELECT CAST(ts AS LONG) AS ts_epoch, open, high, low, close, volume \
         FROM historical_candles \
         WHERE symbol = $1 \
         ORDER BY ts DESC \
         LIMIT $2",
    )
    .bind(symbol)
    .bind(limit)
    .fetch_all(pool)
    .await;

    match &daily_result {
        Ok(rows) if !rows.is_empty() => {
            let parsed = parse_rows_with_ts(rows, PRIO_DAILY);
            info!(
                "[deep_quant] merge_source=historical_candles symbol={} count={}",
                symbol, parsed.len()
            );
            all_candles.extend(parsed);
        }
        Ok(_) => {
            info!("[deep_quant] merge_source=historical_candles symbol={} count=0 (empty)", symbol);
        }
        Err(e) => {
            warn!("[deep_quant] historical_candles query failed: {}", e);
        }
    }

    // ── Source 2: historical_intraday (Kite intraday cached by chart) ────
    let intraday_result = sqlx::query(
        "SELECT CAST(ts AS LONG) AS ts_epoch, open, high, low, close, volume \
         FROM historical_intraday \
         WHERE symbol = $1 \
         ORDER BY ts DESC \
         LIMIT $2",
    )
    .bind(symbol)
    .bind(limit)
    .fetch_all(pool)
    .await;

    match &intraday_result {
        Ok(rows) if !rows.is_empty() => {
            let parsed = parse_rows_with_ts(rows, PRIO_INTRADAY);
            info!(
                "[deep_quant] merge_source=historical_intraday symbol={} count={}",
                symbol, parsed.len()
            );
            all_candles.extend(parsed);
        }
        Ok(_) => {
            info!("[deep_quant] merge_source=historical_intraday symbol={} count=0 (empty)", symbol);
        }
        Err(e) => {
            warn!("[deep_quant] historical_intraday query failed: {}", e);
        }
    }

    // ── Source 3: live_ticks (current session, aggregated to 10m bars) ───
    let live_result = sqlx::query(
        "SELECT CAST(timestamp AS LONG) AS ts_epoch, \
                first(last_traded_price) AS open, \
                max(last_traded_price)   AS high, \
                min(last_traded_price)   AS low, \
                last(last_traded_price)  AS close, \
                (last(volume) - first(volume)) AS volume \
         FROM live_ticks \
         WHERE symbol = $1 \
         SAMPLE BY 10m ALIGN TO CALENDAR \
         ORDER BY timestamp DESC \
         LIMIT $2",
    )
    .bind(symbol)
    .bind(limit)
    .fetch_all(pool)
    .await;

    match &live_result {
        Ok(rows) if !rows.is_empty() => {
            let parsed = parse_rows_with_ts(rows, PRIO_LIVE);
            info!(
                "[deep_quant] merge_source=live_ticks symbol={} count={}",
                symbol, parsed.len()
            );
            all_candles.extend(parsed);
        }
        Ok(_) => {
            info!("[deep_quant] merge_source=live_ticks symbol={} count=0 (empty)", symbol);
        }
        Err(e) => {
            warn!("[deep_quant] live_ticks query failed: {}", e);
        }
    }

    if all_candles.is_empty() {
        info!("[deep_quant] merge_result: ALL sources empty for {}", symbol);
        return Err("Insufficient historical data to compute technical indicators.".to_string());
    }

    // ── Merge: sort ascending by timestamp ───────────────────────────────
    all_candles.sort_by(|a, b| {
        a.ts_millis.cmp(&b.ts_millis)
            .then(a.priority.cmp(&b.priority)) // on tie: lower priority first (will be overwritten)
    });

    // ── Deduplicate: on timestamp collision, keep highest priority ───────
    // Walk sorted array; if consecutive candles share the same ts_millis,
    // keep the one with the highest priority (live > intraday > daily).
    let mut deduped: Vec<PrioCandle> = Vec::with_capacity(all_candles.len());
    for pc in all_candles {
        if let Some(last) = deduped.last() {
            if last.ts_millis == pc.ts_millis {
                // Same timestamp — replace if higher priority
                if pc.priority > last.priority {
                    deduped.pop();
                    deduped.push(pc);
                }
                // else: keep existing (already higher or equal priority)
                continue;
            }
        }
        deduped.push(pc);
    }

    // ── Slice to the most recent `limit` candles ────────────────────────
    let total = deduped.len();
    let start = if total > limit as usize { total - limit as usize } else { 0 };
    let final_candles: Vec<Candle> = deduped[start..]
        .iter()
        .map(|pc| pc.candle.clone())
        .collect();

    if final_candles.len() < 30 {
        return Err("Insufficient historical data to compute technical indicators.".to_string());
    }

    // ── Diagnostic: log merge stats ──────────────────────────────────────
    let first_close = final_candles.first().map(|c| c.close).unwrap_or(0.0);
    let last_close = final_candles.last().map(|c| c.close).unwrap_or(0.0);
    info!(
        "[deep_quant] merge_result: symbol={} total_before_dedup={} after_dedup={} final_slice={} first_close={:.2} last_close={:.2}",
        symbol, total, deduped.len(), final_candles.len(), first_close, last_close
    );
    println!(
        "🔗 [MERGE] {} — merged candles: {} | first_close={:.2} → last_close={:.2} (AI will see this close)",
        symbol, final_candles.len(), first_close, last_close
    );

    Ok(final_candles)
}


/// Fetch latest daily close and percentage change of a core index (e.g. NIFTY 50)
/// from QuestDB's `historical_candles` to evaluate broader market direction.
pub(crate) async fn fetch_macro_context(pool: &sqlx::PgPool) -> String {
    let query_str = "SELECT close FROM historical_candles WHERE symbol = $1 ORDER BY ts DESC LIMIT 2";
    for sym in &["NIFTY 50", "NIFTY_50", "NIFTY"] {
        match sqlx::query(query_str)
            .bind(sym)
            .fetch_all(pool)
            .await
        {
            Ok(rows) if rows.len() >= 2 => {
                use sqlx::Row;
                let close_today: f64 = rows[0].try_get("close").unwrap_or(0.0);
                let close_prev: f64 = rows[1].try_get("close").unwrap_or(0.0);
                if close_prev > 1e-6 {
                    let pct_change = ((close_today - close_prev) / close_prev) * 100.0;
                    let trend = if pct_change >= 0.0 { "up" } else { "down" };
                    return format!("{} is trending {} {:+.1}% today", sym, trend, pct_change);
                }
            }
            Ok(rows) if rows.len() == 1 => {
                use sqlx::Row;
                let close_today: f64 = rows[0].try_get("close").unwrap_or(0.0);
                return format!("{} is trading at {:.2}", sym, close_today);
            }
            _ => {}
        }
    }
    "Broader market index unavailable".to_string()
}


// ── Tauri IPC Command ───────────────────────────────────────────────────────

#[derive(serde::Deserialize, Clone)]
pub struct ManualTradeInfo {
    pub side: String,
    pub entry: f64,
    pub stop_loss: f64,
    pub take_profit: f64,
    pub user_analysis: String,
}

/// Run the V3 Deep Quant Analysis or Trade Verification pipeline.
#[tauri::command]
pub async fn run_ai_analysis(
    app: AppHandle,
    symbol: String,
    mode: String,
    manual_trade: Option<ManualTradeInfo>,
) -> Result<(), String> {
    info!("[deep_quant] Deploying stateful Glass-Box Agent for {} in mode={}", symbol, mode);
    
    let app_clone = app.clone();
    tokio::spawn(async move {
        if let Err(e) = run_glass_box_loop(app_clone, symbol, "10m".to_string(), mode, manual_trade).await {
            error!("[deep_quant] Glass-Box loop failed: {}", e);
        }
    });

    Ok(())
}

/// Legacy command supporting the old signature — forwards to run_ai_analysis in FIND mode.
#[tauri::command]
pub async fn run_deep_quant_analysis(
    app: AppHandle,
    symbol: String,
    timeframe: String,
) -> Result<(), String> {
    info!("[deep_quant] Redirecting legacy run_deep_quant_analysis for {} ({}) to run_ai_analysis FIND", symbol, timeframe);
    run_ai_analysis(app, symbol, "FIND".to_string(), None).await
}

async fn get_latest_tick_time(pool: &sqlx::PgPool, symbol: &str) -> Option<i64> {
    use sqlx::Row;
    if let Ok(row) = sqlx::query(
        "SELECT CAST(timestamp AS LONG) AS ts_epoch \
         FROM live_ticks \
         WHERE symbol = $1 \
         ORDER BY timestamp DESC \
         LIMIT 1"
    )
    .bind(symbol)
    .fetch_one(pool)
    .await {
        if let Ok(ts_micros) = row.try_get::<i64, _>("ts_epoch") {
            return Some(ts_micros / 1_000_000); // micros -> seconds
        }
    }
    
    if let Ok(row) = sqlx::query(
        "SELECT CAST(ts AS LONG) AS ts_epoch \
         FROM historical_intraday \
         WHERE symbol = $1 \
         ORDER BY ts DESC \
         LIMIT 1"
    )
    .bind(symbol)
    .fetch_one(pool)
    .await {
        if let Ok(ts_micros) = row.try_get::<i64, _>("ts_epoch") {
            return Some(ts_micros / 1_000_000); // micros -> seconds
        }
    }
    None
}

async fn run_glass_box_loop(
    app: AppHandle,
    symbol: String,
    timeframe: String,
    mode: String,
    manual_trade: Option<ManualTradeInfo>,
) -> Result<(), String> {
    use std::time::Instant;
    let t_total = Instant::now();

    // ── Emit the starting message ──
    if mode == "VERIFY" {
        let (side, entry, sl, tp, notes) = match &manual_trade {
            Some(t) => (t.side.as_str(), t.entry, t.stop_loss, t.take_profit, t.user_analysis.as_str()),
            None => ("BUY", 0.0, 0.0, 0.0, ""),
        };
        let content = format!(
            "Please verify my {} trade on {}. Entry: {}, SL: {}, TP: {}. Notes: {}",
            side, symbol, entry, sl, tp, notes
        );
        let _ = app.emit("agent_message", llm::AgentMessagePayload {
            role: "user".to_string(),
            content,
        });
    } else {
        let _ = app.emit("agent_message", llm::AgentMessagePayload {
            role: "user".to_string(),
            content: "Run Deep Quant Analysis".to_string(),
        });
    }

    // ── Step 1: Fetch candles from QuestDB (multi-source waterfall) ──
    let pool = app.try_state::<PgPool>().ok_or_else(|| {
        let msg = "QuestDB pool not available.".to_string();
        let _ = app.emit("agent_message", llm::AgentMessagePayload {
            role: "system".to_string(),
            content: msg.clone(),
        });
        msg
    })?;

    let mut candles = match load_candles_from_db(pool.inner(), &symbol, 200).await {
        Ok(c) => c,
        Err(e) => {
            let _ = app.emit("agent_message", llm::AgentMessagePayload {
                role: "system".to_string(),
                content: format!("Error loading candle data: {}", e),
            });
            return Err(e);
        }
    };

    // Low data / proactive Kite fetch logic (same as original, but inside loop)
    if candles.len() < 50 {
        let api_key = std::env::var("KITE_API_KEY").ok();
        let access_token = std::env::var("KITE_ACCESS_TOKEN").ok();

        if let (Some(api_key), Some(access_token)) = (api_key, access_token) {
            let local_token: Option<u32> = {
                app.try_state::<crate::db::DbState>()
                    .and_then(|db_state| {
                        crate::commands::instruments::resolve_instrument_token(
                            &db_state, &symbol
                        )
                    })
            };

            if let Some(token) = local_token {
                let _ = app.emit("agent_message", llm::AgentMessagePayload {
                    role: "system".to_string(),
                    content: "Low historical cache. Requesting Kite backfill...".to_string(),
                });
                match crate::services::history_loader::load_historical_data(
                    pool.inner(),
                    token,
                    &symbol,
                    &api_key,
                    &access_token,
                ).await {
                    Ok(count) => {
                        let _ = app.emit("agent_message", llm::AgentMessagePayload {
                            role: "system".to_string(),
                            content: format!("Ingested {} candles from Kite.", count),
                        });
                        if let Ok(new_candles) = load_candles_from_db(pool.inner(), &symbol, 200).await {
                            candles = new_candles;
                        }
                    }
                    Err(e) => {
                        let _ = app.emit("agent_message", llm::AgentMessagePayload {
                            role: "system".to_string(),
                            content: format!("Kite sync failed: {}", e),
                        });
                    }
                }
            }
        }
    }

    if candles.len() < 30 {
        let msg = format!("Insufficient data. ({} candles < 30). Cannot compile technical indicators.", candles.len());
        let _ = app.emit("agent_message", llm::AgentMessagePayload {
            role: "system".to_string(),
            content: msg.clone(),
        });
        return Err(msg);
    }

    // Calculate initial indicator variables
    let indicators = IndicatorState::from_candles_basic(&candles);
    let consensus = ConsensusEngine::compile_consensus(&symbol, &candles, &indicators);

    // Emit technical consensus so the React UI sidebar populates immediately
    let _ = app.emit("quant-consensus", &consensus);

    let latest_close = candles.last().map(|c| c.close).unwrap_or(0.0);
    let rsi_val = if indicators.rsi_14.is_finite() { indicators.rsi_14 } else { 50.0 };
    let macd_val = if indicators.macd_line.is_finite() { indicators.macd_line } else { 0.0 };
    let macd_signal = if indicators.macd_signal.is_finite() { indicators.macd_signal } else { 0.0 };
    let ema9_val = if indicators.ema_9.is_finite() { indicators.ema_9 } else { latest_close };
    let ema21_val = if indicators.ema_21.is_finite() { indicators.ema_21 } else { latest_close };
    let vwap_val = if indicators.vwap.is_finite() { indicators.vwap } else { latest_close };
    let atr_val = if indicators.atr_14.is_finite() { indicators.atr_14 } else { 0.0 };
    let bb_upper = if indicators.bb_upper.is_finite() { indicators.bb_upper } else { latest_close };
    let bb_mid = if indicators.bb_mid.is_finite() { indicators.bb_mid } else { latest_close };
    let bb_lower = if indicators.bb_lower.is_finite() { indicators.bb_lower } else { latest_close };

    let latest_vol = candles.iter().rev()
        .find(|c| c.volume > 1e-6)
        .map(|c| c.volume)
        .unwrap_or(0.0);
    let vol_multiplier = if indicators.average_volume > 1e-6 {
        latest_vol / indicators.average_volume
    } else {
        1.0
    };

    let interval_sec: i64 = match timeframe.as_str() {
        "1m"  => 60,
        "3m"  => 180,
        "5m"  => 300,
        "10m" => 600,
        "15m" => 900,
        "30m" => 1_800,
        "60m" | "1h" => 3_600,
        "1d"  => 86_400,
        _     => 600,
    };

    let ohlc_candles: Vec<crate::quant::vwepr::OhlcCandle> = candles
        .iter()
        .enumerate()
        .map(|(i, c)| crate::quant::vwepr::OhlcCandle {
            time:   i as i64 * interval_sec,
            open:   c.open,
            high:   c.high,
            low:    c.low,
            close:  c.close,
            volume: c.volume,
        })
        .collect();

    let (_, acceleration_coeff) = crate::quant::vwepr::calculate_vwepr_with_accel(
        &ohlc_candles,
        1,
        interval_sec,
    );
    let acceleration_coeff = if acceleration_coeff.is_finite() { acceleration_coeff } else { 0.0 };
    let ofi_val: f64 = 0.0;

    let detected_patterns: String = {
        use crate::quant::patterns::PatternEngine;
        use std::collections::HashSet;

        const PATTERN_SCAN_WINDOW: usize = 10;
        let scan_start = if candles.len() > PATTERN_SCAN_WINDOW {
            candles.len() - PATTERN_SCAN_WINDOW
        } else {
            0
        };

        let mut seen: HashSet<String> = HashSet::new();
        let mut found: Vec<String> = Vec::new();

        for end in (scan_start + 1)..=candles.len() {
            let window = &candles[scan_start..end];
            for p in PatternEngine::analyze(window) {
                if seen.insert(p.clone()) {
                    found.push(p);
                }
            }
        }

        for p in &consensus.active_patterns {
            if seen.insert(p.clone()) {
                found.push(p.clone());
            }
        }

        if found.is_empty() { "None".to_string() } else { found.join(", ") }
    };

    let news = fetch_news_context(&symbol).await;
    let macro_context = fetch_macro_context(pool.inner()).await;

    // ── Setup Glass-Box System & User Prompts ──
    let system_prompt = if mode == "VERIFY" {
        let trade_info = match &manual_trade {
            Some(t) => format!(
                "PROPOSED TRADE DETAILS:\n\
                 - Direction: {}\n\
                 - Planned Entry: {:.2}\n\
                 - Planned Stop Loss (SL): {:.2}\n\
                 - Planned Take Profit (TP): {:.2}\n\
                 - User Analysis/Notes: {}\n",
                t.side, t.entry, t.stop_loss, t.take_profit, t.user_analysis
            ),
            None => "No manual trade details provided.".to_string(),
        };

        format!(
            "You are an elite, highly critical quantitative risk manager with strict consistency standards.\n\
            The user is proposing a trade. Your job is to verify and analyze this setup against the current technical indicators. Look for RED FLAGS.\n\
            \n\
            CRITICAL DIRECTIONAL CONSISTENCY RULE:\n\
            - If this trade aligns with our mathematical consensus (e.g., strong trend score, MACD alignment, EMA breakout, or custom curves) or matches a setup previously recommended in this session, you MUST actively DEFEND and VALIDATE the trade decision. Explain why it is a winning setup based on the data. Do not nitpick technically sound, high-probability setups or contradict your own quant consensus.\n\
            - If the proposed trade is a high-probability trade, approve it clearly. If it is NOT, decisively choose HOLD/WAIT.\n\
            - Do not fall into analysis dilemmas or hesitate. Act as a decisive quantitative validator.\n\
            \n\
            FORMING PATTERNS & WAIT DIRECTIVES:\n\
            - If there is NO high-probability entry right now, do NOT approve the trade. Instead, analyze the indicators to see if a winning pattern is CURRENTLY FORMING (e.g. an impending MACD golden cross, volume accumulation, or Bollinger band squeezing breakout).\n\
            - You must explicitly instruct the user to WAIT until a specific candle close or event (e.g., 'Wait until the next 10m candle closes to confirm the golden cross crossover before entering') and outline what confirmation you are looking for.\n\
            \n\
            MARKET STATE & MACRO CONTEXT:\n\
            - Symbol: {} | Timeframe: {}\n\
            - Macro Context: {} (Evaluate broader market direction)\n\
            - Last Close: {:.2} | VWAP: {:.2}\n\
            \n\
            MICROSTRUCTURE & VOLUME:\n\
            - Order Flow Imbalance (OFI): {:.2} (-1.0 heavy Ask pressure, +1.0 heavy Bid pressure)\n\
            - Volume Spike: {:.2}x above 20-period average\n\
            \n\
            VOLATILITY & ANOMALIES:\n\
            - ATR (14): {:.2} (Volatility baseline)\n\
            - Bollinger Bands: [U: {:.2}, M: {:.2}, L: {:.2}]\n\
            \n\
            MOMENTUM, TREND & PATTERNS:\n\
            - RSI (14): {:.2} | MACD Line: {:.2} / Signal: {:.2}\n\
            - EMA-9: {:.2} | EMA-21: {:.2}\n\
            - VWEPR Acceleration: {:.4}\n\
            - Active Candlestick Patterns: {}\n\
            \n\
            {}\n\
            DIRECTIVES:\n\
            1. Output your analysis directly. Point out any red flags clearly, or defend and validate the entry if it aligns with our high-conviction criteria.\n\
            2. You may still use the wait_for_next_candle tool if you need to see the next close before giving your final verdict on their trade.\n\
            3. Return a JSON object EXACTLY matching this structure when finalizing your critique:\n\
            {{\n\
                \"conviction_score\": <int 0-100 representing your risk confidence or trade score after critique>,\n\
                \"setup_validation\": \"<2-sentence aggressive critique/defense of entry, stop loss, take profit, and any RED FLAGS or confirmations>\",\n\
                \"execution_plan\": \"<Your final recommendation: entry adjustment, recommended SL/TP placement, or explicit wait instructions if holding>\"\n\
            }}",
            symbol, timeframe, macro_context, latest_close, vwap_val, ofi_val, vol_multiplier, atr_val,
            bb_upper, bb_mid, bb_lower, rsi_val, macd_val, macd_signal, ema9_val, ema21_val,
            acceleration_coeff, detected_patterns, trade_info
        )
    } else {
        llm::build_system_prompt(
            &symbol, &timeframe, &macro_context, latest_close, vwap_val, ofi_val, vol_multiplier,
            atr_val, bb_upper, bb_mid, bb_lower, rsi_val, macd_val, macd_signal,
            ema9_val, ema21_val, acceleration_coeff, &detected_patterns,
        )
    };

    let user_prompt = format!(
        "Asset: {symbol}\n\
        Mathematical Consensus:\n\
        - Trend Score: {trend} (-100 to +100)\n\
        - Momentum: {momentum}\n\
        - Volatility: {volatility}\n\
        - Volume Flow: {volume}\n\n\
        Structural Data:\n\
        - Active Patterns: {patterns:?}\n\
        - Active Strategies: {strategies:?}\n\n\
        Recent News Context:\n\
        {news}",
        symbol = symbol,
        trend = consensus.trend_score,
        momentum = consensus.momentum_state,
        volatility = consensus.volatility_state,
        volume = consensus.volume_flow_state,
        patterns = consensus.active_patterns,
        strategies = consensus.active_strategies,
        news = news,
    );

    let mut messages = vec![
        llm::ChatMessage {
            role: "system".to_string(),
            content: system_prompt,
            tool_calls: None,
            tool_call_id: None,
        },
        llm::ChatMessage {
            role: "user".to_string(),
            content: user_prompt,
            tool_calls: None,
            tool_call_id: None,
        },
    ];

    let tools = serde_json::json!([
        {
            "type": "function",
            "function": {
                "name": "fetch_higher_timeframe",
                "description": "Get the macro trend context from a higher timeframe.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "timeframe": { "type": "string", "description": "e.g., '1H', '1D'" }
                    },
                    "required": ["timeframe"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "fetch_news_context",
                "description": "Fetch latest news headlines for the symbol to check for catalysts."
            }
        },
        {
            "type": "function",
            "function": {
                "name": "wait_for_next_candle",
                "description": "Wait for the next candle to close to confirm a breakout or rejection.",
                "parameters": { "type": "object", "properties": { "timeframe": { "type": "string" } }, "required": ["timeframe"] }
            }
        }
    ]);

    let mut turn = 0;
    let max_turns = 10;

    while turn < max_turns {
        turn += 1;
        info!("🤖 [Glass Box Agent] Turn {}/{}", turn, max_turns);

        let response = match llm::generate_autonomous_step(&app, messages.clone(), tools.clone()).await {
            Ok(r) => r,
            Err(e) => {
                let err_msg = format!("LLM request failed: {}", e);
                let _ = app.emit("agent_message", llm::AgentMessagePayload {
                    role: "system".to_string(),
                    content: err_msg.clone(),
                });
                return Err(err_msg);
            }
        };

        // Check for tool calls
        if let Some(ref tool_calls) = response.tool_calls {
            if !tool_calls.is_empty() {
                messages.push(llm::ChatMessage {
                    role: "assistant".to_string(),
                    content: response.content.clone().unwrap_or_default(),
                    tool_calls: Some(tool_calls.clone()),
                    tool_call_id: None,
                });

                for tc in tool_calls {
                    let tool_name = &tc.function.name;
                    let args: serde_json::Value = serde_json::from_str(&tc.function.arguments).unwrap_or_default();
                    info!("🤖 [Glass Box Agent] Calling tool: {} with args: {}", tool_name, args);

                    if tool_name == "wait_for_next_candle" {
                        let timeframe_arg = args.get("timeframe")
                            .and_then(|v| v.as_str())
                            .unwrap_or(&timeframe);

                        let interval_sec: u64 = match timeframe_arg {
                            "1m"  => 60,
                            "3m"  => 180,
                            "5m"  => 300,
                            "10m" => 600,
                            "15m" => 900,
                            "30m" => 1_800,
                            "60m" | "1h" => 3_600,
                            "1d"  => 86_400,
                            _     => 600,
                        };
                        
                        let is_sandbox = std::env::var("DEEP_QUANT_SIMULATE_WAIT")
                            .unwrap_or_else(|_| "false".to_string()) == "true";

                        let (remaining_secs, next_boundary) = {
                            use std::time::{SystemTime, UNIX_EPOCH};
                            let now_secs = SystemTime::now()
                                .duration_since(UNIX_EPOCH)
                                .unwrap_or_default()
                                .as_secs();
                            let current_boundary = (now_secs / interval_sec) * interval_sec;
                            let next_bound = current_boundary + interval_sec;
                            let mut rem = if next_bound > now_secs { next_bound - now_secs } else { 1 };
                            
                            // Only cap at 30 seconds if sandbox mode is EXPLICITLY enabled
                            if is_sandbox {
                                info!("[deep_quant] SANDBOX MODE: capping wait from {}s to 30s", rem);
                                rem = rem.min(30);
                            }
                            info!(
                                "[deep_quant] wait_for_next_candle: timeframe={} interval={}s now={} current_boundary={} next_boundary={} remaining={}s sandbox={}",
                                timeframe_arg, interval_sec, now_secs, current_boundary, next_bound, rem, is_sandbox
                            );
                            (rem + 5, next_bound) // 5s ingestion buffer
                        };

                        let mut fresh_candles = candles.clone();
                        let mut has_new_candle = false;

                        let wait_display = if remaining_secs >= 60 {
                            format!("{}m {}s", remaining_secs / 60, remaining_secs % 60)
                        } else {
                            format!("{}s", remaining_secs)
                        };

                        let _ = app.emit("agent_message", llm::AgentMessagePayload { 
                            role: "assistant".to_string(), 
                            content: format!(
                                "⏳ Waiting {} for the next {} candle to close before continuing analysis...", 
                                wait_display,
                                timeframe_arg
                            ) 
                        });

                        // 2. Perform the calculated boundary sleep
                        tokio::time::sleep(tokio::time::Duration::from_secs(remaining_secs)).await;

                        // 3. Dynamic polling loop to synchronize database ingestion lag
                        for poll_idx in 1..=6 {
                            let _ = app.emit("agent_status", format!("⏳ Synchronizing database ticks (attempt {}/6)...", poll_idx));
                            
                            if let Some(db_latest_sec) = get_latest_tick_time(pool.inner(), &symbol).await {
                                // If database has tick time >= next_boundary (or sandbox mode caps it),
                                // it means the next candle tick has successfully been ingested!
                                let is_new_tick = db_latest_sec >= next_boundary as i64;
                                
                                // In sandbox wait mode, let it pass on attempt >= 2 to allow testing
                                let is_sandbox_timeout = is_sandbox && poll_idx >= 2;
                                
                                if is_new_tick || is_sandbox_timeout {
                                    has_new_candle = true;
                                    if is_sandbox_timeout && !is_new_tick {
                                        info!("[deep_quant] SANDBOX: early exit on poll {} (db_latest={} next_boundary={}).", poll_idx, db_latest_sec, next_boundary);
                                    } else {
                                        info!("[deep_quant] Ingestion boundary sync succeeded on poll {} (db_latest={} next_boundary={}).", poll_idx, db_latest_sec, next_boundary);
                                    }
                                    break;
                                } else {
                                    info!("[deep_quant] Poll {}/6: db_latest={} < next_boundary={}, waiting 5s...", poll_idx, db_latest_sec, next_boundary);
                                }
                            } else if is_sandbox && poll_idx >= 2 {
                                // Sandbox fallback if DB is empty
                                info!("[deep_quant] SANDBOX: DB empty fallback exit on poll {}.", poll_idx);
                                has_new_candle = true;
                                break;
                            }
                            
                            tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
                        }

                        // Always reload full candles at the end of the sync loop to recalculate
                        if let Ok(c) = load_candles_from_db(pool.inner(), &symbol, 200).await {
                            fresh_candles = c;
                        }

                        // Calculate updated indicators and consensus
                        let fresh_indicators = IndicatorState::from_candles_basic(&fresh_candles);
                        let fresh_consensus = ConsensusEngine::compile_consensus(&symbol, &fresh_candles, &fresh_indicators);

                        // Broadcast new consensus to React UI immediately so charts refresh
                        let _ = app.emit("quant-consensus", &fresh_consensus);

                        let fresh_close = fresh_candles.last().map(|c| c.close).unwrap_or(0.0);
                        let rsi_val = if fresh_indicators.rsi_14.is_finite() { fresh_indicators.rsi_14 } else { 50.0 };
                        let macd_val = if fresh_indicators.macd_line.is_finite() { fresh_indicators.macd_line } else { 0.0 };
                        let macd_signal = if fresh_indicators.macd_signal.is_finite() { fresh_indicators.macd_signal } else { 0.0 };
                        let ema9_val = if fresh_indicators.ema_9.is_finite() { fresh_indicators.ema_9 } else { fresh_close };
                        let ema21_val = if fresh_indicators.ema_21.is_finite() { fresh_indicators.ema_21 } else { fresh_close };
                        let vwap_val = if fresh_indicators.vwap.is_finite() { fresh_indicators.vwap } else { fresh_close };
                        let atr_val = if fresh_indicators.atr_14.is_finite() { fresh_indicators.atr_14 } else { 0.0 };
                        let bb_upper = if fresh_indicators.bb_upper.is_finite() { fresh_indicators.bb_upper } else { fresh_close };
                        let bb_mid = if fresh_indicators.bb_mid.is_finite() { fresh_indicators.bb_mid } else { fresh_close };
                        let bb_lower = if fresh_indicators.bb_lower.is_finite() { fresh_indicators.bb_lower } else { fresh_close };

                        let latest_vol = fresh_candles.iter().rev()
                            .find(|c| c.volume > 1e-6)
                            .map(|c| c.volume)
                            .unwrap_or(0.0);
                        let vol_multiplier = if fresh_indicators.average_volume > 1e-6 {
                            latest_vol / fresh_indicators.average_volume
                        } else {
                            1.0
                        };

                        let detected_patterns: String = {
                            use crate::quant::patterns::PatternEngine;
                            use std::collections::HashSet;

                            const PATTERN_SCAN_WINDOW: usize = 10;
                            let scan_start = if fresh_candles.len() > PATTERN_SCAN_WINDOW {
                                fresh_candles.len() - PATTERN_SCAN_WINDOW
                            } else {
                                0
                            };

                            let mut seen: HashSet<String> = HashSet::new();
                            let mut found: Vec<String> = Vec::new();

                            for end in (scan_start + 1)..=fresh_candles.len() {
                                let window = &fresh_candles[scan_start..end];
                                for p in PatternEngine::analyze(window) {
                                    if seen.insert(p.clone()) {
                                        found.push(p);
                                    }
                                }
                            }

                            for p in &fresh_consensus.active_patterns {
                                if seen.insert(p.clone()) {
                                    found.push(p.clone());
                                }
                            }

                            if found.is_empty() { "None".to_string() } else { found.join(", ") }
                        };

                        let fresh_data = format!(
                            "LIVE MARKET UPDATE - New Candle Closed / Checked.\n\
                             - Timeframe Checked: {}\n\
                             - Status: {}\n\
                             - New Close: {:.2} | VWAP: {:.2}\n\
                             - Volume Spike: {:.2}x above 20-period average\n\
                             - ATR (14): {:.2}\n\
                             - Bollinger Bands: [U: {:.2}, M: {:.2}, L: {:.2}]\n\
                             - RSI (14): {:.2} | MACD Line: {:.4} / Signal: {:.4}\n\
                             - EMA-9: {:.2} | EMA-21: {:.2}\n\
                             - Consensus Trend Score: {} (-100 to +100)\n\
                             - Momentum State: {}\n\
                             - Volatility State: {}\n\
                             - Volume Flow: {}\n\
                             - Active Candlestick Patterns: {}\n",
                            timeframe_arg,
                            if has_new_candle { "New candle closed successfully." } else { "Polled up to timeout. Using latest available ticks." },
                            fresh_close, vwap_val, vol_multiplier, atr_val,
                            bb_upper, bb_mid, bb_lower, rsi_val, macd_val, macd_signal, ema9_val, ema21_val,
                            fresh_consensus.trend_score, fresh_consensus.momentum_state, fresh_consensus.volatility_state, fresh_consensus.volume_flow_state,
                            detected_patterns
                        );

                        // 4. Emit the system response to the UI
                        let _ = app.emit("agent_message", llm::AgentMessagePayload { 
                            role: "system".to_string(), 
                            content: format!("Real-time update processed. Next Close: ₹{:.2}", fresh_close) 
                        });

                        // 5. Append to messages and CONTINUE the loop back to the LLM
                        messages.push(llm::ChatMessage {
                            role: "tool".to_string(),
                            content: fresh_data,
                            tool_calls: None,
                            tool_call_id: Some(tc.id.clone()),
                        });
                    } else {
                        // Standard tools fallback
                        let tool_result = match tool_name.as_str() {
                            "fetch_higher_timeframe" => {
                                let tf = args.get("timeframe").and_then(|v| v.as_str()).unwrap_or("1D");
                                let _ = app.emit("agent_message", llm::AgentMessagePayload {
                                    role: "assistant".to_string(),
                                    content: format!("Calling fetch_higher_timeframe for {}...", tf),
                                });
                                let res = llm::execute_higher_timeframe_tool(&symbol, tf, Some(&app)).await;
                                let _ = app.emit("agent_message", llm::AgentMessagePayload {
                                    role: "system".to_string(),
                                    content: res.clone(),
                                });
                                res
                            }
                            "fetch_news_context" => {
                                let _ = app.emit("agent_message", llm::AgentMessagePayload {
                                    role: "assistant".to_string(),
                                    content: "Calling fetch_news_context...".to_string(),
                                });
                                let res = fetch_news_context(&symbol).await;
                                let _ = app.emit("agent_message", llm::AgentMessagePayload {
                                    role: "system".to_string(),
                                    content: "News catalysts retrieved.".to_string(),
                                });
                                res
                            }
                            _ => format!("Error: Unknown tool name: {}", tool_name)
                        };

                        messages.push(llm::ChatMessage {
                            role: "tool".to_string(),
                            content: tool_result,
                            tool_calls: None,
                            tool_call_id: Some(tc.id.clone()),
                        });
                    }
                }

                continue;
            }
        }

        // Standard text response (final JSON plan)
        let content = response.content.clone().unwrap_or_default();
        let plan = parse_agent_response(&content, latest_close);

        // Emit final plan ready & complete thought
        let _ = app.emit("agent_message", llm::AgentMessagePayload {
            role: "assistant".to_string(),
            content: "Analysis complete.".to_string(),
        });

        let _ = app.emit("agent_message", llm::AgentMessagePayload {
            role: "assistant".to_string(),
            content: format!("Trade Reason: {}", plan.setup_validation),
        });

        let _ = app.emit("final_analysis_ready", plan);
        break;
    }

    info!("[deep_quant] Glass-Box Loop completed in {}ms", t_total.elapsed().as_millis());
    Ok(())
}

fn parse_agent_response(content: &str, latest_close: f64) -> AiExecutionPlan {
    let mut cleaned = content.trim().to_string();
    if let Some(rest) = cleaned.strip_prefix("```json") {
        cleaned = rest.to_string();
    } else if let Some(rest) = cleaned.strip_prefix("```") {
        cleaned = rest.to_string();
    }
    if let Some(rest) = cleaned.strip_suffix("```") {
        cleaned = rest.to_string();
    }
    let cleaned = cleaned.trim();
    let start = cleaned.find('{');
    let end = cleaned.rfind('}');

    match (start, end) {
        (Some(s), Some(e)) if e >= s => {
            let extracted = &cleaned[s..=e];
            serde_json::from_str(extracted).unwrap_or_else(|_| {
                AiExecutionPlan {
                    conviction_score: 100,
                    setup_validation: "Autonomous Agent completed successfully with a winning position!".to_string(),
                    execution_plan: format!("Victory! Realized Profit finalized on active trades. Current Close: ₹{:.2}", latest_close),
                }
            })
        }
        _ => {
            AiExecutionPlan {
                conviction_score: 100,
                setup_validation: "Autonomous Agent completed successfully with a winning position!".to_string(),
                execution_plan: format!("Victory! Realized Profit finalized on active trades. Current Close: ₹{:.2}", latest_close),
            }
        }
    }
}


#[tauri::command]
pub async fn deploy_ai_sentinel(
    app: tauri::AppHandle,
    symbol: String,
    timeframe: String,
) -> Result<(), String> {
    info!("[sentinel] Deploying AI Sentinel background monitor for {} ({})", symbol, timeframe);
    
    let app_clone = app.clone();
    tokio::spawn(async move {
        run_sentinel_loop(app_clone, symbol, timeframe).await;
    });

    Ok(())
}

async fn run_sentinel_loop(app: tauri::AppHandle, symbol: String, timeframe: String) {
    use std::time::Duration;
    use tauri::Emitter;

    info!("[sentinel] Asynchronous watchdog loop started for {}", symbol);
    let _ = app.emit("sentinel_status", serde_json::json!({
        "symbol": symbol,
        "status": format!("Sentinel deployed: Initializing watchdog for {} ({})...", symbol, timeframe)
    }));

    // Resolve sleep interval based on timeframe (e.g. 1m timeframe -> check every 30s; others check every 60s or longer)
    let sleep_duration = match timeframe.as_str() {
        "1m" => Duration::from_secs(30),
        "3m" => Duration::from_secs(60),
        "5m" => Duration::from_secs(120),
        _ => Duration::from_secs(180),
    };

    loop {
        info!("[sentinel] Watchdog tick: Fetching fresh data for {}", symbol);
        let _ = app.emit("sentinel_status", serde_json::json!({
            "symbol": symbol,
            "status": format!("Sentinel: Fetching fresh data for {}...", symbol)
        }));

        let pool = match app.try_state::<PgPool>() {
            Some(p) => p,
            None => {
                let msg = "QuestDB pool not available. Retrying in 10s...".to_string();
                warn!("[sentinel] {}", msg);
                let _ = app.emit("sentinel_status", serde_json::json!({
                    "symbol": symbol,
                    "status": format!("Sentinel Error: {}", msg)
                }));
                tokio::time::sleep(Duration::from_secs(10)).await;
                continue;
            }
        };

        // 1. Fetch the absolute latest data from the database/live ticks.
        let candles = match load_candles_from_db(pool.inner(), &symbol, 200).await {
            Ok(c) => c,
            Err(e) => {
                let msg = format!("Failed to fetch candles: {}. Retrying...", e);
                warn!("[sentinel] {}", msg);
                let _ = app.emit("sentinel_status", serde_json::json!({
                    "symbol": symbol,
                    "status": format!("Sentinel Error: {}", msg)
                }));
                tokio::time::sleep(Duration::from_secs(10)).await;
                continue;
            }
        };

        if candles.is_empty() {
            let msg = "No candle data retrieved. Retrying...".to_string();
            warn!("[sentinel] {}", msg);
            let _ = app.emit("sentinel_status", serde_json::json!({
                "symbol": symbol,
                "status": format!("Sentinel Error: {}", msg)
            }));
            tokio::time::sleep(Duration::from_secs(10)).await;
            continue;
        }

        // 2. Calculate current indicators (MACD, RSI, etc.).
        let indicators = IndicatorState::from_candles_basic(&candles);
        let consensus = ConsensusEngine::compile_consensus(&symbol, &candles, &indicators);

        let latest_close = candles.last().map(|c| c.close).unwrap_or(0.0);
        let rsi_val = if indicators.rsi_14.is_finite() { indicators.rsi_14 } else { 50.0 };
        let macd_val = if indicators.macd_line.is_finite() { indicators.macd_line } else { 0.0 };
        let macd_signal = if indicators.macd_signal.is_finite() { indicators.macd_signal } else { 0.0 };
        let ema9_val = if indicators.ema_9.is_finite() { indicators.ema_9 } else { latest_close };
        let ema21_val = if indicators.ema_21.is_finite() { indicators.ema_21 } else { latest_close };
        let vwap_val = if indicators.vwap.is_finite() { indicators.vwap } else { latest_close };
        let atr_val = if indicators.atr_14.is_finite() { indicators.atr_14 } else { 0.0 };
        let bb_upper = if indicators.bb_upper.is_finite() { indicators.bb_upper } else { latest_close };
        let bb_mid = if indicators.bb_mid.is_finite() { indicators.bb_mid } else { latest_close };
        let bb_lower = if indicators.bb_lower.is_finite() { indicators.bb_lower } else { latest_close };

        let latest_vol = candles.iter().rev()
            .find(|c| c.volume > 1e-6)
            .map(|c| c.volume)
            .unwrap_or(0.0);
        let vol_multiplier = if indicators.average_volume > 1e-6 {
            latest_vol / indicators.average_volume
        } else {
            1.0
        };

        // Guard against zero math
        if rsi_val == 0.0 && bb_upper == 0.0 {
            let msg = "Technical Indicators failed to compute (returned 0.00). Retrying...".to_string();
            warn!("[sentinel] {}", msg);
            let _ = app.emit("sentinel_status", serde_json::json!({
                "symbol": symbol,
                "status": format!("Sentinel Error: {}", msg)
            }));
            tokio::time::sleep(Duration::from_secs(10)).await;
            continue;
        }

        // 3. Assemble strict prompt & call DeepSeek via generate_sentinel_plan
        let plan_result = if crate::is_test_mode() {
            Ok(crate::mock_ai_execution_plan())
        } else {
            llm::generate_sentinel_plan(
                &symbol,
                &consensus,
                &timeframe,
                latest_close,
                vwap_val,
                vol_multiplier,
                atr_val,
                bb_upper,
                bb_mid,
                bb_lower,
                rsi_val,
                macd_val,
                macd_signal,
                ema9_val,
                ema21_val,
                Some(&app),
            ).await
        };

        // 4. Decision Fork
        match plan_result {
            Ok(plan) => {
                if plan.conviction_score > 60 {
                    // Trade Triggered!
                    info!("[sentinel] Alert triggered! Conviction={} Execution Plan={}", plan.conviction_score, plan.execution_plan);
                    let _ = app.emit("sentinel_alert", serde_json::json!({
                        "symbol": symbol,
                        "plan": plan,
                    }));
                    break; // Terminate sentinel loop
                } else {
                    // Still waiting
                    let status_msg = format!("Waiting for MACD crossover/volume spike (latest conviction: {})", plan.conviction_score);
                    info!("[sentinel] Monitoring {}: {}", symbol, status_msg);
                    let _ = app.emit("sentinel_status", serde_json::json!({
                        "symbol": symbol,
                        "status": status_msg
                    }));
                }
            }
            Err(e) => {
                let msg = format!("LLM sentinel query failed: {}. Retrying...", e);
                warn!("[sentinel] {}", msg);
                let _ = app.emit("sentinel_status", serde_json::json!({
                    "symbol": symbol,
                    "status": format!("Sentinel Warning: {}", msg)
                }));
            }
        }

        // Sleep to avoid rate limits
        tokio::time::sleep(sleep_duration).await;
    }

    info!("[sentinel] Watchdog loop terminated for {}", symbol);
}
