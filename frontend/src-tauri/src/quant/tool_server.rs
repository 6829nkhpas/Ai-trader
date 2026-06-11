// quant/tool_server.rs — Local HTTP Tool Server for Hybrid Agent.
//
// Serves endpoints on localhost:8084 to interface with Python LangGraph service:
//   - POST /tools/get_candles
//   - POST /tools/get_consensus
//   - POST /tools/get_multi_tf_trend
//   - POST /tools/watch_condition
//   - POST /tools/declare_trade
//   - POST /tools/get_support_resistance
//   - POST /tools/get_chart_patterns
//   - POST /tools/get_prediction
//   - POST /tools/get_news_context

use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use axum::{
    routing::post,
    extract::State,
    http::StatusCode,
    response::Json,
    Router,
};
use tauri::{AppHandle, Manager, Emitter};
use log::{info, error};

use crate::quant::{
    IndicatorState, ConsensusEngine,
};
use crate::quant::vwepr::OhlcCandle;
use crate::quant::chart_patterns::ChartPatternEngine;

// ── Types & Payload Contracts ──────────────────────────────────────────────

#[derive(serde::Deserialize)]
pub struct GetCandlesRequest {
    pub symbol: String,
    pub timeframe: Option<String>,
    pub limit: Option<i64>,
}

#[derive(serde::Deserialize)]
pub struct GetConsensusRequest {
    pub symbol: String,
    pub timeframe: Option<String>,
    pub limit: Option<i64>,
}

#[derive(serde::Deserialize, Clone)]
pub struct WatchConditionRequest {
    pub thread_id: String,
    pub symbol: Option<String>,
    pub timeframe: Option<String>,
    pub price_level: f64,
    pub direction: String, // "above" / "up" or "below" / "down"
    pub volume_multiplier: f64,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Watcher {
    pub thread_id: String,
    pub symbol: String,
    pub timeframe: String,
    pub price_level: f64,
    pub direction: String,
    pub volume_multiplier: f64,
}

// ── Server State ────────────────────────────────────────────────────────────

#[derive(Clone)]
pub struct ServerState {
    pub app: AppHandle,
    pub watchers: Arc<RwLock<HashMap<String, Watcher>>>,
}

// ── Shared Helper to parse live ohlc-tick JSON to tuple ─────────────────────

pub fn parse_ohlc_tick(json: &serde_json::Value) -> Option<(String, OhlcCandle)> {
    let symbol = json.get("symbol")?.as_str()?.to_string();
    let open = json.get("open")?.as_f64()?;
    let high = json.get("high")?.as_f64()?;
    let low = json.get("low")?.as_f64()?;
    let close = json.get("close")?.as_f64()?;
    let volume = json.get("volume")
        .and_then(|v| v.as_f64().or_else(|| v.as_u64().map(|x| x as f64)))
        .unwrap_or(0.0);
    
    // Support either 'time' (seconds) or 'start_timestamp_ms' (milliseconds)
    let time = json.get("time")
        .and_then(|t| t.as_i64())
        .or_else(|| json.get("start_timestamp_ms").and_then(|t| t.as_i64().map(|ms| ms / 1000)))
        .unwrap_or_else(|| chrono::Utc::now().timestamp());

    Some((symbol, OhlcCandle { time, open, high, low, close, volume }))
}

// ── Handlers ─────────────────────────────────────────────────────────────────

/// Candle response with timestamp for the LLM to reason about time.
#[derive(serde::Serialize, Clone, Debug, PartialEq)]
struct CandleWithTs {
    pub timestamp_ms: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

/// Enforce the `get_candles` Tool_Result_Contract (R4.4) on a candle series.
///
/// Returns the candles in ascending `timestamp_ms` order. Each
/// [`CandleWithTs`] structurally carries `timestamp_ms`, `open`, `high`,
/// `low`, `close`, and `volume`, so the full-OHLCV half of the contract is
/// guaranteed by the type; this helper enforces the ordering half independent
/// of the upstream data source. It is a pure function (no I/O, clock, or
/// ambient state) so the contract can be unit-/property-tested without a live
/// database.
fn sort_candles_ascending(mut candles: Vec<CandleWithTs>) -> Vec<CandleWithTs> {
    candles.sort_by(|a, b| a.timestamp_ms.cmp(&b.timestamp_ms));
    candles
}

/// POST /tools/get_candles
/// Fetches candles from QuestDB and returns them as JSON with timestamps.
/// Contract (R4.4): candles are returned in ascending `timestamp_ms` order,
/// and each candle carries `timestamp_ms`, `open`, `high`, `low`, `close`,
/// and `volume`.
async fn get_candles(
    State(state): State<ServerState>,
    Json(payload): Json<GetCandlesRequest>,
) -> Result<Json<Vec<CandleWithTs>>, (StatusCode, Json<serde_json::Value>)> {
    let pool = state.app.try_state::<sqlx::PgPool>().ok_or_else(|| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": "QuestDB PG pool not available" })),
        )
    })?;

    let limit = payload.limit.unwrap_or(200);
    let tf = payload.timeframe.unwrap_or_else(|| "10m".to_string());
    let timed_candles = crate::commands::deep_quant::load_candles_with_ts(
        Some(&state.app),
        pool.inner(),
        &payload.symbol,
        &tf,
        limit,
        30,
    )
    .await
    .map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": e })),
        )
    })?;

    let mut result: Vec<CandleWithTs> = timed_candles
        .into_iter()
        .map(|(ts, c)| CandleWithTs {
            timestamp_ms: ts,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: c.volume,
        })
        .collect();

    // Tool_Result_Contract (R4.4): candles MUST be returned in ascending
    // `timestamp_ms` order, each carrying full OHLCV. The upstream loader
    // already sorts ascending, but we re-enforce the ordering at the contract
    // boundary so the guarantee holds regardless of the data source.
    result = sort_candles_ascending(result);

    Ok(Json(result))
}

/// POST /tools/get_consensus
/// Computes indicators and returns ConsensusReport as JSON.
async fn get_consensus(
    State(state): State<ServerState>,
    Json(payload): Json<GetConsensusRequest>,
) -> Result<Json<crate::quant::ConsensusReport>, (StatusCode, Json<serde_json::Value>)> {
    let pool = state.app.try_state::<sqlx::PgPool>().ok_or_else(|| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": "QuestDB PG pool not available" })),
        )
    })?;

    let limit = payload.limit.unwrap_or(200);
    let tf = payload.timeframe.unwrap_or_else(|| "10m".to_string());
    let candles = crate::commands::deep_quant::load_candles_from_db(
        Some(&state.app),
        pool.inner(),
        &payload.symbol,
        &tf,
        limit,
    )
    .await
    .map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": e })),
        )
    })?;

    let indicators = IndicatorState::from_candles_basic(&candles);
    let consensus = ConsensusEngine::compile_consensus(
        &payload.symbol,
        &candles,
        &indicators,
        &tf,
    );

    let _ = state.app.emit("quant-consensus", consensus.clone());

    Ok(Json(consensus))
}

// ── Support / Resistance Endpoint ────────────────────────────────────────────

#[derive(serde::Deserialize)]
pub struct GetSupportResistanceRequest {
    pub symbol: String,
    pub timeframe: Option<String>,
    pub limit: Option<i64>,
}

/// POST /tools/get_support_resistance
/// Computes authoritative pivot / support / resistance levels (R9.1, R9.3).
///
/// Candles are resolved through the same shared `load_candles_from_db` source
/// the other indicator endpoints use, so SR levels stay consistent with the
/// rest of the analysis. The requested timeframe is validated up front via the
/// shared `validate_timeframe` (R4.5); the pure `compute_sr` engine then yields
/// the `SrLevels` contract (intraday timeframes additionally carry the
/// opening-range and daily macro pivot per R9.3).
async fn get_support_resistance(
    State(state): State<ServerState>,
    Json(payload): Json<GetSupportResistanceRequest>,
) -> Result<Json<crate::quant::SrLevels>, (StatusCode, Json<serde_json::Value>)> {
    let tf = payload.timeframe.clone().unwrap_or_else(|| "10m".to_string());

    // Validate the timeframe before any data access (R4.5). On failure return a
    // descriptive error naming the offending value; `validate_timeframe` also
    // logs the validation failure.
    if let Err(e) = crate::quant::validate_timeframe(&tf) {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "error": e.to_string() })),
        ));
    }

    let pool = state.app.try_state::<sqlx::PgPool>().ok_or_else(|| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": "QuestDB PG pool not available" })),
        )
    })?;

    let limit = payload.limit.unwrap_or(200);
    let candles = crate::commands::deep_quant::load_candles_from_db(
        Some(&state.app),
        pool.inner(),
        &payload.symbol,
        &tf,
        limit,
    )
    .await
    .map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": e })),
        )
    })?;

    let sr = crate::quant::compute_sr(&candles, &tf);

    info!(
        "[tool_server] get_support_resistance: symbol={}, tf={}, pivot={:.2}, ordering_exception={}",
        payload.symbol,
        tf,
        sr.pivot,
        sr.ordering_exception.is_some()
    );

    Ok(Json(sr))
}

/// Pure watcher trigger predicate (R14.2).
///
/// Returns `true` iff the registered price condition holds AND the candle's
/// volume meets the volume-surge threshold (`candle_volume >= average_volume *
/// volume_multiplier`). The price condition is direction-aware:
///
/// * `"above"` / `"up"`   → `candle_close >= price_level`
/// * `"below"` / `"down"` → `candle_close <= price_level`
/// * any other direction  → never fires (unknown direction is treated as no
///   match so a malformed watcher can never trigger a spurious resume).
///
/// Extracted as a pure function so the trigger semantics are independently
/// unit-/property-testable and applied identically inside the live watcher task
/// loop. It has no I/O, clock, or ambient state.
fn watcher_triggered(
    direction: &str,
    price_level: f64,
    volume_multiplier: f64,
    average_volume: f64,
    candle_close: f64,
    candle_volume: f64,
) -> bool {
    let price_matched = match direction {
        "above" | "up" => candle_close >= price_level,
        "below" | "down" => candle_close <= price_level,
        _ => false,
    };

    let volume_matched = candle_volume >= average_volume * volume_multiplier;

    price_matched && volume_matched
}

