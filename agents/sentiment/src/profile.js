// profile.js — Company profile + financial context from Yahoo Finance.
//
// Replaces the Finnhub client this file grew out of. The switch was forced:
// Finnhub's free tier does not cover Indian equities, so EVERY call for an NSE
// symbol returned
//
//   HTTP 403 — You don't have access to this resource.
//
// and the analyzer ran with `profile=no financials=no` on every cycle. The key was
// never the problem — verified against the live API, the same key returns HTTP 200
// for AAPL and 403 for RELIANCE.NS, so this is a plan boundary and no amount of
// re-keying fixes it. Indian coverage is a paid add-on.
//
// Yahoo's chart endpoint covers NSE keylessly and returns, in ONE call, the fields
// the analyzer actually reasons about (`renderContext` in analyzer.js): company
// name, exchange, and the 52-week high/low that prompt rule 4 compares the price
// against. So both exported functions are served from a single request, cached per
// cycle, rather than the two Finnhub calls they replace.
//
// ── What is honestly lost, and what covers it ────────────────────────────────
// Yahoo's `v10/quoteSummary` (sector, industry, market cap, P/E) now requires a
// session crumb — measured: it returns `401 Invalid Crumb` without one. Scraping a
// crumb would make this module depend on an undocumented handshake that breaks
// silently, which is precisely the failure shape being escaped here. So:
//
//   * `industry` comes from the curated seed in `companyProfiles.js`, which
//     already carries a `sector` per ticker for the SECTOR_MACRO news bucket. It
//     is hand-maintained and therefore more reliable than either API for Indian
//     names.
//   * `marketCap`, `pe`, `netProfitMargin` and `revenueGrowth` are reported as
//     `null`. `renderContext` already omits null fields, so the prompt simply
//     carries less — it is never given a fabricated number. Valuation-based
//     reasoning degrades; 52-week-position reasoning does not.
//
// Both functions stay strictly best-effort, as before: never throw, return null on
// any failure, and let the LLM score the news unaided. A dead context call must not
// stop the poll loop.
//
// Optional env vars:
//   YAHOO_SYMBOL_SUFFIX — exchange suffix when the seed supplies none (default: ".NS").

import axios from 'axios';

// ── Constants ────────────────────────────────────────────────────────────────

const YAHOO_CHART_URL = 'https://query1.finance.yahoo.com/v8/finance/chart';

/** 10 s — don't let a slow upstream stall the agent loop. */
const YAHOO_TIMEOUT_MS = 10_000;

const SYMBOL_SUFFIX = process.env.YAHOO_SYMBOL_SUFFIX ?? '.NS';

/**
 * Yahoo serves an abbreviated body — and sometimes 429s — to clients that do not
 * look like a browser. A plain UA is the difference between data and nothing.
 */
