// tool-server/src/main.rs — Standalone (Tauri-free) market-data tool server.
//
// Serves the /tools/* HTTP surface the deep-quant LangGraph agent calls, backed
// by QuestDB (PG wire) and the shared `quant-core` engine. This is the headless
// extraction of the desktop crate's `quant::tool_server` module: identical tool
// contracts and identical quant logic, with the Tauri `AppHandle` replaced by a
// plain `PgPool` and the desktop UI event emits replaced by structured logs
// (the desktop receives glass-box events over the deep-quant SSE stream, not
// from this server directly).
//
// Runs on the internal 'stratai' Docker network; the Python deep-quant service
// reaches it at http://tool-server:8084. Not exposed publicly.

mod candles;
mod metrics;
mod news;

use std::collections::HashMap;
use std::sync::Arc;

use axum::{
    extract::{MatchedPath, Request, State},
    http::StatusCode,
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::post,
    Json, Router,
};
use log::{error, info};
use quant_core::{
    chart_patterns::{ChartPattern, ChartPatternEngine},
    patterns::Candle,
    predictive::calculate_dual_projection,
    vwepr::OhlcCandle,
    Action, AiExecutionPlan, ConsensusEngine, ExecutionLevels, IndicatorState, ValidatorOutcome,
};
use tokio::sync::RwLock;

use candles::{load_candles, load_candles_with_ts, CandleLoadError};
use metrics::ToolServerMetrics;

// ── Server State ─────────────────────────────────────────────────────────────

#[derive(Clone)]
pub struct ServerState {
    pub pool: sqlx::PgPool,
    pub watchers: Arc<RwLock<HashMap<String, Watcher>>>,
    /// Prometheus handle. Carried in state so handlers can record the outcomes
    /// only they can see — chiefly the `unavailable` markers, which the
    /// middleware below cannot distinguish from a successful answer without
    /// buffering and parsing every response body.
    pub metrics: ToolServerMetrics,
}

// ── Request / payload contracts (identical to the desktop tool_server) ────────

#[derive(serde::Deserialize)]
struct GetCandlesRequest {
    symbol: String,
    timeframe: Option<String>,
    limit: Option<i64>,
}

#[derive(serde::Deserialize)]
struct GetConsensusRequest {
    symbol: String,
    timeframe: Option<String>,
    limit: Option<i64>,
}

#[derive(serde::Deserialize)]
struct GetSupportResistanceRequest {
    symbol: String,
    timeframe: Option<String>,
    limit: Option<i64>,
}

#[derive(serde::Deserialize)]
struct GetChartPatternsRequest {
    symbol: String,
    timeframe: Option<String>,
    limit: Option<i64>,
}

#[derive(serde::Serialize)]
struct ChartPatternResponse {
    symbol: String,
    timeframe: String,
    patterns: Vec<ChartPattern>,
}

#[derive(serde::Deserialize)]
struct MultiTfRequest {
    symbol: String,
}

#[derive(serde::Serialize)]
struct MultiTfResponse {
    symbol: String,
    trend_1h: String,
    trend_4h: String,
    trend_1d: String,
    indicators: serde_json::Value,
}

#[derive(serde::Deserialize)]
struct GetPredictionRequest {
    symbol: String,
    timeframe: Option<String>,
    limit: Option<i64>,
}

#[derive(serde::Deserialize)]
struct GetNewsContextRequest {
    symbol: String,
}

#[derive(serde::Deserialize)]
struct DeclareTradeRequest {
    symbol: Option<String>,
    action: String,
    conviction_score: i32,
    setup_validation: String,
    execution_plan: String,
    entry: Option<f64>,
    stop_loss: Option<f64>,
    take_profit: Option<f64>,
    atr_14: Option<f64>,
    #[serde(default)]
    profile: Option<String>,
}

