// fetcher.js — Financial news fetcher for the Sentiment Agent.
//
// Fetches the latest news articles for a given NSE symbol from the
// Marketaux API. Articles are returned as a raw JSON array so the caller
// (claude.js / index.js) can iterate and score each headline with Claude.
//
// API reference: https://www.marketaux.com/documentation
//
// Required env vars:
//   MARKETAUX_API_KEY  — your Marketaux API token
//
// Optional env vars (with defaults):
//   MARKETAUX_LANGUAGE   — comma-separated language codes (default: "en")
//   MARKETAUX_PAGE_SIZE  — max articles per request (default: 3; free tier = 3/req)

import axios from 'axios';

// ── Constants ────────────────────────────────────────────────────────────────

const MARKETAUX_BASE_URL = 'https://api.marketaux.com/v1/news/all';

// Maximum articles to fetch per symbol per poll cycle.
// Marketaux free tier allows 3 results per request.
const DEFAULT_PAGE_SIZE = parseInt(process.env.MARKETAUX_PAGE_SIZE ?? '3', 10);

// Language filter — financial news in English by default.
const DEFAULT_LANGUAGE = process.env.MARKETAUX_LANGUAGE ?? 'en';

// ── fetchLatestNews ──────────────────────────────────────────────────────────

/**
 * Fetches the latest news articles for the given `symbol` from the Marketaux API.
 *
 * The `filter_entities=true` parameter instructs Marketaux to only return
 * articles that directly mention `symbol` as a tracked entity — reducing noise
 * significantly compared to a plain keyword search.
 *
 * @param {string} symbol - NSE ticker symbol (e.g. "RELIANCE", "INFY").
 * @returns {Promise<Array>} Array of Marketaux article objects. Each object
 *   contains at minimum: `{ uuid, title, description, published_at, entities[] }`.
 *   Returns an empty array if the API call fails (non-fatal; logged to stderr).
 *
 * @throws Never — errors are caught internally and logged; callers receive [].
 */
export async function fetchLatestNews(symbol) {
  const apiKey = process.env.MARKETAUX_API_KEY;

  if (!apiKey) {
    console.warn('[fetcher] MARKETAUX_API_KEY is not set — skipping news fetch.');
    return [];
  }

  // Build query parameters following the Marketaux v1 API spec.
  const params = {
    symbols:         symbol,
    filter_entities: 'true',
    language:        DEFAULT_LANGUAGE,
    limit:           DEFAULT_PAGE_SIZE,
    api_token:       apiKey,
  };

  // Construct the URL for logging (without the API token for safety).
  const safeUrl =
    `${MARKETAUX_BASE_URL}?symbols=${symbol}&filter_entities=true&language=${DEFAULT_LANGUAGE}&limit=${DEFAULT_PAGE_SIZE}`;

  console.log(`[fetcher] GET ${safeUrl}`);

  try {
    const response = await axios.get(MARKETAUX_BASE_URL, {
      params,
      timeout: 10_000, // 10 s — don't let a slow API stall the agent loop
    });

    const articles = response.data?.data ?? [];

    console.log(
      `[fetcher] symbol=${symbol}  articles_received=${articles.length}`
    );

    return articles;
  } catch (err) {
    // Log the error but return [] — a failed fetch should not crash the agent.
    const status = err.response?.status ?? 'network error';
    console.error(
      `[fetcher] Failed to fetch news for symbol='${symbol}': HTTP ${status} — ${err.message}`
    );
    return [];
  }
}
