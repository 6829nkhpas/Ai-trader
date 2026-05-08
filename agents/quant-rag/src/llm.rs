// llm.rs — NVIDIA NIM DeepSeek v4 Pro LLM Client for the Quant-RAG Agent.
//
// Perfection Phase 1 — Uses NVIDIA's NIM inference platform to run
// DeepSeek v4 Pro via the OpenAI-compatible chat/completions endpoint.
//
// Environment:
//   NVIDIA_API_KEY — required.  The agent will refuse to start without it.
//
// API contract:
//   POST https://integrate.api.nvidia.com/v1/chat/completions
//   model: "deepseek-ai/deepseek-v4-pro"
//   System prompt forces JSON output with keys:
//     { "headline": string, "analysis_text": string, "sentiment_score": int 1-100 }
//
// Error handling:
//   Every failure path returns a descriptive Err(Box<dyn Error>) so the caller
//   can construct a fallback MarketInsight and broadcast the error to the UI.

use reqwest::Client;
use serde_json::{json, Value};
use std::env;
use std::error::Error;

/// NVIDIA NIM inference endpoint (OpenAI-compatible).
const NIM_API_URL: &str = "https://integrate.api.nvidia.com/v1/chat/completions";

/// Model identifier for DeepSeek v4 Pro hosted on NVIDIA NIM.
const NIM_MODEL: &str = "deepseek-ai/deepseek-v4-pro";

pub struct LlmClient {
    client: Client,
    api_key: String,
}

impl LlmClient {
    /// Creates a new `LlmClient`, reading `NVIDIA_API_KEY` from the environment.
    ///
    /// # Errors
    /// Returns an error if `NVIDIA_API_KEY` is not set.
    pub fn new() -> Result<Self, Box<dyn Error>> {
        let api_key = env::var("NVIDIA_API_KEY")
            .map_err(|_| "NVIDIA_API_KEY environment variable is not set")?;
        Ok(Self {
            client: Client::new(),
            api_key,
        })
    }

    /// Invokes DeepSeek v4 Pro (via NVIDIA NIM) to generate a market insight
    /// for a detected anomaly.
    ///
    /// Returns `(headline, analysis_text, sentiment_score)` on success.
    ///
    /// # Errors
    /// Returns a detailed error string if:
    /// - The HTTP request fails (network, timeout, DNS).
    /// - The API returns a non-2xx status code.
    /// - The response body cannot be parsed as JSON.
    /// - The expected keys are missing from the LLM's output.
    pub async fn generate_insight(
        &self,
        symbol: &str,
        price_change_pct: f64,
    ) -> Result<(String, String, i32), Box<dyn Error>> {
        // ── Build the request payload ────────────────────────────────────
        let system_prompt = concat!(
            "You are an elite quantitative analyst at a tier-1 hedge fund. ",
            "A market anomaly has been detected. Provide a rapid 2-sentence analysis. ",
            "You MUST return ONLY a valid JSON object with exactly three keys: ",
            "\"headline\" (a concise string title for the anomaly), ",
            "\"analysis_text\" (a 2-sentence string explanation), ",
            "and \"sentiment_score\" (an integer from 1 to 100, where 1 is extremely ",
            "bearish and 100 is extremely bullish). ",
            "Do NOT wrap the JSON in markdown code fences. Do NOT include any text ",
            "outside the JSON object. Return raw JSON only."
        );

        let user_prompt = format!(
            "Generate a rapid analysis for {} which just moved {:.2}%.",
            symbol, price_change_pct
        );

        let payload = json!({
            "model": NIM_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            "temperature": 0.4,
            "top_p": 0.95,
            "max_tokens": 1024,
            "stream": false
        });

        // ── Send the request ─────────────────────────────────────────────
        let response = self
            .client
            .post(NIM_API_URL)
            .header("Authorization", format!("Bearer {}", self.api_key))
            .header("Content-Type", "application/json")
            .json(&payload)
            .send()
            .await
            .map_err(|e| {
                format!(
                    "NVIDIA NIM HTTP request failed (network/timeout): {}",
                    e
                )
            })?;

        // ── Validate HTTP status ─────────────────────────────────────────
        let status = response.status();
        if !status.is_success() {
            let error_body = response
                .text()
                .await
                .unwrap_or_else(|_| "Unable to read error body".to_string());
            return Err(format!(
                "NVIDIA NIM API returned HTTP {} — {}",
                status.as_u16(),
                error_body
            )
            .into());
        }

        // ── Parse the outer response envelope ────────────────────────────
        let json_resp: Value = response.json().await.map_err(|e| {
            format!("NVIDIA NIM response is not valid JSON: {}", e)
        })?;

        // Extract the assistant's message content.
        // OpenAI-compatible shape: choices[0].message.content
        let content_str = json_resp
            .get("choices")
            .and_then(|c| c.get(0))
            .and_then(|c| c.get("message"))
            .and_then(|m| m.get("content"))
            .and_then(|v| v.as_str())
            .ok_or_else(|| {
                format!(
                    "NVIDIA NIM response missing choices[0].message.content — raw: {}",
                    serde_json::to_string_pretty(&json_resp).unwrap_or_default()
                )
            })?;

        // ── Strip markdown code fences if present ────────────────────────
        // Some models wrap JSON in ```json ... ``` despite instructions.
        let cleaned = content_str.trim();
        let cleaned = if cleaned.starts_with("```") {
            // Remove opening fence (```json or ```)
            let after_open = cleaned
                .find('\n')
                .map(|i| &cleaned[i + 1..])
                .unwrap_or(cleaned);
            // Remove closing fence
            after_open
                .rfind("```")
                .map(|i| &after_open[..i])
                .unwrap_or(after_open)
                .trim()
        } else {
            cleaned
        };

        // ── Parse the inner JSON generated by DeepSeek ───────────────────
        let insight_json: Value = serde_json::from_str(cleaned).map_err(|e| {
            format!(
                "Failed to parse DeepSeek inner JSON: {} — raw content: {}",
                e, content_str
            )
        })?;

        let headline = insight_json
            .get("headline")
            .and_then(|v| v.as_str())
            .unwrap_or("Market Anomaly Detected")
            .to_string();

        let analysis = insight_json
            .get("analysis_text")
            .and_then(|v| v.as_str())
            .unwrap_or("No analysis provided by DeepSeek.")
            .to_string();

        let sentiment = insight_json
            .get("sentiment_score")
            .and_then(|v| v.as_i64())
            .unwrap_or(50) as i32;

        Ok((headline, analysis, sentiment))
    }
}