#[derive(serde::Deserialize, Clone)]
struct WatchConditionRequest {
    thread_id: String,
    symbol: Option<String>,
    timeframe: Option<String>,
    price_level: f64,
    direction: String,
    volume_multiplier: f64,
    #[serde(default)]
    invalidation_level: Option<f64>,
    #[serde(default)]
    heartbeat_enabled: bool,
    #[serde(default)]
    heartbeat_cadence_secs: f64,
    #[serde(default)]
    heartbeat_max: u32,
    /// Authenticated user id, carried so a watcher-triggered /resume can resolve
    /// the SAME user's OpenRouter key (no env fallback).
    #[serde(default)]
    user_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Watcher {
    pub thread_id: String,
    pub symbol: String,
    pub timeframe: String,
    pub price_level: f64,
    pub direction: String,
    pub volume_multiplier: f64,
    pub reference_price: f64,
    pub invalidation_level: Option<f64>,
    pub heartbeat_enabled: bool,
    pub heartbeat_cadence_secs: f64,
    pub heartbeat_max: u32,
    pub user_id: Option<String>,
}

// ── get_candles ───────────────────────────────────────────────────────────────

#[derive(serde::Serialize)]
struct CandleWithTs {
    timestamp_ms: i64,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
}

fn sort_candles_ascending(mut candles: Vec<CandleWithTs>) -> Vec<CandleWithTs> {
    candles.sort_by(|a, b| a.timestamp_ms.cmp(&b.timestamp_ms));
    candles
}

/// Map a candle-load failure to an HTTP response, matching the get_candles
/// contract: an Availability_Shortfall degrades to a graceful 200
/// `{"unavailable": true, ...}` marker (the agent treats it as a missing input,
/// not an error), while an Infrastructure_Fault is a 503 naming the cause.
///
/// `tool` and `metrics` are threaded in so the two branches land in different
/// series. They are the same thing to a status-code dashboard — one is a 200 —
/// but opposite things to an operator: a shortfall means backfill the history, a
/// fault means QuestDB is unreachable.
fn candle_load_error_response(
    e: CandleLoadError,
    tool: &str,
    metrics: &ToolServerMetrics,
) -> Response {
    match e {
        CandleLoadError::Shortfall {
            symbol,
            timeframe,
            available,
            needed,
            detail,
        } => {
            metrics.tool_unavailable(tool);
            (
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
                .into_response()
        }
        CandleLoadError::Fault { source, detail } => {
            metrics.db_error("candle_load");
            (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({
                    "error": format!("candle store fault: {}: {}", source, detail),
                })),
            )
                .into_response()
        }
    }
}

/// Records tool/status/latency for every `/tools/*` request in one place, rather
/// than at nine handler exits with four or more return paths each.
///
/// The tool name comes from `MatchedPath` — the route pattern axum matched, not
/// the raw URI — so an unrouted path cannot invent a label series and blow up
/// cardinality. Anything outside `/tools/` is skipped outright: this layer also
/// wraps `/health`, and letting probes through would both add a bogus series and
/// beat the heartbeat every scrape interval, so "time since last real use" would
/// report the probe cadence rather than actual usage.
///
/// What this layer deliberately cannot see is the `unavailable` marker: it is a
/// 200 whose body says the data was not there, and distinguishing it would mean
/// buffering and parsing every response body on the hot path. The handlers
/// record that themselves.
async fn track_tool_call(
    State(state): State<ServerState>,
    request: Request,
    next: Next,
) -> Response {
    let tool = request
        .extensions()
        .get::<MatchedPath>()
        .and_then(|p| p.as_str().strip_prefix("/tools/"))
        .map(str::to_string);

    let started = std::time::Instant::now();
    let response = next.run(request).await;

    if let Some(tool) = tool {
        state.metrics.tool_call_completed(
            &tool,
            response.status().as_u16(),
            started.elapsed().as_secs_f64(),
        );
    }

    response
}

/// Builds the route table. Split out of `main` so tests can exercise the real
/// router — chiefly to prove the metrics middleware ignores `/health`, which is
/// a property of how the layer and the routes are composed and cannot be checked
/// by testing either in isolation.
fn build_router(state: ServerState) -> Router {
    Router::new()
        .route("/tools/get_candles", post(get_candles))
        .route("/tools/get_consensus", post(get_consensus))
        .route("/tools/watch_condition", post(watch_condition))
        .route("/tools/get_multi_tf_trend", post(get_multi_tf_trend_handler))
        .route("/tools/declare_trade", post(declare_trade))
        .route("/tools/get_chart_patterns", post(get_chart_patterns_handler))
        .route("/tools/get_support_resistance", post(get_support_resistance))
        .route("/tools/get_prediction", post(get_prediction))
        .route("/tools/get_news_context", post(get_news_context))
        .route("/health", axum::routing::get(|| async { "ok" }))
        // Layered after every route so `MatchedPath` is populated by the time the
        // middleware runs — it reads the route *pattern*, not the request URI, so
        // an unrouted path cannot invent a `{tool}` series.
        .layer(middleware::from_fn_with_state(
            state.clone(),
            track_tool_call,
        ))
        .with_state(state)
}

async fn get_candles(
    State(state): State<ServerState>,
    Json(payload): Json<GetCandlesRequest>,
) -> Response {
    let limit = payload.limit.unwrap_or(200);
    let tf = payload.timeframe.unwrap_or_else(|| "10m".to_string());

    match load_candles_with_ts(&state.pool, &payload.symbol, &tf, limit, 30).await {
        Ok(timed) => {
            let result: Vec<CandleWithTs> = timed
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
            (StatusCode::OK, Json(sort_candles_ascending(result))).into_response()
        }
        // Was an inlined copy of candle_load_error_response's two arms; folded
        // into the shared helper so the unavailable/fault split is recorded in
        // exactly one place and cannot drift between call sites.
        Err(e) => candle_load_error_response(e, "get_candles", &state.metrics),
    }
}

// ── get_consensus ───────────────────────────────────────────────────────────

async fn get_consensus(
    State(state): State<ServerState>,
    Json(payload): Json<GetConsensusRequest>,
) -> Response {
    let limit = payload.limit.unwrap_or(200);
    let tf = payload.timeframe.unwrap_or_else(|| "10m".to_string());
    let candles = match load_candles(&state.pool, &payload.symbol, &tf, limit).await {
        Ok(c) => c,
        Err(e) => return candle_load_error_response(e, "get_consensus", &state.metrics),
    };

    let indicators = IndicatorState::from_candles_basic(&candles);
    let consensus = ConsensusEngine::compile_consensus(&payload.symbol, &candles, &indicators, &tf);
    (StatusCode::OK, Json(consensus)).into_response()
}

// ── get_support_resistance ────────────────────────────────────────────────────

async fn get_support_resistance(
    State(state): State<ServerState>,
    Json(payload): Json<GetSupportResistanceRequest>,
) -> Response {
    let tf = payload.timeframe.clone().unwrap_or_else(|| "10m".to_string());
    if let Err(e) = quant_core::validate_timeframe(&tf) {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "error": e.to_string() })),
        )
            .into_response();
    }
    let limit = payload.limit.unwrap_or(200);
    let candles = match load_candles(&state.pool, &payload.symbol, &tf, limit).await {
        Ok(c) => c,
        Err(e) => return candle_load_error_response(e, "get_support_resistance", &state.metrics),
    };

    let sr = quant_core::compute_sr(&candles, &tf);
    info!(
        "[tool-server] get_support_resistance: symbol={}, tf={}, pivot={:.2}, ordering_exception={}",
        payload.symbol,
        tf,
        sr.pivot,
        sr.ordering_exception.is_some()
    );
    (StatusCode::OK, Json(sr)).into_response()
}

