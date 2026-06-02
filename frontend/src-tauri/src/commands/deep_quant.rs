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

pub fn get_kite_credentials() -> (String, String) {
    let mut api_key = std::env::var("KITE_API_KEY").unwrap_or_default();
    let mut access_token = std::env::var("KITE_ACCESS_TOKEN").unwrap_or_default();

    if let Ok(mut current_dir) = std::env::current_dir() {
        loop {
            let env_path = current_dir.join(".env");
            if env_path.is_file() {
                if let Ok(content) = std::fs::read_to_string(env_path) {
                    for line in content.lines() {
                        let line = line.trim();
                        if line.starts_with('#') || !line.contains('=') {
                            continue;
                        }
                        let parts: Vec<&str> = line.splitn(2, '=').collect();
                        if parts.len() == 2 {
                            let key = parts[0].trim();
                            let val = parts[1].trim().trim_matches('"').trim_matches('\'');
                            if key == "KITE_API_KEY" && !val.is_empty() {
                                api_key = val.to_string();
                            } else if key == "KITE_ACCESS_TOKEN" && !val.is_empty() {
                                access_token = val.to_string();
                            }
                        }
                    }
                }
                break;
            }
            if !current_dir.pop() {
                break;
            }
        }
    }

    (api_key, access_token)
}

// ── News Fetcher (with Google News RSS fallback) ────────────────────────────

