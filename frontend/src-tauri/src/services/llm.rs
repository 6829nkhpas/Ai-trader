// services/llm.rs — Hugging Face Inference Router → DeepSeek (V3.1).
//
// V3.1 transition note:
//   We migrated off NVIDIA NIM because NIM's free tier was queueing
//   `deepseek-v4-pro` requests for >60s, exceeding our UI budget.
//   We now route through Hugging Face's OpenAI-compatible router
//   (https://router.huggingface.co/v1/chat/completions), which
//   transparently picks the fastest provider hosting the model.
//
// Endpoint resolution:
//   1. LLM_API_URL          — explicit override (tests, ops)
//   2. HF_API_URL           — alias kept for symmetry with HF_API_KEY
//   3. DEEPSEEK_API_URL     — legacy override
//   4. NVIDIA_NIM_API_URL   — legacy override
//   5. Built-in HF router URL
//
// API key resolution (first non-empty wins):
//   1. HF_API_KEY / HUGGINGFACE_API_KEY  — Hugging Face token (preferred)
//   2. NVIDIA_API_KEY                    — legacy NIM key
//   3. DEEPSEEK_API_KEY                  — legacy / test key
//   4. "TEST_KEY" when ALPHA_TEST_MODE=1
//
// Model resolution:
//   1. LLM_MODEL                         — explicit override
//   2. HF_MODEL / DEEPSEEK_MODEL / NVIDIA_NIM_MODEL — legacy
//   3. Built-in default ("deepseek-ai/DeepSeek-V3.1-Terminus:novita")
//
// Every step is logged with elapsed-millis timing and forwarded to
// `audit_logger`, so the Tauri terminal narrates the full pipeline
// the moment the user hits the AI Quant Analysis button.

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
    /// Some providers (Together, Fireworks via HF) accept `response_format`,
    /// others reject it with 400. Default: not sent. The system prompt plus
    /// the post-fence stripper guarantee parseable JSON.
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

// ── System Prompt ───────────────────────────────────────────────────────────

pub const SYSTEM_PROMPT: &str = "\
You are an Elite Quantitative Portfolio Manager. \
You will be provided with a mathematical consensus report and real-time news for a specific asset. \
You must evaluate if the 'Active Strategies' are valid or traps based on the supporting indicators and news. \
You MUST output strictly in JSON format with exactly three keys: \
'conviction_score' (integer 1-100), \
'setup_validation' (string explaining your reasoning), \
and 'execution_plan' (string detailing entry, invalidation, and targets). \
Do NOT include any text outside the JSON object. Do NOT wrap in markdown code fences. \
Output ONLY the raw JSON object.";

// ── Defaults ────────────────────────────────────────────────────────────────

/// Hugging Face's OpenAI-compatible inference router. Picks the fastest
/// provider hosting the requested model under the hood.
const DEFAULT_LLM_URL: &str = "https://router.huggingface.co/v1/chat/completions";

/// Default DeepSeek variant served via the HF router.
/// Format `<repo_id>:<provider>` lets HF pin a specific provider.
const DEFAULT_LLM_MODEL: &str = "deepseek-ai/DeepSeek-V4-Pro";

// ── Request Builder (pure, side-effect free) ────────────────────────────────

