// index.js — Sentiment Agent entry point.
//
// Power Phase 1.4 — Subphases 31-33: Full NLP Pipeline.
//
// Pipeline executed on every poll interval per symbol:
//   1. fetchLatestNews(symbol)      → raw Marketaux article array
//   2. Deduplicate via Redis SET     → skip articles already scored this session
//   3. scoreArticle(symbol, article) → Claude conviction score + reasoning
//   4. publishSentiment(...)        → NewsSentiment Protobuf → signals.sentiment topic
//
// Deduplication strategy:
//   Each article's UUID is stored in Redis with a TTL of REDIS_ARTICLE_TTL_S
//   (default: 86400 s = 24 h). Articles whose UUID is already present in Redis
//   are skipped — preventing the same headline from being scored repeatedly
//   across poll cycles.
//
// Graceful shutdown:
//   SIGTERM and SIGINT handlers flush the Kafka producer before exiting.

import 'dotenv/config';
import { createClient } from 'redis';
import { loadNewsSentimentType } from './protoLoader.js';
import { fetchLatestNews }       from './fetcher.js';
import { scoreArticle }          from './claude.js';
import { initProducer, publishSentiment, disconnectProducer } from './kafkaProducer.js';

// ── Configuration ─────────────────────────────────────────────────────────────

// Comma-separated NSE symbols to poll. Override via SENTIMENT_SYMBOLS env var.
const SYMBOLS = (process.env.SENTIMENT_SYMBOLS ?? 'RELIANCE,INFY,TCS,HDFCBANK,WIPRO')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

const POLL_INTERVAL_MS  = parseInt(process.env.SENTIMENT_POLL_INTERVAL_MS  ?? '60000', 10);
const REDIS_URL         = process.env.REDIS_URL ?? 'redis://localhost:6379';
const REDIS_ARTICLE_TTL = parseInt(process.env.REDIS_ARTICLE_TTL_S ?? '86400', 10); // 24 h
const SIGNAL_TOPIC      = process.env.KAFKA_TOPIC_SENTIMENT ?? 'signals.sentiment';

// ── Required environment variable guard ───────────────────────────────────────

const REQUIRED_KEYS = ['MARKETAUX_API_KEY', 'ANTHROPIC_API_KEY', 'KAFKA_BROKER_URL'];

function validateEnv() {
  const missing = REQUIRED_KEYS.filter((k) => !process.env[k]);
  if (missing.length > 0) {
    console.warn(
      `[index] ⚠️  Missing env vars: ${missing.join(', ')} — some features will be degraded.`
    );
  } else {
    console.log('[index] ✅ All required environment variables present.');
  }
}

// ── Redis deduplication helpers ───────────────────────────────────────────────

/**
 * Returns true if the article UUID has NOT been seen before (i.e. is new).
 * Atomically sets the key with TTL so concurrent processes don't double-score.
 *
 * @param {import('redis').RedisClientType} redis
 * @param {string} uuid - Marketaux article UUID.
 * @returns {Promise<boolean>}
 */
async function isNewArticle(redis, uuid) {
  // SET key value NX EX ttl — returns 'OK' if key was set (new), null if existed.
  const result = await redis.set(
    `sentiment:seen:${uuid}`,
    '1',
    { NX: true, EX: REDIS_ARTICLE_TTL }
  );
  return result === 'OK';
}

// ── Core poll cycle ───────────────────────────────────────────────────────────

/**
 * Runs one complete poll cycle for all configured symbols.
 * Fetches news, deduplicates, scores with Claude, publishes to Kafka.
 *
 * @param {import('redis').RedisClientType} redis
 * @param {import('protobufjs').Type} NewsSentiment
 */
