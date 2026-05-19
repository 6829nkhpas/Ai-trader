// llm.rs — Unified LLM Client for the Quant-RAG Agent.
//
// Uses the same three env vars as the rest of the system:
//   LLM_API_URL   — OpenAI-compatible chat/completions endpoint
//   LLM_API_KEY   — Bearer token
//   LLM_MODEL     — Model identifier
//
// Environment:
//   LLM_API_KEY — required. The agent will refuse to start without it.

use reqwest::Client;
use serde_json::{json, Value};
use std::env;
use std::error::Error;
use std::time::Duration;
use tokio::time::sleep;

/// Default endpoint (HuggingFace Inference Router).
const DEFAULT_API_URL: &str = "https://router.huggingface.co/v1/chat/completions";

/// Default model.
const DEFAULT_MODEL: &str = "deepseek-ai/DeepSeek-V3-0324";

pub struct LlmClient {
    client: Client,
    api_key: String,
    api_url: String,
    model: String,
}

impl LlmClient {
    /// Creates a new `LlmClient`, reading `LLM_API_KEY` from the environment.
    ///
    /// # Errors
    /// Returns an error if `LLM_API_KEY` is not set.
    pub fn new() -> Result<Self, Box<dyn Error>> {
        let api_key = env::var("LLM_API_KEY")
            .map_err(|_| "LLM_API_KEY environment variable is not set")?;
        let api_url = env::var("LLM_API_URL")
            .unwrap_or_else(|_| DEFAULT_API_URL.to_string());
        let model = env::var("LLM_MODEL")
            .unwrap_or_else(|_| DEFAULT_MODEL.to_string());
        Ok(Self {
            client: Client::new(),
            api_key,
            api_url,
            model,
        })
    }

    /// Invokes the LLM to generate a market insight for a detected anomaly.
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
            "model": self.model,
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

        // ── Send the request (with retry for 429 rate-limiting) ───────────
        let max_retries: u32 = 3;
        let mut attempt: u32 = 0;
        let response = loop {
            attempt += 1;

            let resp = self
                .client
                .post(&self.api_url)
                .header("Authorization", format!("Bearer {}", self.api_key))
                .header("Content-Type", "application/json")
                .json(&payload)
                .send()
                .await
                .map_err(|e| {
                    format!(
                        "HF LLM HTTP request failed (network/timeout): {}",
                        e
                    )
                })?;

            // ── Rate-limit backoff ────────────────────────────────────────
            if resp.status().as_u16() == 429 && attempt <= max_retries {
                let backoff_secs = 2u64.pow(attempt);
                log::warn!(
                    "[llm] HTTP 429 rate-limited (attempt {}/{}) — backing off {}s",
                    attempt, max_retries, backoff_secs
                );
                sleep(Duration::from_secs(backoff_secs)).await;
                continue;
            }

            // ── Validate HTTP status ─────────────────────────────────────
            let status = resp.status();
            if !status.is_success() {
                let error_body = resp
                    .text()
                    .await
                    .unwrap_or_else(|_| "Unable to read error body".to_string());
                return Err(format!(
                    "HF LLM API returned HTTP {} — {}",
                    status.as_u16(),
                    error_body
                )
                .into());
            }

            break resp;
        };

        // ── Parse the outer response envelope ────────────────────────────
        let json_resp: Value = response.json().await.map_err(|e| {
            format!("LLM response is not valid JSON: {}", e)
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
                    "LLM response missing choices[0].message.content — raw: {}",
                    serde_json::to_string_pretty(&json_resp).unwrap_or_default()
                )
            })?;

        // ── Strip markdown code fences if present ────────────────────────
        let cleaned = content_str.trim();
        let cleaned = if cleaned.starts_with("```") {
            let after_open = cleaned
                .find('\n')
                .map(|i| &cleaned[i + 1..])
                .unwrap_or(cleaned);
            after_open
                .rfind("```")
                .map(|i| &after_open[..i])
                .unwrap_or(after_open)
                .trim()
        } else {
            cleaned
        };

        // ── Parse the inner JSON generated by the LLM ────────────────────
        let insight_json: Value = serde_json::from_str(cleaned).map_err(|e| {
            format!(
                "Failed to parse LLM inner JSON: {} — raw content: {}",
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
            .unwrap_or("No analysis provided.")
            .to_string();

        let sentiment = insight_json
            .get("sentiment_score")
            .and_then(|v| v.as_i64())
            .unwrap_or(50) as i32;

        Ok((headline, analysis, sentiment))
    }
}
