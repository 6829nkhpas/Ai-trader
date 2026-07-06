// profile.js — Finnhub client for company profile + basic financials context.
//
// The strategic sentiment engine grounds its verdict in the company's *business
// posture*, not just headlines. Finnhub provides two cheap context calls:
//
//   GET /stock/profile2?symbol=X  → name, industry, exchange, market cap, ...
//   GET /stock/metric?symbol=X&metric=all → fundamentals (P/E, 52w range, ...)
//
// Finnhub's Indian coverage is thin, so BOTH functions are strictly best-effort:
//   • Skip cleanly (return null) when FINNHUB_API_KEY is unset.
//   • Never throw — any HTTP / parse / empty-body condition logs and returns null.
//   • A null result simply means "no business context"; the LLM still scores the
//     news on its own. This keeps the polling loop alive no matter what Finnhub does.
//
// Required env vars:
//   FINNHUB_API_KEY  — Finnhub API token (when unset, enrichment is skipped).

import axios from 'axios';

// ── Constants ────────────────────────────────────────────────────────────────

const FINNHUB_BASE_URL = 'https://finnhub.io/api/v1';

// 10 s — don't let a slow/unresponsive Finnhub stall the agent loop.
const FINNHUB_TIMEOUT_MS = 10_000;

// ── fetchCompanyProfile ────────────────────────────────────────────────────────

/**
 * Fetch the company profile from Finnhub `/stock/profile2`.
 *
 * @param {string} symbol - NSE ticker symbol (used for logging only).
 * @param {{finnhubSymbol: string}} seed - Profile seed; `finnhubSymbol` is the
 *   exchange-qualified symbol Finnhub expects (e.g. "RELIANCE.NS").
 * @returns {Promise<{name: string, industry: string, exchange: string, marketCap: (number|null), weburl: string, country: string}|null>}
 *   The picked profile fields, or null when FINNHUB_API_KEY is unset, the body
 *   is empty (`{}`), or any error occurs. Never throws.
 */
export async function fetchCompanyProfile(symbol, seed) {
  const apiKey = process.env.FINNHUB_API_KEY;
  if (!apiKey) {
    console.warn('[profile] FINNHUB_API_KEY is not set — skipping profile fetch.');
    return null;
  }

  const finnhubSymbol = seed?.finnhubSymbol ?? `${symbol}.NS`;

  console.log(`[profile] GET /stock/profile2?symbol=${finnhubSymbol}`);

  try {
    const response = await axios.get(`${FINNHUB_BASE_URL}/stock/profile2`, {
      params:  { symbol: finnhubSymbol, token: apiKey },
      timeout: FINNHUB_TIMEOUT_MS,
    });

    const data = response.data ?? {};

    // Finnhub returns {} for symbols it doesn't cover — treat as no context.
    if (!data || Object.keys(data).length === 0) {
      console.log(`[profile] symbol=${symbol} (${finnhubSymbol}): empty profile — no context.`);
      return null;
    }

    const profile = {
      name:      data.name ?? '',
      industry:  data.finnhubIndustry ?? '',
      exchange:  data.exchange ?? '',
      marketCap: typeof data.marketCapitalization === 'number' ? data.marketCapitalization : null,
      weburl:    data.weburl ?? '',
      country:   data.country ?? '',
    };

    console.log(
      `[profile] symbol=${symbol}  name="${profile.name}"  industry="${profile.industry}"`
    );

    return profile;
  } catch (err) {
    const status = err.response?.status ?? 'network error';
    console.error(
      `[profile] Failed to fetch profile for symbol='${symbol}' (${finnhubSymbol}): ` +
      `HTTP ${status} — ${err.message}`
    );
    return null;
  }
}

// ── fetchBasicFinancials ────────────────────────────────────────────────────────

/**
 * Fetch basic financial metrics from Finnhub `/stock/metric?metric=all` and
 * pick a small, decision-relevant subset.
 *
 * Finnhub field names vary in availability per market; every picked field is
 * defensively optional (null when absent) so a partial response is still useful.
 *
 * @param {string} symbol - NSE ticker symbol (used for logging only).
 * @param {{finnhubSymbol: string}} seed - Profile seed; supplies `finnhubSymbol`.
 * @returns {Promise<{pe: (number|null), week52High: (number|null), week52Low: (number|null), netProfitMargin: (number|null), revenueGrowth: (number|null)}|null>}
 *   The picked metrics subset, or null when FINNHUB_API_KEY is unset, no metrics
 *   are present, or any error occurs. Never throws.
 */
export async function fetchBasicFinancials(symbol, seed) {
  const apiKey = process.env.FINNHUB_API_KEY;
  if (!apiKey) {
    console.warn('[profile] FINNHUB_API_KEY is not set — skipping financials fetch.');
    return null;
  }

  const finnhubSymbol = seed?.finnhubSymbol ?? `${symbol}.NS`;

  console.log(`[profile] GET /stock/metric?symbol=${finnhubSymbol}&metric=all`);

  try {
    const response = await axios.get(`${FINNHUB_BASE_URL}/stock/metric`, {
      params:  { symbol: finnhubSymbol, metric: 'all', token: apiKey },
      timeout: FINNHUB_TIMEOUT_MS,
    });

    const metric = response.data?.metric ?? {};

    if (!metric || Object.keys(metric).length === 0) {
      console.log(`[profile] symbol=${symbol} (${finnhubSymbol}): no metrics — no context.`);
      return null;
    }

    const pick = (key) => (typeof metric[key] === 'number' ? metric[key] : null);

    const financials = {
      pe:              pick('peBasicExclExtraTTM'),
      week52High:      pick('52WeekHigh'),
      week52Low:       pick('52WeekLow'),
      netProfitMargin: pick('netProfitMarginTTM'),
      revenueGrowth:   pick('revenueGrowthTTMYoy'),
    };

    // If every picked metric is null the payload carries no usable context.
    const hasAny = Object.values(financials).some((v) => v !== null);
    if (!hasAny) {
      console.log(`[profile] symbol=${symbol} (${finnhubSymbol}): metrics present but none usable.`);
      return null;
    }

    console.log(
      `[profile] symbol=${symbol}  pe=${financials.pe}  ` +
      `52wHigh=${financials.week52High}  52wLow=${financials.week52Low}`
    );

    return financials;
  } catch (err) {
    const status = err.response?.status ?? 'network error';
    console.error(
      `[profile] Failed to fetch financials for symbol='${symbol}' (${finnhubSymbol}): ` +
      `HTTP ${status} — ${err.message}`
    );
    return null;
  }
}