// ── get_chart_patterns ────────────────────────────────────────────────────────

async fn get_chart_patterns_handler(
    State(state): State<ServerState>,
    Json(payload): Json<GetChartPatternsRequest>,
) -> Response {
    let limit = payload.limit.unwrap_or(200);
    let tf = payload.timeframe.unwrap_or_else(|| "10m".to_string());
    let candles = match load_candles(&state.pool, &payload.symbol, &tf, limit).await {
        Ok(c) => c,
        Err(e) => return candle_load_error_response(e, "get_chart_patterns", &state.metrics),
    };

    let patterns = ChartPatternEngine::analyze(&candles);
    info!(
        "[tool-server] get_chart_patterns: symbol={}, tf={}, detected {} patterns",
        payload.symbol,
        tf,
        patterns.len()
    );
    (
        StatusCode::OK,
        Json(ChartPatternResponse {
            symbol: payload.symbol,
            timeframe: tf,
            patterns,
        }),
    )
        .into_response()
}

// ── get_multi_tf_trend ────────────────────────────────────────────────────────

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

async fn get_multi_tf_trend_handler(
    State(state): State<ServerState>,
    Json(payload): Json<MultiTfRequest>,
) -> Result<Json<MultiTfResponse>, (StatusCode, Json<serde_json::Value>)> {
    let symbol = &payload.symbol;
    // Each horizon degrades independently to an empty series, so a missing 4h
    // history still yields 1h and 1d trends. The cost is that a total QuestDB
    // outage returns three "Neutral" trends with a 200, which is why the empty
    // case is recorded: with no history at any horizon there is nothing behind
    // the answer, and the status code cannot say so.
    let candles_1h = load_candles(&state.pool, symbol, "1h", 200).await.unwrap_or_default();
    let candles_4h = load_candles(&state.pool, symbol, "4h", 200).await.unwrap_or_default();
    let candles_1d = load_candles(&state.pool, symbol, "1d", 200).await.unwrap_or_default();

    if candles_1h.is_empty() && candles_4h.is_empty() && candles_1d.is_empty() {
        state.metrics.tool_unavailable("get_multi_tf_trend");
    }

    let ema_9_1h = IndicatorState::compute_ema(&candles_1h, 9);
    let ema_21_1h = IndicatorState::compute_ema(&candles_1h, 21);
    let trend_1h = horizon_trend(ema_9_1h, ema_21_1h);

    let ema_21_4h = IndicatorState::compute_ema(&candles_4h, 21);
    let ema_50_4h = IndicatorState::compute_ema(&candles_4h, 50);
    let trend_4h = horizon_trend(ema_21_4h, ema_50_4h);

    let ema_50_1d = IndicatorState::compute_ema(&candles_1d, 50);
    let ema_100_1d = IndicatorState::compute_ema(&candles_1d, 100);
    let trend_1d = horizon_trend(ema_50_1d, ema_100_1d);

    let ema_or_null = |v: f64| -> serde_json::Value {
        if v.is_finite() {
            serde_json::json!((v * 100.0).round() / 100.0)
        } else {
            serde_json::Value::Null
        }
    };
    let indicators = serde_json::json!({
        "ema_9_1h": ema_or_null(ema_9_1h),
        "ema_21_1h": ema_or_null(ema_21_1h),
        "ema_21_4h": ema_or_null(ema_21_4h),
        "ema_50_4h": ema_or_null(ema_50_4h),
        "ema_50_1d": ema_or_null(ema_50_1d),
        "ema_100_1d": ema_or_null(ema_100_1d),
    });

    Ok(Json(MultiTfResponse {
        symbol: symbol.to_string(),
        trend_1h: trend_1h.to_string(),
        trend_4h: trend_4h.to_string(),
        trend_1d: trend_1d.to_string(),
        indicators,
    }))
}