const YAHOO_HEADERS = {
  'User-Agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  Accept: 'application/json,text/plain,*/*',
};

/**
 * Per-cycle memo, so `fetchCompanyProfile` and `fetchBasicFinancials` — which
 * `index.js` calls back to back for the same symbol — cost ONE request rather than
 * two. Entries expire quickly: this is request coalescing, not a data cache, and
 * a stale 52-week range would quietly corrupt the analyzer's read of where price
 * sits in its range.
 */
const CYCLE_TTL_MS = 60_000;
/** @type {Map<string, {at: number, meta: (object|null)}>} */
const metaCache = new Map();

// ── Pure helpers (exported for tests) ────────────────────────────────────────

/**
 * The Yahoo symbol for a ticker.
 *
 * Reuses the seed's `finnhubSymbol` when present: it already carries the correct
 * `.NS` suffix per ticker and Yahoo uses the same convention, so the curated map
 * did not need a second column. The name is now a misnomer — kept because renaming
 * it would touch `companyProfiles.js` for no behavioural gain.
 *
 * @param {string} symbol
 * @param {{finnhubSymbol?: string}} [seed]
 * @returns {string}
 */
export function yahooSymbol(symbol, seed) {
  const seeded = seed?.finnhubSymbol;
  if (seeded && String(seeded).trim()) return String(seeded).trim();
  const base = String(symbol ?? '').trim();
  if (!base) return '';
  return base.includes('.') ? base : `${base}${SYMBOL_SUFFIX}`;
}

/**
 * Pull the `meta` block out of a chart response.
 *
 * Total: any shape that is not the expected `chart.result[0].meta` yields null
 * rather than throwing, so an upstream change degrades to "no context".
 *
 * @param {unknown} body
 * @returns {(object|null)}
 */
export function extractMeta(body) {
  const result = body?.chart?.result;
  if (!Array.isArray(result) || result.length === 0) return null;
  const meta = result[0]?.meta;
  return meta && typeof meta === 'object' ? meta : null;
}

/** A finite number, or null. Guards against Yahoo's occasional string values. */
function num(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'string' && value.trim()) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/**
 * Map a chart `meta` block onto the profile shape the analyzer consumes.
 *
 * `industry` is taken from the seed, not the response — see the header note.
 *
 * @param {object} meta
 * @param {{sector?: (string|null), companyName?: string}} [seed]
 * @returns {{name: string, industry: string, exchange: string, marketCap: (number|null), weburl: string, country: string}}
 */
export function toProfile(meta, seed) {
  return {
    name: meta.longName || meta.shortName || seed?.companyName || '',
    // From the curated map: Yahoo's industry field is behind the crumb-gated
    // endpoint, and the hand-maintained sector is better for Indian names anyway.
    industry: seed?.sector || '',
    exchange: meta.fullExchangeName || meta.exchangeName || '',
    // Not available without the crumb-gated endpoint. Null, never guessed —
    // renderContext omits it rather than showing a wrong number.
    marketCap: null,
    weburl: '',
    country: meta.currency === 'INR' ? 'IN' : '',
  };
}

/**
 * Map a chart `meta` block onto the financials shape the analyzer consumes.
 *
 * Only the 52-week range survives the move off Finnhub. That is the field prompt
 * rule 4 actually uses ("if financials show the stock near its 52-week high/low"),
 * so the reasoning it supports is preserved; the valuation fields are null.
 *
 * @param {object} meta
 * @returns {{pe: (number|null), week52High: (number|null), week52Low: (number|null), netProfitMargin: (number|null), revenueGrowth: (number|null), price: (number|null)}}
 */
export function toFinancials(meta) {
  return {
    pe: null,
    week52High: num(meta.fiftyTwoWeekHigh),
    week52Low: num(meta.fiftyTwoWeekLow),
    netProfitMargin: null,
    revenueGrowth: null,
    // Additive: the analyzer can only judge "near its 52-week high" if it knows
    // where price currently sits. Finnhub never supplied this, so the rule was
    // half-usable before.
    price: num(meta.regularMarketPrice),
  };
}

// ── Shared fetch ─────────────────────────────────────────────────────────────

/**
 * Fetch and memoize the chart `meta` for a symbol.
 *
 * Never throws. A 404 (unknown ticker) is logged at a lower volume than a real
 * fault, because an unlisted symbol is a data outcome and not an outage — the two
 * want different responses.
 *
 * @param {string} symbol
 * @param {object} [seed]
 * @returns {Promise<(object|null)>}
 */
async function fetchMeta(symbol, seed) {
  const ySymbol = yahooSymbol(symbol, seed);
  if (!ySymbol) return null;

  const cached = metaCache.get(ySymbol);
  if (cached && Date.now() - cached.at < CYCLE_TTL_MS) return cached.meta;

  console.log(`[profile] GET /v8/finance/chart/${ySymbol}`);

  let meta = null;
  try {
    const response = await axios.get(`${YAHOO_CHART_URL}/${encodeURIComponent(ySymbol)}`, {
      params: { range: '1d', interval: '1d' },
      timeout: YAHOO_TIMEOUT_MS,
      headers: YAHOO_HEADERS,
    });
    meta = extractMeta(response.data);
    if (!meta) {
      console.log(`[profile] symbol=${symbol} (${ySymbol}): no meta in response — no context.`);
    }
  } catch (err) {
    const status = err.response?.status ?? 'network error';
    if (status === 404) {
      console.log(`[profile] symbol=${symbol} (${ySymbol}): not listed on Yahoo — no context.`);
    } else {
      console.error(
        `[profile] Failed to fetch context for symbol='${symbol}' (${ySymbol}): ` +
          `HTTP ${status} — ${err.message}`,
      );
    }
  }

  // Negative results are cached too: without that, an unlisted ticker re-requests
  // on every call and every cycle for no possible benefit.
  metaCache.set(ySymbol, { at: Date.now(), meta });
  return meta;
}

/** Drop the memo. Test-only. */
export function __resetProfileCache() {
  metaCache.clear();
}

// ── fetchCompanyProfile ──────────────────────────────────────────────────────

/**
 * Company profile context.
 *
 * Signature and return shape are unchanged from the Finnhub implementation, so
 * `index.js` and `analyzer.js` did not change.
 *
 * @param {string} symbol - NSE ticker symbol.
 * @param {{finnhubSymbol?: string, sector?: (string|null), companyName?: string}} [seed]
 * @returns {Promise<({name: string, industry: string, exchange: string, marketCap: (number|null), weburl: string, country: string}|null)>}
 *   null when there is no usable context. Never throws.
 */
export async function fetchCompanyProfile(symbol, seed) {
  const meta = await fetchMeta(symbol, seed);
  if (!meta) return null;

  const profile = toProfile(meta, seed);

  // A profile with no name carries nothing the LLM can use.
  if (!profile.name) {
    console.log(`[profile] symbol=${symbol}: no company name resolved — no context.`);
    return null;
  }

  console.log(
    `[profile] symbol=${symbol}  name="${profile.name}"  industry="${profile.industry}"  exchange="${profile.exchange}"`,
  );
  return profile;
}

// ── fetchBasicFinancials ─────────────────────────────────────────────────────

/**
 * Financial context: the 52-week range and current price.
 *
 * @param {string} symbol - NSE ticker symbol.
 * @param {{finnhubSymbol?: string}} [seed]
 * @returns {Promise<({pe: (number|null), week52High: (number|null), week52Low: (number|null), netProfitMargin: (number|null), revenueGrowth: (number|null), price: (number|null)}|null)>}
 *   null when no metric resolved. Never throws.
 */
export async function fetchBasicFinancials(symbol, seed) {
  const meta = await fetchMeta(symbol, seed);
  if (!meta) return null;

  const financials = toFinancials(meta);

  // Same guard as the Finnhub version: all-null carries no context.
  if (!Object.values(financials).some((v) => v !== null)) {
    console.log(`[profile] symbol=${symbol}: metrics present but none usable.`);
    return null;
  }

  console.log(
    `[profile] symbol=${symbol}  price=${financials.price}  ` +
      `52wHigh=${financials.week52High}  52wLow=${financials.week52Low}`,
  );
  return financials;
}
