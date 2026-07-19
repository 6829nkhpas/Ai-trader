// strategicFetcher.js — Materiality-bucketed NewsData.io retrieval.
//
// Instead of one blind keyword query per ticker, the strategic fetcher runs a
// handful of TARGETED queries — each pairing the company's real name/alias with
// a *materiality bucket* (earnings, corporate actions, regulatory, management,
// sector/macro). This surfaces the catalysts that actually move a stock and
// lets the analyzer weight items by category later.
//
// Credit discipline: NewsData.io free tier is ~200 credits/day. We cap the
// number of buckets per symbol (SENTIMENT_NEWS_BUCKETS, default 4) and request
// only 2-3 articles each, dedup in-cycle AND across cycles (Redis) so we never
// burn credits re-pulling the same article.
//
// Reuses the axios + normalize conventions from fetcher.js.
//
// Required env vars:
//   NEWSDATA_API_KEY  — NewsData.io API key (when unset, returns []).
//
// Optional env vars:
//   SENTIMENT_NEWS_BUCKETS — max targeted queries per symbol (default: 4).
//   NEWSDATA_LANGUAGE      — language filter (default: "en").

import axios from 'axios';
import { isArticleProcessed, markArticleProcessed } from './cache.js';

// ── Constants ────────────────────────────────────────────────────────────────

const NEWSDATA_BASE_URL = 'https://newsdata.io/api/1/latest';

// Articles requested per bucket query — kept small to conserve API credits.
const PER_BUCKET_SIZE = 3;

// Language filter — financial news in English by default.
const DEFAULT_LANGUAGE = process.env.NEWSDATA_LANGUAGE ?? 'en';

// Default number of materiality buckets (targeted queries) per symbol.
const DEFAULT_BUCKETS = parseInt(process.env.SENTIMENT_NEWS_BUCKETS ?? '4', 10);

// 10 s — don't let a slow API stall the agent loop.
const NEWSDATA_TIMEOUT_MS = 10_000;

// ── Materiality buckets ────────────────────────────────────────────────────────
//
// Each bucket is a NewsData.io `q` term-group OR'd together. The fetcher ANDs
// the company name/alias with the bucket terms to keep results on-topic.
// Ordered most-material-first so a low SENTIMENT_NEWS_BUCKETS still covers the
// highest-impact categories (earnings, corporate actions, regulatory).

/** @type {Array<{category: string, terms: string, sectorBased?: boolean}>} */
const MATERIALITY_BUCKETS = [
  {
    category: 'EARNINGS',
    terms:    'results OR profit OR revenue OR earnings OR guidance OR dividend',
  },
  {
    category: 'CORPORATE_ACTIONS',
    terms:    'order OR contract OR acquisition OR merger OR capex OR expansion OR stake OR deal',
  },
  {
    category: 'REGULATORY',
    terms:    'SEBI OR RBI OR probe OR penalty OR lawsuit OR ban OR approval OR investigation',
  },
  {
    category: 'MANAGEMENT',
    terms:    'CEO OR MD OR resignation OR appointment OR board',
  },
  {
    // SECTOR_MACRO substitutes the company's sector/industry as the term group.
    category:    'SECTOR_MACRO',
    terms:       '',
    sectorBased: true,
  },
];

// ── Helpers ────────────────────────────────────────────────────────────────────

/**
 * Build the NewsData.io `q` query string for one bucket: the primary company
 * name/alias ANDed with the bucket's OR'd term group (or, for SECTOR_MACRO, the
 * company's sector). Quoting the company name keeps multi-word names intact.
 *
 * @param {{category: string, terms: string, sectorBased?: boolean}} bucket
 * @param {string} primaryAlias - The company's primary name/alias.
 * @param {(string|null)} sector - Company sector (used for SECTOR_MACRO).
 * @returns {(string|null)} The `q` value, or null when the bucket isn't usable
 *   (e.g. SECTOR_MACRO without a known sector).
 */
function buildQuery(bucket, primaryAlias, sector) {
  const name = `"${primaryAlias}"`;

  if (bucket.sectorBased) {
    if (!sector) return null; // No sector → skip the macro bucket cleanly.
    // Use the first sector token (e.g. "Energy" from "Energy / Conglomerate").
    const sectorTerm = sector.split('/')[0].trim();
    if (!sectorTerm) return null;
    return `${name} AND ${sectorTerm}`;
  }

  return `${name} AND (${bucket.terms})`;
}

// ── fetchStrategicNews ───────────────────────────────────────────────────────