// ── get_prediction ────────────────────────────────────────────────────────────

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

fn build_projection(candles: &[Candle], interval_sec: i64) -> Option<(String, f64, f64)> {
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

    let proj = calculate_dual_projection(&ohlc, 1, interval_sec);
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

async fn get_prediction(
    State(state): State<ServerState>,
    Json(payload): Json<GetPredictionRequest>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let tf = payload.timeframe.clone().unwrap_or_else(|| "10m".to_string());
    if let Err(e) = quant_core::validate_timeframe(&tf) {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "error": e.to_string() })),
        ));
    }
    let limit = payload.limit.unwrap_or(200);
    let candles = match load_candles(&state.pool, &payload.symbol, &tf, limit).await {
        Ok(c) => c,
        Err(e) => {
            // This handler flattens both CandleLoadError variants into one 200
            // marker, so a QuestDB fault is attributed here as well as counted
            // as unavailable — otherwise an outage reaching only this tool would
            // be indistinguishable from missing history.
            if matches!(e, CandleLoadError::Fault { .. }) {
                state.metrics.db_error("candle_load");
            }
            state.metrics.tool_unavailable("get_prediction");
            return Ok(Json(serde_json::json!({ "unavailable": true, "reason": e.to_string() })));
        }
    };
    let interval_sec = timeframe_interval_sec(&tf);
    match build_projection(&candles, interval_sec) {
        Some((direction, value, confidence)) => Ok(Json(serde_json::json!({
            "symbol": payload.symbol,
            "timeframe": tf,
            "projected_direction": direction,
            "projected_value": value,
            "confidence": confidence,
        }))),
        None => {
            // Candles loaded but the projection could not be computed. Same
            // marker as a load failure from the agent's point of view, so it
            // belongs in the same series.
            state.metrics.tool_unavailable("get_prediction");
            Ok(Json(serde_json::json!({
                "unavailable": true,
                "reason": "insufficient data to compute projection",
            })))
        }
    }
}

// ── declare_trade ─────────────────────────────────────────────────────────────

fn evaluate_declared_trade_with_profile(
    action_str: &str,
    entry: Option<f64>,
    stop_loss: Option<f64>,
    take_profit: Option<f64>,
    atr_14: Option<f64>,
    profile: Option<&str>,
) -> ValidatorOutcome {
    let action = Action::from_str_lenient(action_str);
    let levels = match (entry, stop_loss, take_profit) {
        (Some(e), Some(sl), Some(tp)) => Some(ExecutionLevels {
            entry: e,
            stop_loss: sl,
            take_profit: tp,
        }),
        _ => None,
    };
    let min_rr = quant_core::min_risk_reward_for_profile(profile);
    quant_core::validate_trade_with_min_rr(action, levels, atr_14, min_rr)
}

async fn declare_trade(
    State(_state): State<ServerState>,
    Json(payload): Json<DeclareTradeRequest>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let conviction = payload.conviction_score.clamp(0, 100);
    let outcome = evaluate_declared_trade_with_profile(
        &payload.action,
        payload.entry,
        payload.stop_loss,
        payload.take_profit,
        payload.atr_14,
        payload.profile.as_deref(),
    );

    if let ValidatorOutcome::Fail { reason } = outcome {
        info!(
            "[tool-server] declare_trade REJECTED: symbol={:?} action={} reason={}",
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
        ValidatorOutcome::Pass { risk_reward } => risk_reward,
        ValidatorOutcome::Fail { .. } => unreachable!("Fail handled above"),
    };

    let plan = AiExecutionPlan {
        conviction_score: conviction,
        setup_validation: payload.setup_validation.clone(),
        execution_plan: payload.execution_plan.clone(),
    };

    info!(
        "[tool-server] declare_trade COMMITTED: symbol={:?} action={} conviction={} risk_reward={:.2} plan_summary={}",
        payload.symbol, payload.action, conviction, risk_reward, plan.setup_validation
    );

    Ok(Json(serde_json::json!({
        "status": "trade_declared",
        "action": payload.action,
        "conviction_score": conviction,
        "risk_reward": risk_reward,
    })))
}

// ── get_news_context ──────────────────────────────────────────────────────────

const DEFAULT_SENTIMENT_SERVICE_URL: &str = "http://localhost:8090/sentiment";

fn sentiment_service_url() -> String {
    std::env::var("SENTIMENT_SERVICE_URL").unwrap_or_else(|_| DEFAULT_SENTIMENT_SERVICE_URL.to_string())
}

fn classify_sentiment_label(conviction_score: f64) -> &'static str {
    if conviction_score >= 60.0 {
        "Bullish"
    } else if conviction_score <= 40.0 {
        "Bearish"
    } else {
        "Neutral"
    }
}

