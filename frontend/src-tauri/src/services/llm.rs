// services/llm.rs — Unified LLM Client (Provider-Agnostic)
//
// All AI inference in the system routes through this module. The provider
// is configured entirely via three environment variables:
//
//   LLM_API_URL   — OpenAI-compatible chat/completions endpoint
//   LLM_API_KEY   — Bearer token for the provider
//   LLM_MODEL     — Model identifier (provider-specific)
//
// To switch providers, just change these three values in .env:
//
//   HuggingFace:  LLM_API_URL=https://router.huggingface.co/v1/chat/completions
//                 LLM_API_KEY=hf_xxxxx
//                 LLM_MODEL=deepseek-ai/DeepSeek-V3-0324
//
//   OpenAI:       LLM_API_URL=https://api.openai.com/v1/chat/completions
//                 LLM_API_KEY=sk-xxxxx
//                 LLM_MODEL=gpt-4o
//
//   Groq:         LLM_API_URL=https://api.groq.com/openai/v1/chat/completions
//                 LLM_API_KEY=gsk_xxxxx
//                 LLM_MODEL=llama-3.3-70b-versatile
//
//   Local:        LLM_API_URL=http://localhost:11434/v1/chat/completions
//                 LLM_API_KEY=ollama
//                 LLM_MODEL=deepseek-r1:14b

use log::{info, warn, error};
use serde::{Deserialize, Serialize};
use std::time::Instant;

use crate::quant::{AiExecutionPlan, ConsensusReport};
use crate::services::audit_logger;

// ── Wire types (OpenAI-compatible) ──────────────────────────────────────────

#[derive(serde::Serialize, Clone)]
pub struct AgentMessagePayload {
    pub role: String,
    pub content: String,
}

#[derive(Deserialize, Serialize, Clone, Debug)]
pub struct ToolCall {
    pub id: String,
    pub r#type: String,
    pub function: ToolFunction,
}

#[derive(Deserialize, Serialize, Clone, Debug)]
pub struct ToolFunction {
    pub name: String,
    pub arguments: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Vec<ToolCall>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
}

#[derive(Serialize, Clone)]
pub struct ChatRequest {
    pub model: String,
    pub messages: Vec<ChatMessage>,
    pub temperature: f64,
    pub max_tokens: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub response_format: Option<ResponseFormat>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tools: Option<serde_json::Value>,
}

#[derive(Serialize, Clone)]
pub struct ResponseFormat {
    #[serde(rename = "type")]
    pub kind: String,
}

#[derive(Deserialize, Debug)]
struct ChatResponse {
    choices: Vec<ChatChoice>,
}

#[derive(Deserialize, Debug)]
struct ChatChoice {
    message: ChatMessageResponse,
}

#[derive(Deserialize, Serialize, Clone, Debug)]
pub struct ChatMessageResponse {
    pub role: String,
    pub content: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Vec<ToolCall>>,
}

pub trait AppHandleExt {
    fn emit_all<S: serde::Serialize + Clone>(&self, event: &str, payload: S) -> Result<(), tauri::Error>;
}

impl AppHandleExt for tauri::AppHandle {
    fn emit_all<S: serde::Serialize + Clone>(&self, event: &str, payload: S) -> Result<(), tauri::Error> {
        use tauri::Emitter;
        self.emit(event, payload)
    }
}

// ── Shared Deep-Quant Tool Schema ───────────────────────────────────────────

/// Single source of truth for the Deep-Quant agent tool schemas.
///
/// Both the agentic loop in `generate_deep_quant_plan_with_url` and the
/// Glass-Box loop in `commands::deep_quant::run_glass_box_loop` consume this
/// so the advertised tools can never drift apart. Every tool that is
/// advertised here MUST have a matching dispatch arm in BOTH loops.
pub fn deep_quant_tool_schema() -> serde_json::Value {
    serde_json::json!([
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
                "description": "Fetch latest news headlines for the symbol to check for catalysts.",
                // Empty-but-present parameters object: required by strict providers
                // (OpenAI new API, HuggingFace strict-schema mode) which reject a
                // function declaration that omits `parameters` entirely.
                "parameters": { "type": "object", "properties": {} }
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
    ])
}

/// Format the Order Flow Imbalance value for prompt injection.
///
/// OFI is only computable when a live order-book depth feed (best bid/ask)
/// is available. When it is not (e.g. market closed, no Full-mode depth),
/// the caller passes `f64::NAN` and we render an explicit "unavailable"
/// string rather than a misleading `0.00` that the model would weight as
/// genuine neutral order flow.
pub fn format_ofi(ofi_val: f64) -> String {
    if ofi_val.is_nan() {
        "N/A — live order-book depth feed unavailable (do not weight order flow)".to_string()
    } else {
        format!("{:.2} (-1.0 heavy Ask pressure, +1.0 heavy Bid pressure)", ofi_val)
    }
}

// ── System Prompt Builder (V3 Phase 6: Microstructure — God Patch) ─────────