/**
 * Run up to `count` targeted NewsData.io queries for a symbol — one per
 * materiality bucket — and return a flat, deduplicated, category-tagged array
 * of articles.
 *
 * Dedup happens twice:
 *   • In-cycle  — a `Set` of seen keys prevents the same article appearing under
 *     two buckets within this call.
 *   • Cross-cycle — `isArticleProcessed`/`markArticleProcessed` (Redis) prevents
 *     re-scoring an article we already handled in the last 24 h.
 *
 * All errors are caught per-bucket and logged; a single failed query never aborts
 * the others, and a total failure simply yields [].
 *
 * @param {string} symbol - NSE ticker symbol (e.g. "RELIANCE").
 * @param {{companyName: string, sector: (string|null), aliases: string[]}} seed
 *   The resolved profile seed supplying the company name + sector for queries.
 * @param {number} [count] - Max buckets/queries to run (default: SENTIMENT_NEWS_BUCKETS).
 * @returns {Promise<Array<{category: string, title: string, description: string, url: string, published_at: string}>>}
 *   Flat array of category-tagged articles (new this cycle only). Never throws.
 */
export async function fetchStrategicNews(symbol, seed, count) {
  const apiKey = process.env.NEWSDATA_API_KEY;
  if (!apiKey) {
    console.warn('[strategicFetcher] NEWSDATA_API_KEY is not set — skipping news fetch.');
    return [];
  }

  const maxBuckets = Number.isFinite(count) && count > 0 ? count : DEFAULT_BUCKETS;
  const buckets = MATERIALITY_BUCKETS.slice(0, maxBuckets);

  const primaryAlias = seed?.aliases?.[0] ?? seed?.companyName ?? symbol;
  const sector = seed?.sector ?? null;

  // In-cycle dedup key set (cross-bucket).
  const seenKeys = new Set();

  /** @type {Array<{category: string, title: string, description: string, url: string, published_at: string}>} */
  const collected = [];

  for (const bucket of buckets) {
    const q = buildQuery(bucket, primaryAlias, sector);
    if (!q) {
      console.log(`[strategicFetcher] ${symbol} bucket=${bucket.category}: no query (skipped).`);
      continue;
    }

    const params = {
      apikey:   apiKey,
      q,
      language: DEFAULT_LANGUAGE,
      category: 'business',
      country:  'in',
      size:     PER_BUCKET_SIZE,
    };

    const safeUrl =
      `${NEWSDATA_BASE_URL}?q=${encodeURIComponent(q)}` +
      `&language=${DEFAULT_LANGUAGE}&category=business&country=in&size=${PER_BUCKET_SIZE}`;
    console.log(`\x1b[35m[strategicFetcher]\x1b[0m \x1b[1m${symbol}\x1b[0m bucket=\x1b[33m${bucket.category}\x1b[0m GET ${safeUrl}`);

    let articles;
    try {
      const response = await axios.get(NEWSDATA_BASE_URL, {
        params,
        timeout: NEWSDATA_TIMEOUT_MS,
      });
      articles = response.data?.results ?? [];
    } catch (err) {
      const status = err.response?.status ?? 'network error';
      const errorMsg = err.response?.data?.results?.message ?? err.message;
      console.error(
        `\x1b[31m[strategicFetcher] ${symbol} bucket=${bucket.category} failed: HTTP ${status} — ${errorMsg}\x1b[0m`
      );
      continue; // One bad bucket shouldn't abort the rest.
    }

    for (const article of articles) {
      const normalized = {
        category:     bucket.category,
        title:        article.title ?? '',
        description:  article.description ?? '',
        url:          article.link ?? '',
        published_at: article.pubDate ?? '',
      };

      const cacheKey = normalized.url || article.article_id || normalized.title;
      if (!cacheKey) {
        continue; // Nothing to dedup on — skip rather than risk dupes.
      }

      // In-cycle dedup across buckets.
      if (seenKeys.has(cacheKey)) {
        continue;
      }

      // Cross-cycle dedup via Redis (24 h window).
      let alreadyProcessed = false;
      try {
        alreadyProcessed = await isArticleProcessed(cacheKey);
      } catch (err) {
        // Treat cache failure as "not processed" so infra blips don't drop news.
        console.error(`\x1b[31m[strategicFetcher] dedup check error: ${err.message}\x1b[0m`);
      }

      if (alreadyProcessed) {
        console.log(
          `\x1b[35m[strategicFetcher]\x1b[0m \x1b[90mSKIP (cached):\x1b[0m "${normalized.title.slice(0, 60)}"`
        );
        continue;
      }

      seenKeys.add(cacheKey);
      collected.push(normalized);

      // Mark immediately so concurrent buckets / next cycle won't re-pull it.
      try {
        await markArticleProcessed(cacheKey);
      } catch (err) {
        console.error(`\x1b[31m[strategicFetcher] markArticleProcessed error: ${err.message}\x1b[0m`);
      }
    }
  }

  console.log(
    `\x1b[35m[strategicFetcher]\x1b[0m symbol=\x1b[1m${symbol}\x1b[0m  new_articles=\x1b[32m${collected.length}\x1b[0m  ` +
    `buckets_run=${buckets.length}`
  );

  return collected;
}
