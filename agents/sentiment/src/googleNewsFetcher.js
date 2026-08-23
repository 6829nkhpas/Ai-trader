// googleNewsFetcher.js — Materiality-bucketed retrieval over Google News RSS.
//
// Replaces the NewsData.io implementation this file grew out of. The switch was
// forced, not cosmetic: the free tier is ~200 credits/day and production had
// exhausted it, so every bucket for every symbol returned
//
//   HTTP 429 — You exceeded your assigned API credits
//
// which meant `new_articles=0`, the analyzer took its no-news path, and the panel
// showed a permanent "Neutral / 0 headlines / No notable headline". The service
// looked healthy the whole time — it polled, it published to Kafka, health checks
// passed — while learning nothing. Google News RSS needs no key and imposes no
// credit budget, so the failure mode disappears rather than being rationed.
//
// The bucket strategy is unchanged: pair the company's real name with a
// *materiality* term group (earnings, corporate actions, regulatory, management,
// sector) so the feed surfaces catalysts that move a stock rather than any mention
// of the ticker. The returned shape is identical to the old fetcher's, so
// `analyzer.js` and `index.js` did not change.
//
// ── What Google News gives us, measured against the live feed ────────────────
//   * quoted phrases and `OR` groups ARE honoured; 100 items per query.
//   * `<description>` is NOT a summary — it is a single `<a href>` back to the
//     article. So `description` comes back empty and the TITLE is the whole
//     signal. Titles are long and descriptive enough for the analyzer to
//     categorise, but this is a real reduction in context vs. NewsData.io, and
//     it is why `sourceName` is now extracted and passed through: publisher
//     identity partially replaces the lost snippet as a credibility signal.
//   * WITHOUT a recency operator the feed happily returns months-old items —
//     measured: an unfiltered August query led with June articles. `when:Nd` is
//     therefore mandatory, not a nicety. Stale news scored as fresh sentiment is
//     worse than no news, because it reads as a live catalyst.
//   * There is no `country=`/`category=` filter. Locale is expressed through
//     `hl`/`gl`/`ceid` instead, which is why those are pinned to India/English.
//
// Optional env vars:
//   SENTIMENT_NEWS_BUCKETS  — max targeted queries per symbol (default: 4).
//   GOOGLE_NEWS_WINDOW_DAYS — recency window in days (default: 7).
//   GOOGLE_NEWS_HL / _GL / _CEID — locale overrides (default: en-IN / IN / IN:en).

import axios from 'axios';
import { isArticleProcessed, markArticleProcessed } from './cache.js';
import { metrics } from './metrics.js';

// ── Constants ────────────────────────────────────────────────────────────────

const GOOGLE_NEWS_BASE_URL = 'https://news.google.com/rss/search';

/**
 * Articles kept per bucket. The feed returns ~100 per query; only the freshest
 * are taken.
 *
 * 4, not 3. The HTTP API surfaces up to `SENTIMENT_MAX_HEADLINES` (default 10) and
 * cross-cycle Redis dedup removes anything already scored, so the ceiling has to
 * exceed the target with room to spare: 4 buckets x 4 = 16 candidates for 10 slots.
 * At 3 the ceiling was 12 and a couple of repeats left the panel short.
 */
const PER_BUCKET_SIZE = parseInt(process.env.SENTIMENT_PER_BUCKET ?? '4', 10);

/** Default number of materiality buckets (targeted queries) per symbol. */
const DEFAULT_BUCKETS = parseInt(process.env.SENTIMENT_NEWS_BUCKETS ?? '4', 10);

/**
 * Recency window. 7 days is deliberately short: sentiment is about what is
 * happening now, and the feed returns stale items without this bound.
 */
const WINDOW_DAYS = (() => {
  const n = parseInt(process.env.GOOGLE_NEWS_WINDOW_DAYS ?? '7', 10);
  return Number.isFinite(n) && n > 0 ? n : 7;
})();

/** Locale. Google expresses region through these rather than a country param. */
const HL = process.env.GOOGLE_NEWS_HL ?? 'en-IN';
const GL = process.env.GOOGLE_NEWS_GL ?? 'IN';
const CEID = process.env.GOOGLE_NEWS_CEID ?? 'IN:en';

/** 10 s — don't let a slow feed stall the agent loop. */
const FETCH_TIMEOUT_MS = 10_000;