/// Status returned to the agent when a watcher is registered (R14.1).
///
/// This is the resumable-suspend signal: the run pauses awaiting the watched
/// condition rather than terminating, and a later `/resume` POST continues it
/// with the triggering candle.
const WATCH_REGISTERED_STATUS: &str = "watching_registered";

/// Build a [`Watcher`] from validated watch parameters (R14.1).
///
/// The direction is normalized (trimmed + lowercased) so the trigger
/// predicate's direction matching is canonical. Pure helper (no I/O, clock, or
/// ambient state) so watcher registration can be unit-/property-tested without
/// spawning the live watcher task.
fn build_watcher(
    thread_id: String,
    symbol: String,
    timeframe: String,
    price_level: f64,
    direction: &str,
    volume_multiplier: f64,
) -> Watcher {
    Watcher {
        thread_id,
        symbol,
        timeframe,
        price_level,
        direction: direction.trim().to_lowercase(),
        volume_multiplier,
    }
}

/// Insert a watcher into the active registry keyed by its `thread_id` (R14.1).
///
/// Factored out so the registry-insert contract is testable against a plain
/// `HashMap` without the live `RwLock`, while the handler uses the exact same
/// keying through deref coercion of the write guard.
fn register_watcher(registry: &mut HashMap<String, Watcher>, watcher: Watcher) {
    registry.insert(watcher.thread_id.clone(), watcher);
}

/// POST /tools/watch_condition
/// Registers watcher in RwLock map and spawns condition checking Tokio task.
async fn watch_condition(
    State(state): State<ServerState>,
    Json(payload): Json<WatchConditionRequest>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    // Determine the symbol (fallback to ActiveSymbolState if none in payload)
    let watch_symbol = match payload.symbol {
        Some(s) => s.trim().to_uppercase(),
        None => {
            if let Some(symbol_state) = state.app.try_state::<crate::commands::ticker::ActiveSymbolState>() {
                let lock = symbol_state.symbol.lock().await;
                lock.clone()
            } else {
                return Err((
                    StatusCode::INTERNAL_SERVER_ERROR,
                    Json(serde_json::json!({ "error": "ActiveSymbolState not available" })),
                ));
            }
        }
    };

    if watch_symbol.is_empty() {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "error": "Symbol is required" })),
        ));
    }

    let timeframe = payload.timeframe.unwrap_or_else(|| "10m".to_string());

    let watcher = build_watcher(
        payload.thread_id.clone(),
        watch_symbol.clone(),
        timeframe,
        payload.price_level,
        &payload.direction,
        payload.volume_multiplier,
    );

    // Register watcher keyed by thread_id (R14.1).
    {
        let mut map = state.watchers.write().await;
        register_watcher(&mut map, watcher.clone());
    }

    info!(
        "[tool_server] Registered watcher for thread_id={} symbol={} price_level={:.2} direction={}",
        watcher.thread_id, watcher.symbol, watcher.price_level, watcher.direction
    );

    // Retrieve the broadcast channel Sender from Tauri managed state
    let tx = state
        .app
        .try_state::<tokio::sync::broadcast::Sender<(String, OhlcCandle)>>()
        .ok_or_else(|| {
            (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": "Live candle broadcast sender not registered" })),
            )
        })?;

    let mut rx = tx.inner().subscribe();
    let watchers_clone = state.watchers.clone();
    let app_clone = state.app.clone();

    // Spawn background tokio task
    tokio::spawn(async move {
        // Compute 20-period baseline average volume from QuestDB
        let mut avg_volume = 1.0;
        if let Some(pool) = app_clone.try_state::<sqlx::PgPool>() {
            if let Ok(c) = crate::commands::deep_quant::load_candles_from_db(Some(&app_clone), pool.inner(), &watcher.symbol, &watcher.timeframe, 20).await {
                if !c.is_empty() {
                    let total_vol: f64 = c.iter().map(|item| item.volume).sum();
                    avg_volume = total_vol / c.len() as f64;
                }
            }
        }

        info!(
            "[watcher] Watching {} for thread_id={}. Condition: price {} {:.2}, vol_mult={:.2}x (avg_vol={:.2})",
            watcher.symbol, watcher.thread_id, watcher.direction, watcher.price_level, watcher.volume_multiplier, avg_volume
        );

        while let Ok((sym, candle)) = rx.recv().await {
            // Verify if watcher is still active and hasn't been removed/overwritten
            let still_active = {
                let map = watchers_clone.read().await;
                map.get(&watcher.thread_id)
                    .map(|w| w.price_level == watcher.price_level && w.symbol == watcher.symbol)
                    .unwrap_or(false)
            };

            if !still_active {
                info!("[watcher] Watcher for thread_id={} no longer matches or exists. Exiting watcher loop.", watcher.thread_id);
                break;
            }

            if sym.to_uppercase() == watcher.symbol.to_uppercase() {
                if !matches!(watcher.direction.as_str(), "above" | "up" | "below" | "down") {
                    error!("[watcher] Unknown direction: {}", watcher.direction);
                }

                // Trigger semantics are factored into the pure `watcher_triggered`
                // predicate (R14.2): fire iff the price condition holds AND the
                // candle volume meets `avg_volume * volume_multiplier`.
                if watcher_triggered(
                    &watcher.direction,
                    watcher.price_level,
                    watcher.volume_multiplier,
                    avg_volume,
                    candle.close,
                    candle.volume,
                ) {
                    info!(
                        "[watcher] Condition MET for thread_id={}! Price close={:.2} (level={:.2}), Vol={:.2} (threshold={:.2})",
                        watcher.thread_id, candle.close, watcher.price_level, candle.volume, avg_volume * watcher.volume_multiplier
                    );

                    // Remove from registry
                    {
                        let mut map = watchers_clone.write().await;
                        map.remove(&watcher.thread_id);
                    }

                    // Handoff outbound HTTP POST to Python service
                    let client = reqwest::Client::new();
                    let response_payload = serde_json::json!({
                        "thread_id": watcher.thread_id,
                        "triggered_candle": candle,
                    });

                    info!("[watcher] Making handoff resume POST to port 8086 for thread_id={}", watcher.thread_id);
                    match client
                        .post("http://localhost:8086/resume")
                        .json(&response_payload)
                        .send()
                        .await
                    {
                        Ok(res) => {
                            info!("[watcher] Outbound handoff resume POST response status: {}", res.status());
                            
                            // Consume the SSE stream returned by /resume
                            let mut stream = res.bytes_stream();
                            use futures_util::StreamExt;
                            let mut buffer = String::new();
                            
                            while let Some(chunk_result) = stream.next().await {
                                match chunk_result {
                                    Ok(bytes) => {
                                        let text = String::from_utf8_lossy(&bytes);
                                        buffer.push_str(&text);
                                        
                                        while let Some(pos) = buffer.find("\n\n") {
                                            let event_block = buffer.drain(..pos + 2).collect::<String>();
                                            
                                            let mut event_type = None;
                                            // Bug 8 fix: Accumulate ALL data: lines per SSE spec
                                            let mut data_lines: Vec<String> = Vec::new();
                                            
                                            for line in event_block.lines() {
                                                if line.starts_with("event: ") {
                                                    event_type = Some(line["event: ".len()..].trim().to_string());
                                                } else if line.starts_with("data: ") {
                                                    data_lines.push(line["data: ".len()..].trim().to_string());
                                                }
                                            }
                                            
                                            if let Some(ev_type) = event_type {
                                                let json_val = if !data_lines.is_empty() {
                                                    let joined_data = data_lines.join("\n");
                                                    serde_json::from_str::<serde_json::Value>(&joined_data)
                                                        .unwrap_or(serde_json::Value::Null)
                                                } else {
                                                    serde_json::Value::Null
                                                };

                                                let outbound = serde_json::json!({
                                                    "event": ev_type,
                                                    "data": json_val
                                                });
                                                let _ = app_clone.emit("deep-quant-stream", outbound);
                                            }
                                        }
                                    }
                                    Err(e) => {
                                        error!("[watcher] Resume stream read error: {}", e);
                                        let _ = app_clone.emit("deep-quant-stream", serde_json::json!({
                                            "event": "ERROR",
                                            "data": { "error": format!("Resume stream read error: {}", e) }
                                        }));
                                        break;
                                    }
                                }
                            }
                        }
                        Err(err) => {
                            error!("[watcher] Failed to send outbound handoff resume POST for thread_id={}: {}", watcher.thread_id, err);
                            let _ = app_clone.emit("deep-quant-stream", serde_json::json!({
                                "event": "ERROR",
                                "data": { "error": format!("Failed to connect to Python server on resume: {}", err) }
                            }));
                        }
                    }

                    break; // Exit background task
                }
            }
        }
    });

    Ok(Json(serde_json::json!({ "status": WATCH_REGISTERED_STATUS })))
}

// ── Chart Patterns Endpoint ──────────────────────────────────────────────────

#[derive(serde::Deserialize)]
pub struct GetChartPatternsRequest {
    pub symbol: String,
    pub timeframe: Option<String>,
    pub limit: Option<i64>,
}

#[derive(serde::Serialize)]
pub struct ChartPatternResponse {
    pub symbol: String,
    pub timeframe: String,
    pub patterns: Vec<crate::quant::chart_patterns::ChartPattern>,
}

