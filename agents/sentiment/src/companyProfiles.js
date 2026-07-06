// companyProfiles.js — Curated ticker → company-profile seed map.
//
// The strategic sentiment engine is *symbol-aware*: instead of querying news
// for a bare ticker (e.g. "RELIANCE"), it queries for the company's real name
// and known aliases ("Reliance Industries", "RIL", ...) so NewsData.io returns
// catalysts that actually move the stock. This module is the single source of
// truth for that mapping.
//
// Each seed carries:
//   • companyName   — canonical full name used in news queries + LLM context.
//   • sector        — coarse sector/industry label (used by SECTOR_MACRO bucket).
//   • aliases       — alternate names/tickers the press uses (query expansion).
//   • finnhubSymbol — the symbol Finnhub expects (NSE listings use a ".NS" suffix).
//
// Finnhub's Indian coverage is thin, so the seed is *best-effort context*: the
// rest of the pipeline degrades gracefully when Finnhub returns nothing.
//
// Optional env vars:
//   FINNHUB_SYMBOL_SUFFIX — exchange suffix for the default Finnhub symbol
//                           (default: ".NS" — NSE).

// ── Constants ─────────────────────────────────────────────────────────────────

/**
 * Default Finnhub symbol suffix for tickers that aren't in the curated map.
 * NSE listings on Finnhub are suffixed ".NS" (e.g. RELIANCE.NS); override via
 * FINNHUB_SYMBOL_SUFFIX for other exchanges.
 */
const FINNHUB_SYMBOL_SUFFIX = process.env.FINNHUB_SYMBOL_SUFFIX ?? '.NS';

// ── Curated profile seeds ─────────────────────────────────────────────────────

/**
 * Curated ticker → profile-seed map. Keyed by upper-case NSE ticker.
 *
 * @type {Record<string, {companyName: string, sector: string, aliases: string[], finnhubSymbol: string}>}
 */
const PROFILE_SEEDS = {
  RELIANCE: {
    companyName:   'Reliance Industries',
    sector:        'Energy / Conglomerate',
    aliases:       ['Reliance Industries', 'RIL', 'Reliance'],
    finnhubSymbol: 'RELIANCE.NS',
  },
  TCS: {
    companyName:   'Tata Consultancy Services',
    sector:        'Information Technology / IT Services',
    aliases:       ['Tata Consultancy Services', 'TCS'],
    finnhubSymbol: 'TCS.NS',
  },
  INFY: {
    companyName:   'Infosys',
    sector:        'Information Technology / IT Services',
    aliases:       ['Infosys', 'Infosys Ltd', 'INFY'],
    finnhubSymbol: 'INFY.NS',
  },
  HDFCBANK: {
    companyName:   'HDFC Bank',
    sector:        'Financials / Banking',
    aliases:       ['HDFC Bank', 'HDFC'],
    finnhubSymbol: 'HDFCBANK.NS',
  },
  ICICIBANK: {
    companyName:   'ICICI Bank',
    sector:        'Financials / Banking',
    aliases:       ['ICICI Bank', 'ICICI'],
    finnhubSymbol: 'ICICIBANK.NS',
  },
  TATAMOTORS: {
    companyName:   'Tata Motors',
    sector:        'Automobiles',
    aliases:       ['Tata Motors', 'Tata Motors Ltd'],
    finnhubSymbol: 'TATAMOTORS.NS',
  },
  TATASTEEL: {
    companyName:   'Tata Steel',
    sector:        'Metals / Mining',
    aliases:       ['Tata Steel', 'Tata Steel Ltd'],
    finnhubSymbol: 'TATASTEEL.NS',
  },
};

// ── resolveProfileSeed ─────────────────────────────────────────────────────────

/**
 * Resolve the curated profile seed for a ticker symbol, or synthesize a sane
 * default when the symbol isn't in the curated map.
 *
 * The default uses the bare symbol as both company name and sole alias, no
 * sector, and a Finnhub symbol of `${symbol}${FINNHUB_SYMBOL_SUFFIX}` so the
 * pipeline still attempts (best-effort) Finnhub enrichment for unknown tickers.
 *
 * @param {string} symbol - NSE ticker symbol (case-insensitive, e.g. "RELIANCE").
 * @returns {{companyName: string, sector: (string|null), aliases: string[], finnhubSymbol: string}}
 *   The curated seed, or a synthesized default. Never throws.
 *
 * @example
 * resolveProfileSeed('RELIANCE');
 * // { companyName: 'Reliance Industries', sector: 'Energy / Conglomerate',
 * //   aliases: ['Reliance Industries','RIL','Reliance'], finnhubSymbol: 'RELIANCE.NS' }
 *
 * @example
 * resolveProfileSeed('SOMECO');
 * // { companyName: 'SOMECO', sector: null, aliases: ['SOMECO'], finnhubSymbol: 'SOMECO.NS' }
 */
export function resolveProfileSeed(symbol) {
  const key = String(symbol ?? '').trim().toUpperCase();

  const seed = PROFILE_SEEDS[key];
  if (seed) {
    // Return a shallow copy so callers can't mutate the curated map.
    return {
      companyName:   seed.companyName,
      sector:        seed.sector,
      aliases:       [...seed.aliases],
      finnhubSymbol: seed.finnhubSymbol,
    };
  }

  // Sane default for an unknown ticker — best-effort, still query-able.
  const safeSymbol = key || String(symbol ?? '');
  return {
    companyName:   safeSymbol,
    sector:        null,
    aliases:       [safeSymbol],
    finnhubSymbol: `${safeSymbol}${FINNHUB_SYMBOL_SUFFIX}`,
  };
}