fn map_sentiment_classification(conviction_score: f64, headlines: Vec<String>) -> serde_json::Value {
    let label = classify_sentiment_label(conviction_score);
    serde_json::json!({
        "headlines": headlines,
        "sentiment": label,
        "sentiment_summary": label,
        "conviction_score": conviction_score,
    })
}

fn unavailable_news(reason: &str) -> serde_json::Value {
    serde_json::json!({
        "sentiment_summary": "Unavailable",
        "sentiment": "Unavailable",
        "sentiment_classified": false,
        "headlines": [],
        "error": reason,
    })
}

async fn get_news_context(
    State(state): State<ServerState>,
    Json(payload): Json<GetNewsContextRequest>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let url = sentiment_service_url();
    let client = reqwest::Client::new();
    let rss_headlines: Vec<String> = news::fetch_news_headlines(&payload.symbol).await;

    // Every degraded path in this handler funnels through here, so the marker
    // goes in the closure rather than at each of the four return sites.
    //
    // Only the empty branch counts as unavailable: with RSS headlines in hand
    // the agent still has something to read, and only the sentiment label is
    // missing. Counting that as unavailable would conflate a partial answer with
    // no answer, and the sentiment service being down is already visible in its
    // own metrics on :9108.
    let headlines_only_fallback = |reason: String| -> serde_json::Value {
        if rss_headlines.is_empty() {
            state.metrics.tool_unavailable("get_news_context");
            unavailable_news(&reason)
        } else {
            serde_json::json!({
                "symbol": payload.symbol.clone(),
                "headlines": rss_headlines.clone(),
                "sentiment": "Unavailable",
                "sentiment_classified": false,
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
                return Ok(Json(headlines_only_fallback(format!(
                    "invalid sentiment service response: {}",
                    e
                ))))
            }
        },
        Ok(r) => {
            return Ok(Json(headlines_only_fallback(format!(
                "sentiment service returned HTTP {}",
                r.status()
            ))))
        }
        Err(e) => {
            return Ok(Json(headlines_only_fallback(format!(
                "sentiment service unreachable: {}",
                e
            ))))
        }
    };

    let conviction_score = body.get("conviction_score").and_then(|v| v.as_f64());
    let upstream_headlines: Vec<String> = body
        .get("headlines")
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|h| h.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default();
    let headlines: Vec<String> = if upstream_headlines.is_empty() {
        rss_headlines.clone()
    } else {
        upstream_headlines
    };

    let upstream_label = body.get("label").and_then(|v| v.as_str());
    let upstream_thesis = body.get("thesis").and_then(|v| v.as_str());
    let has_strategic =
        upstream_label.is_some() || upstream_thesis.is_some() || body.get("drivers").is_some();

    if has_strategic {
        let sentiment: String = match upstream_label {
            Some(l) if !l.trim().is_empty() => l.to_string(),
            _ => conviction_score
                .filter(|s| s.is_finite())
                .map(|s| classify_sentiment_label(s).to_string())
                .unwrap_or_else(|| "Neutral".to_string()),
        };
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
            for key in [
                "label", "thesis", "drivers", "risks", "horizon", "confidence",
                "conviction_score", "industry", "profile",
            ] {
                if let Some(v) = body.get(key) {
                    obj.insert(key.to_string(), v.clone());
                }
            }
        }
        return Ok(Json(response));
    }

    match conviction_score {
        Some(score) if score.is_finite() => {
            let mut mapped = map_sentiment_classification(score, headlines);
            if let Some(obj) = mapped.as_object_mut() {
                obj.insert("symbol".to_string(), serde_json::json!(payload.symbol));
            }
            Ok(Json(mapped))
        }
        _ => Ok(Json(headlines_only_fallback(
            "sentiment service did not return a usable classification".to_string(),
        ))),
    }
}

// ── watch_condition (QuestDB-polling watcher) ────────────────────────────────

const WATCH_REGISTERED_STATUS: &str = "watching_registered";

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "lowercase")]
enum WatcherTrigger {
    Target,
    Invalidation,
}

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

