// analyzer.js — LLM wrapper for the Sentiment Agent.
//
// Two analyzers live here:
//
//   • analyzeStrategicSentiment(symbol, { profile, financials, categorizedNews })
//       The STRATEGIC engine. Synthesizes a structured, materiality-weighted
//       verdict grounded in the company's business context + category-tagged
//       news. Returns a rich object (conviction_score, label, thesis, drivers,
//       risks, horizon, confidence) — validated and clamped.
//
//   • analyzeSentiment(symbol, headlines)
//       Backward-compatible shim. Kept so the legacy {conviction_score,
//       reasoning_snippet} contract (Kafka publisher, older callers) keeps
//       working. Implemented as a thin wrapper over the strategic engine.
//
// Both submit to an OpenAI-compatible chat completions endpoint, at temperature
// 0 for deterministic, backtest-reproducible output, and parse strict raw JSON
// (code fences stripped defensively).
//
// Required env vars:
//   LLM_API_KEY  — your LLM provider API token.
//
// Optional env vars:
//   LLM_MODEL    — model ID (default: deepseek-ai/DeepSeek-V3-0324).
//   LLM_API_URL  — endpoint URL (default: https://api.freemodel.dev/v1/chat/completions).
//
// Defaults above are the DEFAULT_MODEL / DEFAULT_URL constants below; keep them
// in step. This header previously named a HuggingFace router that the code has
// never pointed at — see docs/compliance/BRAND_GUIDELINES.md §4.0.

import { metrics } from './metrics.js';

// ── Constants ─────────────────────────────────────────────────────────────────

const DEFAULT_URL = 'https://api.freemodel.dev/v1/chat/completions';
const DEFAULT_MODEL = 'deepseek-ai/DeepSeek-V3-0324';
const MAX_TOKENS = 256;
const STRATEGIC_MAX_TOKENS = 700; // richer structured verdict needs more room.
const TEMPERATURE = 0;

// ── System prompts ─────────────────────────────────────────────────────────────

const SYSTEM_PROMPT = `You are a high-frequency trading sentiment analyzer specializing in Indian equities (NSE/BSE).

Your job is to analyze a batch of news headlines for a given stock symbol and output a single aggregate bullish conviction score.

Rules:
1. Score range: 1 (extremely bearish) to 100 (extremely bullish). 50 = fully neutral.
2. Weight your score based on recency, magnitude, and market-moving potential of the headlines.
3. Respond ONLY with a raw JSON object — no markdown, no code fences, no explanation outside the JSON.

Required output schema (exact field names, no extras):
{"conviction_score": <integer 1-100>, "reasoning_snippet": "<one sentence, max 150 chars>"}`;

const STRATEGIC_SYSTEM_PROMPT = `You are a buy-side equity research analyst specializing in Indian equities (NSE/BSE). You produce a STRATEGIC, materiality-weighted sentiment verdict for a single stock.

You are given:
  • The company PROFILE (name, sector/industry, exchange, market cap, website).
  • BASIC FINANCIALS context (P/E, 52-week high/low, margins, growth) when available.
  • CATEGORIZED NEWS — recent articles each tagged with a materiality bucket:
      EARNINGS, CORPORATE_ACTIONS, REGULATORY, MANAGEMENT, SECTOR_MACRO.

How to think:
1. MATERIALITY FIRST. Weight each item by how much it actually moves the stock.
   Earnings/results, regulatory actions (SEBI/RBI/penalties/approvals), and M&A /
   large orders move stocks MORE than routine coverage, listicles, or price recaps.
2. RELEVANCE. Use the company profile + financials to judge whether an item is
   truly about THIS business. Treat stale, tangential, or generic-market items as
   LOW weight. An item mentioning the company only in passing is low weight.
3. SYNTHESIZE, don't tally. Combine the weighted items into one directional view.
4. If financials show the stock near its 52-week high/low or stretched valuation,
   factor that into risk framing — but news catalysts dominate the score.

Scoring:
  • conviction_score: integer 1 (extremely bearish) .. 100 (extremely bullish); 50 = neutral.
  • label: "Bullish" | "Bearish" | "Neutral" (consistent with the score).
  • confidence: 0..1 — lower when news is sparse, stale, or low-materiality.
  • horizon: "intraday" | "short-term" | "medium-term" — the timeframe the catalysts act on.

Respond with ONLY a raw JSON object — no markdown, no code fences, no prose outside the JSON.

Required output schema (exact field names):
{
  "conviction_score": <integer 1-100, 50=neutral>,
  "label": "Bullish" | "Bearish" | "Neutral",
  "thesis": "<2-3 sentence synthesis grounded in the news + business context>",
  "drivers": [ { "category": "<bucket>", "headline": "<string>", "impact": "bullish" | "bearish" | "neutral", "weight": <0..1> } ],
  "risks": ["<string>", ...],
  "horizon": "intraday" | "short-term" | "medium-term",
  "confidence": <0..1>
}`;