/// Build the high-conviction institutional system prompt.
///
/// All numeric indicators are computed by the Rust quant engine and injected
/// verbatim — the LLM is explicitly instructed never to guess them.
///
/// New in Phase 6: OFI (Order Flow Imbalance), VWEPR acceleration coefficient,
/// and the list of active candlestick patterns are now part of the system
/// message so the model has full microstructure context before it scores.
#[allow(clippy::too_many_arguments)]
pub fn build_system_prompt(
    symbol: &str,
    timeframe: &str,
    macro_context: &str,
    latest_close: f64,
    vwap_val: f64,
    ofi_val: f64,
    vol_multiplier: f64,
    atr_val: f64,
    bb_upper: f64,
    bb_mid: f64,
    bb_lower: f64,
    rsi_val: f64,
    macd_val: f64,
    macd_signal: f64,
    ema9_val: f64,
    ema21_val: f64,
    acceleration_coeff: f64,
    detected_patterns: &str,
) -> String {
    let system_prompt = format!(
        "You are a seasoned, ruthless Quantitative Trading AI with deep historical market intuition. \n\
        Your primary directive is capital preservation and high-probability directional conviction. \n\
        Be bold, be thorough, and let history guide your execution.\n\
        \n\
        MARKET STATE & MACRO CONTEXT:\n\
        - Symbol: {} | Timeframe: {}\n\
        - Macro Context: {} (Evaluate broader market direction)\n\
        - Last Close: {:.2} | VWAP: {:.2}\n\
        \n\
        MICROSTRUCTURE & VOLUME (Compare against historical breakout thresholds):\n\
        - Order Flow Imbalance (OFI): {}\n\
        - Volume Spike: {:.2}x above 20-period average\n\
        \n\
        VOLATILITY & ANOMALIES:\n\
        - ATR (14): {:.2} (Volatility baseline)\n\
        - Bollinger Bands: [U: {:.2}, M: {:.2}, L: {:.2}]\n\
        \n\
        MOMENTUM, TREND & PATTERNS (Evaluate against historical indicator alignments):\n\
        - RSI (14): {:.2} | MACD Line: {:.2} / Signal: {:.2}\n\
        - EMA-9: {:.2} | EMA-21: {:.2}\n\
        - VWEPR Acceleration: {:.4} (Negative = Exhaustion/Rounding Top, Positive = Parabolic)\n\
        - Active Candlestick Patterns: {}\n\
        STRICT DIRECTIVES:\n\
        1. MANDATORY TOOL USAGE: Unless you see an incredibly obvious, 99%-probability 'Grab Opportunity', you are FORBIDDEN from generating the final JSON execution plan on your first turn. \n\
        2. YOU MUST call the `wait_for_next_candle` tool first to observe the market flow and confirm the momentum.\n\
        3. If you output JSON without using a tool to confirm the setup, you will be penalized.\n\
        4. HIGH-PROBABILITY ONLY (NO DILEMMA): You are strictly forbidden from recommending low-conviction entry trades to avoid analysis dilemmas. If the technical consensus is weak, choppy, or flat, you must DECISIVELY recommend a HOLD / WAIT plan. \n\
        5. IMPENDING PATTERN WAIT DIRECTIVE: If you choose HOLD/WAIT, you must inspect the technical indicators to see if a high-probability pattern is CURRENTLY FORMING (e.g., a rounding bottom from VWEPR acceleration, an impending MACD crossover, or volume contraction). You must explicitly instruct the user to WAIT until a specific candle boundary closes (e.g., 'Wait until the next 10m candle closes to confirm MACD crossover validation') and state exactly what confirmation is needed before re-evaluating.\n\
        \n\
        Return a JSON object EXACTLY matching this structure when finalizing a trade:\n\
        {{\n\
            \"conviction_score\": <int 0-100>,\n\
            \"setup_validation\": \"<2-sentence aggressive synthesis of historical similarities, current signals, and order flow>\",\n\
            \"execution_plan\": \"<Actionable Buy/Sell/Hold plan with precise entry/SL/TP levels, or explicit wait instructions if holding>\"\n\
        }}",
        symbol, timeframe, macro_context, latest_close, vwap_val, format_ofi(ofi_val), vol_multiplier, atr_val, bb_upper, bb_mid, bb_lower, rsi_val, macd_val, macd_signal, ema9_val, ema21_val, acceleration_coeff, detected_patterns
    );
    system_prompt
}

// ── Defaults ────────────────────────────────────────────────────────────────

const DEFAULT_LLM_URL: &str = "https://router.huggingface.co/v1/chat/completions";
const DEFAULT_LLM_MODEL: &str = "deepseek-ai/DeepSeek-V3-0324";
const DEFAULT_TIMEOUT_SECS: u64 = 120;

// ── Config Resolution (clean, no fallbacks) ─────────────────────────────────

fn resolve_endpoint() -> String {
    std::env::var("LLM_API_URL")
        .unwrap_or_else(|_| DEFAULT_LLM_URL.to_string())
}

fn resolve_model() -> String {
    std::env::var("LLM_MODEL")
        .unwrap_or_else(|_| DEFAULT_LLM_MODEL.to_string())
}

fn resolve_api_key() -> Option<String> {
    if let Ok(key) = std::env::var("LLM_API_KEY") {
        if !key.trim().is_empty() {
            return Some(key);
        }
    }
    if crate::is_test_mode() {
        return Some("TEST_KEY".to_string());
    }
    None
}

