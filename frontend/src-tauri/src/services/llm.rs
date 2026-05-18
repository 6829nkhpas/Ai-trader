// services/llm.rs — DeepSeek API Bridge (Master Prompt Constructor).
//
// V3 Phase 3: Constructs the elite quant system prompt, interpolates the
// ConsensusReport into a structured user prompt, calls the DeepSeek API,
// and parses the response into a typed AiExecutionPlan.
//
// The system prompt constrains the LLM to output strict JSON with exactly
// three keys — preventing hallucinated fields or free-form prose.

use log::{info, warn, error};
use serde::{Deserialize, Serialize};

use crate::quant::{AiExecutionPlan, ConsensusReport};

// ── DeepSeek API Types ──────────────────────────────────────────────────────

#[derive(Serialize)]
struct ChatMessage {
    role: String,
    content: String,
}

#[derive(Serialize)]
struct ChatRequest {
    model: String,
    messages: Vec<ChatMessage>,
    temperature: f64,
    max_tokens: u32,
    response_format: ResponseFormat,
}

#[derive(Serialize)]
struct ResponseFormat {
    r#type: String,
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

const SYSTEM_PROMPT: &str = "\
You are an Elite Quantitative Portfolio Manager. \
You will be provided with a mathematical consensus report and real-time news for a specific asset. \
You must evaluate if the 'Active Strategies' are valid or traps based on the supporting indicators and news. \
You MUST output strictly in JSON format with exactly three keys: \
'conviction_score' (integer 1-100), \
'setup_validation' (string explaining your reasoning), \
and 'execution_plan' (string detailing entry, invalidation, and targets). \
Do NOT include any text outside the JSON object. Do NOT wrap in markdown code fences. \
Output ONLY the raw JSON object.";

// ── Public API ──────────────────────────────────────────────────────────────

/// Build the master prompt from the consensus report and news, call DeepSeek,
/// and return a structured `AiExecutionPlan`.
///
/// # Arguments
/// * `symbol`    — Trading symbol (e.g., "RELIANCE").
/// * `consensus` — The pre-computed mathematical consensus from the quant engine.
/// * `news`      — Recent news headlines/context string.
///
/// # Errors
/// Returns `Err(String)` if the API key is missing, the HTTP call fails,
/// or the LLM response cannot be parsed into the expected JSON structure.
pub async fn generate_deep_quant_plan(
    symbol: &str,
    consensus: &ConsensusReport,
    news: &str,
) -> Result<AiExecutionPlan, String> {
    // ── Resolve API configuration ───────────────────────────────────────
    let api_key = std::env::var("DEEPSEEK_API_KEY")
        .map_err(|_| "DEEPSEEK_API_KEY not set in .env".to_string())?;

    let api_url = std::env::var("DEEPSEEK_API_URL")
        .unwrap_or_else(|_| "https://api.deepseek.com/v1/chat/completions".to_string());

    let model = std::env::var("DEEPSEEK_MODEL")
        .unwrap_or_else(|_| "deepseek-chat".to_string());

    // ── Construct the user prompt ───────────────────────────────────────
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

    info!("DeepSeek prompt constructed for {} (trend={}, momentum={}, {} patterns, {} strategies)",
        symbol, consensus.trend_score, consensus.momentum_state,
        consensus.active_patterns.len(), consensus.active_strategies.len());

    // ── Build the API request ───────────────────────────────────────────
    let request_body = ChatRequest {
        model,
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
        temperature: 0.3, // Low temperature for deterministic quant output
        max_tokens: 1024,
        response_format: ResponseFormat {
            r#type: "json_object".to_string(),
        },
    };

    // ── Call the DeepSeek API ────────────────────────────────────────────
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| format!("HTTP client build failed: {}", e))?;

    let response = client
        .post(&api_url)
        .header("Authorization", format!("Bearer {}", api_key))
        .header("Content-Type", "application/json")
        .json(&request_body)
        .send()
        .await
        .map_err(|e| format!("DeepSeek API request failed: {}", e))?;

    let status = response.status();
    if !status.is_success() {
        let error_body = response.text().await.unwrap_or_default();
        error!("DeepSeek API returned HTTP {}: {}", status, error_body);
        return Err(format!("DeepSeek API error (HTTP {}): {}", status, error_body));
    }

    let response_body = response
        .text()
        .await
        .map_err(|e| format!("Failed to read DeepSeek response: {}", e))?;

    info!("DeepSeek response received ({} bytes)", response_body.len());

    // ── Parse the API response ──────────────────────────────────────────
    let chat_response: ChatResponse = serde_json::from_str(&response_body)
        .map_err(|e| format!("Failed to parse DeepSeek envelope: {} | body: {}", e, &response_body[..200.min(response_body.len())]))?;

    let content = chat_response
        .choices
        .first()
        .map(|c| c.message.content.clone())
        .ok_or_else(|| "DeepSeek returned empty choices array".to_string())?;

    // ── Parse the LLM's JSON output into AiExecutionPlan ────────────────
    // Strip any accidental markdown fences the model might emit
    let cleaned = content
        .trim()
        .trim_start_matches("```json")
        .trim_start_matches("```")
        .trim_end_matches("```")
        .trim();

    let plan: AiExecutionPlan = serde_json::from_str(cleaned)
        .map_err(|e| {
            warn!("Failed to parse LLM JSON output: {} | raw: {}", e, cleaned);
            format!(
                "LLM output is not valid AiExecutionPlan JSON: {} | raw: {}",
                e,
                &cleaned[..300.min(cleaned.len())]
            )
        })?;

    // ── Validate bounds ─────────────────────────────────────────────────
    if plan.conviction_score < 1 || plan.conviction_score > 100 {
        warn!("LLM conviction_score {} out of bounds, clamping", plan.conviction_score);
        return Ok(AiExecutionPlan {
            conviction_score: plan.conviction_score.clamp(1, 100),
            ..plan
        });
    }

    info!("AiExecutionPlan generated: conviction={}, plan={}...",
        plan.conviction_score,
        &plan.execution_plan[..60.min(plan.execution_plan.len())]);

    Ok(plan)
}