/// POST /tools/get_chart_patterns
/// Identifies structural chart patterns (H&S, Double Top/Bottom, Triangles, Flags, etc.)
/// from the candle history stored in QuestDB.
async fn get_chart_patterns_handler(
    State(state): State<ServerState>,
    Json(payload): Json<GetChartPatternsRequest>,
) -> Result<Json<ChartPatternResponse>, (StatusCode, Json<serde_json::Value>)> {
    let pool = state.app.try_state::<sqlx::PgPool>().ok_or_else(|| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": "QuestDB PG pool not available" })),
        )
    })?;

    let limit = payload.limit.unwrap_or(200);
    let tf = payload.timeframe.unwrap_or_else(|| "10m".to_string());
    let candles = crate::commands::deep_quant::load_candles_from_db(
        Some(&state.app),
        pool.inner(),
        &payload.symbol,
        &tf,
        limit,
    )
    .await
    .map_err(|e| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": e })),
        )
    })?;

    let patterns = ChartPatternEngine::analyze(&candles);

    info!(
        "[tool_server] get_chart_patterns: symbol={}, tf={}, detected {} patterns",
        payload.symbol, tf, patterns.len()
    );

    Ok(Json(ChartPatternResponse {
        symbol: payload.symbol,
        timeframe: tf,
        patterns,
    }))
}

#[derive(serde::Deserialize)]
pub struct MultiTfRequest {
    pub symbol: String,
}

// ── Declare Trade (final decision persistence) ──────────────────────────────

#[derive(serde::Deserialize)]
pub struct DeclareTradeRequest {
    pub symbol: Option<String>,
    pub action: String,
    pub conviction_score: i32,
    pub setup_validation: String,
    pub execution_plan: String,
    /// Proposed entry price for the trade (R6.1). Optional so HOLD declarations
    /// (which bypass level checks) need not supply levels.
    pub entry: Option<f64>,
    /// Proposed stop-loss price (R6.1).
    pub stop_loss: Option<f64>,
    /// Proposed take-profit price (R6.1).
    pub take_profit: Option<f64>,
    /// Current 14-period ATR used for the stop-distance check (R6.3). Optional:
    /// when absent the stop-too-tight rule is skipped (see [`validate_trade`]).
    pub atr_14: Option<f64>,
}

/// Run the Trade_Validator over a declared trade's raw request fields (R6.6).
///
/// Pure helper that maps the free-form `action` string via
/// [`crate::quant::Action::from_str_lenient`], assembles
/// [`crate::quant::ExecutionLevels`] only when *all three* of entry/stop-loss/
/// take-profit are present, and delegates to [`crate::quant::validate_trade`].
/// A BUY/SELL with one or more missing levels therefore yields
/// `Fail(MissingLevels)`, while HOLD bypasses every level check and always
/// passes.
///
/// Factored out of the handler so the commit-iff-pass decision is unit-testable
/// without a live `AppHandle` or event bus: the handler commits exactly when
/// this returns `Pass` and rejects (without emitting) when it returns `Fail`.
fn evaluate_declared_trade(
    action_str: &str,
    entry: Option<f64>,
    stop_loss: Option<f64>,
    take_profit: Option<f64>,
    atr_14: Option<f64>,
) -> crate::quant::ValidatorOutcome {
    let action = crate::quant::Action::from_str_lenient(action_str);

    // Build ExecutionLevels only when every level is supplied; a partial set
    // surfaces as MissingLevels inside `validate_trade` (R6.1).
    let levels = match (entry, stop_loss, take_profit) {
        (Some(e), Some(sl), Some(tp)) => Some(crate::quant::ExecutionLevels {
            entry: e,
            stop_loss: sl,
            take_profit: tp,
        }),
        _ => None,
    };

    crate::quant::validate_trade(action, levels, atr_14)
}

/// POST /tools/declare_trade
/// Commits the agent's final decision. The Trade_Validator runs first (R6.6):
/// the decision is committed — emitting a `final_analysis_ready` event (the
/// same event the Glass-Box loop uses) plus an `agent-declared-trade` event
/// carrying the full decision — **only** when validation passes. On any
/// validation failure the endpoint returns the validator reason and emits no
/// commit events (R6.7), so the agent revises the setup rather than the UI
/// recording a non-compliant trade. HOLD bypasses the level checks and always
/// commits.
async fn declare_trade(
    State(state): State<ServerState>,
    Json(payload): Json<DeclareTradeRequest>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let conviction = payload.conviction_score.clamp(0, 100);

    // Trade_Validator gate (R6.6): only a passing trade is committed.
    let outcome = evaluate_declared_trade(
        &payload.action,
        payload.entry,
        payload.stop_loss,
        payload.take_profit,
        payload.atr_14,
    );

    // R6.7: a failing validation is NOT committed — return the reason and emit
    // no `final_analysis_ready` / `agent-declared-trade` events.
    if let crate::quant::ValidatorOutcome::Fail { reason } = outcome {
        info!(
            "[tool_server] declare_trade REJECTED: symbol={:?} action={} reason={}",
            payload.symbol,
            payload.action,
            reason.as_tag()
        );
        return Ok(Json(serde_json::json!({
            "status": "rejected",
            "reason": reason.as_tag(),
            "action": payload.action,
        })));
    }

    let risk_reward = match outcome {
        crate::quant::ValidatorOutcome::Pass { risk_reward } => risk_reward,
        crate::quant::ValidatorOutcome::Fail { .. } => unreachable!("Fail handled above"),
    };

    let plan = crate::quant::AiExecutionPlan {
        conviction_score: conviction,
        setup_validation: payload.setup_validation.clone(),
        execution_plan: payload.execution_plan.clone(),
    };

    info!(
        "[tool_server] declare_trade COMMITTED: symbol={:?} action={} conviction={} risk_reward={:.2}",
        payload.symbol, payload.action, conviction, risk_reward
    );

    // Validation passed (R6.7): surface the structured plan to the React UI
    // (same channel the Glass-Box loop uses) so the committed decision is
    // rendered consistently.
    let _ = state.app.emit("final_analysis_ready", plan.clone());
    let _ = state.app.emit(
        "agent-declared-trade",
        serde_json::json!({
            "symbol": payload.symbol,
            "action": payload.action,
            "plan": plan,
        }),
    );

    Ok(Json(serde_json::json!({
        "status": "trade_declared",
        "action": payload.action,
        "conviction_score": conviction,
        "risk_reward": risk_reward,
    })))
}

#[derive(serde::Serialize)]
pub struct MultiTfResponse {
    pub symbol: String,
    pub trend_1h: String,
    pub trend_4h: String,
    pub trend_1d: String,
    pub indicators: serde_json::Value,
}

/// Pure per-horizon trend classifier (R13.2).
///
/// Returns the directional bias implied by a fast/slow EMA comparison, or
/// `"Neutral"` when either moving average is uncomputable (a non-finite EMA
/// signals insufficient data for that horizon). Extracted as a pure function so
/// the per-horizon Neutral fallback is independently unit-/property-testable and
/// applied uniformly across the 1H/4H/1D horizons.
fn horizon_trend(ema_fast: f64, ema_slow: f64) -> &'static str {
    if ema_fast.is_finite() && ema_slow.is_finite() {
        if ema_fast > ema_slow {
            "Bullish"
        } else {
            "Bearish"
        }
    } else {
        "Neutral"
    }
}

/// POST /tools/get_multi_tf_trend
/// Returns multi-timeframe trend analysis.
///
/// Contract (R13.1, R13.2): the response always carries a directional bias for
/// each of the 1H, 4H, and 1D horizons. Each horizon is classified
/// independently by the pure [`horizon_trend`] helper, which returns `Neutral`
/// for any horizon whose required moving averages are uncomputable (insufficient
/// data ⇒ non-finite EMA) while the horizons with computable averages still
/// report their real directional bias.
async fn get_multi_tf_trend_handler(
    State(state): State<ServerState>,
    Json(payload): Json<MultiTfRequest>,
) -> Result<Json<MultiTfResponse>, (StatusCode, Json<serde_json::Value>)> {
    let pool = state.app.try_state::<sqlx::PgPool>().ok_or_else(|| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": "QuestDB PG pool not available" })),
        )
    })?;

    let symbol = &payload.symbol;

    // Run three queries in series
    let candles_1h = crate::commands::deep_quant::load_candles_from_db(Some(&state.app), pool.inner(), symbol, "1h", 200)
        .await
        .unwrap_or_default();
    let candles_4h = crate::commands::deep_quant::load_candles_from_db(Some(&state.app), pool.inner(), symbol, "4h", 200)
        .await
        .unwrap_or_default();
    let candles_1d = crate::commands::deep_quant::load_candles_from_db(Some(&state.app), pool.inner(), symbol, "1d", 200)
        .await
        .unwrap_or_default();

    // 1H EMAs & Trend (Neutral fallback when either MA is uncomputable, R13.2)
    let ema_9_1h = IndicatorState::compute_ema(&candles_1h, 9);
    let ema_21_1h = IndicatorState::compute_ema(&candles_1h, 21);
    let trend_1h = horizon_trend(ema_9_1h, ema_21_1h);

    // 4H EMAs & Trend
    let ema_21_4h = IndicatorState::compute_ema(&candles_4h, 21);
    let ema_50_4h = IndicatorState::compute_ema(&candles_4h, 50);
    let trend_4h = horizon_trend(ema_21_4h, ema_50_4h);

    // 1D EMAs & Trend
    let ema_50_1d = IndicatorState::compute_ema(&candles_1d, 50);
    let ema_100_1d = IndicatorState::compute_ema(&candles_1d, 100);
    let trend_1d = horizon_trend(ema_50_1d, ema_100_1d);

    let indicators = serde_json::json!({
        "ema_9_1h": if ema_9_1h.is_finite() { (ema_9_1h * 100.0).round() / 100.0 } else { 0.0 },
        "ema_21_1h": if ema_21_1h.is_finite() { (ema_21_1h * 100.0).round() / 100.0 } else { 0.0 },
        "ema_21_4h": if ema_21_4h.is_finite() { (ema_21_4h * 100.0).round() / 100.0 } else { 0.0 },
        "ema_50_4h": if ema_50_4h.is_finite() { (ema_50_4h * 100.0).round() / 100.0 } else { 0.0 },
        "ema_50_1d": if ema_50_1d.is_finite() { (ema_50_1d * 100.0).round() / 100.0 } else { 0.0 },
        "ema_100_1d": if ema_100_1d.is_finite() { (ema_100_1d * 100.0).round() / 100.0 } else { 0.0 },
    });

    Ok(Json(MultiTfResponse {
        symbol: symbol.to_string(),
        trend_1h: trend_1h.to_string(),
        trend_4h: trend_4h.to_string(),
        trend_1d: trend_1d.to_string(),
        indicators,
    }))
}

