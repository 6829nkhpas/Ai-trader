// claude.js — Claude AI conviction scorer for the Sentiment Agent.
//
// Takes a raw news article from Marketaux, sends its headline + description to
// Anthropic Claude, and extracts a numeric conviction score (1-100) plus a
// short reasoning snippet that will be published in the NewsSentiment Protobuf.
//
// Design decisions:
//   • Uses claude-3-5-haiku for speed + cost efficiency on high-frequency polling.
//     Switch to claude-3-5-sonnet in ANTHROPIC_MODEL env var for higher fidelity.
//   • Temperature = 0 — deterministic scoring for backtesting reproducibility.
//   • Asks Claude to respond ONLY with a JSON object — easy machine parsing.
//   • If parsing fails the article is skipped (non-fatal).
//
// Required env vars:
//   ANTHROPIC_API_KEY  — your Anthropic API key
//
// Optional env vars:
//   ANTHROPIC_MODEL    — Claude model ID (default: claude-3-5-haiku-20241022)

import Anthropic from '@anthropic-ai/sdk';

// ── Constants ────────────────────────────────────────────────────────────────

const MODEL = process.env.ANTHROPIC_MODEL ?? 'claude-3-5-haiku-20241022';
const MAX_TOKENS = 256; // Score + snippet — short responses only
const TEMPERATURE = 0;  // Deterministic for reproducibility

// ── Lazy singleton client ────────────────────────────────────────────────────

let _client = null;

function getClient() {
  if (!_client) {
    _client = new Anthropic({
      apiKey: process.env.ANTHROPIC_API_KEY,
    });
  }
  return _client;
}

// ── System prompt ────────────────────────────────────────────────────────────

const SYSTEM_PROMPT = `You are a quantitative financial analyst specializing in Indian equities (NSE/BSE).
Your task is to evaluate a news headline and assign a bullish conviction score.

Rules:
1. Score range: 1 (strongly bearish) to 100 (strongly bullish). 50 = neutral.
2. Consider: earnings beats, regulatory approvals, M&A, macro conditions, sector trends.
3. Respond ONLY with a valid JSON object. No markdown, no explanation outside JSON.

Response format (strict):
{"score": <integer 1-100>, "reasoning": "<one sentence max 120 chars>"}`;

// ── scoreArticle ─────────────────────────────────────────────────────────────

/**
 * Sends a single news article to Claude and returns a structured conviction score.
 *
 * @param {string} symbol   - NSE ticker symbol this article is about.
 * @param {Object} article  - Marketaux article object with at minimum `title`.
 * @param {string} article.title        - News headline.
 * @param {string} [article.description] - Article description / lede (optional).
 * @returns {Promise<{score: number, reasoning: string} | null>}
 *   Returns null if the API call fails or Claude's response cannot be parsed.
 */
export async function scoreArticle(symbol, article) {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    console.warn('[claude] ANTHROPIC_API_KEY not set — skipping scoring.');
    return null;
  }

  const headline    = article.title ?? '(no title)';
  const description = article.description ?? '';

  const userMessage =
    `Symbol: ${symbol}\n` +
    `Headline: ${headline}\n` +
    (description ? `Description: ${description.slice(0, 400)}\n` : '');

  console.log(`[claude] Scoring: "${headline.slice(0, 80)}..."`);

  try {
    const client = getClient();

    const response = await client.messages.create({
      model:      MODEL,
      max_tokens: MAX_TOKENS,
      temperature: TEMPERATURE,
      system:     SYSTEM_PROMPT,
      messages: [
        { role: 'user', content: userMessage },
      ],
    });

    // Extract the text content from Claude's response.
    const rawText = response.content
      .filter((block) => block.type === 'text')
      .map((block) => block.text)
      .join('');

    // Parse the JSON response.
    const parsed = JSON.parse(rawText.trim());

    const score = parseInt(parsed.score, 10);

    // Validate score is in the expected range.
    if (isNaN(score) || score < 1 || score > 100) {
      console.warn(`[claude] Score out of range (${parsed.score}) — skipping.`);
      return null;
    }

    const reasoning = String(parsed.reasoning ?? '').slice(0, 120);

    console.log(
      `[claude] symbol=${symbol}  score=${score}  reasoning="${reasoning}"`
    );

    return { score, reasoning };

  } catch (err) {
    // JSON parse errors, network errors, API errors — all non-fatal.
    console.error(
      `[claude] Failed to score article for symbol='${symbol}': ${err.message}`
    );
    return null;
  }
}