fn resolve_timeout() -> u64 {
    std::env::var("LLM_TIMEOUT_SECS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(DEFAULT_TIMEOUT_SECS)
}

/// Mask all but the first 6 chars of an API key for safe logging.
fn mask_key(k: &str) -> String {
    let prefix: String = k.chars().take(6).collect();
    format!("{}…(len={})", prefix, k.chars().count())
}

// ── Request Builder (pure, side-effect free) ────────────────────────────────

#[allow(clippy::too_many_arguments)]
pub fn build_request_body(
    symbol: &str,
    consensus: &ConsensusReport,
    news: &str,
    model: &str,
    timeframe: &str,
    macro_context: &str,
    latest_close: f64,
    vwap_val: f64,
    ofi_val: f64,
    vol_multiplier: f64,
    atr_val: f64,
    bb_upper: f64,
    bb_mid: f64,
    bb_lower: f64,
    rsi_val: f64,
    macd_val: f64,
    macd_signal: f64,
    ema9_val: f64,
    ema21_val: f64,
    acceleration_coeff: f64,
    detected_patterns: &str,
) -> ChatRequest {
    let system_prompt = build_system_prompt(
        symbol, timeframe, macro_context, latest_close, vwap_val, ofi_val, vol_multiplier,
        atr_val, bb_upper, bb_mid, bb_lower, rsi_val, macd_val, macd_signal,
        ema9_val, ema21_val, acceleration_coeff, detected_patterns,
    );

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

    ChatRequest {
        model: model.to_string(),
        messages: vec![
            ChatMessage {
                role: "system".to_string(),
                content: system_prompt,
                tool_calls: None,
                tool_call_id: None,
            },
            ChatMessage {
                role: "user".to_string(),
                content: user_prompt,
                tool_calls: None,
                tool_call_id: None,
            },
        ],
        temperature: 0.3,
        max_tokens: 1024,
        response_format: None,
        tools: None,
    }
}

// ── Public API ──────────────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
pub async fn generate_deep_quant_plan(
    symbol: &str,
    consensus: &ConsensusReport,
    news: &str,
    timeframe: &str,
    macro_context: &str,
    latest_close: f64,
    vwap_val: f64,
    ofi_val: f64,
    vol_multiplier: f64,
    atr_val: f64,
    bb_upper: f64,
    bb_mid: f64,
    bb_lower: f64,
    rsi_val: f64,
    macd_val: f64,
    macd_signal: f64,
    ema9_val: f64,
    ema21_val: f64,
    acceleration_coeff: f64,
    detected_patterns: &str,
    app: Option<&tauri::AppHandle>,
) -> Result<AiExecutionPlan, String> {
    let api_url = resolve_endpoint();
    generate_deep_quant_plan_with_url(
        symbol, consensus, news, &api_url,
        timeframe, macro_context, latest_close, vwap_val, ofi_val, vol_multiplier, atr_val,
        bb_upper, bb_mid, bb_lower, rsi_val, macd_val, macd_signal,
        ema9_val, ema21_val, acceleration_coeff, detected_patterns,
        app,
    ).await
}

/// Same as `generate_deep_quant_plan` but accepts an explicit endpoint URL.
/// Used by the test suite to redirect traffic to a mock HTTP server.
#[allow(clippy::too_many_arguments)]
pub async fn generate_deep_quant_plan_with_url(
    symbol: &str,
    consensus: &ConsensusReport,
    news: &str,
    api_url: &str,
    timeframe: &str,
    macro_context: &str,
    latest_close: f64,
    vwap_val: f64,
    ofi_val: f64,
    vol_multiplier: f64,
    atr_val: f64,
    bb_upper: f64,
    bb_mid: f64,
    bb_lower: f64,
    rsi_val: f64,
    macd_val: f64,
    macd_signal: f64,
    ema9_val: f64,
    ema21_val: f64,
    acceleration_coeff: f64,
    detected_patterns: &str,
    app: Option<&tauri::AppHandle>,
) -> Result<AiExecutionPlan, String> {
    let t0 = Instant::now();

    // ── Resolve API key ─────────────────────────────────────────────────
    let vault_key = app.and_then(|handle| {
        use crate::commands::security::get_api_key_from_vault;
        get_api_key_from_vault(handle, "llm_key")
            .or_else(|| get_api_key_from_vault(handle, "hf_key"))
            .or_else(|| get_api_key_from_vault(handle, "deepseek"))
    });

    let api_key = if let Some(k) = vault_key {
        info!("[llm] step=resolve_key source=SECURE_VAULT");
        k
    } else {
        match resolve_api_key() {
            Some(k) => {
                info!("[llm] step=resolve_key source=LLM_API_KEY");
                k
            }
            None => {
                error!("[llm] no API key configured (set LLM_API_KEY in .env or save via Settings → Security Vault)");
                return Err(
                    "LLM API Failure: no API key found. Set LLM_API_KEY in .env or save via Settings → Security Vault."
                        .to_string(),
                );
            }
        }
    };

    let model = resolve_model();
    let timeout_secs = resolve_timeout();

    // Emit technical consensus back to the React UI so the sidebar populates
    if let Some(handle) = app {
        let _ = handle.emit_all("quant-consensus", consensus.clone());
    }

    info!(
        "[llm] step=resolve_config endpoint={} model={} key={}",
        api_url, model, mask_key(&api_key)
    );

    // ── Construct the request body ──────────────────────────────────────
    let request_body = build_request_body(
        symbol, consensus, news, &model,
        timeframe, macro_context, latest_close, vwap_val, ofi_val, vol_multiplier,
        atr_val, bb_upper, bb_mid, bb_lower, rsi_val, macd_val, macd_signal,
        ema9_val, ema21_val, acceleration_coeff, detected_patterns,
    );

    info!(
        "[llm] step=prompt_built symbol={} trend={} momentum={} patterns={} strategies={} news_chars={}",
        symbol,
        consensus.trend_score,
        consensus.momentum_state,
        consensus.active_patterns.len(),
        consensus.active_strategies.len(),
        news.len(),
    );

    // ═══════════════════════════════════════════════════════════════════
    // 🧠 PROMPT DUMP — exact text that will be sent to DeepSeek
    // Both messages are printed verbatim so you can inspect/copy them.
    // ═══════════════════════════════════════════════════════════════════
    {
        let sep = "═".repeat(70);
        let sys_msg = request_body.messages.iter().find(|m| m.role == "system").map(|m| m.content.as_str()).unwrap_or("<none>");
        let usr_msg = request_body.messages.iter().find(|m| m.role == "user").map(|m| m.content.as_str()).unwrap_or("<none>");
        println!("\n\n{sep}");
        println!("🧠 DEEP QUANT PROMPT DUMP");
        println!("   Model   : {}", model);
        println!("   Endpoint: {}", api_url);
        println!("{sep}");
        println!("── [SYSTEM MESSAGE] ──────────────────────────────────────────────────");
        println!("{}", sys_msg);
        println!("── [USER MESSAGE] ────────────────────────────────────────────────────");
        println!("{}", usr_msg);
        println!("{sep}\n");
    }
    // ═══════════════════════════════════════════════════════════════════

    // Task 1: Define the Tool Schemas (shared single source of truth)
    let tools = deep_quant_tool_schema();

    // ── HTTP client ─────────────────────────────────────────────────────
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(timeout_secs))
        .build()
        .map_err(|e| format!("HTTP client build failed: {}", e))?;

    let mut messages = request_body.messages.clone();
    let mut turn = 0;
    let max_turns = 4;
    let mut final_plan: Option<AiExecutionPlan> = None;

    while turn < max_turns {
        turn += 1;

        if let Some(handle) = app {
            handle.emit_all("agent_status", "🧠 Analyzing structural data...").unwrap();
        }

        let current_request = ChatRequest {
            model: model.clone(),
            messages: messages.clone(),
            temperature: 0.3,
            max_tokens: 1024,
            response_format: None,
            tools: Some(tools.clone()),
        };

        let req_json = serde_json::to_value(&current_request).unwrap_or(serde_json::Value::Null);
        let req_bytes = serde_json::to_vec(&current_request).map(|v| v.len()).unwrap_or(0);

        info!(
            "[llm] step=http_send POST {} turn={} message_count={} timeout={}s payload_bytes={}",
            api_url, turn, messages.len(), timeout_secs, req_bytes
        );

        let send_started = Instant::now();
        let response = match client
            .post(api_url)
            .header("Authorization", format!("Bearer {}", api_key))
            .header("Content-Type", "application/json")
            .json(&current_request)
            .send()
            .await
        {
            Ok(r) => r,
            Err(e) => {
                let detail = format_reqwest_error(&e);
                let elapsed = send_started.elapsed().as_millis();
                error!(
                    "[llm] step=http_send_FAIL elapsed_ms={} url={} detail={}",
                    elapsed, api_url, detail
                );
                audit_logger::log_api_error(
                    &format!("POST {}", api_url),
                    &req_json,
                    &format!("transport error after {}ms: {}", elapsed, detail),
                );
                return Err(format!(
                    "LLM API Failure: request to {} failed after {}ms: {}",
                    api_url, elapsed, detail
                ));
            }
        };

        let status = response.status();
        let read_started = Instant::now();
        let response_body = response.text().await.unwrap_or_default();
        let send_elapsed = send_started.elapsed().as_millis();

        info!(
            "[llm] step=http_recv status={} body_bytes={} send_elapsed_ms={} read_elapsed_ms={}",
            status, response_body.len(), send_elapsed, read_started.elapsed().as_millis()
        );

        let res_json: serde_json::Value = serde_json::from_str(&response_body)
            .unwrap_or_else(|_| serde_json::Value::String(response_body.clone()));

        audit_logger::log_api_transaction(
            &format!("POST {}", api_url),
            &req_json,
            &res_json,
            status.as_u16(),
        );

        if !status.is_success() {
            error!(
                "[llm] step=http_status_error status={} body={}",
                status, truncate(&response_body, 400)
            );
            return Err(format!(
                "LLM API Failure: provider returned HTTP {} — {}",
                status, truncate(&response_body, 400)
            ));
        }

        // ── Parse the API envelope ──────────────────────────────────────────
        let chat_response: ChatResponse = serde_json::from_str(&response_body).map_err(|e| {
            error!("[llm] step=envelope_parse_fail err={} body={}", e, truncate(&response_body, 200));
            format!("LLM API Failure: malformed envelope — {} | body: {}", e, truncate(&response_body, 200))
        })?;

        let choice = chat_response
            .choices
            .first()
            .ok_or_else(|| {
                error!("[llm] step=envelope_empty_choices");
                "LLM API Failure: provider returned empty choices array".to_string()
            })?;

        let msg_response = &choice.message;

        // Check if the LLM response contains tool_calls
        if let Some(ref tool_calls) = msg_response.tool_calls {
            if !tool_calls.is_empty() {
                // Append assistant's response that includes the tool calls
                messages.push(ChatMessage {
                    role: "assistant".to_string(),
                    content: msg_response.content.clone().unwrap_or_default(),
                    tool_calls: Some(tool_calls.clone()),
                    tool_call_id: None,
                });

                for tc in tool_calls {
                    let tool_name = &tc.function.name;
                    if let Some(handle) = app {
                        handle.emit_all("agent_status", format!("🛠️ Executing tool: {}", tool_name)).unwrap();
                    }

                    let args: serde_json::Value = serde_json::from_str(&tc.function.arguments).unwrap_or_default();

                    let tool_result = match tool_name.as_str() {
                        "fetch_higher_timeframe" => {
                            let tf = args.get("timeframe")
                                .and_then(|v| v.as_str())
                                .unwrap_or("1D");
                            execute_higher_timeframe_tool(symbol, tf, app).await
                        }
                        "fetch_news_context" => {
                            execute_news_tool(symbol).await
                        }
                        "wait_for_next_candle" => {
                            let tf = args.get("timeframe")
                                .and_then(|v| v.as_str())
                                .unwrap_or(timeframe);
                            execute_wait_for_next_candle_tool(symbol, tf, app).await
                        }
                        _ => {
                            format!("Error: Unknown tool name: {}", tool_name)
                        }
                    };

                    messages.push(ChatMessage {
                        role: "tool".to_string(),
                        content: tool_result,
                        tool_calls: None,
                        tool_call_id: Some(tc.id.clone()),
                    });
                }

                // Continue the loop
                continue;
            }
        }

        // Standard text response (no tools) -> finalizing trade
        let content = msg_response.content.clone().unwrap_or_default();

        // ═══════════════════════════════════════════════════════════════════
        // 🕵️‍♂️ AUDIT 4 - LLM RAW RESPONSE: Full unparsed string from the LLM
        // This catches hallucinated JSON keys BEFORE serde tries to parse it.
        // ═══════════════════════════════════════════════════════════════════
        println!("🕵️‍♂️ [AUDIT 4 - LLM RAW RESPONSE] Content length: {} chars", content.len());
        println!("🕵️‍♂️ [AUDIT 4 - LLM RAW RESPONSE]:\n{}", content);
        // ═══════════════════════════════════════════════════════════════════

        let mut cleaned = content.trim().to_string();

        // Step 1 — strip leading fence
        if let Some(rest) = cleaned.strip_prefix("```json") {
            cleaned = rest.to_string();
        } else if let Some(rest) = cleaned.strip_prefix("```") {
            cleaned = rest.to_string();
        }
        // Step 2 — strip trailing fence
        if let Some(rest) = cleaned.strip_suffix("```") {
            cleaned = rest.to_string();
        }
        // Step 3 — outer whitespace trim
        let cleaned = cleaned.trim();

        // Step 4 — JSON-boundary extractor: find first '{' and last '}'
        let start = cleaned.find('{');
        let end = cleaned.rfind('}');

        let plan: AiExecutionPlan = match (start, end) {
            (Some(s), Some(e)) if e >= s => {
                let extracted = &cleaned[s..=e];
                match serde_json::from_str(extracted) {
                    Ok(p) => p,
                    Err(err) => {
                        error!("[llm] step=plan_parse_fail err={} raw={}", err, truncate(extracted, 300));
                        return Err(format!(
                            "LLM API Failure: output is not valid AiExecutionPlan JSON — {} | raw: {}",
                            err,
                            content
                        ));
                    }
                }
            }
            _ => {
                error!(
                    "[llm] LLM returned prose (no JSON) for {} — raw: {:?}",
                    symbol,
                    content
                );
                return Err(format!(
                    "LLM API Failure: output is not valid AiExecutionPlan JSON — no JSON object found | raw: {}",
                    content
                ));
            }
        };

        let plan = if plan.conviction_score < 1 || plan.conviction_score > 100 {
            warn!("[llm] step=plan_clamp original_score={} clamped", plan.conviction_score);
            AiExecutionPlan { conviction_score: plan.conviction_score.clamp(1, 100), ..plan }
        } else {
            plan
        };

        final_plan = Some(plan);
        break; // break the loop
    }

    let plan = final_plan.ok_or_else(|| {
        error!("[llm] step=max_turns_exceeded max_turns={}", max_turns);
        format!("LLM API Failure: agentic execution loop exceeded max_turns of {} without resolving", max_turns)
    })?;

    info!(
        "[llm] step=done total_elapsed_ms={} conviction={} plan_preview={}",
        t0.elapsed().as_millis(), plan.conviction_score, truncate(&plan.execution_plan, 80)
    );

    Ok(plan)
}