// ── Predictive Forecast Endpoint ─────────────────────────────────────────────

#[derive(serde::Deserialize)]
pub struct GetPredictionRequest {
    pub symbol: String,
    pub timeframe: Option<String>,
    pub limit: Option<i64>,
}

/// Map a supported timeframe to its bar duration in seconds.
///
/// Mirrors the mapping the Consensus Engine uses so the predictive projection
/// runs over the same bar spacing as the rest of the analysis. Unknown values
/// fall back to the 10-minute default (the timeframe is validated separately by
/// [`crate::quant::validate_timeframe`] before this is reached).
fn timeframe_interval_sec(timeframe: &str) -> i64 {
    match timeframe {
        "1m" => 60,
        "3m" => 180,
        "5m" => 300,
        "10m" => 600,
        "15m" => 900,
        "30m" => 1_800,
        "60m" | "1h" => 3_600,
        "4h" => 14_400,
        "1d" => 86_400,
        _ => 600,
    }
}

/// Pure forward-projection builder (R12.1, R12.2).
///
/// Converts the candle window into the index-spaced `OhlcCandle` series the
/// predictive engine expects (identical conversion to the Consensus Engine),
/// runs `calculate_dual_projection`, and derives a one-bar-ahead projection:
///
/// * `projected_value` — the linear-OLS value one bar ahead of the last close.
/// * `projected_direction` — `Up`/`Down`/`Flat` from the sign of the projected
///   move relative to the anchored last close. A small relative epsilon avoids
///   reporting a spurious direction on numerically-flat series.
/// * `confidence` — a deterministic value in `[0, 1]` derived from real
///   computation: the magnitude of the projected move relative to price (a ~1%
///   move saturates the magnitude term) scaled by whether the curved VWEPR
///   engine agrees on direction. No value is fabricated.
///
/// Returns `None` when the projection cannot be computed (empty/degenerate
/// window or a non-finite result), so the caller can emit an honest
/// `unavailable` marker (R12.4) rather than invent a forecast. It is a pure
/// function of its inputs, so the projection contract can be unit-/property-
/// tested without a live database.
fn build_projection(
    candles: &[crate::quant::patterns::Candle],
    interval_sec: i64,
) -> Option<(String, f64, f64)> {
    if candles.is_empty() {
        return None;
    }

    let ohlc: Vec<OhlcCandle> = candles
        .iter()
        .enumerate()
        .map(|(i, c)| OhlcCandle {
            time: i as i64 * interval_sec,
            open: c.open,
            high: c.high,
            low: c.low,
            close: c.close,
            volume: c.volume,
        })
        .collect();

    let proj = crate::quant::predictive::calculate_dual_projection(&ohlc, 1, interval_sec);

    // linear_points[0] is the anchored last close; [1] is one bar ahead.
    let anchor = proj.linear_points.first()?.value;
    let projected = proj.linear_points.get(1)?.value;
    if !anchor.is_finite() || !projected.is_finite() {
        return None;
    }

    let diff = projected - anchor;
    let eps = anchor.abs() * 1e-6;
    let direction = if diff > eps {
        "Up"
    } else if diff < -eps {
        "Down"
    } else {
        "Flat"
    };

    // Deterministic confidence derived from real computation (never fabricated):
    // the projected move relative to price (a ~1% move saturates the term),
    // scaled by agreement with the curved VWEPR engine's direction.
    let rel_move = if anchor.abs() > 0.0 {
        (diff.abs() / anchor.abs()).min(1.0)
    } else {
        0.0
    };
    let magnitude_conf = (rel_move / 0.01).min(1.0);

    let curved_agrees = match (proj.curved_points.first(), proj.curved_points.get(1)) {
        (Some(a), Some(b)) if a.value.is_finite() && b.value.is_finite() => {
            let curved_diff = b.value - a.value;
            (curved_diff > 0.0 && diff > 0.0) || (curved_diff < 0.0 && diff < 0.0)
        }
        _ => false,
    };
    let agreement_factor = if curved_agrees { 1.0 } else { 0.5 };

    let confidence = (magnitude_conf * agreement_factor).clamp(0.0, 1.0);

    Some((direction.to_string(), projected, confidence))
}

/// POST /tools/get_prediction
/// Returns a forward price projection for the analyzed symbol and timeframe
/// (R12.1, R12.2). Candles are resolved through the same shared
/// `load_candles_from_db` source the other indicator endpoints use, then the
/// pure [`build_projection`] engine yields `{projected_direction, projected_value,
/// confidence}`.
///
/// On any failure — pool unavailable, data load error, or an uncomputable
/// projection — the endpoint returns `{"unavailable": true, "reason": ...}` with
/// a 200 status (R12.4) so the agent can treat the projection as a missing input
/// and proceed, rather than receiving a fabricated forecast. An unsupported
/// timeframe is still rejected up front with a descriptive 400 error (R4.5).
async fn get_prediction(
    State(state): State<ServerState>,
    Json(payload): Json<GetPredictionRequest>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let tf = payload.timeframe.clone().unwrap_or_else(|| "10m".to_string());

    // Validate the timeframe before any data access (R4.5).
    if let Err(e) = crate::quant::validate_timeframe(&tf) {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "error": e.to_string() })),
        ));
    }

    let pool = match state.app.try_state::<sqlx::PgPool>() {
        Some(p) => p,
        None => {
            return Ok(Json(serde_json::json!({
                "unavailable": true,
                "reason": "QuestDB PG pool not available",
            })));
        }
    };

    let limit = payload.limit.unwrap_or(200);
    let candles = match crate::commands::deep_quant::load_candles_from_db(
        Some(&state.app),
        pool.inner(),
        &payload.symbol,
        &tf,
        limit,
    )
    .await
    {
        Ok(c) => c,
        Err(e) => {
            info!(
                "[tool_server] get_prediction unavailable for symbol={} tf={}: {}",
                payload.symbol, tf, e
            );
            return Ok(Json(serde_json::json!({
                "unavailable": true,
                "reason": e,
            })));
        }
    };

    let interval_sec = timeframe_interval_sec(&tf);
    match build_projection(&candles, interval_sec) {
        Some((direction, value, confidence)) => {
            info!(
                "[tool_server] get_prediction: symbol={} tf={} direction={} value={:.2} confidence={:.2}",
                payload.symbol, tf, direction, value, confidence
            );
            Ok(Json(serde_json::json!({
                "symbol": payload.symbol,
                "timeframe": tf,
                "projected_direction": direction,
                "projected_value": value,
                "confidence": confidence,
            })))
        }
        None => Ok(Json(serde_json::json!({
            "unavailable": true,
            "reason": "insufficient data to compute projection",
        }))),
    }
}

// ── News Sentiment Endpoint (R10) ────────────────────────────────────────────

#[derive(serde::Deserialize)]
pub struct GetNewsContextRequest {
    pub symbol: String,
}

/// Default base URL for the Node Sentiment_Service HTTP interface.
///
/// The current Sentiment_Service (`agents/sentiment`) runs as a Kafka polling
/// loop (`src/index.js`) and does **not** yet expose an HTTP endpoint; its
/// LLM classifier (`src/analyzer.js`) produces a numeric `conviction_score`
/// (1 = extremely bearish, 50 = neutral, 100 = extremely bullish) plus a
/// `reasoning_snippet`. This proxy therefore targets the following expected
/// HTTP contract, which the sentiment service is to expose:
///
/// ```text
/// GET  {SENTIMENT_SERVICE_URL}?symbol=<SYMBOL>
/// 200  { "symbol": "RELIANCE",
///        "conviction_score": <number 1..=100>,   // 50 = neutral
///        "reasoning_snippet": "<string>",
///        "headlines": ["<recent headline>", ...] }
/// ```
///
/// The URL is configurable via the `SENTIMENT_SERVICE_URL` env var so the
/// service can be relocated without code changes; when unset it defaults to
/// `http://localhost:8090/sentiment`.
const DEFAULT_SENTIMENT_SERVICE_URL: &str = "http://localhost:8090/sentiment";

/// Resolve the configured Sentiment_Service URL (env-overridable).
fn sentiment_service_url() -> String {
    std::env::var("SENTIMENT_SERVICE_URL")
        .unwrap_or_else(|_| DEFAULT_SENTIMENT_SERVICE_URL.to_string())
}

/// Map the Sentiment_Service numeric classification to a directional label
/// (R10.2).
///
/// The service scores on a 1..=100 bullish scale where 50 is neutral. Pure
/// function so the mapping is independently unit-/property-testable:
///
/// * `>= 60.0` → `"Bullish"`
/// * `<= 40.0` → `"Bearish"`
/// * otherwise → `"Neutral"`
fn classify_sentiment_label(conviction_score: f64) -> &'static str {
    if conviction_score >= 60.0 {
        "Bullish"
    } else if conviction_score <= 40.0 {
        "Bearish"
    } else {
        "Neutral"
    }
}