// ── Internal helpers ──────────────────────────────────────────────────────────

/**
 * Resolve the LLM endpoint + model + key, throwing if the key is unset.
 * @returns {{apiKey: string, endpoint: string, model: string}}
 */
function resolveLlmConfig() {
  const apiKey = process.env.LLM_API_KEY;
  if (!apiKey) {
    throw new Error('[analyzer] LLM_API_KEY is not set.');
  }
  return {
    apiKey,
    endpoint: process.env.LLM_API_URL || DEFAULT_URL,
    model:    process.env.LLM_MODEL || DEFAULT_MODEL,
    // Reasoning effort (low|medium|high|xhigh) + its body key. Empty effort => omit.
    effort:      (process.env.LLM_EFFORT || '').trim(),
    effortField: (process.env.LLM_EFFORT_FIELD || 'reasoning_effort').trim(),
  };
}

/**
 * Strip markdown code fences and parse a model response as JSON.
 * @param {string} rawText - The raw model content.
 * @returns {Object} The parsed object.
 * @throws {Error} If the cleaned text isn't valid JSON.
 */
function parseJsonResponse(rawText) {
  const cleaned = (rawText ?? '')
    .replace(/```json\s*/g, '')
    .replace(/```\s*/g, '')
    .trim();
  return JSON.parse(cleaned);
}