/// Build the chat-completions request body from the consensus report and news
/// context. Pure helper — performs no network I/O. Exposed so contract tests
/// can verify that every ConsensusReport field is correctly interpolated
/// into the user prompt before it leaves the process.
pub fn build_request_body(
    symbol: &str,
    consensus: &ConsensusReport,
    news: &str,
    model: &str,
) -> ChatRequest {
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
                content: SYSTEM_PROMPT.to_string(),
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

// ── Resolvers ───────────────────────────────────────────────────────────────

fn first_non_empty(vars: &[&str]) -> Option<String> {
    for v in vars {
        if let Ok(val) = std::env::var(v) {
            if !val.trim().is_empty() {
                return Some(val);
            }
        }
    }
    None
}

fn resolve_endpoint() -> String {
    first_non_empty(&[
        "LLM_API_URL",
        "HF_API_URL",
        "DEEPSEEK_API_URL",
        "NVIDIA_NIM_API_URL",
    ])
    .unwrap_or_else(|| DEFAULT_LLM_URL.to_string())
}

fn resolve_model() -> String {
    first_non_empty(&[
        "LLM_MODEL",
        "HF_MODEL",
        "DEEPSEEK_MODEL",
        "NVIDIA_NIM_MODEL",
    ])
    .unwrap_or_else(|| DEFAULT_LLM_MODEL.to_string())
}

fn resolve_api_key() -> Option<String> {
    if let Some(k) = first_non_empty(&[
        "HF_API_KEY",
        "HUGGINGFACE_API_KEY",
        "HUGGING_FACE_API_KEY",
        "NVIDIA_API_KEY",
        "DEEPSEEK_API_KEY",
    ]) {
        return Some(k);
    }
    if crate::is_test_mode() {
        return Some("TEST_KEY".to_string());
    }
    None
}

/// Mask all but the first 6 chars of an API key for safe logging.
fn mask_key(k: &str) -> String {
    let prefix: String = k.chars().take(6).collect();
    format!("{}…(len={})", prefix, k.chars().count())
}

// ── Public API ──────────────────────────────────────────────────────────────

/// Build the master prompt from the consensus report and news, call the
/// configured LLM provider (Hugging Face router by default), and return a
/// structured `AiExecutionPlan`. Delegates to
/// `generate_deep_quant_plan_with_url` so contract tests can pin the URL.
pub async fn generate_deep_quant_plan(
    symbol: &str,
    consensus: &ConsensusReport,
    news: &str,
) -> Result<AiExecutionPlan, String> {
    let api_url = resolve_endpoint();
    generate_deep_quant_plan_with_url(symbol, consensus, news, &api_url).await
}

/// Same as `generate_deep_quant_plan` but accepts an explicit endpoint URL.
/// Used by the Alpha Crucible test suite to redirect traffic to a mock
/// HTTP server while exercising the *real* code path end-to-end.
pub async fn generate_deep_quant_plan_with_url(
    symbol: &str,
    consensus: &ConsensusReport,
    news: &str,
    api_url: &str,
) -> Result<AiExecutionPlan, String> {
    let t0 = Instant::now();

    // ── Resolve API key ─────────────────────────────────────────────────
    let api_key = match resolve_api_key() {
        Some(k) => k,
        None => {
            error!("[llm] no API key configured (HF_API_KEY / NVIDIA_API_KEY / DEEPSEEK_API_KEY)");
            return Err(
                "LLM API Failure: no API key set in .env (HF_API_KEY, NVIDIA_API_KEY, or DEEPSEEK_API_KEY)"
                    .to_string(),
            );
        }
    };

    let model = resolve_model();

    info!(
        "[llm] step=resolve_config endpoint={} model={} key={}",
        api_url,
        model,
        mask_key(&api_key)
    );

    // ── Construct the request body via the pure builder ─────────────────
    let request_body = build_request_body(symbol, consensus, news, &model);

    info!(
        "[llm] step=prompt_built symbol={} trend={} momentum={} patterns={} strategies={} news_chars={}",
        symbol,
        consensus.trend_score,
        consensus.momentum_state,
        consensus.active_patterns.len(),
        consensus.active_strategies.len(),
        news.len(),
    );

    // ── HTTP client ─────────────────────────────────────────────────────
    let timeout_secs: u64 = std::env::var("LLM_TIMEOUT_SECS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(120);

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(timeout_secs))
        .build()
        .map_err(|e| format!("HTTP client build failed: {}", e))?;

    // Snapshot the request as JSON for the audit log (and tests).
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
        status,
        response_body.len(),
        send_elapsed,
        read_started.elapsed().as_millis()
    );

    // Best-effort JSON parse for the audit record; raw body if not JSON.
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
            status,
            truncate(&response_body, 400)
        );
        return Err(format!(
            "LLM API Failure: provider returned HTTP {} — {}",
            status,
            truncate(&response_body, 400)
        ));
    }

    // ── Parse the API envelope ──────────────────────────────────────────
    let chat_response: ChatResponse = serde_json::from_str(&response_body).map_err(|e| {
        error!(
            "[llm] step=envelope_parse_fail err={} body={}",
            e,
            truncate(&response_body, 200)
        );
        format!(
            "LLM API Failure: malformed envelope — {} | body: {}",
            e,
            truncate(&response_body, 200)
        )
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

    // ── Parse the LLM's JSON output into AiExecutionPlan ────────────────
    let cleaned = content
        .trim()
        .trim_start_matches("```json")
        .trim_start_matches("```")
        .trim_end_matches("```")
        .trim();

    let plan: AiExecutionPlan = serde_json::from_str(cleaned).map_err(|e| {
        error!(
            "[llm] step=plan_parse_fail err={} raw={}",
            e,
            truncate(cleaned, 300)
        );
        format!(
            "LLM API Failure: output is not valid AiExecutionPlan JSON — {} | raw: {}",
            e,
            truncate(cleaned, 300)
        )
    })?;

    // ── Validate bounds ─────────────────────────────────────────────────
    let plan = if plan.conviction_score < 1 || plan.conviction_score > 100 {
        warn!(
            "[llm] step=plan_clamp original_score={} clamped",
            plan.conviction_score
        );
        AiExecutionPlan {
            conviction_score: plan.conviction_score.clamp(1, 100),
            ..plan
        }
    } else {
        plan
    };

    info!(
        "[llm] step=done total_elapsed_ms={} conviction={} plan_preview={}",
        t0.elapsed().as_millis(),
        plan.conviction_score,
        truncate(&plan.execution_plan, 80)
    );

    Ok(plan)
}

// ── Helpers ─────────────────────────────────────────────────────────────────

#[inline]
fn truncate(s: &str, max: usize) -> &str {
    if s.len() <= max {
        s
    } else {
        let mut end = max;
        while end > 0 && !s.is_char_boundary(end) {
            end -= 1;
        }
        &s[..end]
    }
}

/// Render a `reqwest::Error` together with its full `source()` chain.
fn format_reqwest_error(err: &reqwest::Error) -> String {
    use std::error::Error as _;

    let mut parts: Vec<String> = vec![err.to_string()];
    let mut src: Option<&dyn std::error::Error> = err.source();
    let mut depth = 0;
    while let Some(e) = src {
        parts.push(format!("caused by: {}", e));
        src = e.source();
        depth += 1;
        if depth > 8 {
            break;
        }
    }

    let mut tags: Vec<&str> = Vec::new();
    if err.is_timeout() { tags.push("timeout"); }
    if err.is_connect() { tags.push("connect"); }
    if err.is_request() { tags.push("request"); }
    if err.is_body()    { tags.push("body"); }
    if err.is_decode()  { tags.push("decode"); }
    if err.is_redirect(){ tags.push("redirect"); }
    if err.is_status()  { tags.push("status"); }
    if !tags.is_empty() {
        parts.push(format!("kind: [{}]", tags.join(", ")));
    }

    parts.join(" | ")
}