/// POST a `/resume` handoff to the Python deep-quant service. Returns Ok(true)
/// when the run was resumable (2xx), Ok(false) when not (4xx → run ended), or
/// Err on transport failure. The returned SSE stream is drained/logged; in
/// headless mode there is no desktop to forward it to (the desktop receives the
/// resume stream when it re-attaches to the run), so events are logged.
async fn post_resume(
    thread_id: &str,
    candle: &OhlcCandle,
    trigger_kind: serde_json::Value,
    heartbeat_seq: Option<u32>,
    user_id: Option<&str>,
) -> Result<bool, String> {
    let client = reqwest::Client::new();
    let mut payload = serde_json::json!({
        "thread_id": thread_id,
        "triggered_candle": candle,
        "trigger_kind": trigger_kind,
    });
    if let Some(seq) = heartbeat_seq {
        payload["heartbeat_seq"] = serde_json::json!(seq);
    }
    // Carry the user id so the resume run resolves the same user's OpenRouter key.
    if let Some(uid) = user_id {
        if !uid.is_empty() {
            payload["user_id"] = serde_json::json!(uid);
        }
    }
    let resume_url = format!(
        "{}/resume",
        std::env::var("DEEP_QUANT_URL")
            .unwrap_or_else(|_| "http://localhost:8086".to_string())
            .trim_end_matches('/')
    );
    match client.post(&resume_url).json(&payload).send().await {
        Ok(res) => {
            let resumable = res.status().is_success();
            info!(
                "[watcher] resume POST thread_id={} trigger={} status={}",
                thread_id, payload["trigger_kind"], res.status()
            );
            // Drain the SSE body so the server-side run streams to completion.
            use futures_util::StreamExt;
            let mut stream = res.bytes_stream();
            while let Some(chunk) = stream.next().await {
                if chunk.is_err() {
                    break;
                }
            }
            Ok(resumable)
        }
        Err(err) => {
            error!("[watcher] resume POST failed thread_id={}: {}", thread_id, err);
            Err(err.to_string())
        }
    }
}