/**
 * Hard belt-and-braces recency check, in ms.
 *
 * `when:Nd` is applied in the query, but it is Google's filter and not a contract
 * we control, so every item's own `pubDate` is re-checked here. A slightly wider
 * bound than the query avoids discarding an item that is legitimately at the edge
 * of the window because of timezone rounding.
 */
const MAX_AGE_MS = (WINDOW_DAYS + 1) * 24 * 60 * 60 * 1000;

// ── Materiality buckets ──────────────────────────────────────────────────────
//
// Ordered most-material-first, so a low SENTIMENT_NEWS_BUCKETS still covers the
// highest-impact categories. Term groups are richer than the NewsData.io versions
// because Google imposes no query-length limit — the old 100-char cap is gone,
// along with the `fitQuery` trimming that existed only to satisfy it.

/** @type {Array<{category: string, terms: string, sectorBased?: boolean}>} */
const MATERIALITY_BUCKETS = [
  {
    category: 'EARNINGS',
    terms: 'earnings OR profit OR revenue OR dividend OR results OR guidance OR margin',
  },
  {
    category: 'CORPORATE_ACTIONS',
    terms: 'acquisition OR merger OR deal OR stake OR buyback OR demerger OR fundraise',
  },
  {
    category: 'REGULATORY',
    terms: 'SEBI OR RBI OR lawsuit OR ban OR probe OR penalty OR investigation OR notice',
  },
  {
    category: 'MANAGEMENT',
    terms: 'CEO OR CFO OR resignation OR board OR appointment OR "management change"',
  },
  {
    // SECTOR_MACRO substitutes the company's sector as the term group.
    category: 'SECTOR_MACRO',
    terms: '',
    sectorBased: true,
  },
];

// ── Pure helpers (exported for tests) ────────────────────────────────────────

/**
 * Build the `q` value for one bucket: the company name ANDed with the bucket's
 * term group, bounded by the recency window.
 *
 * Returns null when the bucket is not usable — SECTOR_MACRO without a known
 * sector — which the caller treats as a clean skip rather than a failure.
 *
 * @param {{category: string, terms: string, sectorBased?: boolean}} bucket
 * @param {string} primaryAlias
 * @param {(string|null)} sector
 * @param {number} [windowDays]
 * @returns {(string|null)}
 */
export function buildQuery(bucket, primaryAlias, sector, windowDays = WINDOW_DAYS) {
  const name = String(primaryAlias ?? '').trim();
  if (!name) return null;

  const quoted = `"${name}"`;
  const recency = `when:${windowDays}d`;

  if (bucket.sectorBased) {
    if (!sector) return null;
    // First sector token only — "Energy" from "Energy / Conglomerate".
    const sectorTerm = String(sector).split('/')[0].trim();
    if (!sectorTerm) return null;
    return `${quoted} ${sectorTerm} ${recency}`;
  }

  if (!bucket.terms) return null;
  return `${quoted} (${bucket.terms}) ${recency}`;
}

/**
 * Strip the trailing " - Publisher" that Google appends to every headline.
 *
 * Left in place, the publisher name becomes part of the text the LLM scores, and a
 * wire service's name is not sentiment.
 *
 * ONLY an exact match against the item's own `<source>` element is stripped. There
 * is deliberately no "looks like a publisher" fallback: measured against a live
 * 100-item feed, every single item carried a `<source>` element AND every title
 * ended in exactly `" - <source>"`, so a heuristic would add no coverage — and a
 * first attempt at one turned "Reliance - Jio merger talks advance" into
 * "Reliance", destroying the headline. A stray publisher name in the text is a far
 * cheaper error than a truncated headline shown to the user.
 *
 * @param {string} title
 * @param {(string|null)} [sourceName]
 * @returns {string}
 */
export function cleanTitle(title, sourceName) {
  const out = String(title ?? '').trim();
  if (!out || !sourceName) return out;

  const suffix = ` - ${String(sourceName).trim()}`;
  return out.endsWith(suffix) ? out.slice(0, -suffix.length).trim() : out;
}

/**
 * Decode the XML entities an RSS feed carries. Ampersands LAST, so a
 * double-encoded `&amp;lt;` does not turn into a tag.
 *
 * @param {string} value
 * @returns {string}
 */
export function decodeEntities(value) {
  return String(value ?? '')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, d) => String.fromCharCode(parseInt(d, 10)))
    .replace(/&amp;/g, '&');
}