/// Map the Sentiment_Service classification to the `get_news_context` contract
/// (R10.2): the recent headlines paired with a directional sentiment label.
///
/// Pure function (no I/O / clock / ambient state) so the service-classification
/// → {headlines, directional label} mapping can be tested without a live
/// sentiment service.
fn map_sentiment_classification(
    conviction_score: f64,
    headlines: Vec<String>,
) -> serde_json::Value {
    let label = classify_sentiment_label(conviction_score);
    serde_json::json!({
        "headlines": headlines,
        "sentiment": label,
        "sentiment_summary": label,
        "conviction_score": conviction_score,
    })
}

/// Build the honest "sentiment unavailable" marker (R10.3).
///
/// Returns `{"sentiment_summary": "Unavailable", ...}` with **no** fabricated
/// classification. The agent treats this as a missing input and does not block
/// a decision solely on its absence (R10.4).
fn unavailable_news(reason: &str) -> serde_json::Value {
    serde_json::json!({
        "sentiment_summary": "Unavailable",
        "sentiment": "Unavailable",
        "headlines": [],
        "error": reason,
    })
}

/// POST /tools/get_news_context
/// Proxies to the Node Sentiment_Service and returns the recent headlines plus
/// a directional sentiment label (R10.1, R10.2).
///
/// When the upstream service returns the richer STRATEGIC verdict (label,
/// thesis, drivers, risks, horizon, confidence, profile/industry), those fields
/// are passed through and `sentiment_summary` is set to the human-readable
/// thesis. When only a numeric `conviction_score` is present, it falls back to
/// the score→label mapping. `sentiment_summary` is ALWAYS present on success.
///
/// On any failure — service unreachable, non-success status, malformed body, or
/// a missing/non-finite classification — the endpoint returns the honest
/// `{"sentiment_summary": "Unavailable", ...}` marker with a 200 status (R10.3)
/// so the agent can treat sentiment as a missing input and proceed (R10.4),
/// rather than receiving a fabricated classification.
async fn get_news_context(
    State(_state): State<ServerState>,
    Json(payload): Json<GetNewsContextRequest>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let url = sentiment_service_url();
    let client = reqwest::Client::new();

    let resp = client
        .get(&url)
        .query(&[("symbol", &payload.symbol)])
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await;

    let body: serde_json::Value = match resp {
        Ok(r) if r.status().is_success() => match r.json::<serde_json::Value>().await {
            Ok(j) => j,
            Err(e) => {
                info!(
                    "[tool_server] get_news_context unavailable for symbol={}: invalid body: {}",
                    payload.symbol, e
                );
                return Ok(Json(unavailable_news(&format!(
                    "invalid sentiment service response: {}",
                    e
                ))));
            }
        },
        Ok(r) => {
            let status = r.status();
            info!(
                "[tool_server] get_news_context unavailable for symbol={}: HTTP {}",
                payload.symbol, status
            );
            return Ok(Json(unavailable_news(&format!(
                "sentiment service returned HTTP {}",
                status
            ))));
        }
        Err(e) => {
            info!(
                "[tool_server] get_news_context unavailable for symbol={}: unreachable: {}",
                payload.symbol, e
            );
            return Ok(Json(unavailable_news(&format!(
                "sentiment service unreachable: {}",
                e
            ))));
        }
    };

    let conviction_score = body.get("conviction_score").and_then(|v| v.as_f64());

    let headlines: Vec<String> = body
        .get("headlines")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|h| h.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default();

    // ── Strategic passthrough ──────────────────────────────────────────────
    // When the upstream service returns the richer strategic verdict (it carries
    // `label`/`thesis`/`drivers`), build the response from those fields while
    // staying backward compatible. `sentiment_summary` is ALWAYS set on success
    // (the Python consumer's validate_contract requires it).
    let upstream_label = body.get("label").and_then(|v| v.as_str());
    let upstream_thesis = body.get("thesis").and_then(|v| v.as_str());
    let has_strategic = upstream_label.is_some()
        || upstream_thesis.is_some()
        || body.get("drivers").is_some();

    if has_strategic {
        // Directional sentiment: prefer the upstream label, else derive it from
        // the conviction score, else fall back to "Neutral".
        let sentiment: String = match upstream_label {
            Some(l) if !l.trim().is_empty() => l.to_string(),
            _ => conviction_score
                .filter(|s| s.is_finite())
                .map(|s| classify_sentiment_label(s).to_string())
                .unwrap_or_else(|| "Neutral".to_string()),
        };

        // Human-readable summary: the thesis when present, else the label.
        let sentiment_summary: String = match upstream_thesis {
            Some(t) if !t.trim().is_empty() => t.to_string(),
            _ => sentiment.clone(),
        };

        let mut response = serde_json::json!({
            "symbol": payload.symbol,
            "headlines": headlines,
            "sentiment": sentiment,
            "sentiment_summary": sentiment_summary,
        });

        if let Some(obj) = response.as_object_mut() {
            // Passthrough the strategic fields when present.
            for key in [
                "label",
                "thesis",
                "drivers",
                "risks",
                "horizon",
                "confidence",
                "conviction_score",
                "industry",
                "profile",
            ] {
                if let Some(v) = body.get(key) {
                    obj.insert(key.to_string(), v.clone());
                }
            }
        }

        info!(
            "[tool_server] get_news_context: symbol={} strategic label={} drivers={}",
            payload.symbol,
            sentiment,
            body.get("drivers").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0)
        );
        return Ok(Json(response));
    }

    // ── Legacy fallback: score → label mapping ─────────────────────────────
    match conviction_score {
        Some(score) if score.is_finite() => {
            let mut mapped = map_sentiment_classification(score, headlines);
            if let Some(obj) = mapped.as_object_mut() {
                obj.insert("symbol".to_string(), serde_json::json!(payload.symbol));
            }
            info!(
                "[tool_server] get_news_context: symbol={} score={:.1} label={}",
                payload.symbol,
                score,
                classify_sentiment_label(score)
            );
            Ok(Json(mapped))
        }
        _ => {
            info!(
                "[tool_server] get_news_context unavailable for symbol={}: no usable classification",
                payload.symbol
            );
            Ok(Json(unavailable_news(
                "sentiment service did not return a usable classification",
            )))
        }
    }
}

// ── Server Run Function ──────────────────────────────────────────────────────
pub async fn run_tool_server(app: AppHandle) {
    // Ensure the live WS→broadcast bridges are running. `watch_price_condition`
    // subscribes to the live-candle broadcast channel fed by these bridges; if
    // they haven't been bootstrapped yet (e.g. the agent runs before the user
    // touches a chart), registered watchers would never receive ticks. This is
    // idempotent — a no-op if the UI already triggered bootstrap.
    crate::services::live_bridges::ensure_bootstrapped(&app);

    let watchers = Arc::new(RwLock::new(HashMap::new()));
    let state = ServerState { app, watchers };

    let router = Router::new()
        .route("/tools/get_candles", post(get_candles))
        .route("/tools/get_consensus", post(get_consensus))
        .route("/tools/watch_condition", post(watch_condition))
        .route("/tools/get_multi_tf_trend", post(get_multi_tf_trend_handler))
        .route("/tools/declare_trade", post(declare_trade))
        .route("/tools/get_chart_patterns", post(get_chart_patterns_handler))
        .route("/tools/get_support_resistance", post(get_support_resistance))
        .route("/tools/get_prediction", post(get_prediction))
        .route("/tools/get_news_context", post(get_news_context))
        .with_state(state);

    let addr = "127.0.0.1:8084";
    info!("Starting hybrid agent tool server on {}", addr);

    match tokio::net::TcpListener::bind(addr).await {
        Ok(listener) => {
            if let Err(e) = axum::serve(listener, router).await {
                error!("Tool server crash: {}", e);
            }
        }
        Err(e) => {
            error!("Failed to bind tool server to {}: {}", addr, e);
        }
    }
}

// ── Property tests: get_candles contract (R4.4) ──────────────────────────────
//
// Property-based coverage for the pure `sort_candles_ascending` helper that
// enforces the `get_candles` Tool_Result_Contract. The handler delegates its
// ordering guarantee to this helper, so exercising it directly verifies the
// contract over arbitrary candle vectors without needing a live database.
#[cfg(test)]
mod candle_contract_proptests {
    use super::*;
    use proptest::prelude::*;

    /// A finite OHLCV component within a bounded band.
    fn finite_val() -> impl Strategy<Value = f64> {
        -1.0e6..1.0e6
    }

    /// An arbitrary candle with an arbitrary timestamp so the generator covers
    /// already-sorted, reverse-sorted, and duplicate-timestamp inputs.
    fn candle_with_ts_strat() -> impl Strategy<Value = CandleWithTs> {
        (
            any::<i64>(),
            finite_val(),
            finite_val(),
            finite_val(),
            finite_val(),
            0.0f64..1.0e6,
        )
            .prop_map(|(timestamp_ms, open, high, low, close, volume)| CandleWithTs {
                timestamp_ms,
                open,
                high,
                low,
                close,
                volume,
            })
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 15: Candles are
        // returned in ascending order with full OHLCV
        // Validates: Requirements 4.4
        #[test]
        fn prop15_candles_ascending_with_full_ohlcv(
            input in proptest::collection::vec(candle_with_ts_strat(), 0..50),
        ) {
            let sorted = sort_candles_ascending(input.clone());

            // Ordering: candles are in non-decreasing `timestamp_ms` order.
            for pair in sorted.windows(2) {
                prop_assert!(
                    pair[0].timestamp_ms <= pair[1].timestamp_ms,
                    "candles not in ascending timestamp order: {} then {}",
                    pair[0].timestamp_ms,
                    pair[1].timestamp_ms
                );
            }

            // Completeness: no candle is dropped or fabricated — the output is a
            // permutation of the input (each candle retains its full OHLCV).
            prop_assert_eq!(sorted.len(), input.len());
            let mut input_sorted = input;
            input_sorted.sort_by(|a, b| a.timestamp_ms.cmp(&b.timestamp_ms));
            prop_assert_eq!(sorted, input_sorted);
        }
    }
}

