// commands/deep_quant.rs — Tauri IPC Command: Deep Quant Analysis.
//
// V3 Phase 3: The frontend calls `invoke("run_deep_quant_analysis", { symbol })`
// which triggers the full pipeline:
//   1. Fetch recent candles from QuestDB
//   2. Compute indicators → ConsensusReport via the quant engine
//   3. Fetch recent news headlines (with graceful fallback)
//   4. Call DeepSeek API with the Master Prompt
//   5. Return AiExecutionPlan to React UI

use log::{info, warn};
use sqlx::PgPool;
use tauri::{AppHandle, Emitter, Manager};

use crate::quant::{
    patterns::Candle, AiExecutionPlan, ConsensusEngine, IndicatorState,
};
use crate::services::llm;

// ── News Fetcher ────────────────────────────────────────────────────────────

/// Fetch recent news headlines for a symbol from the aggregator's REST API.
/// Falls back to a "No recent news available" string on any failure.
async fn fetch_news_context(symbol: &str) -> String {
    let news_api_url = std::env::var("NEWS_API_URL")
        .unwrap_or_else(|_| "http://127.0.0.1:8084".to_string());

    let url = format!("{}/api/news?symbol={}", news_api_url, symbol);

    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .build()
    {
        Ok(c) => c,
        Err(e) => {
            warn!("News HTTP client failed: {} — using fallback", e);
            return format!("No recent news available for {}.", symbol);
        }
    };

    match client.get(&url).send().await {
        Ok(resp) if resp.status().is_success() => {
            match resp.text().await {
                Ok(body) if !body.trim().is_empty() => body,
                _ => format!("No recent news available for {}.", symbol),
            }
        }
        Ok(resp) => {
            warn!("News API returned HTTP {} for {}", resp.status(), symbol);
            format!("No recent news available for {}.", symbol)
        }
        Err(e) => {
            warn!("News fetch failed for {}: {} — using fallback", symbol, e);
            format!("No recent news available for {}.", symbol)
        }
    }
}

// ── Candle Loader ───────────────────────────────────────────────────────────

/// Load the most recent N candles from QuestDB for quant analysis.
async fn load_candles_from_db(pool: &PgPool, symbol: &str, limit: i64) -> Result<Vec<Candle>, String> {
    use sqlx::Row;

    let rows = sqlx::query(
        "SELECT open, high, low, close, volume \
         FROM historical_candles \
         WHERE symbol = $1 \
         ORDER BY ts DESC \
         LIMIT $2",
    )
    .bind(symbol)
    .bind(limit)
    .fetch_all(pool)
    .await
    .map_err(|e| format!("QuestDB candle fetch failed for {}: {}", symbol, e))?;

    // Reverse to chronological order (oldest first)
    let mut candles: Vec<Candle> = rows
        .iter()
        .filter_map(|row| {
            let open: f64 = row.try_get("open").ok()?;
            let high: f64 = row.try_get("high").ok()?;
            let low: f64 = row.try_get("low").ok()?;
            let close: f64 = row.try_get("close").ok()?;
            let volume: i64 = row.try_get("volume").ok()?;
            Some(Candle {
                open,
                high,
                low,
                close,
                volume: volume as f64,
            })
        })
        .collect();

    candles.reverse();
    Ok(candles)
}

// ── Tauri IPC Command ───────────────────────────────────────────────────────

/// Run the full V3 Deep Quant Analysis pipeline for a given symbol.
///
/// # Frontend Usage
/// ```typescript
/// const plan = await invoke<AiExecutionPlan>("run_deep_quant_analysis", {
///   symbol: "RELIANCE"
/// });
/// ```
///
/// # Pipeline
/// 1. Load 200 most recent candles from QuestDB
/// 2. Compute IndicatorState + ConsensusReport
/// 3. Fetch recent news (with fallback)
/// 4. Call DeepSeek with the Master Prompt
/// 5. Return structured AiExecutionPlan
#[tauri::command]
pub async fn run_deep_quant_analysis(
    app: AppHandle,
    symbol: String,
) -> Result<AiExecutionPlan, String> {
    info!("╔══════════════════════════════════════════════════╗");
    info!("║  Deep Quant Analysis — V3 Pipeline Starting     ║");
    info!("║  Symbol: {:<40} ║", symbol);
    info!("╚══════════════════════════════════════════════════╝");

    // ── Step 1: Fetch candles from QuestDB ───────────────────────────────
    let pool = app
        .try_state::<PgPool>()
        .ok_or_else(|| "QuestDB pool not yet available — try again shortly.".to_string())?;

    let candles = load_candles_from_db(pool.inner(), &symbol, 200).await?;

    if candles.len() < 2 {
        return Err(format!(
            "Insufficient data for {}: only {} candles available (need ≥2).",
            symbol,
            candles.len()
        ));
    }

    info!("Step 1 complete: {} candles loaded for {}", candles.len(), symbol);

    // ── Step 2: Compute indicators and consensus ────────────────────────
    let indicators = IndicatorState::from_candles_basic(&candles);
    let consensus = ConsensusEngine::compile_consensus(&symbol, &candles, &indicators);

    info!(
        "Step 2 complete: trend={}, momentum={}, volatility={}, volume={}, patterns={:?}, strategies={:?}",
        consensus.trend_score,
        consensus.momentum_state,
        consensus.volatility_state,
        consensus.volume_flow_state,
        consensus.active_patterns,
        consensus.active_strategies
    );

    // Emit consensus to frontend for real-time dashboard display
    let _ = app.emit("quant-consensus", serde_json::json!(&consensus));

    // ── Step 3: Fetch news context ──────────────────────────────────────
    let news = fetch_news_context(&symbol).await;
    info!("Step 3 complete: news context ({} chars)", news.len());

    // ── Step 4: Call DeepSeek via LLM bridge ────────────────────────────
    info!("Step 4: calling DeepSeek API...");
    let plan = llm::generate_deep_quant_plan(&symbol, &consensus, &news).await?;

    info!(
        "Step 4 complete: conviction={}, plan preview: {}...",
        plan.conviction_score,
        &plan.execution_plan[..80.min(plan.execution_plan.len())]
    );

    // ── Step 5: Emit result event and return ────────────────────────────
    let _ = app.emit("deep-quant-result", serde_json::json!(&plan));

    info!("Deep Quant Analysis complete for {} — conviction: {}", symbol, plan.conviction_score);

    Ok(plan)
}