/**
 * Parse a Google News RSS document into normalized items.
 *
 * Hand-rolled rather than pulling in an XML parser: this feed is a fixed, flat
 * `<item>` shape from one publisher, and the five fields wanted are unambiguous.
 * Adding a dependency to a service that currently has six would buy nothing.
 * `[\s\S]` is used instead of the `s` flag so the regexes behave identically on
 * older runtimes.
 *
 * Total: never throws. A malformed document yields [], and an item missing a
 * title or link is dropped rather than admitted half-built.
 *
 * @param {string} xml
 * @returns {Array<{title: string, url: string, published_at: string, sourceName: (string|null), guid: (string|null)}>}
 */
export function parseRssItems(xml) {
  const text = String(xml ?? '');
  const out = [];

  const itemRe = /<item>([\s\S]*?)<\/item>/g;
  let match;
  while ((match = itemRe.exec(text)) !== null) {
    const block = match[1];

    const pick = (tag) => {
      const m = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`).exec(block);
      if (!m) return null;
      // Unwrap CDATA when present, then decode entities.
      const raw = m[1].replace(/^<!\[CDATA\[([\s\S]*?)\]\]>$/, '$1');
      return decodeEntities(raw).trim();
    };

    const rawTitle = pick('title');
    const url = pick('link');
    if (!rawTitle || !url) continue;

    const sourceName = pick('source');
    out.push({
      title: cleanTitle(rawTitle, sourceName),
      url,
      published_at: pick('pubDate') ?? '',
      sourceName: sourceName || null,
      guid: pick('guid'),
    });
  }

  return out;
}

/**
 * True when `published_at` is missing, unparseable, or inside the recency window.
 *
 * Unparseable dates are ADMITTED rather than dropped: the item was returned by a
 * `when:Nd` query, so Google already considers it recent, and discarding it on a
 * date-format quirk would lose real news. The check exists to catch the case
 * measured on the live feed — months-old items in an unfiltered result — not to
 * be a strict validator.
 *
 * @param {string} publishedAt
 * @param {number} [nowMs]
 * @param {number} [maxAgeMs]
 * @returns {boolean}
 */
export function isRecent(publishedAt, nowMs, maxAgeMs = MAX_AGE_MS) {
  if (!publishedAt) return true;
  const t = Date.parse(publishedAt);
  if (Number.isNaN(t)) return true;
  const now = Number.isFinite(nowMs) ? nowMs : Date.now();
  return now - t <= maxAgeMs;
}

// ── fetchStrategicNews ───────────────────────────────────────────────────────

/**
 * Run up to `count` targeted Google News RSS queries for a symbol — one per
 * materiality bucket — and return a flat, deduplicated, category-tagged array.
 *
 * Signature and return shape are byte-compatible with the NewsData.io fetcher
 * this replaces, so `index.js` and `analyzer.js` are untouched. `description` is
 * always `''`: the feed carries no summary (see the header note).
 *
 * Dedup happens twice, as before:
 *   • In-cycle — a `Set` prevents the same article appearing under two buckets.
 *   • Cross-cycle — Redis (24 h) prevents re-scoring, and therefore re-paying for,
 *     an article already handled.
 *
 * Errors are caught per bucket: one failed query never aborts the others, and a
 * total failure yields [] rather than throwing into the poll loop.
 *
 * @param {string} symbol
 * @param {{companyName: string, sector: (string|null), aliases: string[]}} seed
 * @param {number} [count]
 * @param {{bypassDedup?: boolean}} [opts] - `bypassDedup` ignores the Redis
 *   already-seen window for this call. Used only on a COLD START: the dedup window
 *   outlives the in-memory verdict cache, so after a restart every article reads as
 *   already-scored and the symbol would have no verdict at all to serve. Costs one
 *   extra LLM scoring pass per symbol per restart, which is the correct trade
 *   against showing "no notable headline" for a stock that has news.
 * @returns {Promise<Array<{category: string, title: string, description: string, url: string, published_at: string, source: (string|null)}>>}
 */
export async function fetchStrategicNews(symbol, seed, count, opts = {}) {
  const bypassDedup = opts?.bypassDedup === true;
  const maxBuckets = Number.isFinite(count) && count > 0 ? count : DEFAULT_BUCKETS;
  const buckets = MATERIALITY_BUCKETS.slice(0, maxBuckets);

  const primaryAlias = seed?.aliases?.[0] ?? seed?.companyName ?? symbol;
  const sector = seed?.sector ?? null;

  const seenKeys = new Set();
  const collected = [];
  let dedupedCount = 0;
  let staleCount = 0;

  for (const bucket of buckets) {
    const q = buildQuery(bucket, primaryAlias, sector);
    if (!q) {
      console.log(`[googleNews] ${symbol} bucket=${bucket.category}: no query (skipped).`);
      metrics.newsFetchCompleted('skipped');
      continue;
    }

    const safeUrl = `${GOOGLE_NEWS_BASE_URL}?q=${encodeURIComponent(q)}&hl=${HL}&gl=${GL}&ceid=${CEID}`;
    console.log(
      `\x1b[35m[googleNews]\x1b[0m \x1b[1m${symbol}\x1b[0m bucket=\x1b[33m${bucket.category}\x1b[0m GET ${safeUrl}`
    );

    let items;
    try {
      const response = await axios.get(GOOGLE_NEWS_BASE_URL, {
        params: { q, hl: HL, gl: GL, ceid: CEID },
        timeout: FETCH_TIMEOUT_MS,
        // Google serves the feed as text/xml; take it verbatim so axios does not
        // try to JSON-parse it.
        responseType: 'text',
        transformResponse: [(d) => d],
        headers: {
          // Some Google endpoints return an abbreviated body to unknown agents.
          'User-Agent': 'Mozilla/5.0 (compatible; StratAiSentiment/1.0)',
          Accept: 'application/rss+xml, application/xml, text/xml',
        },
      });
      items = parseRssItems(response.data);
      metrics.newsFetchCompleted('ok');
    } catch (err) {
      const status = err.response?.status ?? 'network error';
      console.error(
        `\x1b[31m[googleNews] ${symbol} bucket=${bucket.category} failed: HTTP ${status} — ${err.message}\x1b[0m`
      );
      metrics.newsFetchCompleted(err.response ? 'http_error' : 'network_error');
      continue;
    }

    let kept = 0;
    for (const item of items) {
      if (kept >= PER_BUCKET_SIZE) break;

      if (!isRecent(item.published_at)) {
        staleCount += 1;
        continue;
      }

      // `guid` is stable per article; the redirect URL is not guaranteed to be,
      // so prefer the guid as the dedup key and fall back through url → title.
      const cacheKey = item.guid || item.url || item.title;
      if (!cacheKey) continue;

      if (seenKeys.has(cacheKey)) {
        dedupedCount += 1;
        continue;
      }

      let alreadyProcessed = false;
      if (!bypassDedup) {
        try {
          alreadyProcessed = await isArticleProcessed(cacheKey);
        } catch (err) {
          // Treat a cache failure as "not processed" so an infra blip does not drop
          // news. The trade is duplicate LLM spend, which is why it is counted.
          metrics.cacheError('dedup_check');
          console.error(`\x1b[31m[googleNews] dedup check error: ${err.message}\x1b[0m`);
        }
      }

      if (alreadyProcessed) {
        dedupedCount += 1;
        console.log(
          `\x1b[35m[googleNews]\x1b[0m \x1b[90mSKIP (cached):\x1b[0m "${item.title.slice(0, 60)}"`
        );
        continue;
      }

      seenKeys.add(cacheKey);
      collected.push({
        category: bucket.category,
        title: item.title,
        // No summary exists in this feed. Empty rather than a duplicate of the
        // title, so the analyzer cannot mistake a repeat for corroboration.
        description: '',
        url: item.url,
        published_at: item.published_at,
        source: item.sourceName,
      });
      kept += 1;

      try {
        await markArticleProcessed(cacheKey);
      } catch (err) {
        metrics.cacheError('dedup_mark');
        console.error(`\x1b[31m[googleNews] markArticleProcessed error: ${err.message}\x1b[0m`);
      }
    }
  }

  metrics.articlesCollected(collected.length, dedupedCount);

  console.log(
    `\x1b[35m[googleNews]\x1b[0m symbol=\x1b[1m${symbol}\x1b[0m  new_articles=\x1b[32m${collected.length}\x1b[0m  ` +
      `buckets_run=${buckets.length}  stale_dropped=${staleCount}`
  );

  return collected;
}