async fn watch_condition(
    State(state): State<ServerState>,
    Json(payload): Json<WatchConditionRequest>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<serde_json::Value>)> {
    let watch_symbol = match payload.symbol.clone() {
        Some(s) => s.trim().to_uppercase(),
        None => {
            return Err((
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({ "error": "Symbol is required" })),
            ))
        }
    };
    if watch_symbol.is_empty() {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({ "error": "Symbol is required" })),
        ));
    }

    let timeframe = payload.timeframe.clone().unwrap_or_else(|| "10m".to_string());

    // Authoritative current price from QuestDB (latest candle close).
    let reference_price = match load_candles(&state.pool, &watch_symbol, &timeframe, 1).await {
        Ok(c) if !c.is_empty() => c.last().unwrap().close,
        Ok(_) => {
            return Err((
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({
                    "error": format!("No current price available for {} on {}", watch_symbol, timeframe)
                })),
            ))
        }
        Err(e) => {
            return Err((
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({ "error": e.to_string() })),
            ))
        }
    };

    let direction_norm = payload.direction.trim().to_lowercase();
    match direction_norm.as_str() {
        "above" | "up" => {
            if !(payload.price_level > reference_price) {
                return Err((
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({ "error": format!("price_level {} is not above current price {}", payload.price_level, reference_price) })),
                ));
            }
        }
        "below" | "down" => {
            if !(payload.price_level < reference_price) {
                return Err((
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({ "error": format!("price_level {} is not below current price {}", payload.price_level, reference_price) })),
                ));
            }
        }
        other => {
            return Err((
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({ "error": format!("Unknown direction '{}'", other) })),
            ))
        }
    }
    if let Some(inv) = payload.invalidation_level {
        match direction_norm.as_str() {
            "above" | "up" if !(inv < reference_price) => {
                return Err((
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({ "error": format!("invalidation_level {} must be below current price {}", inv, reference_price) })),
                ))
            }
            "below" | "down" if !(inv > reference_price) => {
                return Err((
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({ "error": format!("invalidation_level {} must be above current price {}", inv, reference_price) })),
                ))
            }
            _ => {}
        }
    }

    let watcher = Watcher {
        thread_id: payload.thread_id.clone(),
        symbol: watch_symbol.clone(),
        timeframe: timeframe.clone(),
        price_level: payload.price_level,
        direction: direction_norm,
        volume_multiplier: payload.volume_multiplier,
        reference_price,
        invalidation_level: payload.invalidation_level,
        heartbeat_enabled: payload.heartbeat_enabled,
        heartbeat_cadence_secs: payload.heartbeat_cadence_secs,
        heartbeat_max: payload.heartbeat_max,
        user_id: payload.user_id.clone(),
    };
    {
        let mut map = state.watchers.write().await;
        map.insert(watcher.thread_id.clone(), watcher.clone());
        // Reported from inside the lock, from the map's own length, so the gauge
        // is the registry's size rather than a running tally. A tally would drift
        // permanently on any path that removes a watcher without decrementing —
        // and there are three such paths below.
        state.metrics.set_active_watchers(map.len());
    }
    info!(
        "[tool-server] Registered watcher thread_id={} symbol={} level={:.2} dir={} ref={:.2} inv={:?}",
        watcher.thread_id, watcher.symbol, watcher.price_level, watcher.direction, watcher.reference_price, watcher.invalidation_level
    );

    // Spawn a QuestDB-polling watcher task. On the droplet there is no in-process
    // live-candle broadcast (that was a desktop WS bridge), so we poll the latest
    // candle every few seconds — low-latency enough for price-level triggers.
    let pool = state.pool.clone();
    let watchers = state.watchers.clone();
    let watch_metrics = state.metrics.clone();
    tokio::spawn(async move {
        // 20-period baseline average volume.
        let mut avg_volume = 1.0;
        if let Ok(c) = load_candles(&pool, &watcher.symbol, &watcher.timeframe, 20).await {
            if !c.is_empty() {
                avg_volume = c.iter().map(|x| x.volume).sum::<f64>() / c.len() as f64;
            }
        }

        let poll = tokio::time::Duration::from_secs(5);
        let heartbeat_active = watcher.heartbeat_enabled
            && watcher.heartbeat_max > 0
            && watcher.heartbeat_cadence_secs > 0.0;
        let mut heartbeat_seq: u32 = 0;
        let mut last_heartbeat = tokio::time::Instant::now();

        loop {
            tokio::time::sleep(poll).await;

            // Still registered / unchanged?
            let still_active = {
                let map = watchers.read().await;
                map.get(&watcher.thread_id)
                    .map(|w| w.price_level == watcher.price_level && w.symbol == watcher.symbol)
                    .unwrap_or(false)
            };
            if !still_active {
                break;
            }

            // Latest candle from QuestDB.
            let latest = match load_candles(&pool, &watcher.symbol, &watcher.timeframe, 1).await {
                Ok(c) if !c.is_empty() => c.last().cloned().unwrap(),
                _ => continue,
            };
            let candle = OhlcCandle {
                time: chrono::Utc::now().timestamp(),
                open: latest.open,
                high: latest.high,
                low: latest.low,
                close: latest.close,
                volume: latest.volume,
            };
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
                    "[watcher] Condition MET ({:?}) thread_id={} close={:.2}",
                    trigger_kind, watcher.thread_id, candle.close
                );
                {
                    let mut map = watchers.write().await;
                    map.remove(&watcher.thread_id);
                    watch_metrics.set_active_watchers(map.len());
                }
                watch_metrics.watcher_triggered();
                let tk = serde_json::to_value(trigger_kind).unwrap_or(serde_json::json!("target"));
                // The watcher is already deregistered, so a failed resume is not
                // retried by anything. Counted here because this is the one
                // failure where the user was explicitly waiting for the answer
                // and simply never receives it.
                if post_resume(&watcher.thread_id, &candle, tk, None, watcher.user_id.as_deref())
                    .await
                    .is_err()
                {
                    watch_metrics.resume_failed();
                }
                break;
            }

            // Heartbeat cadence (bounded).
            if heartbeat_active && heartbeat_seq < watcher.heartbeat_max {
                let elapsed = last_heartbeat.elapsed().as_secs_f64();
                if elapsed >= watcher.heartbeat_cadence_secs {
                    {
                        let seq = heartbeat_seq + 1;
                        match post_resume(&watcher.thread_id, &candle, serde_json::json!("heartbeat"), Some(seq), watcher.user_id.as_deref()).await {
                            Ok(true) => {
                                heartbeat_seq = seq;
                                last_heartbeat = tokio::time::Instant::now();
                            }
                            Ok(false) => {
                                let mut map = watchers.write().await;
                                map.remove(&watcher.thread_id);
                                watch_metrics.set_active_watchers(map.len());
                                break;
                            }
                            Err(_) => {
                                last_heartbeat = tokio::time::Instant::now();
                            }
                        }
                    }
                }
            }
        }
    });

    Ok(Json(serde_json::json!({ "status": WATCH_REGISTERED_STATUS })))
}