// ── Helpers ─────────────────────────────────────────────────────────────────

#[inline]
fn truncate(s: &str, max: usize) -> &str {
    if s.len() <= max { s }
    else {
        let mut end = max;
        while end > 0 && !s.is_char_boundary(end) { end -= 1; }
        &s[..end]
    }
}

fn format_reqwest_error(err: &reqwest::Error) -> String {
    use std::error::Error as _;
    let mut parts: Vec<String> = vec![err.to_string()];
    let mut src: Option<&dyn std::error::Error> = err.source();
    let mut depth = 0;
    while let Some(e) = src {
        parts.push(format!("caused by: {}", e));
        src = e.source();
        depth += 1;
        if depth > 8 { break; }
    }
    let mut tags: Vec<&str> = Vec::new();
    if err.is_timeout() { tags.push("timeout"); }
    if err.is_connect() { tags.push("connect"); }
    if err.is_request() { tags.push("request"); }
    if err.is_body()    { tags.push("body"); }
    if err.is_decode()  { tags.push("decode"); }
    if err.is_redirect(){ tags.push("redirect"); }
    if err.is_status()  { tags.push("status"); }
    if !tags.is_empty() { parts.push(format!("kind: [{}]", tags.join(", "))); }
    parts.join(" | ")
}

// ── Real Tool Actions ───────────────────────────────────────────────────────

