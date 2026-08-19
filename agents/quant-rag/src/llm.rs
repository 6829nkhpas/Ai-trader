// llm.rs — Unified LLM Client for the Quant-RAG Agent.
//
// Uses the same three env vars as the rest of the system:
//   LLM_API_URL   — OpenAI-compatible chat/completions endpoint
//   LLM_API_KEY   — Bearer token
//   LLM_MODEL     — Model identifier
//
// Environment:
//   LLM_API_KEY — required. The agent will refuse to start without it.

use crate::metrics::QuantRagMetrics;
use reqwest::Client;
use serde_json::{json, Value};
use std::env;
use std::error::Error;
use std::time::Duration;
use tokio::time::sleep;

/// Default endpoint (FreeModel — OpenAI-compatible).
const DEFAULT_API_URL: &str = "https://api.freemodel.dev/v1/chat/completions";

/// Default model.
const DEFAULT_MODEL: &str = "deepseek-ai/DeepSeek-V3-0324";

pub struct LlmClient {
    client: Client,
    api_key: String,
    api_url: String,
    model: String,
    /// Reasoning effort level (low|medium|high|xhigh); empty = omit.
    effort: String,
    /// JSON body key carrying the effort value (default: reasoning_effort).
    effort_field: String,
    /// Prometheus handle. Held here rather than in the caller because the five
    /// failure kinds below are only distinguishable at the point each is
    /// detected — by the time an error has been flattened into a string, the
    /// only way to classify it is to match on its wording, which silently
    /// mislabels every failure the day someone edits a message.
    metrics: QuantRagMetrics,
}

impl LlmClient {
    /// Creates a new `LlmClient`, reading `LLM_API_KEY` from the environment.
    ///
    /// # Errors
    /// Returns an error if `LLM_API_KEY` is not set.
    pub fn new(metrics: QuantRagMetrics) -> Result<Self, Box<dyn Error>> {
        let api_key = env::var("LLM_API_KEY")
            .map_err(|_| "LLM_API_KEY environment variable is not set")?;
        let api_url = env::var("LLM_API_URL")
            .unwrap_or_else(|_| DEFAULT_API_URL.to_string());
        let model = env::var("LLM_MODEL")
            .unwrap_or_else(|_| DEFAULT_MODEL.to_string());
        let effort = env::var("LLM_EFFORT")
            .map(|s| s.trim().to_string())
            .unwrap_or_default();
        let effort_field = env::var("LLM_EFFORT_FIELD")
            .ok()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "reasoning_effort".to_string());
        Ok(Self {
            client: Client::new(),
            api_key,
            api_url,
            model,
            effort,
            effort_field,
            metrics,
        })
    }

    /// The model identifier actually in use, after `LLM_MODEL` resolution.
    ///
    /// Exposed so the process can log what it resolved to at startup. A
    /// configurable model is only auditable if the resolved value is recorded
    /// somewhere — see `docs/compliance/AI_MODEL_GOVERNANCE.md` §2.
    pub fn model(&self) -> &str {
        &self.model
    }

    /// The endpoint actually in use, after `LLM_API_URL` resolution. Never
    /// includes the API key.
    pub fn endpoint(&self) -> &str {
        &self.api_url
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
        active_pattern: Option<String>,
    ) -> Result<(String, String, i32), Box<dyn Error>> {
        // ── Build the request payload ────────────────────────────────────
        let mut system_prompt = String::from(concat!(
            "You are an elite quantitative analyst at a tier-1 hedge fund. ",
            "A market anomaly has been detected. Provide a rapid 2-sentence analysis. ",
            "You MUST return ONLY a valid JSON object with exactly three keys: ",
            "\"headline\" (a concise string title for the anomaly), ",
            "\"analysis_text\" (a 2-sentence string explanation), ",
            "and \"sentiment_score\" (an integer from 1 to 100, where 1 is extremely ",
            "bearish and 100 is extremely bullish). ",
            "Do NOT wrap the JSON in markdown code fences. Do NOT include any text ",
            "outside the JSON object. Return raw JSON only."
        ));

        if let Some(ref pattern_json) = active_pattern {
            system_prompt.push_str(&format!(
                "\n\nActive Market Structure Pattern:\n{}\nUse this exact geometric context for your market analysis.",
                pattern_json
            ));
        }

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

        // Attach the reasoning-effort level (e.g. low|medium|high|xhigh) under the
        // configured body key when set. Omitted entirely when LLM_EFFORT is blank
        // so plain (non-reasoning) models receive an unchanged payload.
        let mut payload = payload;
        if !self.effort.is_empty() {
            if let Value::Object(ref mut map) = payload {
                map.insert(
                    self.effort_field.clone(),
                    Value::String(self.effort.clone()),
                );
            }
        }

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
                .header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
                .json(&payload)
                .send()
                .await
                .map_err(|e| {
                    // The request never reached the provider: DNS, TCP, TLS or
                    // timeout. Distinguished from http_status because this one
                    // can be our own egress rather than their outage.
                    self.metrics.llm_failed("network");
                    format!(
                        "HF LLM HTTP request failed (network/timeout): {}",
                        e
                    )
                })?;

            // ── Rate-limit backoff ────────────────────────────────────────
            if resp.status().as_u16() == 429 && attempt <= max_retries {
                let backoff_secs = 2u64.pow(attempt);
                // Counted separately from llm_errors_total: a retry that
                // eventually succeeds is not a failure, but a rising retry rate
                // against flat errors is the warning that arrives before the
                // failures do.
                self.metrics.llm_retried();
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
                // Reached the provider and was refused. Note this also catches
                // a 429 that outlived the retry budget, which is the case worth
                // seeing: throttling the agent could no longer absorb.
                self.metrics.llm_failed("http_status");
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
            // A 2xx whose body is not JSON at all — usually a proxy or WAF page
            // returned in place of the provider's response.
            self.metrics.llm_failed("invalid_json");
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
                // Valid JSON in an unexpected shape: a provider-side error
                // object, or a content filter that dropped the completion.
                self.metrics.llm_failed("missing_content");
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
            // The model answered but ignored the JSON contract. Unlike the
            // provider-side kinds above, this does not clear on its own — it
            // needs a prompt or model change, so it must be visible as its own
            // series rather than folded into a general error count.
            self.metrics.llm_failed("malformed_output");
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
