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
async fn load_candles_from_db(pool: &PgPool, symbol: &str, limit: i64) -> Result<Vec<Candle>, String> {
    use sqlx::Row;

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
        return Ok(vec![]);
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
async fn fetch_macro_context(pool: &sqlx::PgPool) -> String {
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

/// Run the full V3 Deep Quant Analysis pipeline for a given symbol.
///
/// # Frontend Usage
/// ```typescript
/// const plan = await invoke<AiExecutionPlan>("run_deep_quant_analysis", {
///   symbol: "RELIANCE",
///   timeframe: "10m"
/// });
/// ```
///
/// # Pipeline
/// 1. Load 200 most recent candles from QuestDB
/// 2. Compute IndicatorState + ConsensusReport
/// 3. Extract RAG context: RSI, MACD line/signal, EMA-9/21, latest close
/// 4. Fetch recent news (with fallback)
/// 5. Call LLM (Hugging Face router → DeepSeek) with data-aware Master Prompt
/// 6. Return structured AiExecutionPlan
#[tauri::command]
pub async fn run_deep_quant_analysis(
    app: AppHandle,
    symbol: String,
    timeframe: String,
) -> Result<AiExecutionPlan, String> {
    use std::time::Instant;
    let t_total = Instant::now();

    // ═══════════════════════════════════════════════════════════════════
    // 🕵️‍♂️ AUDIT 2 - RUST RECEIVE: Verify what Tauri received from the UI
    // ═══════════════════════════════════════════════════════════════════
    println!("🕵️‍♂️ [AUDIT 2 - RUST RECEIVE] Triggered for Symbol: {}, Timeframe: {}", symbol, timeframe);
    println!("🕵️‍♂️ [AUDIT 2 - RUST RECEIVE] Timestamp: {:?}", std::time::SystemTime::now());
    // ═══════════════════════════════════════════════════════════════════

    info!("╔══════════════════════════════════════════════════╗");
    info!("║  Deep Quant Analysis — V3 Pipeline Starting     ║");
    info!("║  Symbol: {:<40} ║", symbol);
    info!("║  Timeframe: {:<37} ║", timeframe);
    info!("╚══════════════════════════════════════════════════╝");

    // ── Step 1: Fetch candles from QuestDB (multi-source waterfall) ────
    let t_step = Instant::now();
    info!("[deep_quant] step=1/5 candle_load_start symbol={}", symbol);

    let pool = app.try_state::<PgPool>().ok_or_else(|| {
        let msg = "QuestDB pool not yet available — try again shortly.";
        warn!("[deep_quant] step=1/5 FAIL {}", msg);
        msg.to_string()
    })?;

    let mut candles = load_candles_from_db(pool.inner(), &symbol, 200)
        .await
        .map_err(|e| {
            warn!("[deep_quant] step=1/5 FAIL elapsed_ms={} err={}", t_step.elapsed().as_millis(), e);
            e
        })?;

    // ── Proactive Kite Fetch (self-healing when data is insufficient) ────
    // If the merged result has fewer than 50 candles, try fetching daily
    // candles from the Kite Historical API directly (like charts.rs does).
    // This covers: indices (NIFTY BANK), newly-added symbols, and cases
    // where only live_ticks have data from the current session.
    if candles.len() < 50 {
        info!(
            "[deep_quant] step=1/5 insufficient_data ({} candles < 50) — triggering proactive Kite fetch for {}",
            candles.len(), symbol
        );

        let api_key = std::env::var("KITE_API_KEY").ok();
        let access_token = std::env::var("KITE_ACCESS_TOKEN").ok();

        if let (Some(api_key), Some(access_token)) = (api_key, access_token) {
            // Resolve instrument token from the local SQLite cache
            let local_token: Option<u32> = {
                app.try_state::<crate::db::DbState>()
                    .and_then(|db_state| {
                        crate::commands::instruments::resolve_instrument_token(
                            &db_state, &symbol
                        )
                    })
            };

            if let Some(token) = local_token {
                info!(
                    "[deep_quant] proactive_fetch: {} token={} — calling Kite Historical API",
                    symbol, token
                );
                match crate::services::history_loader::load_historical_data(
                    pool.inner(),
                    token,
                    &symbol,
                    &api_key,
                    &access_token,
                ).await {
                    Ok(count) => {
                        info!(
                            "[deep_quant] proactive_fetch: {} — {} candles ingested. Retrying DB load.",
                            symbol, count
                        );
                        // Retry the DB load now that data exists
                        candles = load_candles_from_db(pool.inner(), &symbol, 200)
                            .await
                            .unwrap_or_default();
                    }
                    Err(e) => {
                        warn!(
                            "[deep_quant] proactive_fetch: Kite API failed for {}: {}",
                            symbol, e
                        );
                    }
                }
            } else {
                warn!(
                    "[deep_quant] proactive_fetch: could not resolve instrument token for {} — cannot fetch from Kite",
                    symbol
                );
            }
        } else {
            warn!(
                "[deep_quant] proactive_fetch: KITE_API_KEY/KITE_ACCESS_TOKEN not set — cannot fetch for {}",
                symbol
            );
        }
    }

    // ── AI RECEIVER TRACER ──────────────────────────────────────────────
    // Diagnostic: verify exactly what Rust has before calling DeepSeek.
    println!("🧠 [RUST AI RECEIVER] Symbol: {} | Timeframe: {} | Candles received: {} (after merge + proactive fetch)", symbol, timeframe, candles.len());

    if candles.is_empty() {
        let msg = format!(
            "Cannot run AI analysis for {}: No candle data found in any source (historical_candles, historical_intraday, live_ticks) and Kite API fetch failed or unavailable.",
            symbol
        );
        warn!("[deep_quant] step=1/5 FAIL {}", msg);
        return Err(msg);
    }

    // Hard minimum: 15 candles (enough for RSI-14, the tightest core indicator).
    // Indicators needing more data (Bollinger=20, MACD=35, SMA-50/200) will
    // gracefully return NaN, and the NaN guards downstream replace them with
    // safe defaults (latest_close for VWAP/EMAs, 0.0 for ATR/MACD, 50.0 for RSI).
    if candles.len() < 15 {
        let msg = format!(
            "Insufficient data for {}: only {} candles available (minimum 15 required for RSI-14 calculation).",
            symbol,
            candles.len()
        );
        warn!("[deep_quant] step=1/5 FAIL {}", msg);
        return Err(msg);
    }

    // Warn (but don't block) when between 15–49 candles
    if candles.len() < 50 {
        warn!(
            "[deep_quant] step=1/5 LOW_DATA: {} has only {} candles — some indicators (MACD, SMA-50) will use defaults. Analysis accuracy reduced.",
            symbol, candles.len()
        );
        println!(
            "⚠️ [LOW DATA] {} — {} candles (< 50). MACD/Bollinger may be approximate.",
            symbol, candles.len()
        );
    }


    info!(
        "[deep_quant] step=1/5 candle_load_done elapsed_ms={} candles={} symbol={}",
        t_step.elapsed().as_millis(),
        candles.len(),
        symbol,
    );

    // ── Step 2: Compute indicators and consensus ────────────────────────
    let t_step = Instant::now();
    info!("[deep_quant] step=2/5 consensus_compute_start");

    let indicators = IndicatorState::from_candles_basic(&candles);
    let consensus = ConsensusEngine::compile_consensus(&symbol, &candles, &indicators);

    info!(
        "[deep_quant] step=2/5 consensus_compute_done elapsed_ms={} trend={} momentum={} volatility={} volume={} patterns={:?} strategies={:?}",
        t_step.elapsed().as_millis(),
        consensus.trend_score,
        consensus.momentum_state,
        consensus.volatility_state,
        consensus.volume_flow_state,
        consensus.active_patterns,
        consensus.active_strategies
    );

    // ── Step 2b: Extract RAG context for LLM prompt injection ────────
    let latest_close = candles.last().map(|c| c.close).unwrap_or(0.0);
    let rsi_val = if indicators.rsi_14.is_finite() { indicators.rsi_14 } else { 50.0 };
    let macd_val = if indicators.macd_line.is_finite() { indicators.macd_line } else { 0.0 };
    let macd_signal = if indicators.macd_signal.is_finite() { indicators.macd_signal } else { 0.0 };
    let ema9_val = if indicators.ema_9.is_finite() { indicators.ema_9 } else { latest_close };
    let ema21_val = if indicators.ema_21.is_finite() { indicators.ema_21 } else { latest_close };
    // Institutional expansion: VWAP, ATR, Bollinger Bands, Volume Anomaly
    let vwap_val = if indicators.vwap.is_finite() { indicators.vwap } else { latest_close };
    let atr_val = if indicators.atr_14.is_finite() { indicators.atr_14 } else { 0.0 };
    let bb_upper = if indicators.bb_upper.is_finite() { indicators.bb_upper } else { latest_close };
    let bb_mid = if indicators.bb_mid.is_finite() { indicators.bb_mid } else { latest_close };
    let bb_lower = if indicators.bb_lower.is_finite() { indicators.bb_lower } else { latest_close };
    // Volume spike multiplier: latest *non-zero-volume* candle volume / 20-period average.
    //
    // Root cause of "0.00x": the most recent merged bar often has volume = 0
    // because it is a partially-formed live_tick bar that hasn't closed yet.
    // Walk backwards to find the last candle with meaningful volume so the
    // multiplier reflects real activity instead of a stale empty bar.
    let latest_vol = candles.iter().rev()
        .find(|c| c.volume > 1e-6)
        .map(|c| c.volume)
        .unwrap_or(0.0);
    let vol_multiplier = if indicators.average_volume > 1e-6 {
        latest_vol / indicators.average_volume
    } else {
        1.0
    };

    // ── Phase 6 (God Patch) — Microstructure Additions ───────────────────────

    // ── VWEPR Acceleration Coefficient ────────────────────────────────
    // Convert the quant Candle slice to OhlcCandle (vwepr module's type).
    // We synthesise a timestamp by spacing each candle `interval_sec` apart
    // from Unix epoch — the absolute time doesn't affect the polynomial fit.
    let interval_sec: i64 = match timeframe.as_str() {
        "1m"  => 60,
        "3m"  => 180,
        "5m"  => 300,
        "10m" => 600,
        "15m" => 900,
        "30m" => 1_800,
        "60m" | "1h" => 3_600,
        "1d"  => 86_400,
        _     => 600, // sensible default (10m)
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
        1,           // we only need the coefficient, not a long projection
        interval_sec,
    );
    let acceleration_coeff = if acceleration_coeff.is_finite() { acceleration_coeff } else { 0.0 };

    // ── Order Flow Imbalance (OFI) ──────────────────────────────────
    // Kite WebSocket does not currently expose a real-time L2 depth stream
    // in this pipeline. Default to 0.0 (neutral) until the depth feed is
    // plumbed through. The LLM prompt documents this semantic clearly.
    let ofi_val: f64 = 0.0;

    // ── Detected Candlestick Patterns (rolling window scan) ─────────────
    //
    // PatternEngine::analyze() only looks at the last 1-2 candles in the slice,
    // so a single call on the full candle array only catches patterns on the
    // very last bar. We solve this by scanning the final N candles with a
    // rolling window so any pattern formed in the recent session shows up.
    //
    // Window size: 10 bars (captures intraday structure without over-reporting).
    // Deduplication: a pattern is included at most once regardless of how many
    // bars it fired on.
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

        // Slide a window of [2..=PATTERN_SCAN_WINDOW] candles ending at each bar
        // so both single-candle and two-candle patterns are detectable.
        for end in (scan_start + 1)..=candles.len() {
            let window = &candles[scan_start..end];
            for p in PatternEngine::analyze(window) {
                if seen.insert(p.clone()) {
                    found.push(p);
                }
            }
        }

        // Also include patterns the ConsensusEngine already found (deduped)
        for p in &consensus.active_patterns {
            if seen.insert(p.clone()) {
                found.push(p.clone());
            }
        }

        if found.is_empty() { "None".to_string() } else { found.join(", ") }
    };

    info!(
        "[deep_quant] step=2b rag_context: close={:.2} rsi={:.2} macd={:.4} signal={:.4} ema9={:.2} ema21={:.2} vwap={:.2} atr={:.2} bb=[{:.2},{:.2},{:.2}] vol_mult={:.2}x accel={:.6} ofi={:.4} patterns={:?} tf={}",
        latest_close, rsi_val, macd_val, macd_signal, ema9_val, ema21_val, vwap_val, atr_val, bb_upper, bb_mid, bb_lower, vol_multiplier, acceleration_coeff, ofi_val, &detected_patterns, timeframe
    );

    // ═══════════════════════════════════════════════════════════════════
    // 🕵️‍♂️ AUDIT 3 - RUST PROMPT: All extracted math variables BEFORE LLM call
    // ═══════════════════════════════════════════════════════════════════
    println!("🕵️‍♂️ [AUDIT 3 - RUST PROMPT] Extracted Math Variables:");
    println!("  - Latest Close: {:.2}", latest_close);
    println!("  - VWAP: {:.2}, ATR: {:.2}", vwap_val, atr_val);
    println!("  - RSI: {:.2}, Vol Spike: {:.2}x", rsi_val, vol_multiplier);
    println!("  - MACD Line: {:.4}, MACD Signal: {:.4}", macd_val, macd_signal);
    println!("  - EMA-9: {:.2}, EMA-21: {:.2}", ema9_val, ema21_val);
    println!("  - Bollinger: Upper={:.2}, Mid={:.2}, Lower={:.2}", bb_upper, bb_mid, bb_lower);
    println!("  - Consensus Trend Score: {}", consensus.trend_score);
    println!("  - Momentum: {}, Volatility: {}, Volume: {}", consensus.momentum_state, consensus.volatility_state, consensus.volume_flow_state);
    println!("  - Active Patterns: {:?}", consensus.active_patterns);
    println!("  - Active Strategies: {:?}", consensus.active_strategies);
    println!("  - Candles fed to indicators: {}", candles.len());
    println!("  - Timeframe: {}", timeframe);
    // ═══════════════════════════════════════════════════════════════════

    // Emit consensus to frontend for real-time dashboard display
    let _ = app.emit("quant-consensus", serde_json::json!(&consensus));
    info!("[deep_quant] step=2/5 emit=quant-consensus");

    // ── Step 3: Fetch news context ──────────────────────────────────────
    let t_step = Instant::now();
    info!("[deep_quant] step=3/5 news_fetch_start symbol={}", symbol);

    let news = fetch_news_context(&symbol).await;
    info!(
        "[deep_quant] step=3/5 news_fetch_done elapsed_ms={} chars={}",
        t_step.elapsed().as_millis(),
        news.len()
    );

    // ── Step 3b: Fetch macro index context ──────────────────────────────
    let macro_context = fetch_macro_context(pool.inner()).await;
    info!("[deep_quant] step=3b macro_context_fetched: {}", macro_context);

    // ── Step 4: Call LLM via bridge (or mock in test mode) ──────────────
    let t_step = Instant::now();
    let plan = if crate::is_test_mode() {
        info!("[deep_quant] step=4/5 llm_call_start mode=TEST_MODE_MOCK");
        let mocked = crate::mock_ai_execution_plan();
        info!(
            "[deep_quant] step=4/5 llm_call_done elapsed_ms={} mode=mocked conviction={}",
            t_step.elapsed().as_millis(),
            mocked.conviction_score
        );
        mocked
    } else {
        info!("[deep_quant] step=4/5 llm_call_start mode=LIVE");
        match llm::generate_deep_quant_plan(
            &symbol,
            &consensus,
            &news,
            &timeframe,
            &macro_context,
            latest_close,
            vwap_val,
            ofi_val,
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
            acceleration_coeff,
            &detected_patterns,
            Some(&app),
        ).await {
            Ok(p) => {
                info!(
                    "[deep_quant] step=4/5 llm_call_done elapsed_ms={} conviction={}",
                    t_step.elapsed().as_millis(),
                    p.conviction_score
                );
                p
            }
            Err(e) => {
                error!(
                    "[deep_quant] step=4/5 llm_call_FAIL elapsed_ms={} err={}",
                    t_step.elapsed().as_millis(),
                    e
                );
                return Err(e);
            }
        }
    };

    // ── Step 5: Emit result event and return ────────────────────────────
    let _ = app.emit("deep-quant-result", serde_json::json!(&plan));
    info!("[deep_quant] step=5/5 emit=deep-quant-result");

    info!(
        "[deep_quant] PIPELINE_DONE symbol={} total_elapsed_ms={} conviction={}",
        symbol,
        t_total.elapsed().as_millis(),
        plan.conviction_score
    );

    Ok(plan)
}