pub async fn execute_higher_timeframe_tool(
    symbol: &str,
    timeframe: &str,
    app: Option<&tauri::AppHandle>,
) -> String {
    use tauri::Manager;
    let pool = match app.and_then(|handle| handle.try_state::<sqlx::PgPool>()) {
        Some(p) => p,
        None => return "Error: QuestDB connection pool not available".to_string(),
    };

    let tf_normalized = timeframe.trim().to_uppercase();

    // Fetch candles from QuestDB based on timeframe
    let query_result: Result<Vec<sqlx::postgres::PgRow>, sqlx::Error> = if tf_normalized.contains('D') || tf_normalized.contains('W') {
        // Daily / Weekly
        sqlx::query(
            "SELECT CAST(ts AS LONG) AS ts_epoch, open, high, low, close, volume \
             FROM historical_candles \
             WHERE symbol = $1 \
             ORDER BY ts DESC \
             LIMIT 50"
        )
        .bind(symbol)
        .fetch_all(pool.inner())
        .await
    } else {
        // Intraday (e.g., 1H)
        let db_tf = if tf_normalized.contains('H') { "1h" } else { "15m" };
        sqlx::query(
            "SELECT CAST(ts AS LONG) AS ts_epoch, open, high, low, close, volume \
             FROM historical_intraday \
             WHERE symbol = $1 AND timeframe = $2 \
             ORDER BY ts DESC \
             LIMIT 50"
        )
        .bind(symbol)
        .bind(db_tf)
        .fetch_all(pool.inner())
        .await
    };

    let rows = match query_result {
        Ok(r) if !r.is_empty() => r,
        _ => {
            // Fallback: try loading general historical daily candles
            match sqlx::query(
                "SELECT CAST(ts AS LONG) AS ts_epoch, open, high, low, close, volume \
                 FROM historical_candles \
                 WHERE symbol = $1 \
                 ORDER BY ts DESC \
                 LIMIT 50"
            )
            .bind(symbol)
            .fetch_all(pool.inner())
            .await {
                Ok(r) => r,
                Err(e) => return format!("Error fetching candles for {}: {}", symbol, e),
            }
        }
    };

    if rows.is_empty() {
        return format!("No higher timeframe data available for symbol: {}", symbol);
    }

    use crate::quant::patterns::Candle;
    use sqlx::Row;
    let mut candles: Vec<Candle> = rows
        .iter()
        .map(|row: &sqlx::postgres::PgRow| {
            let open: f64 = row.try_get("open").unwrap_or(0.0);
            let high: f64 = row.try_get("high").unwrap_or(0.0);
            let low: f64 = row.try_get("low").unwrap_or(0.0);
            let close: f64 = row.try_get("close").unwrap_or(0.0);
            let volume: i64 = row.try_get::<i64, _>("volume")
                .or_else(|_| row.try_get::<i32, _>("volume").map(|v| v as i64))
                .unwrap_or(0);
            Candle { open, high, low, close, volume: volume as f64 }
        })
        .collect();

    // Reverse to chronological order (oldest first)
    candles.reverse();

    if candles.len() < 15 {
        return format!("Higher Timeframe Context ({}) - Pricing data insufficient (minimum 15 candles required).", timeframe);
    }

    let current_close = candles.last().map(|c| c.close).unwrap_or(0.0);
    
    // Compute EMA-9, EMA-21, RSI-14
    let ema_9 = compute_ema_helper(&candles, 9);
    let ema_21 = compute_ema_helper(&candles, 21);
    let rsi_val = compute_rsi_helper(&candles, 14);

    let ema_trend_string = if ema_9.is_finite() && ema_21.is_finite() {
        if ema_9 > ema_21 { "Bullish crossover (EMA-9 > EMA-21)" } else { "Bearish crossover (EMA-9 < EMA-21)" }
    } else {
        "Neutral/unaligned"
    };

    let rsi_string = if rsi_val.is_finite() {
        format!("{:.2}", rsi_val)
    } else {
        "50.0".to_string()
    };

    format!(
        "Higher Timeframe Context ({}) - Price: {:.2}, RSI: {}, EMAs show: {}",
        timeframe, current_close, rsi_string, ema_trend_string
    )
}

