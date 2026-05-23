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

#[derive(Serialize, Clone)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
}

#[derive(Serialize, Clone)]
pub struct ChatRequest {
    pub model: String,
    pub messages: Vec<ChatMessage>,
    pub temperature: f64,
    pub max_tokens: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub response_format: Option<ResponseFormat>,
}

#[derive(Serialize, Clone)]
pub struct ResponseFormat {
    #[serde(rename = "type")]
    pub kind: String,
}

#[derive(Deserialize)]
struct ChatResponse {
    choices: Vec<ChatChoice>,
}

#[derive(Deserialize)]
struct ChatChoice {
    message: ChatMessageResponse,
}

#[derive(Deserialize)]
struct ChatMessageResponse {
    content: String,
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
        - Order Flow Imbalance (OFI): {:.2} (-1.0 heavy Ask pressure, +1.0 heavy Bid pressure)\n\
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
        \n\
        STRICT DIRECTIVES:\n\
        1. HISTORICAL SYNTHESIS: Weigh current parameters, patterns, and user-provided news against past similar setups in your quantitative memory. How did similar alignments play out in the past?\n\
        2. FORCED CONVICTION: Make a definitive trade call (Buy, Sell, or Hold). Do NOT return a score between 40 and 60 unless Volume is completely dead and ATR is microscopic.\n\
        3. SCORING: 0-39 = Bearish/Sell. 61-100 = Bullish/Buy. Base this conviction score on how closely today’s scenario matches past winning quantitative trades.\n\
        \n\
        Return a JSON object EXACTLY matching this structure:\n\
        {{\n\
            \"conviction_score\": <int 0-100>,\n\
            \"setup_validation\": \"<2-sentence aggressive synthesis of historical similarities, current signals, and order flow>\",\n\
            \"execution_plan\": \"<Actionable Buy/Sell/Hold plan with precise entry/SL/TP levels based on the data>\"\n\
        }}",
        symbol, timeframe, macro_context, latest_close, vwap_val, ofi_val, vol_multiplier, atr_val, bb_upper, bb_mid, bb_lower, rsi_val, macd_val, macd_signal, ema9_val, ema21_val, acceleration_coeff, detected_patterns
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
            },
            ChatMessage {
                role: "user".to_string(),
                content: user_prompt,
            },
        ],
        temperature: 0.3,
        max_tokens: 1024,
        response_format: None,
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

    // ── HTTP client ─────────────────────────────────────────────────────
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(timeout_secs))
        .build()
        .map_err(|e| format!("HTTP client build failed: {}", e))?;

    let req_json = serde_json::to_value(&request_body).unwrap_or(serde_json::Value::Null);
    let req_bytes = serde_json::to_vec(&request_body).map(|v| v.len()).unwrap_or(0);

    info!(
        "[llm] step=http_send POST {} timeout={}s payload_bytes={}",
        api_url, timeout_secs, req_bytes
    );

    let send_started = Instant::now();
    let response = match client
        .post(api_url)
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&request_body)
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

    let content = chat_response
        .choices
        .first()
        .map(|c| c.message.content.clone())
        .ok_or_else(|| {
            error!("[llm] step=envelope_empty_choices");
            "LLM API Failure: provider returned empty choices array".to_string()
        })?;

    info!("[llm] step=content_extracted chars={}", content.len());

    // ═══════════════════════════════════════════════════════════════════
    // 🕵️‍♂️ AUDIT 4 - LLM RAW RESPONSE: Full unparsed string from the LLM
    // This catches hallucinated JSON keys BEFORE serde tries to parse it.
    // ═══════════════════════════════════════════════════════════════════
    println!("🕵️‍♂️ [AUDIT 4 - LLM RAW RESPONSE] Content length: {} chars", content.len());
    println!("🕵️‍♂️ [AUDIT 4 - LLM RAW RESPONSE]:\n{}", content);
    // ═══════════════════════════════════════════════════════════════════

    // ── Parse the LLM's JSON output into AiExecutionPlan ────────────────
    //
    // Task 1 (God Patch): Robust JSON sanitizer.
    //
    // DeepSeek occasionally wraps the payload in markdown fences with an
    // optional language tag and/or a leading newline:
    //   ```json\n{...}\n```
    //   ```\n{...}\n```
    //   {"conviction_score": ...}   ← already clean
    //
    // Strategy:
    //   1. Strip leading fence (```json or ```) via strip_prefix.
    //   2. Strip trailing ``` via strip_suffix.
    //   3. Trim surrounding whitespace.
    //   4. As a final fallback, slice to the outermost { … } boundaries
    //      so stray prose before/after the JSON object is harmless.
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
    // This silently discards any prose the model added before or after.
    let cleaned = match (cleaned.find('{'), cleaned.rfind('}')) {
        (Some(start), Some(end)) if start <= end => &cleaned[start..=end],
        _ => cleaned, // no braces found — let serde produce a meaningful error
    };

    let plan: AiExecutionPlan = serde_json::from_str(cleaned).map_err(|e| {
        error!("[llm] step=plan_parse_fail err={} raw={}", e, truncate(cleaned, 300));
        format!("LLM API Failure: output is not valid AiExecutionPlan JSON — {} | raw: {}", e, truncate(cleaned, 300))
    })?;

    let plan = if plan.conviction_score < 1 || plan.conviction_score > 100 {
        warn!("[llm] step=plan_clamp original_score={} clamped", plan.conviction_score);
        AiExecutionPlan { conviction_score: plan.conviction_score.clamp(1, 100), ..plan }
    } else {
        plan
    };

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