/** Clamp a value to an integer in [min, max], or return fallback when invalid. */
function clampInt(value, min, max, fallback) {
  const n = parseInt(value, 10);
  if (Number.isNaN(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

/** Clamp a value to a float in [min, max], or return fallback when invalid. */
function clampFloat(value, min, max, fallback) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

/** Normalize an impact string to one of bullish|bearish|neutral. */
function normalizeImpact(value) {
  const v = String(value ?? '').toLowerCase();
  if (v === 'bullish' || v === 'bearish' || v === 'neutral') return v;
  return 'neutral';
}

/** Normalize a horizon string to one of the allowed values (default short-term). */
function normalizeHorizon(value) {
  const v = String(value ?? '').toLowerCase();
  if (v === 'intraday' || v === 'short-term' || v === 'medium-term') return v;
  return 'short-term';
}

/** Derive a label from a conviction score (>=60 Bullish, <=40 Bearish, else Neutral). */
function labelFromScore(score) {
  if (score >= 60) return 'Bullish';
  if (score <= 40) return 'Bearish';
  return 'Neutral';
}

/** Normalize a label to Bullish|Bearish|Neutral, falling back to score-derived. */
function normalizeLabel(value, score) {
  const v = String(value ?? '').toLowerCase();
  if (v === 'bullish') return 'Bullish';
  if (v === 'bearish') return 'Bearish';
  if (v === 'neutral') return 'Neutral';
  return labelFromScore(score);
}

/**
 * Build the neutral fallback verdict used when there are no fresh catalysts.
 * @param {string} symbol
 * @returns {Object} A valid strategic verdict with a low-confidence neutral stance.
 */
function neutralVerdict(symbol) {
  return {
    conviction_score: 50,
    label:            'Neutral',
    thesis:           'No fresh material catalysts; sentiment driven by baseline business posture.',
    drivers:          [],
    risks:            [],
    horizon:          'short-term',
    confidence:       0.2,
  };
}

/**
 * POST a chat-completions request and return the raw assistant text.
 * @param {{endpoint: string, apiKey: string, model: string}} cfg
 * @param {string} systemPrompt
 * @param {string} userMessage
 * @param {number} maxTokens
 * @returns {Promise<string>} The raw assistant message content.
 * @throws {Error} On non-2xx HTTP status.
 */
async function callLlm(cfg, systemPrompt, userMessage, maxTokens) {
  const response = await fetch(cfg.endpoint, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${cfg.apiKey}`,
      'Content-Type': 'application/json',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    },
    body: JSON.stringify({
      model: cfg.model,
      // Some gateways (omniroute) default to Server-Sent Events and return
      // `text/event-stream` unless streaming is EXPLICITLY disabled, which
      // makes response.json() throw on every call. Ask for a single JSON body.
      stream: false,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userMessage },
      ],
      max_tokens:  maxTokens,
      temperature: TEMPERATURE,
      ...(cfg.effort ? { [cfg.effortField]: cfg.effort } : {}),
    }),
  });

  if (!response.ok) {
    const errText = await response.text().catch(() => '');
    const err = new Error(
      `[analyzer] LLM API returned HTTP ${response.status}: ${errText.slice(0, 200)}`
    );
    // Carried as a property so callers can tell "the provider answered and
    // refused" from "the request never completed" without re-parsing this
    // message. `fetch` rejects with a TypeError that has no status, so the
    // presence of this field is the whole classification.
    err.status = response.status;
    throw err;
  }

  const data = await readCompletion(response);
  return data.choices?.[0]?.message?.content ?? '';
}

/**
 * Read a chat-completions response as a single completion object, accepting
 * EITHER a plain JSON body or a Server-Sent Events stream.
 *
 * `callLlm` sends `stream: false`, so the JSON path is the normal one. The SSE
 * path exists because a gateway that ignores that flag would otherwise make
 * `response.json()` throw on every single call — the failure mode that silently
 * killed every sentiment verdict ("Unexpected token 'd', \"data: {\"id\"...").
 * Tolerating both means a gateway swap (omniroute <-> OpenRouter) cannot
 * reintroduce it.
 *
 * SSE chunks are reassembled into the non-streaming shape by concatenating
 * `choices[0].delta.content`, so the caller sees one uniform object either way.
 * @param {Response} response
 * @returns {Promise<Object>} A chat-completion-shaped object.
 */
async function readCompletion(response) {
  const raw = await response.text();
  const trimmed = raw.trimStart();

  // Non-streaming: a normal JSON body.
  if (!trimmed.startsWith('data:')) {
    return JSON.parse(raw);
  }

  // Streaming: fold `data:` frames into a single message. `[DONE]` is the
  // terminator sentinel and is not JSON; malformed frames are skipped rather
  // than aborting a response that is otherwise complete.
  let content = '';
  let finishReason = null;
  for (const line of raw.split(/\r?\n/)) {
    if (!line.startsWith('data:')) continue;
    const payload = line.slice(5).trim();
    if (!payload || payload === '[DONE]') continue;
    let frame;
    try {
      frame = JSON.parse(payload);
    } catch {
      continue;
    }
    const choice = frame.choices?.[0];
    if (!choice) continue;
    // `delta.content` is the streaming field; `message.content` appears when a
    // gateway emits one non-incremental frame.
    content += choice.delta?.content ?? choice.message?.content ?? '';
    if (choice.finish_reason) finishReason = choice.finish_reason;
  }

  return {
    choices: [{ index: 0, finish_reason: finishReason, message: { role: 'assistant', content } }],
  };
}

/**
 * Render the company profile + financials into a compact context block for the
 * strategic prompt. Gracefully notes when either is unavailable.
 * @param {Object|null} profile
 * @param {Object|null} financials
 * @returns {string}
 */
function renderContext(profile, financials) {
  const lines = [];

  if (profile) {
    lines.push('COMPANY PROFILE:');
    if (profile.name)      lines.push(`  name: ${profile.name}`);
    if (profile.industry)  lines.push(`  industry: ${profile.industry}`);
    if (profile.exchange)  lines.push(`  exchange: ${profile.exchange}`);
    if (profile.marketCap != null) lines.push(`  marketCap: ${profile.marketCap}`);
    if (profile.country)   lines.push(`  country: ${profile.country}`);
    if (profile.weburl)    lines.push(`  weburl: ${profile.weburl}`);
  } else {
    lines.push('COMPANY PROFILE: (unavailable — score the news on its own)');
  }

  if (financials) {
    lines.push('BASIC FINANCIALS:');
    if (financials.pe != null)              lines.push(`  pe: ${financials.pe}`);
    if (financials.week52High != null)      lines.push(`  52WeekHigh: ${financials.week52High}`);
    if (financials.week52Low != null)       lines.push(`  52WeekLow: ${financials.week52Low}`);
    if (financials.netProfitMargin != null) lines.push(`  netProfitMargin: ${financials.netProfitMargin}`);
    if (financials.revenueGrowth != null)   lines.push(`  revenueGrowth: ${financials.revenueGrowth}`);
  } else {
    lines.push('BASIC FINANCIALS: (unavailable)');
  }

  return lines.join('\n');
}

/**
 * Render categorized news into a numbered, category-tagged block for the prompt.
 * @param {Array<{category: string, title: string, description: string, published_at: string}>} categorizedNews
 * @returns {string}
 */
function renderNews(categorizedNews) {
  return categorizedNews
    .map((a, i) => {
      const desc = a.description ? ` — ${String(a.description).slice(0, 200)}` : '';
      const when = a.published_at ? ` (${a.published_at})` : '';
      return `${i + 1}. [${a.category}] ${a.title}${desc}${when}`;
    })
    .join('\n');
}

// ── analyzeStrategicSentiment ──────────────────────────────────────────────────

/**
 * Produce a STRATEGIC, materiality-weighted sentiment verdict for a symbol,
 * grounded in the company's business context and category-tagged news.
 *
 * When `categorizedNews` is empty, returns a low-confidence neutral verdict
 * WITHOUT calling the LLM. Otherwise calls the LLM (temperature 0), parses the
 * raw JSON (code fences stripped), then validates and clamps every field so the
 * caller always receives a schema-valid object.
 *
 * @param {string} symbol - NSE ticker symbol (e.g. "RELIANCE").
 * @param {Object} ctx
 * @param {Object|null} ctx.profile        - Finnhub profile (or null).
 * @param {Object|null} ctx.financials     - Finnhub basic financials (or null).
 * @param {Array<{category: string, title: string, description: string, url: string, published_at: string}>} ctx.categorizedNews
 *   Category-tagged news from strategicFetcher.
 * @returns {Promise<{conviction_score: number, label: string, thesis: string, drivers: Array, risks: string[], horizon: string, confidence: number}>}
 *   A validated strategic verdict. Throws only on LLM/transport errors (callers
 *   in index.js catch and fall back); an empty news set never throws.
 */
export async function analyzeStrategicSentiment(symbol, ctx = {}) {
  const { profile = null, financials = null, categorizedNews = [] } = ctx;

  // No fresh catalysts → deterministic neutral verdict, no LLM spend.
  if (!Array.isArray(categorizedNews) || categorizedNews.length === 0) {
    console.log(`\x1b[33m[analyzer]\x1b[0m symbol=\x1b[1m${symbol}\x1b[0m: \x1b[90mno categorized news — neutral verdict.\x1b[0m`);
    return neutralVerdict(symbol);
  }

  let cfg;
  try {
    cfg = resolveLlmConfig();
  } catch (err) {
    // No request was issued, so no latency is observed — a duration for a
    // missing API key would measure nothing and skew the histogram toward zero.
    metrics.llmCallCompleted('no_api_key');
    throw err;
  }

  const userMessage =
    `Symbol: ${symbol}\n\n` +
    `${renderContext(profile, financials)}\n\n` +
    `CATEGORIZED NEWS (${categorizedNews.length}):\n` +
    `${renderNews(categorizedNews)}`;

  console.log(
    `\x1b[33m[analyzer]\x1b[0m Calling LLM (\x1b[36m${cfg.model}\x1b[0m) for STRATEGIC verdict symbol=\x1b[1m${symbol}\x1b[0m ` +
    `with ${categorizedNews.length} categorized article(s)...`
  );

  const startedAt = performance.now();
  const elapsed = () => (performance.now() - startedAt) / 1000;

  let rawText;
  try {
    rawText = await callLlm(cfg, STRATEGIC_SYSTEM_PROMPT, userMessage, STRATEGIC_MAX_TOKENS);
  } catch (err) {
    // `err.status` is set by callLlm only when the provider answered. Note that
    // this fetch has no timeout, so a provider that simply hangs never lands
    // here at all — it stalls the poll cycle, and the heartbeat is what surfaces
    // that.
    metrics.llmCallCompleted(err.status ? 'http_error' : 'network_error', elapsed());
    throw err;
  }

  let parsed;
  try {
    parsed = parseJsonResponse(rawText);
  } catch (parseErr) {
    // Distinct from http_error on purpose: the provider was reachable and
    // answered, just not with JSON. That is usually a model or prompt change,
    // which needs a code fix rather than a retry.
    metrics.llmCallCompleted('parse_error', elapsed());
    throw new Error(
      `[analyzer] Failed to parse strategic LLM response as JSON. ` +
      `Raw output: "${String(rawText).slice(0, 200)}"`
    );
  }

  metrics.llmCallCompleted('ok', elapsed());

  // ── Validate + clamp every field ──────────────────────────────────────────
  const score = clampInt(parsed.conviction_score, 1, 100, 50);
  const label = normalizeLabel(parsed.label, score);

  const thesis =
    typeof parsed.thesis === 'string' && parsed.thesis.trim()
      ? parsed.thesis.trim()
      : 'Verdict synthesized from categorized news and business context.';

  const drivers = Array.isArray(parsed.drivers)
    ? parsed.drivers.slice(0, 10).map((d) => ({
        category: typeof d?.category === 'string' ? d.category : 'SECTOR_MACRO',
        headline: typeof d?.headline === 'string' ? d.headline : '',
        impact:   normalizeImpact(d?.impact),
        weight:   clampFloat(d?.weight, 0, 1, 0),
      }))
    : [];

  const risks = Array.isArray(parsed.risks)
    ? parsed.risks.filter((r) => typeof r === 'string' && r.trim()).slice(0, 10)
    : [];

  const horizon = normalizeHorizon(parsed.horizon);
  const confidence = clampFloat(parsed.confidence, 0, 1, 0.5);

  const verdict = { conviction_score: score, label, thesis, drivers, risks, horizon, confidence };

  console.log(
    `\x1b[33m[analyzer]\x1b[0m symbol=\x1b[1m${symbol}\x1b[0m  conviction_score=\x1b[36m${score}\x1b[0m  label=\x1b[32m${label}\x1b[0m  ` +
    `drivers=${drivers.length}  confidence=${confidence}`
  );

  return verdict;
}

// ── analyzeSentiment (backward-compatible shim) ─────────────────────────────────

/**
 * Backward-compatible sentiment scorer returning the legacy
 * `{conviction_score, reasoning_snippet}` contract.
 *
 * Reimplemented as a thin wrapper over {@link analyzeStrategicSentiment}: the
 * plain headline array is mapped into single-category (SECTOR_MACRO) news items
 * with no profile/financials context, and the rich verdict is projected back
 * onto the legacy shape (`reasoning_snippet` = thesis truncated to 150 chars).
 *
 * @param {string}   symbol         - NSE ticker symbol (e.g. "TATA", "INFY").
 * @param {string[]} headlinesArray - Array of headline strings to analyze.
 * @returns {Promise<{conviction_score: number, reasoning_snippet: string}>}
 * @throws {Error} If the API call fails or the response cannot be parsed.
 */
export async function analyzeSentiment(symbol, headlinesArray) {
  if (!headlinesArray || headlinesArray.length === 0) {
    throw new Error('[analyzer] headlinesArray must contain at least one headline.');
  }

  const categorizedNews = headlinesArray.map((h) => ({
    category:     'SECTOR_MACRO',
    title:        String(h ?? ''),
    description:  '',
    url:          '',
    published_at: '',
  }));

  const verdict = await analyzeStrategicSentiment(symbol, {
    profile: null,
    financials: null,
    categorizedNews,
  });

  return {
    conviction_score:  verdict.conviction_score,
    reasoning_snippet: String(verdict.thesis ?? '').slice(0, 150),
  };
}