async fn execute_news_tool(symbol: &str) -> String {
    let result = crate::commands::deep_quant::fetch_news_context(symbol).await;
    if result.trim().is_empty() || result.contains("No recent news available") {
        return "No recent news context available for catalysts check.".to_string();
    }
    result
}

/// Real implementation of the `wait_for_next_candle` tool for the
/// non-Glass-Box agentic path. Sleeps until the next candle boundary (with a
/// short ingestion buffer), then re-reads the freshest close from QuestDB so
/// the model receives genuine post-wait market data instead of a stub.
///
/// Honors `DEEP_QUANT_SIMULATE_WAIT=true` to cap the sleep at 30s for tests
/// and sandbox runs. Never sleeps longer than one candle interval.
pub async fn execute_wait_for_next_candle_tool(
    symbol: &str,
    timeframe: &str,
    app: Option<&tauri::AppHandle>,
) -> String {
    use std::time::{SystemTime, UNIX_EPOCH};

    let interval_sec: u64 = match timeframe.trim().to_lowercase().as_str() {
        "1m" | "1min" => 60,
        "3m" | "3min" => 180,
        "5m" | "5min" => 300,
        "10m" | "10min" => 600,
        "15m" | "15min" => 900,
        "30m" | "30min" => 1_800,
        "60m" | "1h" | "1hour" => 3_600,
        "1d" | "day" => 86_400,
        _ => 600,
    };

    let is_sandbox = std::env::var("DEEP_QUANT_SIMULATE_WAIT")
        .map(|v| v == "true")
        .unwrap_or(false);

    let now_secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    let current_boundary = (now_secs / interval_sec) * interval_sec;
    let next_boundary = current_boundary + interval_sec;
    let mut remaining = next_boundary.saturating_sub(now_secs).max(1);
    if is_sandbox {
        remaining = remaining.min(30);
    }
    remaining += 5; // ingestion buffer

    info!(
        "[llm] wait_for_next_candle: symbol={} timeframe={} sleeping={}s sandbox={}",
        symbol, timeframe, remaining, is_sandbox
    );

    tokio::time::sleep(std::time::Duration::from_secs(remaining)).await;

    // Re-read the freshest candle from QuestDB so the model sees a real close.
    use tauri::Manager;
    let pool = match app.and_then(|handle| handle.try_state::<sqlx::PgPool>()) {
        Some(p) => p,
        None => {
            return format!(
                "Waited {}s for the next {} candle on {}. (QuestDB pool unavailable — re-evaluate with existing data.)",
                remaining, timeframe, symbol
            );
        }
    };

    match crate::commands::deep_quant::load_candles_from_db(app, pool.inner(), symbol, timeframe, 60).await {
        Ok(candles) if !candles.is_empty() => {
            let indicators = crate::quant::IndicatorState::from_candles_basic(&candles);
            let consensus = crate::quant::ConsensusEngine::compile_consensus(symbol, &candles, &indicators, timeframe);
            if let Some(handle) = app {
                let _ = handle.emit_all("quant-consensus", consensus.clone());
            }
            let close = candles.last().map(|c| c.close).unwrap_or(0.0);
            let rsi = if indicators.rsi_14.is_finite() { indicators.rsi_14 } else { 50.0 };
            let macd = if indicators.macd_line.is_finite() { indicators.macd_line } else { 0.0 };
            let macd_sig = if indicators.macd_signal.is_finite() { indicators.macd_signal } else { 0.0 };
            format!(
                "LIVE MARKET UPDATE — next {} candle observed for {}.\n\
                 - New Close: {:.2}\n\
                 - RSI(14): {:.2} | MACD: {:.4} / Signal: {:.4}\n\
                 - Consensus Trend Score: {} | Momentum: {} | Volume Flow: {}",
                timeframe, symbol, close, rsi, macd, macd_sig,
                consensus.trend_score, consensus.momentum_state, consensus.volume_flow_state
            )
        }
        _ => format!(
            "Waited {}s for the next {} candle on {}, but no fresh candle could be read from the database yet.",
            remaining, timeframe, symbol
        ),
    }
}