/// Fetch recent news headlines for a symbol.
///
/// Strategy:
///   1. If `NEWS_API_URL` is explicitly configured, try that aggregator first.
///      (There is no local news service shipped in this repo, so by default
///      this step is skipped entirely — we do NOT probe a phantom endpoint.)
///   2. Otherwise / on failure → Google News RSS (no API key required).
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

    // ── Optional primary: explicitly-configured NEWS_API_URL ─────────────
    // Only attempted when the operator has set NEWS_API_URL. We no longer
    // default to a non-existent local endpoint (which always 404'd and just
    // wasted a round-trip before falling through to RSS).
    if let Ok(news_api_url) = std::env::var("NEWS_API_URL") {
        let news_api_url = news_api_url.trim().trim_end_matches('/').to_string();
        if !news_api_url.is_empty() {
            let url = format!("{}/api/news?symbol={}", news_api_url, symbol);
            let req_json = serde_json::json!({ "method": "GET", "url": &url, "symbol": symbol });

            match client.get(&url).send().await {
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
                        info!("[news] NEWS_API_URL returned {} chars for {}", body.len(), symbol);
                        return body;
                    }
                    warn!("[news] NEWS_API_URL returned HTTP {} for {} — falling back to RSS", status, symbol);
                }
                Err(e) => {
                    warn!("[news] NEWS_API_URL unreachable for {}: {} — falling back to RSS", symbol, e);
                    audit_logger::log_api_error(
                        &format!("GET {}", url),
                        &req_json,
                        &format!("transport error: {}", e),
                    );
                }
            }
        }
    }

    // ── Google News RSS (default source) ─────────────────────────────────
    info!("[news] Fetching Google News RSS for {}", symbol);
    let headlines = fetch_google_news_rss_for_context(&client, symbol).await;

    if headlines.is_empty() {
        warn!("[news] Google News RSS returned 0 headlines for {}", symbol);
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
pub(crate) async fn load_candles_from_db(
    app: Option<&AppHandle>,
    pool: &PgPool,
    symbol: &str,
    timeframe: &str,
    limit: i64,
) -> Result<Vec<Candle>, String> {
    // AI / consensus callers need a meaningful indicator window — keep the
    // historical 30-candle floor for them.
    let timed = load_candles_with_ts(app, pool, symbol, timeframe, limit, 30).await?;
    Ok(timed.into_iter().map(|(_, c)| c).collect())
}

/// Timestamp-preserving variant of [`load_candles_from_db`].
///
/// Returns `(ts_millis, Candle)` pairs in ascending chronological order. The
/// Quant Radar's located scanner needs each candle's UNIX timestamp to place
/// pattern / strategy markers on the correct bar, so it consumes this variant
/// directly. [`load_candles_from_db`] is now a thin wrapper that drops the
/// timestamps for callers (AI / consensus) that only need the OHLCV series.
///
/// `min_candles` is the floor below which the function errors with
/// "insufficient data". The AI path uses 30 (enough for indicators); the
/// radar passes a low value so it can still locate candle patterns on
/// timeframes that have only just begun caching.
pub(crate) async fn load_candles_with_ts(
    app: Option<&AppHandle>,
    pool: &PgPool,
    symbol: &str,
    timeframe: &str,
    limit: i64,
    min_candles: usize,
) -> Result<Vec<(i64, Candle)>, String> {
    use sqlx::Row;

    // Hardcode minimum fetch limit to 100 candles
    let limit = limit.max(100);

    // ── Timeframe family classification ─────────────────────────────────
    // Case matters here: "1M" = 1 month (daily family), "1m" = 1 minute
    // (intraday family). Lowercasing first — as the intraday branches do —
    // would collide month with minute, so daily-family detection is done on
    // the raw string with case-sensitive checks for the W / M suffixes.
    let is_weekly = timeframe == "1W"
        || timeframe.eq_ignore_ascii_case("1week")
        || timeframe.eq_ignore_ascii_case("week");
    let is_monthly = timeframe == "1M"
        || timeframe.eq_ignore_ascii_case("1month")
        || timeframe.eq_ignore_ascii_case("1mon")
        || timeframe.eq_ignore_ascii_case("month");
    let is_plain_daily = timeframe.eq_ignore_ascii_case("1d")
        || timeframe.eq_ignore_ascii_case("day");
    // Anything resolving to the daily archive (day / week / month).
    let is_daily = is_plain_daily || is_weekly || is_monthly;

    // QuestDB SAMPLE BY unit for daily-family aggregation from the daily
    // archive. Daily needs no resampling; week = 7d; month = 30d (matching
    // the convention already used by get_historical_view in charts.rs).
    let daily_sample: Option<&str> = if is_weekly {
        Some("7d")
    } else if is_monthly {
        Some("30d")
    } else {
        None
    };

    // ── Proactive Zerodha Kite loading if AppHandle is provided ──────────────────
    if let Some(app) = app {
        let (api_key_val, access_token_val) = get_kite_credentials();
        let api_key = if !api_key_val.is_empty() { Some(api_key_val) } else { None };
        let access_token = if !access_token_val.is_empty() { Some(access_token_val) } else { None };

        if let (Some(api_key), Some(access_token)) = (api_key, access_token) {
            let local_token: Option<u32> = {
                app.try_state::<crate::db::DbState>()
                    .and_then(|db_state| {
                        crate::commands::instruments::resolve_instrument_token(
                            &db_state, symbol
                        )
                    })
            };

            if let Some(token) = local_token {
                if is_daily {
                    info!("[deep_quant] Proactive fetch daily: {} (token {})", symbol, token);
                    let _ = crate::services::history_loader::load_historical_data(
                        pool,
                        token,
                        symbol,
                        &api_key,
                        &access_token,
                    ).await;
                } else {
                    let base_tf = match timeframe.to_lowercase().as_str() {
                        "1m" | "1min" | "2m" | "2min" | "4m" | "4min" => "1m",
                        "3m" | "3min" => "3m",
                        "5m" | "5min" => "5m",
                        "10m" | "10min" => "10m",
                        "15m" | "15min" | "75m" | "75min" | "125m" | "125min" => "15m",
                        "30m" | "30min" => "30m",
                        "1h" | "60m" | "2h" | "120m" | "3h" | "180m" | "4h" | "240m" => "1h",
                        _ => "10m",
                    };
                    info!("[deep_quant] Proactive fetch intraday: {} (token {}) [base_tf={}]", symbol, token, base_tf);
                    let _ = crate::services::history_loader::load_intraday_data(
                        pool,
                        token,
                        symbol,
                        base_tf,
                        &api_key,
                        &access_token,
                    ).await;
                }
            }
        }
    }

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
                let ts_dt = row.try_get::<chrono::NaiveDateTime, _>("ts");
                let ts_i64 = row.try_get::<i64, _>("ts");
                
                let ts_micros = match ts_dt {
                    Ok(dt) => dt.and_utc().timestamp_micros(),
                    Err(e1) => match ts_i64 {
                        Ok(val) => val,
                        Err(e2) => {
                            error!("[deep_quant] ts parsing failed! NaiveDateTime err: {}, i64 err: {}", e1, e2);
                            0
                        }
                    }
                };
                let ts_millis = ts_micros / 1000;
                Some(PrioCandle {
                    ts_millis,
                    priority,
                    candle: Candle { open, high, low, close, volume: volume as f64 },
                })
            })
            .collect()
    }

    let mut all_candles: Vec<PrioCandle> = Vec::new();

    if is_daily {
        // ── Source 1: historical_candles (daily archive) ─────────────────────
        // Plain daily reads rows as-is; weekly/monthly aggregate the daily
        // archive via QuestDB SAMPLE BY so the scanner sees true W/M candles.
        let daily_result = if let Some(unit) = daily_sample {
            let agg_query = format!(
                "SELECT ts, \
                        first(open) AS open, \
                        max(high) AS high, \
                        min(low) AS low, \
                        last(close) AS close, \
                        sum(volume) AS volume \
                 FROM historical_candles \
                 WHERE symbol = $1 \
                 SAMPLE BY {} ALIGN TO CALENDAR \
                 ORDER BY ts DESC \
                 LIMIT $2",
                unit
            );
            sqlx::query(&agg_query)
                .bind(symbol)
                .bind(limit)
                .fetch_all(pool)
                .await
        } else {
            sqlx::query(
                "SELECT ts, open, high, low, close, volume \
                 FROM historical_candles \
                 WHERE symbol = $1 \
                 ORDER BY ts DESC \
                 LIMIT $2",
            )
            .bind(symbol)
            .bind(limit)
            .fetch_all(pool)
            .await
        };

        match &daily_result {
            Ok(rows) if !rows.is_empty() => {
                let parsed = parse_rows_with_ts(rows, PRIO_DAILY);
                info!(
                    "[deep_quant] merge_source=historical_candles symbol={} count={}",
                    symbol, parsed.len()
                );
                all_candles.extend(parsed);
            }
            Ok(_)=> {
                info!("[deep_quant] merge_source=historical_candles symbol={} count=0 (empty)", symbol);
            }
            Err(e) => {
                warn!("[deep_quant] historical_candles query failed: {}", e);
            }
        }
    } else {
        // ── Source 2: historical_intraday (filtered by timeframe) ────────────
        let base_tf = match timeframe.to_lowercase().as_str() {
            "1m" | "1min" | "2m" | "2min" | "4m" | "4min" => "1m",
            "3m" | "3min" => "3m",
            "5m" | "5min" => "5m",
            "10m" | "10min" => "10m",
            "15m" | "15min" | "75m" | "75min" | "125m" | "125min" => "15m",
            "30m" | "30min" => "30m",
            "1h" | "60m" | "2h" | "120m" | "3h" | "180m" | "4h" | "240m" => "1h",
            _ => "10m",
        };
        let is_derived = timeframe.to_lowercase() != base_tf;

        let intraday_result = if is_derived {
            let sample_interval = match timeframe.to_lowercase().as_str() {
                "2m" | "2min" => "2m",
                "4m" | "4min" => "4m",
                "75m" | "75min" => "75m",
                "125m" | "125min" => "125m",
                "2h" | "120m" => "2h",
                "3h" | "180m" => "3h",
                "4h" | "240m" => "4h",
                _ => timeframe,
            };
            let derived_query = format!(
                "SELECT ts, \
                        first(open) AS open, \
                        max(high) AS high, \
                        min(low) AS low, \
                        last(close) AS close, \
                        sum(volume) AS volume \
                 FROM historical_intraday \
                 WHERE symbol = $1 AND timeframe = $2 \
                 SAMPLE BY {} ALIGN TO CALENDAR \
                 ORDER BY ts DESC \
                 LIMIT $3",
                sample_interval
            );
            sqlx::query(&derived_query)
                .bind(symbol)
                .bind(base_tf)
                .bind(limit)
                .fetch_all(pool)
                .await
        } else {
            sqlx::query(
                "SELECT ts, open, high, low, close, volume \
                 FROM historical_intraday \
                 WHERE symbol = $1 AND timeframe = $2 \
                 ORDER BY ts DESC \
                 LIMIT $3",
            )
            .bind(symbol)
            .bind(timeframe)
            .bind(limit)
            .fetch_all(pool)
            .await
        };

        match &intraday_result {
            Ok(rows) if !rows.is_empty() => {
                let parsed = parse_rows_with_ts(rows, PRIO_INTRADAY);
                info!(
                    "[deep_quant] merge_source=historical_intraday symbol={} timeframe={} count={}",
                    symbol, timeframe, parsed.len()
                );
                all_candles.extend(parsed);
            }
            Ok(_) => {
                info!("[deep_quant] merge_source=historical_intraday symbol={} timeframe={} count=0 (empty)", symbol, timeframe);
            }
            Err(e) => {
                warn!("[deep_quant] historical_intraday query failed: {}", e);
            }
        }
    }

    // ── Source 3: live_ticks (dynamically sampled by timeframe) ──────────
    // Only for intraday + plain-daily timeframes. Weekly/monthly aggregate
    // the daily archive exclusively — mixing in current-session live ticks
    // sampled at a finer unit would corrupt the W/M candle series.
    if !is_weekly && !is_monthly {
    let sample_interval = match timeframe.to_lowercase().as_str() {
        "1m" | "1min" => "1m",
        "3m" | "3min" => "3m",
        "5m" | "5min" => "5m",
        "15m" | "15min" => "15m",
        "30m" | "30min" => "30m",
        "1h" | "60m" | "1hour" => "1h",
        "4h" | "240m" | "4hour" => "4h",
        "1d" | "day" => "1d",
        _ => "10m",
    };

    let live_query = format!(
        "SELECT timestamp AS ts, \
                first(last_traded_price) AS open, \
                max(last_traded_price)   AS high, \
                min(last_traded_price)   AS low, \
                last(last_traded_price)  AS close, \
                (last(volume) - first(volume)) AS volume \
         FROM live_ticks \
         WHERE symbol = $1 \
         SAMPLE BY {} ALIGN TO CALENDAR \
         ORDER BY timestamp DESC \
         LIMIT $2",
        sample_interval
    );

    let live_result = sqlx::query(&live_query)
        .bind(symbol)
        .bind(limit)
        .fetch_all(pool)
        .await;

    match &live_result {
        Ok(rows) if !rows.is_empty() => {
            let parsed = parse_rows_with_ts(rows, PRIO_LIVE);
            info!(
                "[deep_quant] merge_source=live_ticks symbol={} sample={} count={}",
                symbol, sample_interval, parsed.len()
            );
            all_candles.extend(parsed);
        }
        Ok(_) => {
            info!("[deep_quant] merge_source=live_ticks symbol={} sample={} count=0 (empty)", symbol, sample_interval);
        }
        Err(e) => {
            warn!("[deep_quant] live_ticks query failed for sample={}: {}", sample_interval, e);
        }
    }
    }

    if all_candles.is_empty() {
        info!("[deep_quant] merge_result: ALL sources empty for {}", symbol);
        return Err("Insufficient historical data to compute technical indicators.".to_string());
    }

    // ── Merge: sort ascending by timestamp ───────────────────────────────
    all_candles.sort_by(|a, b| {
        a.ts_millis.cmp(&b.ts_millis)
            .then(a.priority.cmp(&b.priority))
    });

    // ── Deduplicate: on timestamp collision, keep highest priority ───────
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

    // ── Slice to the most recent `limit` candles ────────────────────────
    let total = deduped.len();
    let start = if total > limit as usize { total - limit as usize } else { 0 };
    let final_candles: Vec<(i64, Candle)> = deduped[start..]
        .iter()
        .map(|pc| (pc.ts_millis, pc.candle.clone()))
        .collect();

    if final_candles.len() < min_candles {
        return Err(format!(
            "Insufficient data for {} [{}]: {} candle(s) available, need {}.",
            symbol, timeframe, final_candles.len(), min_candles
        ));
    }

    let first_close = final_candles.first().map(|(_, c)| c.close).unwrap_or(0.0);
    let last_close = final_candles.last().map(|(_, c)| c.close).unwrap_or(0.0);
    info!(
        "[deep_quant] merge_result: symbol={} timeframe={} total_before_dedup={} after_dedup={} final_slice={} first_close={:.2} last_close={:.2}",
        symbol, timeframe, total, deduped.len(), final_candles.len(), first_close, last_close
    );
    println!(
        "🔗 [MERGE] {} [{}] — merged candles: {} | first_close={:.2} → last_close={:.2} (AI will see this close)",
        symbol, timeframe, final_candles.len(), first_close, last_close
    );

    Ok(final_candles)
}