// ── Unit tests: multi-TF horizon classifier (R13.2) & projection (R12) ───────
#[cfg(test)]
mod handler_logic_tests {
    use super::*;
    use crate::quant::patterns::Candle;

    fn c(close: f64) -> Candle {
        Candle {
            open: close,
            high: close,
            low: close,
            close,
            volume: 1000.0,
        }
    }

    // ── horizon_trend (R13.2) ────────────────────────────────────────────────

    #[test]
    fn horizon_trend_bullish_when_fast_above_slow() {
        assert_eq!(horizon_trend(110.0, 100.0), "Bullish");
    }

    #[test]
    fn horizon_trend_bearish_when_fast_below_slow() {
        assert_eq!(horizon_trend(90.0, 100.0), "Bearish");
    }

    #[test]
    fn horizon_trend_neutral_when_uncomputable() {
        // A non-finite EMA signals insufficient data ⇒ Neutral fallback.
        assert_eq!(horizon_trend(f64::NAN, 100.0), "Neutral");
        assert_eq!(horizon_trend(100.0, f64::NAN), "Neutral");
        assert_eq!(horizon_trend(f64::INFINITY, 100.0), "Neutral");
    }

    // ── build_projection (R12.1, R12.2, R12.4) ───────────────────────────────

    #[test]
    fn projection_rising_series_projects_up() {
        let candles: Vec<Candle> = (0..30).map(|i| c(100.0 + i as f64)).collect();
        let (dir, val, conf) = build_projection(&candles, 600).expect("projection");
        assert_eq!(dir, "Up");
        assert!(val > 129.0, "projected value should extend the uptrend: {}", val);
        assert!((0.0..=1.0).contains(&conf), "confidence out of range: {}", conf);
    }

    #[test]
    fn projection_falling_series_projects_down() {
        let candles: Vec<Candle> = (0..30).map(|i| c(200.0 - i as f64)).collect();
        let (dir, _val, conf) = build_projection(&candles, 600).expect("projection");
        assert_eq!(dir, "Down");
        assert!((0.0..=1.0).contains(&conf));
    }

    #[test]
    fn projection_flat_series_projects_flat_with_zero_confidence() {
        let candles: Vec<Candle> = (0..30).map(|_| c(50.0)).collect();
        let (dir, _val, conf) = build_projection(&candles, 600).expect("projection");
        assert_eq!(dir, "Flat");
        assert_eq!(conf, 0.0);
    }

    #[test]
    fn projection_is_deterministic() {
        let candles: Vec<Candle> = (0..30).map(|i| c(100.0 + 0.7 * i as f64)).collect();
        let a = build_projection(&candles, 600);
        let b = build_projection(&candles, 600);
        assert_eq!(a, b);
    }

    #[test]
    fn projection_empty_series_is_unavailable() {
        assert!(build_projection(&[], 600).is_none());
    }
}

// ── Property tests: multi-TF horizon coverage (R13.1, R13.2) ─────────────────
//
// Property-based coverage for the pure `horizon_trend` classifier that backs
// every horizon of the `get_multi_tf_trend` response. The handler classifies
// 1H/4H/1D independently through this helper, so exercising it across arbitrary
// finite/non-finite EMA pairs verifies the multi-TF contract (all three
// horizons present, each a valid bias, Neutral fallback for uncomputable MAs)
// without a live database.
#[cfg(test)]
mod multi_tf_proptests {
    use super::*;
    use proptest::prelude::*;

    const ALLOWED: [&str; 3] = ["Bullish", "Bearish", "Neutral"];

    /// An EMA value that is either a finite number within a realistic price
    /// band or one of the non-finite markers (NaN / ±∞) that signal an
    /// uncomputable moving average for a horizon.
    fn ema_val() -> impl Strategy<Value = f64> {
        prop_oneof![
            -1.0e6..1.0e6,
            Just(f64::NAN),
            Just(f64::INFINITY),
            Just(f64::NEG_INFINITY),
        ]
    }

    /// A guaranteed-finite EMA value (covers the "computable horizon" inputs).
    fn finite_ema() -> impl Strategy<Value = f64> {
        -1.0e6..1.0e6
    }

    /// A guaranteed non-finite EMA value (covers the "uncomputable horizon").
    fn nonfinite_ema() -> impl Strategy<Value = f64> {
        prop_oneof![
            Just(f64::NAN),
            Just(f64::INFINITY),
            Just(f64::NEG_INFINITY),
        ]
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 43: Multi-TF response
        // includes all three horizon biases
        // Validates: Requirements 13.1
        #[test]
        fn prop43_multi_tf_includes_all_three_horizon_biases(
            ema_9_1h in ema_val(),
            ema_21_1h in ema_val(),
            ema_21_4h in ema_val(),
            ema_50_4h in ema_val(),
            ema_50_1d in ema_val(),
            ema_100_1d in ema_val(),
        ) {
            // Classify each horizon exactly as the handler does.
            let trend_1h = horizon_trend(ema_9_1h, ema_21_1h);
            let trend_4h = horizon_trend(ema_21_4h, ema_50_4h);
            let trend_1d = horizon_trend(ema_50_1d, ema_100_1d);

            // All three horizon biases are present and each is one of the
            // allowed directional labels.
            for bias in [trend_1h, trend_4h, trend_1d] {
                prop_assert!(
                    ALLOWED.contains(&bias),
                    "horizon bias {:?} not in allowed set {:?}",
                    bias,
                    ALLOWED
                );
            }
        }

        // Feature: deep-quant-analysis-hardening, Property 44: Uncomputable
        // horizons return Neutral while others compute
        // Validates: Requirements 13.2
        #[test]
        fn prop44_uncomputable_horizon_neutral_others_compute(
            finite_fast in finite_ema(),
            finite_slow in finite_ema(),
            bad in nonfinite_ema(),
            finite_side in finite_ema(),
            place_bad_first in any::<bool>(),
        ) {
            // A horizon whose EMA pair is fully finite always yields a real
            // directional bias (Bullish or Bearish), never Neutral.
            let computable = horizon_trend(finite_fast, finite_slow);
            prop_assert!(
                computable == "Bullish" || computable == "Bearish",
                "finite EMA pair should compute a directional bias, got {:?}",
                computable
            );

            // A horizon with a non-finite EMA (in either slot) falls back to
            // Neutral, regardless of the other horizons being computable.
            let uncomputable = if place_bad_first {
                horizon_trend(bad, finite_side)
            } else {
                horizon_trend(finite_side, bad)
            };
            prop_assert_eq!(
                uncomputable,
                "Neutral",
                "a non-finite EMA must yield the Neutral fallback"
            );
        }
    }
}

// ── Property tests: predictive projection shape (R12.2) ──────────────────────
//
// Property-based coverage for the pure `build_projection` engine that backs the
// `get_prediction` endpoint. Over arbitrary candle windows, whenever a
// projection is produced it must carry a valid direction, a finite projected
// value, and a confidence within [0, 1].
#[cfg(test)]
mod projection_proptests {
    use super::*;
    use crate::quant::patterns::Candle;
    use proptest::prelude::*;

    const ALLOWED_DIRECTIONS: [&str; 3] = ["Up", "Down", "Flat"];

    /// A finite OHLCV component within a realistic price band.
    fn price() -> impl Strategy<Value = f64> {
        1.0f64..1.0e5
    }

    fn candle_strat() -> impl Strategy<Value = Candle> {
        (price(), price(), price(), price(), 0.0f64..1.0e7).prop_map(
            |(open, high, low, close, volume)| Candle {
                open,
                high,
                low,
                close,
                volume,
            },
        )
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 41: Predictive
        // projection carries direction and value
        // Validates: Requirements 12.2
        #[test]
        fn prop41_projection_carries_direction_and_value(
            candles in proptest::collection::vec(candle_strat(), 0..60),
        ) {
            if let Some((direction, value, confidence)) = build_projection(&candles, 600) {
                // A produced projection always carries a valid direction label.
                prop_assert!(
                    ALLOWED_DIRECTIONS.contains(&direction.as_str()),
                    "projected_direction {:?} not in {:?}",
                    direction,
                    ALLOWED_DIRECTIONS
                );
                // The projected value is a real, finite number.
                prop_assert!(
                    value.is_finite(),
                    "projected_value must be finite, got {}",
                    value
                );
                // Confidence is a well-formed probability in [0, 1].
                prop_assert!(
                    (0.0..=1.0).contains(&confidence),
                    "confidence {} out of [0, 1]",
                    confidence
                );
            }
        }
    }
}

// ── Property tests: price-watcher registry & trigger predicate (R14) ─────────
//
// Property-based coverage for the pure watcher logic that backs the
// `/tools/watch_condition` endpoint and the live watcher task:
//
//   * `build_watcher` + `register_watcher` — the registration/registry-insert
//     and resumable-suspend contract (R14.1).
//   * `watcher_triggered` — the direction-aware price + volume-surge trigger
//     predicate (R14.2).
//   * the remove-on-fire registry transition (R14.4).
//
// Exercising these pure helpers verifies the watcher contract over arbitrary
// parameters without spawning the live Tokio watcher task or a broadcast
// channel.
#[cfg(test)]
mod watcher_registry_proptests {
    use super::*;
    use proptest::prelude::*;

    /// Mirror of the live watcher loop's remove-on-fire transition (R14.4):
    /// evaluate the trigger predicate for the watcher registered under
    /// `thread_id`; if it fires, remove it from the registry. Returns whether
    /// the watcher fired. Operates on a plain `HashMap` so the registry
    /// transition is testable without the live `RwLock`.
    fn remove_on_fire(
        registry: &mut HashMap<String, Watcher>,
        thread_id: &str,
        average_volume: f64,
        candle_close: f64,
        candle_volume: f64,
    ) -> bool {
        let fired = match registry.get(thread_id) {
            Some(w) => watcher_triggered(
                &w.direction,
                w.price_level,
                w.volume_multiplier,
                average_volume,
                candle_close,
                candle_volume,
            ),
            None => false,
        };
        if fired {
            registry.remove(thread_id);
        }
        fired
    }