// ── Entrypoint ────────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    // ── Metrics ──────────────────────────────────────────────────────────────
    // Started before the pool connect, which `exit(1)`s on failure. A service
    // that cannot reach QuestDB is the single most useful thing this surface can
    // report, and it can only report it if the listener is already up when the
    // exit happens — otherwise the scrape simply fails and the state is
    // indistinguishable from "never deployed".
    let metrics = ToolServerMetrics::new();
    metrics.serve();

    let db_url = std::env::var("QUESTDB_POSTGRES_URL")
        .unwrap_or_else(|_| "postgresql://admin:quest@127.0.0.1:8812/qdb".to_string());

    let pool = match sqlx::postgres::PgPoolOptions::new()
        .max_connections(10)
        .connect(&db_url)
        .await
    {
        Ok(p) => {
            info!("[tool-server] QuestDB PG pool connected.");
            p
        }
        Err(e) => {
            error!("[tool-server] FATAL: could not connect to QuestDB ({}): {}", db_url, e);
            std::process::exit(1);
        }
    };

    // Ensure the historical candle tables exist (idempotent) so first-run queries
    // return a graceful empty result rather than a "table does not exist" fault.
    //
    // A failure here is not fatal, and that is exactly why it is counted: every
    // later read of the missing table degrades to a graceful "no history" answer,
    // so the service goes on returning 200s with empty bodies forever.
    for _ in 0..candles::migrate(&pool).await {
        metrics.db_error("migrate");
    }

    // ── Pool sampler ─────────────────────────────────────────────────────────
    // Sampled on a timer rather than inside a handler: exhaustion matters most
    // when every request is parked waiting for a connection, and in that state no
    // handler is running to report it.
    let pool_metrics = metrics.clone();
    let sampled_pool = pool.clone();
    tokio::spawn(async move {
        let mut ticker = tokio::time::interval(std::time::Duration::from_secs(10));
        loop {
            ticker.tick().await;
            pool_metrics.set_pool_state(sampled_pool.size(), sampled_pool.num_idle());
        }
    });

    let state = ServerState {
        pool,
        watchers: Arc::new(RwLock::new(HashMap::new())),
        metrics,
    };

    let router = build_router(state);

    let addr = std::env::var("QUANT_TOOL_SERVER_ADDR").unwrap_or_else(|_| {
        let port = std::env::var("QUANT_TOOL_SERVER_PORT").unwrap_or_else(|_| "8084".to_string());
        format!("0.0.0.0:{}", port)
    });
    info!("[tool-server] Listening on {}", addr);

    match tokio::net::TcpListener::bind(&addr).await {
        Ok(listener) => {
            if let Err(e) = axum::serve(listener, router).await {
                error!("[tool-server] serve error: {}", e);
            }
        }
        Err(e) => {
            error!("[tool-server] failed to bind {}: {}", addr, e);
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod router_tests {
    use super::*;
    use axum::body::Body;
    use axum::http::Request as HttpRequest;
    use tower::ServiceExt;

    /// A lazily-connected pool. Never dialled: the routes exercised here do not
    /// touch the database, and the point is to test route/middleware composition
    /// without standing up QuestDB.
    fn state() -> ServerState {
        ServerState {
            pool: sqlx::postgres::PgPoolOptions::new()
                .connect_lazy("postgresql://unused:unused@127.0.0.1:1/none")
                .expect("lazy pool"),
            watchers: Arc::new(RwLock::new(HashMap::new())),
            metrics: ToolServerMetrics::new(),
        }
    }

    #[tokio::test]
    async fn a_health_probe_is_not_a_tool_call() {
        // Docker and Prometheus hit /health on a fixed interval. If the metrics
        // layer counted those, `last_work_age_seconds` would track the probe
        // cadence instead of real usage — the gauge would read "busy" on a
        // completely unused server, which is the exact opposite of its purpose.
        let st = state();
        let metrics = st.metrics.clone();

        let response = build_router(st)
            .oneshot(
                HttpRequest::builder()
                    .uri("/health")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);

        let out = metrics.render_for_test();
        assert!(
            !out.contains(r#"tool="/health""#) && !out.contains(r#"tool="health""#),
            "a probe must not create a tool series:\n{out}"
        );
        assert!(
            out.contains(r#"tool_server_work_completed_total{service="tool-server"} 0"#),
            "a probe must not beat the heartbeat:\n{out}"
        );
    }

    #[tokio::test]
    async fn a_tool_call_is_labelled_by_route_pattern() {
        // The other half of the same property: a real /tools/* request is
        // recorded, under the bare tool name rather than the full path.
        let st = state();
        let metrics = st.metrics.clone();

        let response = build_router(st)
            .oneshot(
                HttpRequest::builder()
                    .method("POST")
                    .uri("/tools/declare_trade")
                    .header("content-type", "application/json")
                    .body(Body::from(
                        r#"{"action":"BUY","entry":100.0,"stop_loss":99.0,
                            "take_profit":103.0,"conviction_score":80,
                            "setup_validation":"ok","execution_plan":"plan"}"#,
                    ))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);

        let out = metrics.render_for_test();
        assert!(
            out.contains(r#"tool_server_tool_calls_total{outcome="success",tool="declare_trade"} 1"#),
            "expected a labelled success:\n{out}"
        );
        assert!(
            out.contains(r#"tool_server_work_completed_total{service="tool-server"} 1"#),
            "a real tool call must beat the heartbeat:\n{out}"
        );
    }
}