/// Compute a real Order Flow Imbalance (OFI) proxy from the live tick stream.
///
/// True L2-depth OFI requires per-tick best-bid/ask size deltas. As an
/// honest, fully-dynamic proxy we use the **tick rule** over the most recent
/// `live_ticks`: each tick's traded volume delta is signed by the direction of
/// the price change (uptick = buy pressure, downtick = sell pressure). The
/// imbalance is the net signed volume normalised by total volume, giving a
/// value in [-1.0, +1.0].
///
/// Returns `f64::NAN` when there is not enough live tick data to compute a
/// trustworthy value — callers must render that as "unavailable" rather than
/// fabricating a neutral `0.0`.
pub(crate) async fn compute_order_flow_imbalance(pool: &sqlx::PgPool, symbol: &str) -> f64 {
    use sqlx::Row;

    let rows = match sqlx::query(
        "SELECT last_traded_price, volume, best_bid, best_ask \
         FROM live_ticks \
         WHERE symbol = $1 \
         ORDER BY timestamp DESC \
         LIMIT 200",
    )
    .bind(symbol)
    .fetch_all(pool)
    .await
    {
        Ok(r) => r,
        Err(e) => {
            warn!("[deep_quant] OFI query failed for {}: {} — OFI unavailable", symbol, e);
            return f64::NAN;
        }
    };

    // Need a meaningful sample of ticks to derive a stable imbalance.
    if rows.len() < 10 {
        return f64::NAN;
    }

    // Rows are DESC (newest first); reverse to chronological order.
    struct Tick { ltp: f64, vol: f64, bid: f64, ask: f64 }
    let ticks: Vec<Tick> = rows
        .iter()
        .rev()
        .map(|row| Tick {
            ltp: row.try_get::<f64, _>("last_traded_price").unwrap_or(0.0),
            vol: row
                .try_get::<i64, _>("volume")
                .or_else(|_| row.try_get::<i32, _>("volume").map(|v| v as i64))
                .unwrap_or(0) as f64,
            bid: row.try_get::<f64, _>("best_bid").unwrap_or(0.0),
            ask: row.try_get::<f64, _>("best_ask").unwrap_or(0.0),
        })
        .collect();

    // ── Tick-rule on cumulative-volume deltas, refined by quote location ──
    // live_ticks.volume is the day's cumulative traded volume, so the
    // per-tick traded size is the positive delta between consecutive ticks.
    // Each delta is signed by price direction (uptick = buy, downtick = sell);
    // when a live best-bid/ask quote is present we refine the sign using the
    // trade's location relative to the mid-price (Lee-Ready style).
    let mut signed_vol = 0.0_f64;
    let mut total_vol = 0.0_f64;
    let mut last_sign = 1.0_f64;
    for i in 1..ticks.len() {
        let dv = ticks[i].vol - ticks[i - 1].vol;
        // Guard against cumulative-counter resets (new session) → skip negatives.
        if dv <= 0.0 {
            continue;
        }
        let dp = ticks[i].ltp - ticks[i - 1].ltp;
        let tick_sign = if dp > 0.0 {
            1.0
        } else if dp < 0.0 {
            -1.0
        } else {
            last_sign // zero-tick inherits previous direction (tick rule)
        };
        last_sign = tick_sign;

        let refined_sign = if ticks[i].bid > 0.0 && ticks[i].ask > 0.0 && ticks[i].ask >= ticks[i].bid {
            let mid = (ticks[i].bid + ticks[i].ask) / 2.0;
            if ticks[i].ltp > mid {
                1.0
            } else if ticks[i].ltp < mid {
                -1.0
            } else {
                tick_sign
            }
        } else {
            tick_sign
        };

        signed_vol += refined_sign * dv;
        total_vol += dv;
    }

    if total_vol < 1e-6 {
        return f64::NAN;
    }

    (signed_vol / total_vol).clamp(-1.0, 1.0)
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

#[derive(serde::Serialize, serde::Deserialize, Clone)]
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

    let mut candles = match load_candles_from_db(Some(&app), pool.inner(), &symbol, &timeframe, 200).await {
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
        let (api_key_val, access_token_val) = get_kite_credentials();
        let api_key = if !api_key_val.is_empty() { Some(api_key_val) } else { None };
        let access_token = if !access_token_val.is_empty() { Some(access_token_val) } else { None };

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
                        if let Ok(new_candles) = load_candles_from_db(Some(&app), pool.inner(), &symbol, &timeframe, 200).await {
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
    let consensus = ConsensusEngine::compile_consensus(&symbol, &candles, &indicators, &timeframe);

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
    // Real Order Flow Imbalance from the live tick stream (NaN when unavailable).
    let ofi_val: f64 = compute_order_flow_imbalance(pool.inner(), &symbol).await;

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
            - Order Flow Imbalance (OFI): {}\n\
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
            symbol, timeframe, macro_context, latest_close, vwap_val, llm::format_ofi(ofi_val), vol_multiplier, atr_val,
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

    let tools = llm::deep_quant_tool_schema();

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
                        if let Ok(c) = load_candles_from_db(Some(&app), pool.inner(), &symbol, &timeframe, 200).await {
                            fresh_candles = c;
                        }

                        // Calculate updated indicators and consensus
                        let fresh_indicators = IndicatorState::from_candles_basic(&fresh_candles);
                        let fresh_consensus = ConsensusEngine::compile_consensus(&symbol, &fresh_candles, &fresh_indicators, &timeframe);

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

    // Honest fallback: when the model returns prose we could not parse into a
    // structured plan, we surface a LOW-conviction HOLD with a diagnostic
    // message that includes the model's own words. We MUST NOT fabricate a
    // high-conviction "winning" result — doing so would feed a fake trading
    // signal to the UI that is indistinguishable from a real analysis.
    let diagnostic_fallback = |raw: &str| {
        let snippet: String = raw.chars().take(280).collect();
        let detail = if snippet.trim().is_empty() {
            "the model returned an empty response".to_string()
        } else {
            format!("the model returned unstructured text: \"{}\"", snippet.trim())
        };
        AiExecutionPlan {
            conviction_score: 1,
            setup_validation: format!(
                "No actionable plan — analysis could not be parsed into a structured decision because {}.",
                detail
            ),
            execution_plan: format!(
                "HOLD / NO TRADE. The agent did not produce a valid execution plan; re-run the analysis. \
                 (Reference close: ₹{:.2}.)",
                latest_close
            ),
        }
    };

    match (start, end) {
        (Some(s), Some(e)) if e >= s => {
            let extracted = &cleaned[s..=e];
            match serde_json::from_str::<AiExecutionPlan>(extracted) {
                Ok(mut plan) => {
                    // Clamp out-of-range scores to the valid 1..=100 band.
                    if plan.conviction_score < 1 || plan.conviction_score > 100 {
                        plan.conviction_score = plan.conviction_score.clamp(1, 100);
                    }
                    plan
                }
                Err(e) => {
                    warn!("[deep_quant] parse_agent_response: JSON parse failed ({}). Returning HOLD diagnostic.", e);
                    diagnostic_fallback(content)
                }
            }
        }
        _ => {
            warn!("[deep_quant] parse_agent_response: no JSON object in model output. Returning HOLD diagnostic.");
            diagnostic_fallback(content)
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
        let candles = match load_candles_from_db(Some(&app), pool.inner(), &symbol, "10m", 200).await {
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
        let consensus = ConsensusEngine::compile_consensus(&symbol, &candles, &indicators, "10m");

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
        let plan_result = llm::generate_sentinel_plan(
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
        ).await;

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

/// Run deep quant agent loop with real-time SSE stream proxy.
#[tauri::command]
pub async fn run_deep_quant_agent(
    app: tauri::AppHandle,
    symbol: String,
    mode: Option<String>,
    manual_trade: Option<ManualTradeInfo>,
) -> Result<(), String> {
    let mode_str = mode.unwrap_or_else(|| "FIND".to_string());
    info!("[deep_quant_agent] Starting LangGraph ReAct loop proxy for {} in mode={}", symbol, mode_str);
    
    // Generate a unique thread ID
    let thread_id = format!("thread_{}_{}", symbol, chrono::Utc::now().timestamp_millis());
    
    let message = if mode_str == "VERIFY" && manual_trade.is_some() {
        let info = manual_trade.as_ref().unwrap();
        format!(
            "Verify the following proposed trade setup for the trading ticker symbol '{}':\n\
             - Side: {}\n\
             - Entry Price: {}\n\
             - Stop Loss: {}\n\
             - Target/Take Profit: {}\n\
             - My Trade Logic/Analysis: '{}'\n\
             Please evaluate this setup against recent candlestick data and technical consensus, validate the risk-reward profile, and recommend whether to execute, adjust, or reject the trade.",
            symbol,
            info.side,
            info.entry,
            info.stop_loss,
            info.take_profit,
            info.user_analysis
        )
    } else {
        format!("Analyze the trading ticker symbol '{}' and recommend a setup.", symbol)
    };
    
    // Prepare the payload for Python FastAPI
    let payload = serde_json::json!({
        "thread_id": thread_id,
        "message": message,
        "mode": mode_str,
        "symbol": symbol,
        "manual_trade": manual_trade
    });
    
    // Spawn the streaming reqwest client in the background
    tokio::spawn(async move {
        let client = reqwest::Client::new();
        let url = "http://localhost:8086/run";
        let mut saw_run_finished = false;
        
        match client.post(url).json(&payload).send().await {
            Ok(response) => {
                let mut stream = response.bytes_stream();
                use futures_util::StreamExt;
                let mut buffer = String::new();
                
                while let Some(chunk_result) = stream.next().await {
                    match chunk_result {
                        Ok(bytes) => {
                            let text = String::from_utf8_lossy(&bytes);
                            buffer.push_str(&text);
                            
                            // Process SSE event blocks
                            while let Some(pos) = buffer.find("\n\n") {
                                let event_block = buffer.drain(..pos + 2).collect::<String>();
                                
                                let mut event_type = None;
                                // Bug 8 fix: Accumulate ALL data: lines per SSE spec.
                                // The SSE spec says multiple `data:` lines in a single
                                // event block should be joined with newlines.
                                let mut data_lines: Vec<String> = Vec::new();
                                
                                for line in event_block.lines() {
                                    if line.starts_with("event: ") {
                                        event_type = Some(line["event: ".len()..].trim().to_string());
                                    } else if line.starts_with("data: ") {
                                        data_lines.push(line["data: ".len()..].trim().to_string());
                                    }
                                }
                                
                                if let Some(ref ev_type) = event_type {
                                    if ev_type == "RUN_FINISHED" {
                                        saw_run_finished = true;
                                    }
                                }
                                
                                if let (Some(ev_type), false) = (event_type, data_lines.is_empty()) {
                                    let joined_data = data_lines.join("\n");
                                    if let Ok(json_val) = serde_json::from_str::<serde_json::Value>(&joined_data) {
                                        let outbound = serde_json::json!({
                                            "event": ev_type,
                                            "data": json_val
                                        });
                                        let _ = app.emit("deep-quant-stream", outbound);
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            error!("[deep_quant_agent] Stream read error: {}", e);
                            let _ = app.emit("deep-quant-stream", serde_json::json!({
                                "event": "ERROR",
                                "data": { "error": format!("Stream read error: {}", e) }
                            }));
                            break;
                        }
                    }
                }
                
                // Bug 10 fix: If the stream ended without a RUN_FINISHED event
                // (e.g. Python server crashed mid-stream or connection dropped cleanly),
                // emit a synthetic RUN_FINISHED so the frontend always transitions
                // out of the 'running' state.
                if !saw_run_finished {
                    warn!("[deep_quant_agent] Stream ended without RUN_FINISHED — emitting synthetic completion.");
                    let _ = app.emit("deep-quant-stream", serde_json::json!({
                        "event": "RUN_FINISHED",
                        "data": { "thread_id": thread_id, "status": "completed" }
                    }));
                }
            }
            Err(e) => {
                error!("[deep_quant_agent] Failed to connect to Python server: {}", e);
                let _ = app.emit("deep-quant-stream", serde_json::json!({
                    "event": "ERROR",
                    "data": { "error": format!("Failed to connect to Python server: {}", e) }
                }));
            }
        }
        
        info!("[deep_quant_agent] Stream proxy finished for thread={}", thread_id);
    });
    
    Ok(())
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct MultiTfChartPatterns {
    pub timeframe: String,
    pub patterns: Vec<crate::quant::chart_patterns::ChartPattern>,
}

#[tauri::command]
pub async fn get_multi_timeframe_chart_patterns(
    app: tauri::AppHandle,
    pool: tauri::State<'_, sqlx::PgPool>,
    symbol: String,
) -> Result<Vec<MultiTfChartPatterns>, String> {
    info!("[deep_quant] get_multi_timeframe_chart_patterns for symbol={}", symbol);
    let timeframes = vec!["1m", "5m", "10m", "15m", "1h", "4h", "1d"];
    let mut tasks = vec![];

    for tf in timeframes {
        let app_clone = app.clone();
        let pool_clone = pool.inner().clone();
        let symbol_clone = symbol.clone();
        let tf_str = tf.to_string();

        tasks.push(tokio::spawn(async move {
            let candles = load_candles_from_db(
                Some(&app_clone),
                &pool_clone,
                &symbol_clone,
                &tf_str,
                200,
            )
            .await;

            match candles {
                Ok(c) => {
                    let patterns = crate::quant::chart_patterns::ChartPatternEngine::analyze(&c);
                    MultiTfChartPatterns {
                        timeframe: tf_str,
                        patterns,
                    }
                }
                Err(e) => {
                    warn!("[deep_quant] get_multi_timeframe_chart_patterns failed for {}: {}", tf_str, e);
                    MultiTfChartPatterns {
                        timeframe: tf_str,
                        patterns: vec![],
                    }
                }
            }
        }));
    }

    let results = futures_util::future::join_all(tasks).await;
    let mut output = vec![];
    for res in results {
        if let Ok(item) = res {
            output.push(item);
        }
    }

    Ok(output)
}