fn compute_ema_helper(candles: &[crate::quant::patterns::Candle], period: usize) -> f64 {
    if candles.len() < period {
        return f64::NAN;
    }
    let multiplier = 2.0 / (period as f64 + 1.0);
    let sma: f64 = candles[..period].iter().map(|c| c.close).sum::<f64>() / period as f64;
    let mut ema = sma;
    for candle in &candles[period..] {
        ema = (candle.close - ema) * multiplier + ema;
    }
    ema
}

fn compute_rsi_helper(candles: &[crate::quant::patterns::Candle], period: usize) -> f64 {
    if candles.len() < period + 1 {
        return f64::NAN;
    }
    let slice = &candles[candles.len() - period - 1..];
    let mut gains = 0.0;
    let mut losses = 0.0;
    for i in 1..slice.len() {
        let delta = slice[i].close - slice[i - 1].close;
        if delta > 0.0 { gains += delta; } else { losses -= delta; }
    }
    let avg_gain = gains / period as f64;
    let avg_loss = losses / period as f64;
    if avg_loss < 1e-12 { return 100.0; }
    let rs = avg_gain / avg_loss;
    100.0 - (100.0 / (1.0 + rs))
}

#[allow(clippy::too_many_arguments)]
pub async fn generate_sentinel_plan(
    symbol: &str,
    consensus: &ConsensusReport,
    timeframe: &str,
    latest_close: f64,
    vwap_val: f64,
    vol_multiplier: f64,
    atr_val: f64,
    bb_upper: f64,
    bb_mid: f64,
    bb_lower: f64,
    rsi_val: f64,
    macd_val: f64,
    macd_signal: f64,
    ema9_val: f64,
    ema21_val: f64,
    app: Option<&tauri::AppHandle>,
) -> Result<AiExecutionPlan, String> {
    let t0 = Instant::now();
    let api_url = resolve_endpoint();
    let api_key = if let Some(handle) = app {
        use crate::commands::security::get_api_key_from_vault;
        get_api_key_from_vault(handle, "llm_key")
            .or_else(|| get_api_key_from_vault(handle, "hf_key"))
            .or_else(|| get_api_key_from_vault(handle, "deepseek"))
            .or_else(|| resolve_api_key())
    } else {
        resolve_api_key()
    }.ok_or_else(|| "LLM API Key not found. Set LLM_API_KEY in .env or Settings.".to_string())?;

    let model = resolve_model();
    let timeout_secs = resolve_timeout();

    let system_prompt = format!(
        "You are a Quantitative sentinel AI monitoring {symbol} on the {timeframe} timeframe.\n\
        Your primary directive is to watch for high-probability trade entry triggers (like an MACD crossover, volume spike, or Bollinger Band breakout) and execute immediately when they occur.\n\
        \n\
        LATEST LIVE TECHNICAL DATA:\n\
        - Last Close: {latest_close:.2} | VWAP: {vwap_val:.2}\n\
        - Volume Spike: {vol_multiplier:.2}x above 20-period average\n\
        - ATR (14): {atr_val:.2}\n\
        - Bollinger Bands: [U: {bb_upper:.2}, M: {bb_mid:.2}, L: {bb_lower:.2}]\n\
        - RSI (14): {rsi_val:.2} | MACD Line: {macd_val:.4} / Signal: {macd_signal:.4}\n\
        - EMA-9: {ema9_val:.2} | EMA-21: {ema21_val:.2}\n\
        - Trend Score: {trend_score} (-100 to +100)\n\
        - Momentum State: {momentum}\n\
        \n\
        STRICT MONITORING DIRECTIVE:\n\
        Evaluate if a high-probability entry trigger has occurred right now.\n\
        - If YES (Bullish/Bearish trigger occurred): You must return a conviction_score > 60, and detail the entry plan.\n\
        - If NO (Market is choppy, flat, or no trigger has occurred): You must return a conviction_score < 40, and state what trigger you are waiting for.\n\
        \n\
        Return a JSON object EXACTLY matching this structure:\n\
        {{\n\
            \"conviction_score\": <int 0-100>,\n\
            \"setup_validation\": \"<1-sentence explanation of what trigger was met or what we are waiting for>\",\n\
            \"execution_plan\": \"<Actionable trade plan if conviction > 60, otherwise specify the exact crossover or trigger we are waiting for>\"\n\
        }}",
        symbol = symbol,
        timeframe = timeframe,
        latest_close = latest_close,
        vwap_val = vwap_val,
        vol_multiplier = vol_multiplier,
        atr_val = atr_val,
        bb_upper = bb_upper,
        bb_mid = bb_mid,
        bb_lower = bb_lower,
        rsi_val = rsi_val,
        macd_val = macd_val,
        macd_signal = macd_signal,
        ema9_val = ema9_val,
        ema21_val = ema21_val,
        trend_score = consensus.trend_score,
        momentum = consensus.momentum_state,
    );

    let request = ChatRequest {
        model: model.clone(),
        messages: vec![
            ChatMessage {
                role: "system".to_string(),
                content: system_prompt,
                tool_calls: None,
                tool_call_id: None,
            },
            ChatMessage {
                role: "user".to_string(),
                content: format!("Evaluate the latest technical indicators for {symbol} and decide whether to EXECUTE or HOLD."),
                tool_calls: None,
                tool_call_id: None,
            }
        ],
        temperature: 0.2,
        max_tokens: 512,
        response_format: None,
        tools: None,
    };

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(timeout_secs))
        .build()
        .map_err(|e| format!("HTTP client build failed: {}", e))?;

    let response = client.post(&api_url)
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&request)
        .send()
        .await
        .map_err(|e| format!("Sentinel LLM request failed: {}", e))?;

    let status = response.status();
    let response_body = response.text().await.unwrap_or_default();

    if !status.is_success() {
        return Err(format!("Sentinel LLM returned HTTP error status: {}", status));
    }

    let chat_response: ChatResponse = serde_json::from_str(&response_body).map_err(|e| {
        format!("Sentinel LLM malformed envelope: {}", e)
    })?;

    let choice = chat_response.choices.first().ok_or_else(|| {
        "Sentinel LLM empty choices".to_string()
    })?;

    let content = choice.message.content.clone().unwrap_or_default();
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

    let plan: AiExecutionPlan = match (start, end) {
        (Some(s), Some(e)) if e >= s => {
            let extracted = &cleaned[s..=e];
            serde_json::from_str(extracted).map_err(|err| {
                format!("Sentinel LLM JSON parse failed: {} | raw: {}", err, truncate(extracted, 200))
            })?
        }
        _ => {
            return Err("Sentinel LLM output does not contain a JSON block".to_string());
        }
    };

    info!(
        "[sentinel] step=done total_elapsed_ms={} conviction={} plan_preview={}",
        t0.elapsed().as_millis(), plan.conviction_score, truncate(&plan.execution_plan, 80)
    );

    Ok(plan)
}

