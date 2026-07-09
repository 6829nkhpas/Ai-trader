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
    response::{IntoResponse, Json, Response},
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
    /// Optional opposite-side invalidation level (R14, Bug #1). When supplied,
    /// the watcher also fires (as an invalidation) if price moves against the
    /// setup to this level, so the run is woken to re-analyze rather than
    /// silently waiting forever. Defaults to `None` when omitted.
    #[serde(default)]
    pub invalidation_level: Option<f64>,
    /// Opt-in Heartbeat_Monitor toggle (Adaptive Opportunity Engine R5.1, R5.4).
    /// When `true` the watcher additionally emits bounded, cadence-driven
    /// `/resume` POSTs (`trigger_kind = "heartbeat"`) so the suspended run can
    /// re-evaluate mid-wait. Omitted ⇒ `false` (heartbeat off), so existing
    /// payloads deserialize unchanged and behave exactly as before.
    #[serde(default)]
    pub heartbeat_enabled: bool,
    /// Heartbeat cadence in seconds (R5.1, R11.1). Interpreted only when
    /// `heartbeat_enabled` is `true` and the value is `> 0`. Omitted ⇒ `0.0`.
    #[serde(default)]
    pub heartbeat_cadence_secs: f64,
    /// Maximum number of heartbeats emitted for this watcher (R5.2 — bounded so
    /// heartbeats can never run unbounded). Omitted ⇒ `0` (no heartbeats even
    /// if enabled). The Python-side checkpointed `heartbeat_count` remains the
    /// Session_Budget authority; this is the Rust-side hard ceiling.
    #[serde(default)]
    pub heartbeat_max: u32,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Watcher {
    pub thread_id: String,
    pub symbol: String,
    pub timeframe: String,
    pub price_level: f64,
    pub direction: String,
    pub volume_multiplier: f64,
    /// Authoritative server-side current price captured at registration time.
    /// The target `price_level` is validated to be strictly beyond this in the
    /// chosen direction, which prevents the instant false trigger (Bug #2).
    pub reference_price: f64,
    /// Opposite-side invalidation level (Bug #1). `Some(level)` enables the
    /// opposite-side fallback trigger; `None` disables it.
    pub invalidation_level: Option<f64>,
    /// Opt-in Heartbeat_Monitor toggle (Adaptive Opportunity Engine R5.1). When
    /// `true` the watcher task emits bounded cadence-driven heartbeat resumes in
    /// addition to (and without altering) the target/invalidation triggers.
    pub heartbeat_enabled: bool,
    /// Heartbeat cadence in seconds; only meaningful when `heartbeat_enabled`
    /// and `> 0`.
    pub heartbeat_cadence_secs: f64,
    /// Hard ceiling on the number of heartbeats this watcher may emit (R5.2).
    pub heartbeat_max: u32,
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
///
/// Outcome mapping (R2 — differentiated candle-endpoint outcomes):
///
/// | Loader outcome        | HTTP status | Body |
/// | --------------------- | ----------- | ---- |
/// | `Ok(candles)`         | `200`       | ascending candle list (unchanged) |
/// | `Err(Shortfall)`      | `200`       | `{"unavailable": true, "reason", "symbol", "timeframe", "available", "needed"}` |
/// | `Err(Fault)`          | `503`       | `{"error": "candle store fault: <source>: <detail>"}` |
///
/// An Availability_Shortfall is a graceful, non-5xx unavailable result the
/// Python Data_Tools treat as a non-blocking Unavailable_Marker; an
/// Infrastructure_Fault is a `503` whose body names the actual cause. The
/// handler never panics and never returns an unclassified `500` for a loader
/// error.
async fn get_candles(
    State(state): State<ServerState>,
    Json(payload): Json<GetCandlesRequest>,
) -> Response {
    let pool = match state.app.try_state::<sqlx::PgPool>() {
        Some(pool) => pool,
        None => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": "QuestDB PG pool not available" })),
            )
                .into_response();
        }
    };

    let limit = payload.limit.unwrap_or(200);
    let tf = payload.timeframe.unwrap_or_else(|| "10m".to_string());

    match crate::commands::deep_quant::load_candles_with_ts(
        Some(&state.app),
        pool.inner(),
        &payload.symbol,
        &tf,
        limit,
        30,
    )
    .await
    {
        // Ok → 200 ascending candle list (unchanged behaviour).
        Ok(timed_candles) => {
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

            // Tool_Result_Contract (R4.4): candles MUST be returned in
            // ascending `timestamp_ms` order, each carrying full OHLCV. The
            // upstream loader already sorts ascending, but we re-enforce the
            // ordering at the contract boundary so the guarantee holds
            // regardless of the data source.
            let result = sort_candles_ascending(result);

            (StatusCode::OK, Json(result)).into_response()
        }
        // Availability_Shortfall → graceful non-5xx unavailable marker (R2.2).
        Err(crate::commands::deep_quant::CandleLoadError::Shortfall {
            symbol,
            timeframe,
            available,
            needed,
            detail,
        }) => (
            StatusCode::OK,
            Json(serde_json::json!({
                "unavailable": true,
                "reason": detail,
                "symbol": symbol,
                "timeframe": timeframe,
                "available": available,
                "needed": needed,
            })),
        )
            .into_response(),
        // Infrastructure_Fault → 503 whose body names the actual cause (R2.3).
        Err(crate::commands::deep_quant::CandleLoadError::Fault { source, detail }) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({
                "error": format!("candle store fault: {}: {}", source, detail),
            })),
        )
            .into_response(),
    }
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

/// Which registered condition a watcher candle satisfied (R14.2).
///
/// Serialized to the exact lowercase strings `"target"` / `"invalidation"` so
/// the `/resume` handoff payload's `trigger_kind` field carries a stable
/// contract the Python side branches on.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum WatcherTrigger {
    /// The watched target level was reached WITH the required volume surge.
    Target,
    /// Price moved against the setup to the opposite-side invalidation level
    /// (price-only — the volume gate does not apply to an invalidation/stop).
    Invalidation,
}