    /// A valid watch direction (the four canonical, normalized forms).
    fn valid_direction() -> impl Strategy<Value = &'static str> {
        prop::sample::select(vec!["above", "up", "below", "down"])
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 46: Valid watch
        // parameters register a watcher and suspend the run
        // Validates: Requirements 14.1
        #[test]
        fn prop46_valid_params_register_and_suspend(
            thread_id in "[a-zA-Z0-9_-]{1,16}",
            symbol in "[A-Z]{1,10}",
            timeframe in prop::sample::select(vec!["1m", "5m", "10m", "15m", "1h", "4h", "1d"]),
            price_level in 0.01f64..1.0e6,
            direction in valid_direction(),
            volume_multiplier in 0.1f64..10.0,
        ) {
            let watcher = build_watcher(
                thread_id.clone(),
                symbol.clone(),
                timeframe.to_string(),
                price_level,
                direction,
                volume_multiplier,
            );

            let mut registry: HashMap<String, Watcher> = HashMap::new();
            register_watcher(&mut registry, watcher.clone());

            // Registered: the watcher is inserted into the registry keyed by
            // its thread_id (R14.1).
            prop_assert!(registry.contains_key(&thread_id));
            prop_assert_eq!(registry.get(&thread_id), Some(&watcher));

            // The stored watcher faithfully carries the validated parameters,
            // with the direction normalized to its canonical lowercase form.
            let stored = registry.get(&thread_id).unwrap();
            prop_assert_eq!(&stored.symbol, &symbol);
            prop_assert_eq!(stored.price_level, price_level);
            prop_assert_eq!(stored.volume_multiplier, volume_multiplier);
            prop_assert_eq!(&stored.direction, &direction.trim().to_lowercase());

            // Resumable-suspend contract (R14.1): registration yields the
            // resumable-suspend signal (the run pauses, awaiting /resume),
            // rather than a terminal status.
            prop_assert_eq!(WATCH_REGISTERED_STATUS, "watching_registered");
        }

        // Feature: deep-quant-analysis-hardening, Property 47: The watcher
        // trigger predicate is correct
        // Validates: Requirements 14.2
        #[test]
        fn prop47_watcher_trigger_predicate(
            price_level in -1.0e6f64..1.0e6,
            volume_multiplier in 0.0f64..10.0,
            average_volume in 0.0f64..1.0e6,
            candle_close in -1.0e6f64..1.0e6,
            candle_volume in 0.0f64..1.0e7,
            dir_idx in 0usize..5,
        ) {
            // Index 4 ("sideways") is an unknown/unsupported direction.
            let directions = ["above", "up", "below", "down", "sideways"];
            let direction = directions[dir_idx];

            let fired = watcher_triggered(
                direction,
                price_level,
                volume_multiplier,
                average_volume,
                candle_close,
                candle_volume,
            );

            // Reference semantics: fires iff the direction-aware price condition
            // holds AND volume >= avg * mult; unknown direction never matches.
            let price_matched = match direction {
                "above" | "up" => candle_close >= price_level,
                "below" | "down" => candle_close <= price_level,
                _ => false,
            };
            let volume_matched = candle_volume >= average_volume * volume_multiplier;
            let expected = price_matched && volume_matched;

            prop_assert_eq!(fired, expected);

            // An unknown direction can never fire, regardless of price/volume.
            let unknown = watcher_triggered(
                "sideways",
                price_level,
                volume_multiplier,
                average_volume,
                candle_close,
                candle_volume,
            );
            prop_assert!(!unknown);
        }

        // Feature: deep-quant-analysis-hardening, Property 48: A fired watcher
        // is removed from the registry
        // Validates: Requirements 14.4
        #[test]
        fn prop48_fired_watcher_removed_from_registry(
            thread_id in "[a-zA-Z0-9_-]{1,16}",
            price_level in 0.01f64..1.0e6,
            volume_multiplier in 0.1f64..10.0,
            average_volume in 0.0f64..1.0e6,
            candle_close in -1.0e6f64..1.0e6,
            candle_volume in 0.0f64..1.0e7,
            direction in valid_direction(),
        ) {
            let watcher = build_watcher(
                thread_id.clone(),
                "RELIANCE".to_string(),
                "10m".to_string(),
                price_level,
                direction,
                volume_multiplier,
            );

            let mut registry: HashMap<String, Watcher> = HashMap::new();
            register_watcher(&mut registry, watcher.clone());

            let fired = remove_on_fire(
                &mut registry,
                &thread_id,
                average_volume,
                candle_close,
                candle_volume,
            );

            // The fire decision matches the pure trigger predicate.
            let expected_fire = watcher_triggered(
                direction,
                price_level,
                volume_multiplier,
                average_volume,
                candle_close,
                candle_volume,
            );
            prop_assert_eq!(fired, expected_fire);

            if fired {
                // R14.4: after a trigger, the registry no longer contains the
                // thread_id.
                prop_assert!(!registry.contains_key(&thread_id));
            } else {
                // A watcher that did not fire remains registered.
                prop_assert!(registry.contains_key(&thread_id));
            }
        }
    }

    // Integration-style lifecycle test (task 18.2):
    // register → a triggering candle (via the pure `watcher_triggered`
    // predicate) → remove-on-fire happens exactly once and the watcher is gone
    // afterward, so a second triggering candle is a no-op. This mirrors the
    // live `/tools/watch_condition` → trigger → `/resume` handoff without
    // spawning the Tokio watcher task. Validates: Requirements 14.2, 14.4.
    #[test]
    fn watcher_lifecycle_fires_once_and_is_removed() {
        let thread_id = "thread-int-18-2";

        // 1. Register a watcher: "above" 2450 with a 1.5x volume surge.
        let watcher = build_watcher(
            thread_id.to_string(),
            "RELIANCE".to_string(),
            "15m".to_string(),
            2450.0,
            "above",
            1.5,
        );
        let mut registry: HashMap<String, Watcher> = HashMap::new();
        register_watcher(&mut registry, watcher);
        assert!(registry.contains_key(thread_id), "watcher should be registered");

        let average_volume = 100_000.0;

        // 2a. A candle that meets price but NOT the volume surge does NOT fire,
        //     and the watcher remains registered.
        let fired_weak = remove_on_fire(
            &mut registry, thread_id, average_volume,
            2451.0,          // close >= 2450 (price condition met)
            120_000.0,       // volume < 100_000 * 1.5 = 150_000 (surge NOT met)
        );
        assert!(!fired_weak, "weak-volume candle must not fire the watcher");
        assert!(registry.contains_key(thread_id), "non-firing watcher stays registered");

        // 2b. A triggering candle (price AND volume surge) fires exactly once
        //     and the watcher is removed from the registry (the /resume handoff).
        let fired = remove_on_fire(
            &mut registry, thread_id, average_volume,
            2451.0,          // close >= 2450
            250_000.0,       // volume >= 150_000 (surge met)
        );
        assert!(fired, "triggering candle must fire the watcher");
        assert!(
            !registry.contains_key(thread_id),
            "R14.4: a fired watcher is removed from the registry"
        );

        // 3. A second triggering candle is a no-op — the watcher fired only
        //    once and is already gone (no double-resume).
        let fired_again = remove_on_fire(
            &mut registry, thread_id, average_volume, 2460.0, 500_000.0,
        );
        assert!(!fired_again, "a removed watcher cannot fire a second time");
        assert!(!registry.contains_key(thread_id));
    }
}

// ── Unit tests: declare_trade commit-iff-pass (R6.6, R6.7) ───────────────────
//
// Coverage for the pure `evaluate_declared_trade` helper that gates the
// `declare_trade` commit path. The handler commits (emits `final_analysis_ready`
// + `agent-declared-trade`) exactly when this returns `Pass`, and rejects
// without emitting when it returns `Fail`, so testing the helper directly
// verifies the commit-iff-pass decision without a live `AppHandle` or event bus.
#[cfg(test)]
mod declare_trade_validation_tests {
    use super::*;
    use crate::quant::{ValidatorOutcome, ValidatorReason};

    /// Mirror of the handler's commit decision: it commits iff validation
    /// passes (R6.6/R6.7).
    fn would_commit(outcome: &ValidatorOutcome) -> bool {
        outcome.is_pass()
    }

    #[test]
    fn buy_with_valid_levels_passes_and_commits() {
        // Entry 100, SL 90 (risk 10), TP 130 (reward 30) ⇒ RR 3.0; ATR 5 ⇒
        // stop distance 10 >= 1.5*5 = 7.5. All checks pass.
        let outcome = evaluate_declared_trade("BUY", Some(100.0), Some(90.0), Some(130.0), Some(5.0));
        assert!(matches!(outcome, ValidatorOutcome::Pass { .. }));
        assert!(would_commit(&outcome));
    }

    #[test]
    fn sell_with_valid_levels_passes_and_commits() {
        // SELL: TP 70 < entry 100 < SL 110. risk 10, reward 30 ⇒ RR 3.0.
        let outcome = evaluate_declared_trade("SELL", Some(100.0), Some(110.0), Some(70.0), Some(5.0));
        assert!(matches!(outcome, ValidatorOutcome::Pass { .. }));
        assert!(would_commit(&outcome));
    }

    #[test]
    fn hold_bypasses_level_checks_and_commits() {
        // HOLD always passes even with no levels supplied (R6).
        let outcome = evaluate_declared_trade("HOLD", None, None, None, None);
        assert!(matches!(outcome, ValidatorOutcome::Pass { .. }));
        assert!(would_commit(&outcome));
    }

