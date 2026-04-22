// index.js — Sentiment Agent integration test (Subphases 31-33).
//
// Wires the fetcher, cache, and analyzer together into a single testable flow:
//
//   1. fetchLatestNews("TATA")       → raw Marketaux article array
//   2. Filter articles through isArticleProcessed (Redis deduplication)
//   3. markArticleProcessed for each new article (24 h TTL)
//   4. Collect headlines from all new articles
//   5. analyzeSentiment(symbol, headlines) → Claude conviction score
//   6. console.log the resulting JSON object
//
// This is NOT an infinite loop — it is a single, testable pass designed to
// validate the end-to-end pipeline before the full polling loop is wired.
//
// Required env vars:
//   MARKETAUX_API_KEY  — Marketaux API token
//   ANTHROPIC_API_KEY  — Anthropic API key
//   REDIS_URL          — Redis connection string (default: redis://localhost:6379)

import 'dotenv/config';
import { fetchLatestNews }     from './fetcher.js';
import { isArticleProcessed, markArticleProcessed } from './cache.js';
import { analyzeSentiment }    from './analyzer.js';

// ── Configuration ──────────────────────────────────────────────────────────────

const SYMBOL = 'TATA';

// ── main ───────────────────────────────────────────────────────────────────────

async function main() {
  console.log('╔══════════════════════════════════════════════════════╗');
  console.log('║  Sentiment Agent — Subphases 31-33 Integration Test  ║');
  console.log('║  Redis Cache  ·  Claude Analyzer  ·  Pipeline Check   ║');
  console.log('╚══════════════════════════════════════════════════════╝\n');

  // ── Step 1: Fetch latest news ────────────────────────────────────────────────
  console.log(`[index] Step 1 — Fetching latest news for symbol: ${SYMBOL}`);
  const articles = await fetchLatestNews(SYMBOL);

  if (articles.length === 0) {
    console.log('[index] No articles returned from Marketaux. Exiting.');
    process.exit(0);
  }

  console.log(`[index] Received ${articles.length} article(s) from Marketaux.\n`);

  // ── Step 2 & 3: Filter through Redis cache, mark new articles ────────────────
  console.log('[index] Step 2 — Filtering articles through Redis deduplication cache...');

  const newArticles = [];

  for (const article of articles) {
    // Use the article URL as the canonical cache key.
    const articleUrl = article.url ?? article.uuid ?? article.title;

    if (!articleUrl) {
      console.warn('[index] Article has no URL/UUID — skipping.');
      continue;
    }

    const alreadyProcessed = await isArticleProcessed(articleUrl);

    if (alreadyProcessed) {
      console.log(`[index] SKIP (already processed): "${(article.title ?? '').slice(0, 70)}"`);
    } else {
      // Step 3: Mark the article as processed before scoring to prevent
      // double-scoring if a concurrent process runs.
      await markArticleProcessed(articleUrl);
      newArticles.push(article);
      console.log(`[index] NEW article queued: "${(article.title ?? '').slice(0, 70)}"`);
    }
  }

  console.log(`\n[index] ${newArticles.length} new article(s) to analyze (${articles.length - newArticles.length} skipped as duplicates).\n`);

  if (newArticles.length === 0) {
    console.log('[index] All articles already processed in this cache window. Nothing to score.');
    process.exit(0);
  }

  // ── Step 4: Collect headlines ─────────────────────────────────────────────────
  const headlinesArray = newArticles
    .map((a) => a.title)
    .filter(Boolean);

  console.log('[index] Step 3 — Headlines to analyze:');
  headlinesArray.forEach((h, i) => console.log(`  ${i + 1}. ${h}`));
  console.log('');

  // ── Step 5 & 6: Call Claude analyzer and log result ───────────────────────────
  console.log('[index] Step 4 — Calling Claude sentiment analyzer...\n');

  try {
    const result = await analyzeSentiment(SYMBOL, headlinesArray);

    console.log('\n[index] ✅ Claude analysis complete. Result:');
    console.log(JSON.stringify(result, null, 2));
  } catch (err) {
    console.error(`\n[index] ❌ analyzeSentiment failed: ${err.message}`);
    process.exit(1);
  }

  console.log('\n[index] Integration test complete. Pipeline verified ✅');
  process.exit(0);
}

main().catch((err) => {
  console.error('[index] Fatal error:', err);
  process.exit(1);
});