async function runPollCycle(redis, NewsSentiment) {
  const cycleStart = Date.now();
  console.log(`\n[index] ── Poll cycle started (${new Date().toISOString()}) ──`);

  for (const symbol of SYMBOLS) {
    try {
      // 1. Fetch latest news articles for this symbol.
      const articles = await fetchLatestNews(symbol);

      if (articles.length === 0) {
        console.log(`[index] No articles for ${symbol} — skipping.`);
        continue;
      }

      for (const article of articles) {
        const uuid = article.uuid ?? article.url ?? article.title;

        if (!uuid) {
          console.warn(`[index] Article for ${symbol} has no UUID — skipping.`);
          continue;
        }

        // 2. Deduplicate — skip articles we've already scored.
        const isNew = await isNewArticle(redis, uuid);
        if (!isNew) {
          console.log(`[index] Already scored: "${(article.title ?? '').slice(0, 60)}" — skip.`);
          continue;
        }

        // 3. Score with Claude.
        const result = await scoreArticle(symbol, article);
        if (!result) {
          // Claude returned null (API error or parse failure) — skip publish.
          continue;
        }

        // 4. Publish NewsSentiment to Kafka.
        const sentimentPayload = {
          symbol,
          timestamp_ms:           Date.now(),
          headline:               article.title ?? '',
          claude_conviction_score: result.score,
          reasoning_snippet:      result.reasoning,
        };

        await publishSentiment(NewsSentiment, sentimentPayload);
      }

    } catch (err) {
      // Per-symbol errors are non-fatal — continue with the next symbol.
      console.error(`[index] Unhandled error for symbol='${symbol}': ${err.message}`);
    }
  }

  const elapsed = Date.now() - cycleStart;
  console.log(`[index] ── Poll cycle complete in ${elapsed}ms ──`);
}

// ── Graceful shutdown ─────────────────────────────────────────────────────────

let _redisClient = null;

async function shutdown(signal) {
  console.log(`\n[index] Received ${signal} — shutting down gracefully...`);
  await disconnectProducer();
  if (_redisClient) {
    await _redisClient.quit();
    console.log('[index] Redis disconnected.');
  }
  process.exit(0);
}

// ── main ──────────────────────────────────────────────────────────────────────

async function main() {
  console.log('╔══════════════════════════════════════════════════════╗');
  console.log('║  Sentiment Agent — NLP / Claude Conviction Engine    ║');
  console.log('║  Master Phase 1 → Power Phase 1.4  SP 31-33          ║');
  console.log('╚══════════════════════════════════════════════════════╝');

  validateEnv();

  console.log(`[index] Symbols      : ${SYMBOLS.join(', ')}`);
  console.log(`[index] Poll interval: ${POLL_INTERVAL_MS}ms`);
  console.log(`[index] Signal topic : ${SIGNAL_TOPIC}`);
  console.log(`[index] Redis URL    : ${REDIS_URL}`);

  // ── Load Protobuf schema ─────────────────────────────────────────────────
  console.log('[index] Loading NewsSentiment Protobuf schema...');
  const NewsSentiment = await loadNewsSentimentType();
  console.log(`[index] ✅ Schema loaded: ${NewsSentiment.fullName}`);

  // ── Connect Redis ────────────────────────────────────────────────────────
  console.log('[index] Connecting to Redis...');
  _redisClient = createClient({ url: REDIS_URL });
  _redisClient.on('error', (err) => {
    console.error(`[index] Redis error: ${err.message}`);
  });
  await _redisClient.connect();
  console.log('[index] ✅ Redis connected.');

  // ── Connect Kafka Producer ───────────────────────────────────────────────
  console.log('[index] Initialising Kafka producer...');
  await initProducer();
  console.log('[index] ✅ Kafka producer ready.');

  // ── Register shutdown handlers ───────────────────────────────────────────
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT',  () => shutdown('SIGINT'));

  console.log('─────────────────────────────────────────────────────────');
  console.log('[index] All subsystems online. Starting poll loop...');
  console.log('─────────────────────────────────────────────────────────');

  // ── Polling loop ─────────────────────────────────────────────────────────
  // Run the first cycle immediately, then on every POLL_INTERVAL_MS.
  await runPollCycle(_redisClient, NewsSentiment);

  setInterval(async () => {
    try {
      await runPollCycle(_redisClient, NewsSentiment);
    } catch (err) {
      console.error('[index] Poll cycle threw unexpectedly:', err.message);
    }
  }, POLL_INTERVAL_MS);
}

main().catch((err) => {
  console.error('[index] Fatal startup error:', err);
  process.exit(1);
});