#[allow(clippy::too_many_arguments)]
pub async fn generate_autonomous_step(
    app: &tauri::AppHandle,
    messages: Vec<ChatMessage>,
    tools: serde_json::Value,
) -> Result<ChatMessageResponse, String> {
    let api_url = resolve_endpoint();
    let api_key = if let Some(k) = {
        use crate::commands::security::get_api_key_from_vault;
        get_api_key_from_vault(app, "llm_key")
            .or_else(|| get_api_key_from_vault(app, "hf_key"))
            .or_else(|| get_api_key_from_vault(app, "deepseek"))
    } {
        k
    } else {
        match resolve_api_key() {
            Some(k) => k,
            None => {
                return Err("LLM API Failure: no API key found.".to_string());
            }
        }
    };

    let model = resolve_model();
    let timeout_secs = resolve_timeout();

    let current_request = ChatRequest {
        model: model.clone(),
        messages,
        temperature: 0.3,
        max_tokens: 1024,
        response_format: None,
        tools: Some(tools),
    };

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(timeout_secs))
        .build()
        .map_err(|e| format!("HTTP client build failed: {}", e))?;

    let response = client
        .post(&api_url)
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&current_request)
        .send()
        .await
        .map_err(|e| format!("Autonomous request failed: {}", e))?;

    let status = response.status();
    let response_body = response.text().await.unwrap_or_default();

    if !status.is_success() {
        return Err(format!("LLM API returned HTTP {} — {}", status, response_body));
    }

    let chat_response: ChatResponse = serde_json::from_str(&response_body)
        .map_err(|e| format!("Malformed envelope: {}", e))?;

    let choice = chat_response.choices.first()
        .ok_or_else(|| "Empty choices array".to_string())?;

    Ok(choice.message.clone())
}