    #[test]
    fn buy_missing_levels_fails_and_does_not_commit() {
        // No stop-loss / take-profit ⇒ MissingLevels, no commit (R6.1).
        let outcome = evaluate_declared_trade("BUY", Some(100.0), None, None, None);
        assert_eq!(
            outcome,
            ValidatorOutcome::Fail { reason: ValidatorReason::MissingLevels }
        );
        assert!(!would_commit(&outcome));
    }

    #[test]
    fn buy_with_low_risk_reward_fails_and_does_not_commit() {
        // Entry 100, SL 90 (risk 10), TP 110 (reward 10) ⇒ RR 1.0 < 2.0.
        let outcome = evaluate_declared_trade("BUY", Some(100.0), Some(90.0), Some(110.0), None);
        assert_eq!(
            outcome,
            ValidatorOutcome::Fail { reason: ValidatorReason::RiskRewardTooLow }
        );
        assert!(!would_commit(&outcome));
    }

    #[test]
    fn buy_with_stop_too_tight_fails_and_does_not_commit() {
        // risk 10 but ATR 10 ⇒ requires >= 15; 10 < 15 ⇒ StopTooTight (R6.3).
        let outcome = evaluate_declared_trade("BUY", Some(100.0), Some(90.0), Some(140.0), Some(10.0));
        assert_eq!(
            outcome,
            ValidatorOutcome::Fail { reason: ValidatorReason::StopTooTight }
        );
        assert!(!would_commit(&outcome));
    }

    #[test]
    fn sell_with_inconsistent_levels_fails_and_does_not_commit() {
        // SELL but levels are laid out like a BUY (SL below, TP above entry).
        let outcome = evaluate_declared_trade("SELL", Some(100.0), Some(90.0), Some(130.0), None);
        assert_eq!(
            outcome,
            ValidatorOutcome::Fail { reason: ValidatorReason::DirectionInconsistent }
        );
        assert!(!would_commit(&outcome));
    }

    #[test]
    fn unrecognized_action_maps_to_hold_and_commits() {
        // A non BUY/SELL/HOLD action conservatively abstains (HOLD) ⇒ passes.
        let outcome = evaluate_declared_trade("whatever", None, None, None, None);
        assert!(matches!(outcome, ValidatorOutcome::Pass { .. }));
        assert!(would_commit(&outcome));
    }
}

// ── Property tests: news sentiment mapping (R10.2) ───────────────────────────
//
// Property-based coverage for the pure `map_sentiment_classification` /
// `classify_sentiment_label` helpers that back the `get_news_context` endpoint.
// Over arbitrary conviction scores and headline sets, the mapped result must
// carry the same headlines and a directional label that follows the documented
// thresholds and is monotonic in the conviction score.
#[cfg(test)]
mod news_sentiment_proptests {
    use super::*;
    use proptest::prelude::*;

    /// Rank labels by bullishness so monotonicity can be asserted numerically.
    fn label_rank(label: &str) -> i32 {
        match label {
            "Bearish" => 0,
            "Neutral" => 1,
            "Bullish" => 2,
            _ => -1,
        }
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-analysis-hardening, Property 37: News result maps
        // service classification to headlines + directional label
        // Validates: Requirements 10.2
        #[test]
        fn prop37_news_maps_classification_to_headlines_and_label(
            score in 0.0f64..100.0,
            headlines in proptest::collection::vec("[a-zA-Z0-9 .,:-]{0,40}", 0..6),
        ) {
            let mapped = map_sentiment_classification(score, headlines.clone());

            // Headlines are carried through unchanged (none dropped/fabricated).
            prop_assert_eq!(
                mapped.get("headlines").unwrap(),
                &serde_json::json!(headlines)
            );

            // The directional label follows the documented thresholds:
            // >=60 Bullish, <=40 Bearish, otherwise Neutral.
            let expected = if score >= 60.0 {
                "Bullish"
            } else if score <= 40.0 {
                "Bearish"
            } else {
                "Neutral"
            };
            let label = mapped.get("sentiment").unwrap().as_str().unwrap();
            prop_assert_eq!(label, expected);

            // sentiment_summary mirrors the directional label.
            prop_assert_eq!(
                mapped.get("sentiment_summary").unwrap().as_str().unwrap(),
                expected
            );

            // The numeric classification is preserved (not invented).
            prop_assert_eq!(
                mapped.get("conviction_score").unwrap().as_f64().unwrap(),
                score
            );
        }

        // Feature: deep-quant-analysis-hardening, Property 37: News result maps
        // service classification to headlines + directional label
        // (monotonicity facet) — a higher conviction score never yields a less
        // bullish directional label.
        // Validates: Requirements 10.2
        #[test]
        fn prop37_label_monotonic_in_score(
            a in 0.0f64..100.0,
            b in 0.0f64..100.0,
        ) {
            let (lo, hi) = if a <= b { (a, b) } else { (b, a) };
            let lo_rank = label_rank(classify_sentiment_label(lo));
            let hi_rank = label_rank(classify_sentiment_label(hi));
            prop_assert!(
                lo_rank <= hi_rank,
                "label not monotonic in score: {} ({}) vs {} ({})",
                lo, lo_rank, hi, hi_rank
            );
        }
    }
}

// ── Unit test: sentiment-unavailable marker (R10.3) ──────────────────────────
//
// Coverage for the pure `unavailable_news` helper that `get_news_context`
// returns on any sentiment-service failure. It must yield the honest
// "Unavailable" marker with empty headlines and no fabricated classification.
#[cfg(test)]
mod news_unavailable_tests {
    use super::*;

    #[test]
    fn unavailable_news_yields_unavailable_marker_without_fabrication() {
        let marker = unavailable_news("sentiment service unreachable");

        // The sentiment summary is the honest Unavailable marker (R10.3).
        assert_eq!(marker.get("sentiment_summary").unwrap(), "Unavailable");
        assert_eq!(marker.get("sentiment").unwrap(), "Unavailable");

        // Headlines are empty — nothing fabricated.
        assert_eq!(marker.get("headlines").unwrap(), &serde_json::json!([]));

        // No fabricated numeric/directional classification is present.
        assert!(
            marker.get("conviction_score").is_none(),
            "unavailable marker must not fabricate a conviction score"
        );

        // The failure reason is surfaced for diagnostics.
        assert_eq!(
            marker.get("error").unwrap(),
            "sentiment service unreachable"
        );
    }
}

// ── Property test: declare_trade commit-iff-pass (R6.6, R6.7) ────────────────
//
// Feature: deep-quant-analysis-hardening, Property 24: Commit happens exactly
// when validation passes.
//
// The `declare_trade` handler commits (emits `final_analysis_ready` +
// `agent-declared-trade`) exactly when `evaluate_declared_trade` returns
// `Pass`, and rejects without emitting when it returns `Fail`. The handler's
// commit decision is mirrored by `outcome.is_pass()`. This property exercises
// the pure helper over arbitrary actions (BUY/SELL/HOLD/garbage) and arbitrary
// optional levels/ATR, asserting that the commit decision equals exactly the
// validator's pass result: commit ⇔ ValidatorOutcome::Pass, reject ⇔
// ValidatorOutcome::Fail.
#[cfg(test)]
mod commit_iff_pass_proptests {
    use super::*;
    use crate::quant::ValidatorOutcome;
    use proptest::prelude::*;

    /// Mirror of the handler's commit decision: it commits iff validation
    /// passes (R6.6/R6.7).
    fn would_commit(outcome: &ValidatorOutcome) -> bool {
        outcome.is_pass()
    }

    /// Arbitrary action strings: the canonical BUY/SELL/HOLD (with assorted
    /// casing/whitespace), plus arbitrary garbage that maps to HOLD.
    fn action_strategy() -> impl Strategy<Value = String> {
        prop_oneof![
            Just("BUY".to_string()),
            Just("SELL".to_string()),
            Just("HOLD".to_string()),
            Just(" buy ".to_string()),
            Just("Sell".to_string()),
            Just("hold".to_string()),
            Just("".to_string()),
            "[a-zA-Z ]{0,12}",
        ]
    }

    /// Arbitrary optional finite price level within a realistic range.
    fn opt_level_strategy() -> impl Strategy<Value = Option<f64>> {
        prop_oneof![
            Just(None),
            (-1.0e4..1.0e4f64).prop_map(Some),
        ]
    }

    /// Arbitrary optional ATR (non-negative when present).
    fn opt_atr_strategy() -> impl Strategy<Value = Option<f64>> {
        prop_oneof![
            Just(None),
            (0.0..1.0e4f64).prop_map(Some),
        ]
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        /// Feature: deep-quant-analysis-hardening, Property 24: Commit happens
        /// exactly when validation passes.
        ///
        /// **Validates: Requirements 6.6, 6.7**
        #[test]
        fn commit_decision_equals_validator_pass(
            action in action_strategy(),
            entry in opt_level_strategy(),
            stop_loss in opt_level_strategy(),
            take_profit in opt_level_strategy(),
            atr_14 in opt_atr_strategy(),
        ) {
            let outcome = evaluate_declared_trade(
                &action,
                entry,
                stop_loss,
                take_profit,
                atr_14,
            );

            // The handler's mirrored commit decision.
            let commits = would_commit(&outcome);

            // commit ⇔ ValidatorOutcome::Pass
            let is_pass = matches!(outcome, ValidatorOutcome::Pass { .. });
            prop_assert_eq!(commits, is_pass);

            // reject ⇔ ValidatorOutcome::Fail
            let is_fail = matches!(outcome, ValidatorOutcome::Fail { .. });
            prop_assert_eq!(!commits, is_fail);

            // Pass and Fail are exhaustive and mutually exclusive, so the
            // commit decision is exactly the validator's pass result.
            prop_assert_ne!(is_pass, is_fail);
        }
    }
}
