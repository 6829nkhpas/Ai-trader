// analyzer.js — Claude LLM wrapper for the Sentiment Agent.
//
// Accepts a symbol and an array of headline strings, submits them to
// Anthropic's Claude API, and returns a quantitative conviction score plus
// a one-sentence reasoning snippet.
//
// Prompt engineering decisions:
//   • Model: claude-3-haiku-20240307 — optimised for speed and cost on
//     high-frequency polling workloads.
//   • Temperature: 0 — deterministic output, critical for backtesting
//     reproducibility; the same headlines always produce the same score.
//   • System message frames Claude as a high-frequency trading sentiment
//     analyzer and mandates strict raw JSON output (no markdown, no prose).
//   • Output schema is enforced textually and validated programmatically:
//       { "conviction_score": <int 1-100>, "reasoning_snippet": "<string>" }
//   • If parsing or validation fails the function throws — callers should
//     handle the error and treat the result as non-fatal.
//
// Required env vars:
//   ANTHROPIC_API_KEY  — your Anthropic API key

import Anthropic from '@anthropic-ai/sdk';

// ── Constants ─────────────────────────────────────────────────────────────────

const MODEL      = 'claude-3-haiku-20240307';
const MAX_TOKENS = 256; // Score + 1-sentence snippet — very short response
const TEMPERATURE = 0;  // Deterministic for reproducibility

// ── System prompt ─────────────────────────────────────────────────────────────
//
// Strict role + output format instructions.
// Claude is explicitly forbidden from wrapping output in markdown code fences
// or adding any conversational text — machine-parseable raw JSON only.

const SYSTEM_PROMPT = `You are a high-frequency trading sentiment analyzer specializing in Indian equities (NSE/BSE).

Your job is to analyze a batch of news headlines for a given stock symbol and output a single aggregate bullish conviction score.

Rules:
1. Score range: 1 (extremely bearish) to 100 (extremely bullish). 50 = fully neutral.
2. Weight your score based on recency, magnitude, and market-moving potential of the headlines.
3. Respond ONLY with a raw JSON object — no markdown, no code fences, no explanation outside the JSON.

Required output schema (exact field names, no extras):
{"conviction_score": <integer 1-100>, "reasoning_snippet": "<one sentence, max 150 chars>"}`;

// ── Anthropic client singleton ────────────────────────────────────────────────

let _client = null;

/**
 * Returns the shared Anthropic client, initialising it on first call.
 * Lazy initialisation so the module can be imported without a valid API key
 * in environments that don't require Claude (e.g. unit tests).
 *
 * @returns {Anthropic}
 */
function getClient() {
  if (!_client) {
    _client = new Anthropic({
      apiKey: process.env.ANTHROPIC_API_KEY,
    });
  }
  return _client;
}

// ── analyzeSentiment ──────────────────────────────────────────────────────────

/**
 * Sends a batch of headlines for the given symbol to Claude and returns a
 * structured conviction score with a reasoning snippet.
 *
 * @param {string}   symbol         - NSE ticker symbol (e.g. "TATA", "INFY").
 * @param {string[]} headlinesArray - Array of headline strings to analyze.
 * @returns {Promise<{conviction_score: number, reasoning_snippet: string}>}
 *   Resolves to the parsed JSON object from Claude.
 *   Rejects if the API call fails or the response cannot be parsed/validated.
 *
 * @example
 * const result = await analyzeSentiment('TATA', [
 *   'Tata Motors Q4 profit surges 35% on EV demand',
 *   'Tata Steel raises capex guidance amid commodity rally',
 * ]);
 * // { conviction_score: 82, reasoning_snippet: "Strong earnings and capex signal robust bullish momentum." }
 */
export async function analyzeSentiment(symbol, headlinesArray) {
  if (!process.env.ANTHROPIC_API_KEY) {
    throw new Error('[analyzer] ANTHROPIC_API_KEY is not set.');
  }

  if (!headlinesArray || headlinesArray.length === 0) {
    throw new Error('[analyzer] headlinesArray must contain at least one headline.');
  }

  // Build the numbered headlines list for the user message.
  const numberedHeadlines = headlinesArray
    .map((h, i) => `${i + 1}. ${h}`)
    .join('\n');

  const userMessage =
    `Symbol: ${symbol}\n` +
    `Headlines (${headlinesArray.length}):\n` +
    numberedHeadlines;

  console.log(
    `[analyzer] Calling Claude (${MODEL}) for symbol=${symbol} ` +
    `with ${headlinesArray.length} headline(s)...`
  );

  const client = getClient();

  const response = await client.messages.create({
    model:       MODEL,
    max_tokens:  MAX_TOKENS,
    temperature: TEMPERATURE,
    system:      SYSTEM_PROMPT,
    messages: [
      { role: 'user', content: userMessage },
    ],
  });

  // Extract and join all text blocks from the response.
  const rawText = response.content
    .filter((block) => block.type === 'text')
    .map((block) => block.text)
    .join('');

  // Parse Claude's JSON response.
  let parsed;
  try {
    parsed = JSON.parse(rawText.trim());
  } catch (parseErr) {
    throw new Error(
      `[analyzer] Failed to parse Claude response as JSON. ` +
      `Raw output: "${rawText.slice(0, 200)}"`
    );
  }

  // Validate conviction_score.
  const score = parseInt(parsed.conviction_score, 10);
  if (isNaN(score) || score < 1 || score > 100) {
    throw new Error(
      `[analyzer] conviction_score out of valid range [1-100]: ${parsed.conviction_score}`
    );
  }

  // Validate reasoning_snippet.
  if (typeof parsed.reasoning_snippet !== 'string') {
    throw new Error('[analyzer] reasoning_snippet must be a string.');
  }

  const result = {
    conviction_score:   score,
    reasoning_snippet: parsed.reasoning_snippet.slice(0, 150),
  };

  console.log(
    `[analyzer] symbol=${symbol}  conviction_score=${result.conviction_score}  ` +
    `reasoning="${result.reasoning_snippet}"`
  );

  return result;
}
