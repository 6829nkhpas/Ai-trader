// services/llm.rs — DeepSeek API Bridge (Master Prompt Constructor).
//
// V3 Phase 3: Constructs the elite quant system prompt, interpolates the
// ConsensusReport into a structured user prompt, calls the DeepSeek API,
// and parses the response into a typed AiExecutionPlan.
//
// The system prompt constrains the LLM to output strict JSON with exactly
// three keys — preventing hallucinated fields or free-form prose.
//
// Alpha Crucible additions (V3):
//   • A pure helper, `build_request_body`, exposes the exact request payload
//     so contract tests can assert that the ConsensusReport strings are
//     interpolated correctly into the prompt.
//   • `generate_deep_quant_plan_with_url` accepts an arbitrary base URL,
//     letting `tests/api_tests.rs` redirect calls at a `mockito::Server`.
//   • Every outbound DeepSeek transaction is forwarded to `audit_logger`,
//     producing a verifiable on-disk record of the wire traffic.

use log::{info, warn, error};
use serde::{Deserialize, Serialize};

use crate::quant::{AiExecutionPlan, ConsensusReport};
use crate::services::audit_logger;

// ── DeepSeek API Types ──────────────────────────────────────────────────────

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
    pub response_format: ResponseFormat,
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

// ── Request Builder (pure, side-effect free) ────────────────────────────────

/// Build the full DeepSeek `ChatRequest` from the consensus report and news
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
        response_format: ResponseFormat {
            kind: "json_object".to_string(),
        },
    }
}

// ── Public API ──────────────────────────────────────────────────────────────

/// Build the master prompt from the consensus report and news, call DeepSeek,
/// and return a structured `AiExecutionPlan`.
///
/// Reads the production endpoint from `DEEPSEEK_API_URL` (with fallback) and
/// delegates to `generate_deep_quant_plan_with_url`.
pub async fn generate_deep_quant_plan(
    symbol: &str,
    consensus: &ConsensusReport,
    news: &str,
) -> Result<AiExecutionPlan, String> {
    let api_url = std::env::var("DEEPSEEK_API_URL")
        .unwrap_or_else(|_| "https://api.deepseek.com/v1/chat/completions".to_string());
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
    // ── Resolve API key (allow blank in test mode) ──────────────────────
    let api_key = std::env::var("DEEPSEEK_API_KEY").unwrap_or_else(|_| {
        if crate::is_test_mode() { "TEST_KEY".to_string() } else { String::new() }
    });
    if api_key.is_empty() {
        return Err("DEEPSEEK_API_KEY not set in .env".to_string());
    }

    let model = std::env::var("DEEPSEEK_MODEL")
        .unwrap_or_else(|_| "deepseek-chat".to_string());

    // ── Construct the request body via the pure builder ─────────────────
    let request_body = build_request_body(symbol, consensus, news, &model);

    info!(
        "DeepSeek prompt constructed for {} (trend={}, momentum={}, {} patterns, {} strategies)",
        symbol,
        consensus.trend_score,
        consensus.momentum_state,
        consensus.active_patterns.len(),
        consensus.active_strategies.len(),
    );

    // ── HTTP client ─────────────────────────────────────────────────────
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| format!("HTTP client build failed: {}", e))?;

    // Snapshot the request as JSON for the audit log (and tests).
    let req_json = serde_json::to_value(&request_body).unwrap_or(serde_json::Value::Null);

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
            audit_logger::log_api_error(
                &format!("POST {}", api_url),
                &req_json,
                &format!("transport error: {}", e),
            );
            return Err(format!("LLM API Failure: DeepSeek request failed: {}", e));
        }
    };

    let status = response.status();
    let response_body = response.text().await.unwrap_or_default();

    // Best-effort JSON parse for the audit record; raw body if not JSON.
    let res_json: serde_json::Value =
        serde_json::from_str(&response_body).unwrap_or_else(|_| serde_json::Value::String(response_body.clone()));

    audit_logger::log_api_transaction(
        &format!("POST {}", api_url),
        &req_json,
        &res_json,
        status.as_u16(),
    );

    if !status.is_success() {
        error!("DeepSeek API returned HTTP {}: {}", status, response_body);
        return Err(format!(
            "LLM API Failure: DeepSeek returned HTTP {} — {}",
            status,
            truncate(&response_body, 200)
        ));
    }

    info!("DeepSeek response received ({} bytes)", response_body.len());

    // ── Parse the API envelope ──────────────────────────────────────────
    let chat_response: ChatResponse = serde_json::from_str(&response_body).map_err(|e| {
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
        .ok_or_else(|| "LLM API Failure: DeepSeek returned empty choices array".to_string())?;

    // ── Parse the LLM's JSON output into AiExecutionPlan ────────────────
    let cleaned = content
        .trim()
        .trim_start_matches("```json")
        .trim_start_matches("```")
        .trim_end_matches("```")
        .trim();

    let plan: AiExecutionPlan = serde_json::from_str(cleaned).map_err(|e| {
        warn!("Failed to parse LLM JSON output: {} | raw: {}", e, cleaned);
        format!(
            "LLM API Failure: output is not valid AiExecutionPlan JSON — {} | raw: {}",
            e,
            truncate(cleaned, 300)
        )
    })?;

    // ── Validate bounds ─────────────────────────────────────────────────
    if plan.conviction_score < 1 || plan.conviction_score > 100 {
        warn!(
            "LLM conviction_score {} out of bounds, clamping",
            plan.conviction_score
        );
        return Ok(AiExecutionPlan {
            conviction_score: plan.conviction_score.clamp(1, 100),
            ..plan
        });
    }

    info!(
        "AiExecutionPlan generated: conviction={}, plan={}...",
        plan.conviction_score,
        truncate(&plan.execution_plan, 60)
    );

    Ok(plan)
}

// ── Helpers ─────────────────────────────────────────────────────────────────

#[inline]
fn truncate(s: &str, max: usize) -> &str {
    if s.len() <= max {
        s
    } else {
        // Walk back to a char boundary to avoid panicking on multi-byte cuts.
        let mut end = max;
        while end > 0 && !s.is_char_boundary(end) {
            end -= 1;
        }
        &s[..end]
    }
}