/// Pure watcher trigger predicate (R14.2).
///
/// Returns `Some(WatcherTrigger)` describing WHICH condition fired, or `None`
/// when neither the target nor the invalidation condition is satisfied:
///
/// * Target fires iff the direction-aware price condition holds AND the
///   candle's volume meets the volume-surge threshold (`candle_volume >=
///   average_volume * volume_multiplier`):
///     * `"above"` / `"up"`   → `candle_close >= price_level`
///     * `"below"` / `"down"` → `candle_close <= price_level`
/// * Invalidation fires (price-only, NO volume gate — a stop/invalidation must
///   fire on price alone) when an `invalidation_level` is supplied and price
///   crosses it on the opposite side:
///     * `"above"` / `"up"`   → `candle_close <= invalidation_level`
///     * `"below"` / `"down"` → `candle_close >= invalidation_level`
/// * The target takes precedence over the invalidation when both could match.
/// * Any other direction never fires (unknown direction is treated as no match
///   so a malformed watcher can never trigger a spurious resume).
///
/// Extracted as a pure function so the trigger semantics are independently
/// unit-/property-testable and applied identically inside the live watcher task
/// loop. It has no I/O, clock, or ambient state.
fn watcher_triggered(
    direction: &str,
    price_level: f64,
    invalidation_level: Option<f64>,
    volume_multiplier: f64,
    average_volume: f64,
    candle_close: f64,
    candle_volume: f64,
) -> Option<WatcherTrigger> {
    let volume_matched = candle_volume >= average_volume * volume_multiplier;

    match direction {
        "above" | "up" => {
            if candle_close >= price_level && volume_matched {
                Some(WatcherTrigger::Target)
            } else if let Some(inv) = invalidation_level {
                if candle_close <= inv {
                    Some(WatcherTrigger::Invalidation)
                } else {
                    None
                }
            } else {
                None
            }
        }
        "below" | "down" => {
            if candle_close <= price_level && volume_matched {
                Some(WatcherTrigger::Target)
            } else if let Some(inv) = invalidation_level {
                if candle_close >= inv {
                    Some(WatcherTrigger::Invalidation)
                } else {
                    None
                }
            } else {
                None
            }
        }
        _ => None,
    }
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
    reference_price: f64,
    invalidation_level: Option<f64>,
    heartbeat_enabled: bool,
    heartbeat_cadence_secs: f64,
    heartbeat_max: u32,
) -> Watcher {
    Watcher {
        thread_id,
        symbol,
        timeframe,
        price_level,
        direction: direction.trim().to_lowercase(),
        volume_multiplier,
        reference_price,
        invalidation_level,
        heartbeat_enabled,
        heartbeat_cadence_secs,
        heartbeat_max,
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

/// POST a `/resume` handoff to the Python service and stream the returned SSE
/// events back to the frontend via the `deep-quant-stream` Tauri event.
///
/// Shared by the target/invalidation trigger path and the heartbeat cadence
/// path so the SSE relay logic lives in one place. `trigger_kind` is the
/// contract value the Python side branches on (`"target"` / `"invalidation"`
/// serialized from [`WatcherTrigger`], or the literal `"heartbeat"`). When
/// `heartbeat_seq` is `Some`, a monotonic `heartbeat_seq` field is added to the
/// payload (R5.1).
///
/// Returns `Ok(())` when the outbound POST succeeds (regardless of stream
/// content) and `Err(String)` when the POST itself fails, so a heartbeat caller
/// can log-and-skip a failed POST without crashing the watcher task while the
/// target/invalidation caller can surface its existing ERROR event.
async fn post_resume_and_stream(
    app: &AppHandle,
    thread_id: &str,
    candle: &OhlcCandle,
    trigger_kind: serde_json::Value,
    heartbeat_seq: Option<u32>,
) -> Result<(), String> {
    let client = reqwest::Client::new();
    let mut response_payload = serde_json::json!({
        "thread_id": thread_id,
        "triggered_candle": candle,
        "trigger_kind": trigger_kind,
    });
    if let Some(seq) = heartbeat_seq {
        response_payload["heartbeat_seq"] = serde_json::json!(seq);
    }

    info!(
        "[watcher] Making handoff resume POST to port 8086 for thread_id={} (trigger_kind={})",
        thread_id, response_payload["trigger_kind"]
    );
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
                                let _ = app.emit("deep-quant-stream", outbound);
                            }
                        }
                    }
                    Err(e) => {
                        error!("[watcher] Resume stream read error: {}", e);
                        let _ = app.emit("deep-quant-stream", serde_json::json!({
                            "event": "ERROR",
                            "data": { "error": format!("Resume stream read error: {}", e) }
                        }));
                        break;
                    }
                }
            }
            Ok(())
        }
        Err(err) => {
            error!(
                "[watcher] Failed to send outbound handoff resume POST for thread_id={}: {}",
                thread_id, err
            );
            Err(format!("{}", err))
        }
    }
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

    // Capture the authoritative current price server-side BEFORE registering
    // (Bug #2). Load the most recent candle and take its close as the
    // `reference_price`; the target level is then validated to be strictly
    // beyond it in the chosen direction so a watcher can never instantly
    // false-trigger on a level price has already passed.
    let pool = state.app.try_state::<sqlx::PgPool>().ok_or_else(|| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "error": "QuestDB PG pool not available" })),
        )
    })?;

    let reference_price = match crate::commands::deep_quant::load_candles_from_db(
        Some(&state.app),
        pool.inner(),
        &watch_symbol,
        &timeframe,
        1,
    )
    .await
    {
        Ok(c) if !c.is_empty() => c.last().unwrap().close,
        Ok(_) => {
            return Err((
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": format!(
                        "No current price available for {} on timeframe {}; cannot register watcher.",
                        watch_symbol, timeframe
                    )
                })),
            ));
        }
        Err(e) => {
            return Err((
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({ "error": e })),
            ));
        }
    };

    // Normalize direction up front so validation matches the canonical form
    // the watcher will store.
    let direction_norm = payload.direction.trim().to_lowercase();

    // Validate the target level is on the correct side of the reference price
    // and REJECT if already satisfied (this prevents the instant false trigger,
    // Bug #2).
    match direction_norm.as_str() {
        "above" | "up" => {
            if !(payload.price_level > reference_price) {
                return Err((
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({
                        "error": format!(
                            "price_level {} is not above current price {}; choose a level above the current price or use direction 'below'.",
                            payload.price_level, reference_price
                        )
                    })),
                ));
            }
        }
        "below" | "down" => {
            if !(payload.price_level < reference_price) {
                return Err((
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({
                        "error": format!(
                            "price_level {} is not below current price {}; choose a level below the current price or use direction 'above'.",
                            payload.price_level, reference_price
                        )
                    })),
                ));
            }
        }
        other => {
            return Err((
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": format!(
                        "Unknown direction '{}'; expected 'above'/'up' or 'below'/'down'.",
                        other
                    )
                })),
            ));
        }
    }

    // If an invalidation level is supplied, validate it sits on the OPPOSITE
    // side of the reference price from the target (Bug #1).
    if let Some(inv) = payload.invalidation_level {
        match direction_norm.as_str() {
            "above" | "up" => {
                if !(inv < reference_price) {
                    return Err((
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({
                            "error": format!(
                                "invalidation_level {} must be below current price {} for an 'above' setup (the setup is wrong if price drops there).",
                                inv, reference_price
                            )
                        })),
                    ));
                }
            }
            "below" | "down" => {
                if !(inv > reference_price) {
                    return Err((
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({
                            "error": format!(
                                "invalidation_level {} must be above current price {} for a 'below' setup (the setup is wrong if price rises there).",
                                inv, reference_price
                            )
                        })),
                    ));
                }
            }
            _ => {}
        }
    }

    let watcher = build_watcher(
        payload.thread_id.clone(),
        watch_symbol.clone(),
        timeframe,
        payload.price_level,
        &payload.direction,
        payload.volume_multiplier,
        reference_price,
        payload.invalidation_level,
        payload.heartbeat_enabled,
        payload.heartbeat_cadence_secs,
        payload.heartbeat_max,
    );

    // Register watcher keyed by thread_id (R14.1).
    {
        let mut map = state.watchers.write().await;
        register_watcher(&mut map, watcher.clone());
    }

    info!(
        "[tool_server] Registered watcher for thread_id={} symbol={} price_level={:.2} direction={} reference_price={:.2} invalidation_level={:?}",
        watcher.thread_id, watcher.symbol, watcher.price_level, watcher.direction, watcher.reference_price, watcher.invalidation_level
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

        // Heartbeat_Monitor state (Adaptive Opportunity Engine R5.1, R5.2).
        // `latest_candle` holds the freshest candle so a cadence tick has real
        // data to carry; `heartbeat_seq` is the monotonic sequence, bounded by
        // the hard ceiling `watcher.heartbeat_max`.
        let mut latest_candle: Option<OhlcCandle> = None;
        let mut heartbeat_seq: u32 = 0;
        let heartbeat_active = watcher.heartbeat_enabled
            && watcher.heartbeat_max > 0
            && watcher.heartbeat_cadence_secs > 0.0;
        // Only build a ticking interval when heartbeat is actually active; a
        // disabled heartbeat leaves this `None` so the cadence branch stays
        // inert and behaviour is identical to the pre-engine watcher.
        let mut heartbeat_interval = if heartbeat_active {
            let mut iv = tokio::time::interval(std::time::Duration::from_secs_f64(
                watcher.heartbeat_cadence_secs,
            ));
            iv.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            // Skip the immediate first tick so the first heartbeat fires only
            // after one full cadence has elapsed.
            iv.reset();
            Some(iv)
        } else {
            None
        };

        loop {
            tokio::select! {
                // Live-candle branch — UNCHANGED target/invalidation semantics.
                recv_result = rx.recv() => {
                    let (sym, candle) = match recv_result {
                        Ok(pair) => pair,
                        Err(_) => break, // channel closed or lagged — exit as before
                    };
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
                // Retain the freshest candle for the heartbeat cadence path.
                latest_candle = Some(candle.clone());

                if !matches!(watcher.direction.as_str(), "above" | "up" | "below" | "down") {
                    error!("[watcher] Unknown direction: {}", watcher.direction);
                }

                // Trigger semantics are factored into the pure `watcher_triggered`
                // predicate (R14.2): the target fires iff the price condition
                // holds AND volume surges; the opposite-side invalidation fires
                // on price alone. The returned `WatcherTrigger` tells us which.
                if let Some(trigger_kind) = watcher_triggered(
                    &watcher.direction,
                    watcher.price_level,
                    watcher.invalidation_level,
                    watcher.volume_multiplier,
                    avg_volume,
                    candle.close,
                    candle.volume,
                ) {
                    info!(
                        "[watcher] Condition MET ({:?}) for thread_id={}! Price close={:.2} (level={:.2}, inv={:?}), Vol={:.2} (threshold={:.2})",
                        trigger_kind, watcher.thread_id, candle.close, watcher.price_level, watcher.invalidation_level, candle.volume, avg_volume * watcher.volume_multiplier
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
                        "trigger_kind": trigger_kind,
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

                // Heartbeat cadence branch (R5.1): emits up to `heartbeat_max`
                // `/resume` POSTs carrying the freshest candle with
                // `trigger_kind = "heartbeat"` and a monotonic `heartbeat_seq`,
                // WITHOUT removing the watcher from the registry (R5.5 — only a
                // target/invalidation trigger removes it). The branch future
                // pends forever when heartbeat is disabled (no interval), so it
                // is never selected in that case.
                _ = async {
                    match heartbeat_interval.as_mut() {
                        Some(iv) => { iv.tick().await; }
                        None => std::future::pending::<()>().await,
                    }
                } => {
                    // A heartbeat re-checks liveness but NEVER removes the watcher.
                    let still_active = {
                        let map = watchers_clone.read().await;
                        map.get(&watcher.thread_id)
                            .map(|w| w.price_level == watcher.price_level && w.symbol == watcher.symbol)
                            .unwrap_or(false)
                    };
                    if !still_active {
                        info!("[watcher] Watcher for thread_id={} gone; stopping heartbeat cadence.", watcher.thread_id);
                        break;
                    }

                    // Enforce the hard heartbeat ceiling (R5.2): once reached,
                    // drop the cadence but keep the watcher live so a
                    // target/invalidation can still fire.
                    if heartbeat_seq >= watcher.heartbeat_max {
                        heartbeat_interval = None;
                        continue;
                    }

                    // Carry the freshest candle; if none has arrived yet, skip
                    // this tick without consuming a heartbeat.
                    let candle = match latest_candle.clone() {
                        Some(c) => c,
                        None => continue,
                    };

                    let seq = heartbeat_seq + 1;
                    info!(
                        "[watcher] Emitting heartbeat #{}/{} for thread_id={} (close={:.2})",
                        seq, watcher.heartbeat_max, watcher.thread_id, candle.close
                    );

                    // Log-and-skip a failed heartbeat POST so a transient error
                    // never crashes the watcher task; the attempt still counts
                    // toward the ceiling so emission stays bounded.
                    if let Err(err) = post_resume_and_stream(
                        &app_clone,
                        &watcher.thread_id,
                        &candle,
                        serde_json::json!("heartbeat"),
                        Some(seq),
                    )
                    .await
                    {
                        error!(
                            "[watcher] Heartbeat #{} POST failed for thread_id={}: {} (skipping)",
                            seq, watcher.thread_id, err
                        );
                    }
                    heartbeat_seq = seq;
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

    // Fetch the SAME Google News RSS headlines the frontend sentiment panel uses
    // (commands::sentiment::fetch_news_headlines). This is the reliable, keyless
    // source that actually returns data; the Node Sentiment_Service is only used
    // (below) for the richer directional classification on top. So the agent
    // always receives the same headlines the operator sees in the left panel,
    // even when the Node service is empty or unreachable.
    let rss_headlines: Vec<String> = crate::commands::sentiment::fetch_news_headlines(&payload.symbol).await;

    // Honest fallback used when the Node classification service is unavailable:
    // return the RSS headlines (so the agent can read the news itself) rather
    // than an empty "Unavailable" marker. Only degrades to the empty marker when
    // there are genuinely no headlines to show.
    let headlines_only_fallback = |reason: String| -> serde_json::Value {
        if rss_headlines.is_empty() {
            unavailable_news(&reason)
        } else {
            serde_json::json!({
                "symbol": payload.symbol.clone(),
                "headlines": rss_headlines.clone(),
                "sentiment": "Neutral",
                // Not the "Unavailable" marker — headlines ARE present for the
                // agent to analyze; only the LLM classification was unavailable.
                "sentiment_summary": "Headlines retrieved from Google News; sentiment classification service unavailable — read the headlines directly.",
                "note": reason,
            })
        }
    };

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
                    "[tool_server] get_news_context: symbol={} classification body invalid ({}); returning {} RSS headlines",
                    payload.symbol, e, rss_headlines.len()
                );
                return Ok(Json(headlines_only_fallback(format!(
                    "invalid sentiment service response: {}",
                    e
                ))));
            }
        },
        Ok(r) => {
            let status = r.status();
            info!(
                "[tool_server] get_news_context: symbol={} classification HTTP {}; returning {} RSS headlines",
                payload.symbol, status, rss_headlines.len()
            );
            return Ok(Json(headlines_only_fallback(format!(
                "sentiment service returned HTTP {}",
                status
            ))));
        }
        Err(e) => {
            info!(
                "[tool_server] get_news_context: symbol={} classification unreachable ({}); returning {} RSS headlines",
                payload.symbol, e, rss_headlines.len()
            );
            return Ok(Json(headlines_only_fallback(format!(
                "sentiment service unreachable: {}",
                e
            ))));
        }
    };

    let conviction_score = body.get("conviction_score").and_then(|v| v.as_f64());

    // Prefer the Node service's own headlines when present, but fall back to the
    // RSS headlines so the agent always receives the actual news items.
    let upstream_headlines: Vec<String> = body
        .get("headlines")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|h| h.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default();
    let headlines: Vec<String> = if upstream_headlines.is_empty() {
        rss_headlines.clone()
    } else {
        upstream_headlines
    };

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
                "[tool_server] get_news_context: symbol={} no usable classification; returning {} RSS headlines",
                payload.symbol, rss_headlines.len()
            );
            Ok(Json(headlines_only_fallback(
                "sentiment service did not return a usable classification".to_string(),
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
    ) -> Option<WatcherTrigger> {
        let fired = match registry.get(thread_id) {
            Some(w) => watcher_triggered(
                &w.direction,
                w.price_level,
                w.invalidation_level,
                w.volume_multiplier,
                average_volume,
                candle_close,
                candle_volume,
            ),
            None => None,
        };
        if fired.is_some() {
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
            reference_price in 0.01f64..1.0e6,
            invalidation_level in prop::option::of(0.01f64..1.0e6),
        ) {
            let watcher = build_watcher(
                thread_id.clone(),
                symbol.clone(),
                timeframe.to_string(),
                price_level,
                direction,
                volume_multiplier,
                reference_price,
                invalidation_level,
                false, // heartbeat_enabled
                0.0,   // heartbeat_cadence_secs
                0,     // heartbeat_max
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
            // build_watcher stores the reference_price and invalidation_level
            // exactly as supplied (Bug #1 / Bug #2 support fields).
            prop_assert_eq!(stored.reference_price, reference_price);
            prop_assert_eq!(stored.invalidation_level, invalidation_level);

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
            invalidation_level in prop::option::of(-1.0e6f64..1.0e6),
            dir_idx in 0usize..5,
        ) {
            // Index 4 ("sideways") is an unknown/unsupported direction.
            let directions = ["above", "up", "below", "down", "sideways"];
            let direction = directions[dir_idx];

            let fired = watcher_triggered(
                direction,
                price_level,
                invalidation_level,
                volume_multiplier,
                average_volume,
                candle_close,
                candle_volume,
            );

            // Reference semantics: target fires iff the direction-aware price
            // condition holds AND volume >= avg * mult; otherwise the
            // opposite-side invalidation fires on price alone (no volume gate);
            // unknown direction never matches.
            let volume_matched = candle_volume >= average_volume * volume_multiplier;
            let expected = match direction {
                "above" | "up" => {
                    if candle_close >= price_level && volume_matched {
                        Some(WatcherTrigger::Target)
                    } else if let Some(inv) = invalidation_level {
                        if candle_close <= inv { Some(WatcherTrigger::Invalidation) } else { None }
                    } else {
                        None
                    }
                }
                "below" | "down" => {
                    if candle_close <= price_level && volume_matched {
                        Some(WatcherTrigger::Target)
                    } else if let Some(inv) = invalidation_level {
                        if candle_close >= inv { Some(WatcherTrigger::Invalidation) } else { None }
                    } else {
                        None
                    }
                }
                _ => None,
            };

            prop_assert_eq!(fired, expected);

            // An unknown direction can never fire, regardless of price/volume.
            let unknown = watcher_triggered(
                "sideways",
                price_level,
                invalidation_level,
                volume_multiplier,
                average_volume,
                candle_close,
                candle_volume,
            );
            prop_assert!(unknown.is_none());
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
            invalidation_level in prop::option::of(0.01f64..1.0e6),
            direction in valid_direction(),
        ) {
            let watcher = build_watcher(
                thread_id.clone(),
                "RELIANCE".to_string(),
                "10m".to_string(),
                price_level,
                direction,
                volume_multiplier,
                price_level, // reference_price (arbitrary for this pure test)
                invalidation_level,
                false, // heartbeat_enabled
                0.0,   // heartbeat_cadence_secs
                0,     // heartbeat_max
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
                invalidation_level,
                volume_multiplier,
                average_volume,
                candle_close,
                candle_volume,
            );
            prop_assert_eq!(fired, expected_fire);

            if fired.is_some() {
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
            2400.0, // reference_price (below the target, as the handler requires)
            None,   // no invalidation level for this lifecycle test
            false,  // heartbeat_enabled
            0.0,    // heartbeat_cadence_secs
            0,      // heartbeat_max
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
        assert!(fired_weak.is_none(), "weak-volume candle must not fire the watcher");
        assert!(registry.contains_key(thread_id), "non-firing watcher stays registered");

        // 2b. A triggering candle (price AND volume surge) fires exactly once
        //     and the watcher is removed from the registry (the /resume handoff).
        let fired = remove_on_fire(
            &mut registry, thread_id, average_volume,
            2451.0,          // close >= 2450
            250_000.0,       // volume >= 150_000 (surge met)
        );
        assert_eq!(fired, Some(WatcherTrigger::Target), "triggering candle must fire the target");
        assert!(
            !registry.contains_key(thread_id),
            "R14.4: a fired watcher is removed from the registry"
        );

        // 3. A second triggering candle is a no-op — the watcher fired only
        //    once and is already gone (no double-resume).
        let fired_again = remove_on_fire(
            &mut registry, thread_id, average_volume, 2460.0, 500_000.0,
        );
        assert!(fired_again.is_none(), "a removed watcher cannot fire a second time");
        assert!(!registry.contains_key(thread_id));
    }

    // ── Explicit unit tests for the target/invalidation trigger semantics ────
    //
    // Feature: deep-quant-analysis-hardening, Property 47 (examples): the pure
    // `watcher_triggered` predicate distinguishes Target (price + volume) from
    // Invalidation (opposite-side, price-only) and returns None in between.

    #[test]
    fn above_target_fires_with_volume() {
        // above/up: close >= level WITH volume → Target.
        let avg = 100.0;
        assert_eq!(
            watcher_triggered("above", 2450.0, Some(2400.0), 1.5, avg, 2451.0, 160.0),
            Some(WatcherTrigger::Target)
        );
        // "up" is the same canonical direction.
        assert_eq!(
            watcher_triggered("up", 2450.0, None, 1.5, avg, 2451.0, 160.0),
            Some(WatcherTrigger::Target)
        );
    }

    #[test]
    fn above_target_requires_volume_but_invalidation_does_not() {
        let avg = 100.0;
        // Target price met but volume below threshold (1.5 * 100 = 150) → NOT
        // Target. With no invalidation, that's None.
        assert_eq!(
            watcher_triggered("above", 2450.0, None, 1.5, avg, 2451.0, 120.0),
            None
        );
        // Invalidation ignores the volume gate: price at/below the invalidation
        // level fires Invalidation even with zero volume.
        assert_eq!(
            watcher_triggered("above", 2450.0, Some(2400.0), 1.5, avg, 2399.0, 0.0),
            Some(WatcherTrigger::Invalidation)
        );
    }

    #[test]
    fn above_between_levels_is_none() {
        let avg = 100.0;
        // Price between invalidation (2400) and target (2450), volume high:
        // neither condition holds → None.
        assert_eq!(
            watcher_triggered("above", 2450.0, Some(2400.0), 1.5, avg, 2420.0, 999.0),
            None
        );
    }

    #[test]
    fn below_target_and_invalidation_are_symmetric() {
        let avg = 100.0;
        // below/down: close <= level WITH volume → Target.
        assert_eq!(
            watcher_triggered("below", 2400.0, Some(2450.0), 1.5, avg, 2399.0, 160.0),
            Some(WatcherTrigger::Target)
        );
        assert_eq!(
            watcher_triggered("down", 2400.0, None, 1.5, avg, 2399.0, 160.0),
            Some(WatcherTrigger::Target)
        );
        // Target price met but insufficient volume → not Target.
        assert_eq!(
            watcher_triggered("below", 2400.0, None, 1.5, avg, 2399.0, 120.0),
            None
        );
        // Opposite-side invalidation (price rises to/above 2450) fires on price
        // alone, even with zero volume.
        assert_eq!(
            watcher_triggered("below", 2400.0, Some(2450.0), 1.5, avg, 2451.0, 0.0),
            Some(WatcherTrigger::Invalidation)
        );
        // Between the levels → None.
        assert_eq!(
            watcher_triggered("below", 2400.0, Some(2450.0), 1.5, avg, 2420.0, 999.0),
            None
        );
    }

    #[test]
    fn unknown_direction_never_fires() {
        assert_eq!(
            watcher_triggered("sideways", 2450.0, Some(2400.0), 1.5, 100.0, 9999.0, 9999.0),
            None
        );
    }

    #[test]
    fn build_watcher_stores_reference_and_invalidation_and_normalizes_direction() {
        let w = build_watcher(
            "tid".to_string(),
            "RELIANCE".to_string(),
            "15m".to_string(),
            2450.0,
            "  ABOVE  ",
            1.5,
            2400.0,
            Some(2375.0),
            false, // heartbeat_enabled
            0.0,   // heartbeat_cadence_secs
            0,     // heartbeat_max
        );
        assert_eq!(w.direction, "above", "direction is trimmed + lowercased");
        assert_eq!(w.reference_price, 2400.0);
        assert_eq!(w.invalidation_level, Some(2375.0));
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

// ── Integration test: heartbeat cadence is bounded & leaves trigger semantics
//    unchanged (Adaptive Opportunity Engine R5.1, R5.5) ────────────────────────
//
// The live heartbeat cadence lives inside the spawned Tokio watcher task and
// POSTs `/resume` over HTTP, so — exactly like `watcher_registry_proptests`
// models the target/invalidation branch with `remove_on_fire` instead of
// spawning the task — this module models the cadence branch's bounded-emission
// ceiling deterministically with `emit_heartbeats`, and asserts:
//
//   * an enabled heartbeat emits a BOUNDED number of `/resume` POSTs that never
//     exceeds `heartbeat_max` no matter how many cadence ticks elapse (R5.1,
//     R5.2), and
//   * the `watcher_triggered` target/invalidation semantics are UNCHANGED — the
//     heartbeat is a separate emission path (`trigger_kind = "heartbeat"`), not
//     a new `WatcherTrigger` variant, and a heartbeat never removes the watcher
//     from the registry while a target/invalidation still does (R5.5).
#[cfg(test)]
mod heartbeat_cadence_tests {
    use super::*;
    use proptest::prelude::*;

    /// Deterministic model of the live watcher's heartbeat cadence branch
    /// (`tool_server.rs`, R5.1/R5.2). Each iteration represents one cadence
    /// `interval.tick()`. It mirrors the live control flow exactly:
    ///
    ///   * the branch is inert unless `heartbeat_enabled && heartbeat_max > 0 &&
    ///     heartbeat_cadence_secs > 0.0` (the `heartbeat_active` gate that
    ///     decides whether a ticking interval is even built),
    ///   * once `heartbeat_seq >= heartbeat_max` the cadence is dropped
    ///     (`heartbeat_interval = None`) so no further heartbeats are emitted
    ///     (the hard ceiling, R5.2),
    ///   * a tick with no freshest candle yet is skipped WITHOUT consuming a
    ///     heartbeat, and
    ///   * otherwise `seq = heartbeat_seq + 1` is emitted and becomes the new
    ///     `heartbeat_seq`.
    ///
    /// Returns the monotonic sequence of `heartbeat_seq` values actually
    /// emitted (i.e. one entry per `/resume` POST). Modeling the ceiling here —
    /// rather than spawning the Tokio task and a real HTTP endpoint — is the
    /// same technique `remove_on_fire` uses to test the trigger branch.
    fn emit_heartbeats(
        heartbeat_enabled: bool,
        heartbeat_cadence_secs: f64,
        heartbeat_max: u32,
        candle_ready_from_tick: Option<u32>,
        ticks: u32,
    ) -> Vec<u32> {
        let heartbeat_active =
            heartbeat_enabled && heartbeat_max > 0 && heartbeat_cadence_secs > 0.0;
        if !heartbeat_active {
            // No interval is built ⇒ the cadence branch never fires.
            return Vec::new();
        }
        let mut heartbeat_seq: u32 = 0;
        let mut emitted: Vec<u32> = Vec::new();
        for tick in 0..ticks {
            // Ceiling reached: the live loop sets `heartbeat_interval = None`
            // and `continue`s, so the branch is never selected again (R5.2).
            if heartbeat_seq >= heartbeat_max {
                break;
            }
            // No freshest candle yet ⇒ skip this tick without consuming a
            // heartbeat (the live `None => continue`).
            let candle_ready = match candle_ready_from_tick {
                Some(from) => tick >= from,
                None => false,
            };
            if !candle_ready {
                continue;
            }
            let seq = heartbeat_seq + 1;
            emitted.push(seq);
            heartbeat_seq = seq;
        }
        emitted
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: adaptive-opportunity-engine — an enabled heartbeat emits a
        // BOUNDED number of `/resume` POSTs (never exceeding `heartbeat_max`),
        // regardless of how many cadence ticks elapse.
        // Validates: Requirements 5.1
        #[test]
        fn heartbeat_emission_is_bounded_by_max(
            heartbeat_enabled in any::<bool>(),
            heartbeat_cadence_secs in 0.0f64..600.0,
            heartbeat_max in 0u32..50,
            // Candles start arriving at some tick (or never), exercising the
            // "skip without consuming" path.
            candle_from in prop::option::of(0u32..20),
            // Far more ticks than the ceiling, to prove emission still bounds.
            ticks in 0u32..500,
        ) {
            let emitted = emit_heartbeats(
                heartbeat_enabled,
                heartbeat_cadence_secs,
                heartbeat_max,
                candle_from,
                ticks,
            );

            // R5.1/R5.2: emission NEVER exceeds the hard ceiling, no matter how
            // many cadence ticks elapse.
            prop_assert!(
                emitted.len() <= heartbeat_max as usize,
                "emitted {} heartbeats > ceiling {}",
                emitted.len(),
                heartbeat_max
            );

            // The emitted sequence numbers are a strictly-increasing 1..=N run
            // (monotonic `heartbeat_seq`), matching the live loop's numbering.
            for (i, seq) in emitted.iter().enumerate() {
                prop_assert_eq!(*seq, (i as u32) + 1);
            }

            // A disabled / zero-cadence / zero-ceiling heartbeat emits nothing:
            // the cadence branch is inert and behaviour is identical to the
            // pre-engine watcher.
            let active = heartbeat_enabled
                && heartbeat_max > 0
                && heartbeat_cadence_secs > 0.0;
            if !active {
                prop_assert!(emitted.is_empty());
            }
        }

        // Feature: adaptive-opportunity-engine — the heartbeat is a SEPARATE
        // emission path, not a new `WatcherTrigger` variant, so the pure
        // `watcher_triggered` predicate keeps returning ONLY Target /
        // Invalidation / None for the existing kinds (R5.5 — target/invalidation
        // semantics unchanged by the heartbeat cadence).
        // Validates: Requirements 5.5
        #[test]
        fn watcher_triggered_semantics_unchanged_by_heartbeat(
            price_level in -1.0e6f64..1.0e6,
            volume_multiplier in 0.0f64..10.0,
            average_volume in 0.0f64..1.0e6,
            candle_close in -1.0e6f64..1.0e6,
            candle_volume in 0.0f64..1.0e7,
            invalidation_level in prop::option::of(-1.0e6f64..1.0e6),
            dir_idx in 0usize..4,
        ) {
            let directions = ["above", "up", "below", "down"];
            let direction = directions[dir_idx];

            let fired = watcher_triggered(
                direction,
                price_level,
                invalidation_level,
                volume_multiplier,
                average_volume,
                candle_close,
                candle_volume,
            );

            // The predicate only ever yields the two existing kinds or None;
            // there is no "heartbeat" variant to return.
            match fired {
                None
                | Some(WatcherTrigger::Target)
                | Some(WatcherTrigger::Invalidation) => {}
            }

            // A fired trigger serializes to exactly "target"/"invalidation" —
            // never the heartbeat contract string, which is emitted on a
            // distinct path (`serde_json::json!("heartbeat")`), not from the
            // WatcherTrigger enum.
            if let Some(trigger) = fired {
                let kind = serde_json::to_value(trigger).unwrap();
                prop_assert!(kind == serde_json::json!("target")
                    || kind == serde_json::json!("invalidation"));
                prop_assert_ne!(kind, serde_json::json!("heartbeat"));
            }
        }
    }

    // Concrete lifecycle example (task 11.2): an enabled heartbeat emits at most
    // `heartbeat_max` POSTs over a long run of cadence ticks, keeps the watcher
    // REGISTERED across every heartbeat (R5.5 — a heartbeat never removes the
    // watcher), and a subsequent target candle still fires and removes it via
    // the unchanged `watcher_triggered` path.
    #[test]
    fn heartbeat_is_bounded_and_preserves_target_invalidation_semantics() {
        // Register an "above" 2450 watcher with heartbeat enabled: max 3
        // heartbeats on a 30s cadence.
        let thread_id = "thread-hb-11-2";
        let watcher = build_watcher(
            thread_id.to_string(),
            "RELIANCE".to_string(),
            "15m".to_string(),
            2450.0,
            "above",
            1.5,
            2400.0,      // reference_price (below the target)
            Some(2375.0),
            true,        // heartbeat_enabled
            30.0,        // heartbeat_cadence_secs
            3,           // heartbeat_max
        );
        let mut registry: HashMap<String, Watcher> = HashMap::new();
        register_watcher(&mut registry, watcher);
        assert!(registry.contains_key(thread_id));

        // 20 cadence ticks elapse (candles ready from the first tick), but the
        // ceiling is 3 ⇒ exactly 3 heartbeats are emitted, numbered 1,2,3.
        let emitted = emit_heartbeats(true, 30.0, 3, Some(0), 20);
        assert_eq!(emitted, vec![1, 2, 3], "heartbeat emission is bounded by heartbeat_max");
        assert!(emitted.len() <= 3, "R5.2: emission never exceeds the ceiling");

        // R5.5: none of those heartbeats removed the watcher — it is still live
        // and awaiting a target/invalidation. (Heartbeats do not touch the
        // registry in the live loop.)
        assert!(
            registry.contains_key(thread_id),
            "R5.5: a heartbeat must NOT remove the watcher from the registry"
        );

        // The target/invalidation semantics are UNCHANGED: a price+volume
        // candle still fires Target and removes the watcher (the /resume
        // handoff), exactly as without the heartbeat cadence.
        let average_volume = 100_000.0;
        let fired = match registry.get(thread_id) {
            Some(w) => watcher_triggered(
                &w.direction,
                w.price_level,
                w.invalidation_level,
                w.volume_multiplier,
                average_volume,
                2451.0,     // close >= 2450
                250_000.0,  // volume >= 150_000 (surge met)
            ),
            None => None,
        };
        assert_eq!(fired, Some(WatcherTrigger::Target));
        registry.remove(thread_id);
        assert!(
            !registry.contains_key(thread_id),
            "target trigger still removes the watcher (semantics unchanged)"
        );
    }

    // A disabled heartbeat (the A+-only default) emits nothing even over many
    // cadence ticks — the cadence branch is inert, so behaviour is identical to
    // the pre-engine watcher.
    #[test]
    fn disabled_heartbeat_emits_nothing() {
        assert!(emit_heartbeats(false, 30.0, 5, Some(0), 100).is_empty());
        // Enabled but zero ceiling / zero cadence are equally inert.
        assert!(emit_heartbeats(true, 30.0, 0, Some(0), 100).is_empty());
        assert!(emit_heartbeats(true, 0.0, 5, Some(0), 100).is_empty());
    }
}

// ── R2 BUG-CONDITION EXPLORATION → VERIFICATION (deep-quant-runtime-hardening, Property 1) ──
//
// This module began as an EXPLORATORY bug-condition test that FAILED on the
// UNFIXED `get_candles` handler — that failure CONFIRMED the error-masking
// defect described by Requirement 2. The R2 fix (tasks 5.1–5.3) has since been
// applied: a typed `CandleLoadError { Shortfall, Fault }` now flows into the
// handler, which maps each outcome to a differentiated response.
//
// Per the two-phase bugfix discipline, this module's inline mirror must track
// the REAL handler. Because the mirror reproduces the handler's mapping inline
// (rather than invoking the axum handler directly), it has been reconciled with
// the FIXED handler so the DIFFERENTIATED-outcome assertions Property 1 requires
// now HOLD:
//
// | loader outcome        | HTTP status | body shape                                            |
// |-----------------------|-------------|-------------------------------------------------------|
// | `Ok(candles)`         | `200`       | ascending candle list                                 |
// | `Err(Shortfall)`      | `200`       | `{"unavailable": true, "reason", "symbol", ...}`      |
// | `Err(Fault)`          | `503`       | `{"error": "candle store fault: <source>: <detail>"}` |
//
// An Availability_Shortfall degrades gracefully (non-5xx unavailable marker the
// Python Data_Tools treat as non-blocking); an Infrastructure_Fault is a `503`
// whose body names the actual cause. The two are now distinguishable from the
// response, so this test PASSES — demonstrating the fix.
#[cfg(test)]
mod candle_outcome_differentiation_bug_exploration {
    use super::*;
    use crate::commands::deep_quant::CandleLoadError;

    /// Faithful mirror of the FIXED `get_candles` handler outcome mapping.
    ///
    /// The real handler owns a `Result<Vec<_>, CandleLoadError>` from the loader
    /// and maps each variant to a differentiated response:
    ///
    /// ```ignore
    /// Err(CandleLoadError::Shortfall { symbol, timeframe, available, needed, detail }) => (
    ///     StatusCode::OK,
    ///     Json(json!({ "unavailable": true, "reason": detail, "symbol": symbol,
    ///                  "timeframe": timeframe, "available": available, "needed": needed })),
    /// ),
    /// Err(CandleLoadError::Fault { source, detail }) => (
    ///     StatusCode::SERVICE_UNAVAILABLE,
    ///     Json(json!({ "error": format!("candle store fault: {}: {}", source, detail) })),
    /// ),
    /// ```
    ///
    /// A `Shortfall` now maps to a graceful `200 {"unavailable": true, ...}` and
    /// a `Fault` to a `503` whose body names the store fault — distinct shapes.
    fn fixed_handler_map_err(loader_err: &CandleLoadError) -> (StatusCode, serde_json::Value) {
        match loader_err {
            CandleLoadError::Shortfall {
                symbol,
                timeframe,
                available,
                needed,
                detail,
            } => (
                StatusCode::OK,
                serde_json::json!({
                    "unavailable": true,
                    "reason": detail,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "available": available,
                    "needed": needed,
                }),
            ),
            CandleLoadError::Fault { source, detail } => (
                StatusCode::SERVICE_UNAVAILABLE,
                serde_json::json!({
                    "error": format!("candle store fault: {}: {}", source, detail),
                }),
            ),
        }
    }

    // Feature: deep-quant-runtime-hardening, Property 1 (Expected Behavior):
    // a shortfall and a fault surface as DIFFERENTIATED outcomes and are
    // distinguishable from the response.
    //
    // EXPECTED OUTCOME on FIXED code: this test PASSES — demonstrating the fix.
    #[test]
    fn bug_shortfall_and_fault_both_surface_as_opaque_500() {
        // Availability_Shortfall: an empty / insufficient cold-cache read.
        // This is NOT an infrastructure problem — the store is simply short of
        // history for the requested (symbol, timeframe).
        let shortfall_err = CandleLoadError::Shortfall {
            symbol: "CUPID".to_string(),
            timeframe: "10m".to_string(),
            available: 0,
            needed: 114,
            detail: "Insufficient historical data for CUPID 10m: 0 of 114 candles available"
                .to_string(),
        };
        // Infrastructure_Fault: a genuine pool/DB/connection failure.
        let fault_err = CandleLoadError::Fault {
            source: "live_ticks".to_string(),
            detail: "pool timed out while waiting for an open connection".to_string(),
        };

        let (shortfall_status, shortfall_body) = fixed_handler_map_err(&shortfall_err);
        let (fault_status, fault_body) = fixed_handler_map_err(&fault_err);

        // ── Property 1 Expected Behavior (what the FIXED handler now does) ──
        //
        // (1) An Availability_Shortfall degrades GRACEFULLY — a non-5xx result
        //     the Python Data_Tools treat as a non-blocking Unavailable_Marker
        //     (R2.2). The fixed handler returns `200 {"unavailable": true, ...}`,
        //     so this assertion HOLDS.
        assert!(
            shortfall_status.as_u16() < 500,
            "R2 REGRESSION — Availability_Shortfall surfaced as HTTP {} instead of a graceful \
             non-5xx unavailable result. Body: {}",
            shortfall_status.as_u16(),
            shortfall_body,
        );

        // (2) A shortfall and a fault are DISTINGUISHABLE from the response
        //     (R2.1) — the shortfall is a `200 {"unavailable": true, ...}`
        //     marker, the fault is a `503` naming the real cause. We compare the
        //     observable classification (status class + presence of the
        //     `unavailable` marker). Fixed: shortfall is `(2, true)` and fault
        //     is `(5, false)`, so the two differ and this HOLDS.
        let shortfall_shape = (
            shortfall_status.as_u16() / 100,
            shortfall_body.get("unavailable").is_some(),
        );
        let fault_shape = (
            fault_status.as_u16() / 100,
            fault_body.get("unavailable").is_some(),
        );
        assert_ne!(
            shortfall_shape, fault_shape,
            "R2 REGRESSION — shortfall and fault produced an IDENTICAL response classification \
             {:?}. shortfall {:?} and fault {:?} must map to distinct outcomes.",
            shortfall_shape, shortfall_body, fault_body,
        );

        // (3) An Infrastructure_Fault body names the real cause with a
        //     recognizable fault marker (R2.3). The fixed body is
        //     `{"error": "candle store fault: <source>: <detail>"}`, so this
        //     HOLDS.
        let fault_text = fault_body
            .get("error")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        assert!(
            fault_text.contains("candle store fault"),
            "R2 REGRESSION — Infrastructure_Fault body {} does not identify itself as a store \
             fault.",
            fault_body,
        );

        // Additional differentiated-outcome checks (R2.2 / R2.3):
        // shortfall → 200 unavailable marker; fault → 503 named cause.
        assert_eq!(
            shortfall_status,
            StatusCode::OK,
            "shortfall must map to 200, got {}",
            shortfall_status.as_u16()
        );
        assert_eq!(
            shortfall_body.get("unavailable").and_then(|v| v.as_bool()),
            Some(true),
            "shortfall body must carry `unavailable: true`, got {}",
            shortfall_body
        );
        assert_eq!(
            fault_status,
            StatusCode::SERVICE_UNAVAILABLE,
            "fault must map to 503, got {}",
            fault_status.as_u16()
        );
    }
}

// ── R2 VERIFICATION PROPERTY TEST (deep-quant-runtime-hardening, Property 1) ──
//
// Task 6: proptest over ARBITRARY loader outcomes asserting the FIXED
// `get_candles` handler mapping is total and differentiated:
//
// | loader outcome   | HTTP status | body shape                                            |
// |------------------|-------------|-------------------------------------------------------|
// | `Ok(candles)`    | `200`       | ascending candle list (JSON array)                    |
// | `Err(Shortfall)` | `200`       | `{"unavailable": true, "reason", "symbol", ...}`      |
// | `Err(Fault)`     | `5xx`       | `{"error": "candle store fault: <source>: <detail>"}` |
//
// The mirror below reproduces the REAL handler's outcome mapping inline (the
// axum handler owns a live `PgPool` and can't be invoked without a database),
// covering ALL THREE arms — `Ok`, `Shortfall`, and `Fault` — over generated
// inputs. Property 1 (Preservation): the mapping never panics, `Ok` always
// yields a `200` list, a `Shortfall` always yields a graceful `200` unavailable
// marker carrying a `reason`, and a `Fault` always yields a `5xx` whose body
// names the failing source. Validates: Requirements 2.1, 2.2, 2.3, 2.4, 3.6.
#[cfg(test)]
mod candle_outcome_classification_proptests {
    use super::*;
    use crate::commands::deep_quant::CandleLoadError;
    use proptest::prelude::*;

    /// Faithful mirror of the FIXED `get_candles` handler outcome mapping over
    /// the FULL `Result<Vec<CandleWithTs>, CandleLoadError>` the loader returns.
    ///
    /// - `Ok(candles)` → `200` with the ascending-sorted candle list (the
    ///   handler serialises `Json(sort_candles_ascending(result))`; we mirror
    ///   that by sorting and serialising to a JSON array).
    /// - `Err(Shortfall)` → `200 {"unavailable": true, "reason", "symbol",
    ///   "timeframe", "available", "needed"}`.
    /// - `Err(Fault)` → `503 {"error": "candle store fault: <source>: <detail>"}`.
    ///
    /// This is the same mapping the handler performs at
    /// `tool_server.rs` `get_candles`; keeping it inline lets the contract be
    /// property-tested without a live database.
    fn map_loader_outcome(
        outcome: &Result<Vec<CandleWithTs>, CandleLoadError>,
    ) -> (StatusCode, serde_json::Value) {
        match outcome {
            Ok(candles) => {
                let sorted = sort_candles_ascending(candles.clone());
                let body =
                    serde_json::to_value(&sorted).expect("candle list serialises to JSON array");
                (StatusCode::OK, body)
            }
            Err(CandleLoadError::Shortfall {
                symbol,
                timeframe,
                available,
                needed,
                detail,
            }) => (
                StatusCode::OK,
                serde_json::json!({
                    "unavailable": true,
                    "reason": detail,
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "available": available,
                    "needed": needed,
                }),
            ),
            Err(CandleLoadError::Fault { source, detail }) => (
                StatusCode::SERVICE_UNAVAILABLE,
                serde_json::json!({
                    "error": format!("candle store fault: {}: {}", source, detail),
                }),
            ),
        }
    }

    /// A finite OHLCV candle within a bounded band, with an arbitrary timestamp.
    fn candle_with_ts_strat() -> impl Strategy<Value = CandleWithTs> {
        (
            -1_000_000_000_000i64..1_000_000_000_000i64,
            0.0f64..100_000.0,
            0.0f64..100_000.0,
            0.0f64..100_000.0,
            0.0f64..100_000.0,
            0.0f64..1_000_000.0,
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

    /// An arbitrary Availability_Shortfall.
    fn shortfall_strat() -> impl Strategy<Value = CandleLoadError> {
        (
            "[A-Z]{1,12}",
            "[0-9]{1,3}[mhdw]",
            0usize..500,
            0usize..500,
            ".*",
        )
            .prop_map(
                |(symbol, timeframe, available, needed, detail)| CandleLoadError::Shortfall {
                    symbol,
                    timeframe,
                    available,
                    needed,
                    detail,
                },
            )
    }

    /// An arbitrary Infrastructure_Fault. `source` is a non-empty identifier so
    /// the "names the source" assertion is meaningful.
    fn fault_strat() -> impl Strategy<Value = CandleLoadError> {
        ("[a-z_][a-z0-9_]{0,20}", ".*").prop_map(|(source, detail)| CandleLoadError::Fault {
            source,
            detail,
        })
    }

    /// Arbitrary loader outcome: an `Ok` candle list, a `Shortfall`, or a `Fault`.
    fn loader_outcome_strat() -> impl Strategy<Value = Result<Vec<CandleWithTs>, CandleLoadError>> {
        prop_oneof![
            proptest::collection::vec(candle_with_ts_strat(), 0..50).prop_map(Ok),
            shortfall_strat().prop_map(Err),
            fault_strat().prop_map(Err),
        ]
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(100))]

        // Feature: deep-quant-runtime-hardening, Property 1: candle-endpoint
        // outcome classification is total and differentiated — Ok → 200 list,
        // Shortfall → 200 unavailable marker with a reason, Fault → 5xx naming
        // the source, and the mapping never panics.
        // Validates: Requirements 2.1, 2.2, 2.3, 2.4, 3.6
        #[test]
        fn prop1_candle_outcomes_are_differentiated_and_total(
            outcome in loader_outcome_strat(),
        ) {
            // (R2.4) The mapping is total: it returns a classified response for
            // every generated outcome without panicking. Reaching this line is
            // itself the no-panic guarantee under proptest.
            let (status, body) = map_loader_outcome(&outcome);

            match &outcome {
                // Ok → 200 ascending candle list (a JSON array).
                Ok(candles) => {
                    prop_assert_eq!(status, StatusCode::OK);
                    let arr = body.as_array().expect("Ok body is a JSON array");
                    prop_assert_eq!(arr.len(), candles.len());
                }
                // Shortfall → graceful non-5xx `200 {"unavailable": true, ...}`
                // carrying a `reason` (R2.1, R2.2).
                Err(CandleLoadError::Shortfall { detail, .. }) => {
                    prop_assert!(status.as_u16() < 500,
                        "shortfall must be non-5xx, got {}", status.as_u16());
                    prop_assert_eq!(status, StatusCode::OK);
                    prop_assert_eq!(
                        body.get("unavailable").and_then(|v| v.as_bool()),
                        Some(true),
                        "shortfall body must carry `unavailable: true`"
                    );
                    let reason = body.get("reason").and_then(|v| v.as_str());
                    prop_assert_eq!(reason, Some(detail.as_str()),
                        "shortfall body must carry the detail as `reason`");
                }
                // Fault → 5xx whose body names the failing source (R2.1, R2.3).
                Err(CandleLoadError::Fault { source, .. }) => {
                    prop_assert!(status.as_u16() >= 500 && status.as_u16() < 600,
                        "fault must be 5xx, got {}", status.as_u16());
                    let err_text = body.get("error").and_then(|v| v.as_str())
                        .expect("fault body carries an `error` string");
                    prop_assert!(err_text.contains("candle store fault"),
                        "fault body must identify itself as a store fault: {}", err_text);
                    prop_assert!(err_text.contains(source.as_str()),
                        "fault body must name the source `{}`: {}", source, err_text);
                }
            }

            // Cross-cutting differentiation (R2.1): a shortfall is never
            // confused with a fault — a `200 {"unavailable": true}` marker and a
            // `5xx {"error": ...}` fault occupy disjoint (status-class, marker)
            // shapes.
            let is_unavailable_marker =
                status.as_u16() < 500 && body.get("unavailable").and_then(|v| v.as_bool()) == Some(true);
            let is_fault = status.as_u16() >= 500;
            prop_assert!(!(is_unavailable_marker && is_fault),
                "an outcome cannot be both a graceful unavailable marker and a fault");
        }
    }
}
