// quant/tool_server.rs — Local HTTP Tool Server for Hybrid Agent.
//
// Serves endpoints on localhost:8084 to interface with Python LangGraph service:
//   - POST /tools/get_candles
//   - POST /tools/get_consensus
//   - POST /tools/get_multi_tf_trend
//   - POST /tools/watch_condition
//   - POST /tools/declare_trade

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

#[derive(Debug, Clone, serde::Serialize)]
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
#[derive(serde::Serialize)]
struct CandleWithTs {
    pub timestamp_ms: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

/// POST /tools/get_candles
/// Fetches candles from QuestDB and returns them as JSON with timestamps.
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

    let result: Vec<CandleWithTs> = timed_candles
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

    let watcher = Watcher {
        thread_id: payload.thread_id.clone(),
        symbol: watch_symbol.clone(),
        timeframe,
        price_level: payload.price_level,
        direction: payload.direction.trim().to_lowercase(),
        volume_multiplier: payload.volume_multiplier,
    };

    // Register watcher
    {
        let mut map = state.watchers.write().await;
        map.insert(payload.thread_id.clone(), watcher.clone());
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
                let price_matched = match watcher.direction.as_str() {
                    "above" | "up" => candle.close >= watcher.price_level,
                    "below" | "down" => candle.close <= watcher.price_level,
                    _ => {
                        error!("[watcher] Unknown direction: {}", watcher.direction);
                        false
                    }
                };

                let volume_matched = candle.volume >= avg_volume * watcher.volume_multiplier;

                if price_matched && volume_matched {
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
                                            
                                            if let (Some(ev_type), false) = (event_type, data_lines.is_empty()) {
                                                let joined_data = data_lines.join("\n");
                                                if let Ok(json_val) = serde_json::from_str::<serde_json::Value>(&joined_data) {
                                                    let outbound = serde_json::json!({
                                                        "event": ev_type,
                                                        "data": json_val
                                                    });
                                                    let _ = app_clone.emit("deep-quant-stream", outbound);
                                                }
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

    Ok(Json(serde_json::json!({ "status": "watching_registered" })))
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
}

/// POST /tools/declare_trade
/// Commits the agent's final decision. Emits a `final_analysis_ready` event
/// (the same event the Glass-Box loop uses) plus an `agent-declared-trade`
/// event carrying the full decision, so the UI records a real, structured
/// plan instead of relying on the model's prose being re-parsed downstream.
async fn declare_trade(
    State(state): State<ServerState>,
    Json(payload): Json<DeclareTradeRequest>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let conviction = payload.conviction_score.clamp(0, 100);

    let plan = crate::quant::AiExecutionPlan {
        conviction_score: conviction,
        setup_validation: payload.setup_validation.clone(),
        execution_plan: payload.execution_plan.clone(),
    };

    info!(
        "[tool_server] declare_trade: symbol={:?} action={} conviction={}",
        payload.symbol, payload.action, conviction
    );

    // Surface the structured plan to the React UI (same channel the Glass-Box
    // loop uses) so the committed decision is rendered consistently.
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

/// POST /tools/get_multi_tf_trend
/// Returns multi-timeframe trend analysis.
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

    // 1H EMAs & Trend
    let ema_9_1h = IndicatorState::compute_ema(&candles_1h, 9);
    let ema_21_1h = IndicatorState::compute_ema(&candles_1h, 21);
    let trend_1h = if ema_9_1h.is_finite() && ema_21_1h.is_finite() {
        if ema_9_1h > ema_21_1h { "Bullish" } else { "Bearish" }
    } else {
        "Neutral"
    };

    // 4H EMAs & Trend
    let ema_21_4h = IndicatorState::compute_ema(&candles_4h, 21);
    let ema_50_4h = IndicatorState::compute_ema(&candles_4h, 50);
    let trend_4h = if ema_21_4h.is_finite() && ema_50_4h.is_finite() {
        if ema_21_4h > ema_50_4h { "Bullish" } else { "Bearish" }
    } else {
        "Neutral"
    };

    // 1D EMAs & Trend
    let ema_50_1d = IndicatorState::compute_ema(&candles_1d, 50);
    let ema_100_1d = IndicatorState::compute_ema(&candles_1d, 100);
    let trend_1d = if ema_50_1d.is_finite() && ema_100_1d.is_finite() {
        if ema_50_1d > ema_100_1d { "Bullish" } else { "Bearish" }
    } else {
        "Neutral"
    };

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
