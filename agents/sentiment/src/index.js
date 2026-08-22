// index.js — Sentiment Agent — Full Production Polling Loop (Subphases 34-36).
//
// SP35-36: Replaces the single-pass integration test with a continuous
// `setInterval` background process that polls NewsData.io, deduplicates via
// Redis, scores with LLM, and broadcasts to Kafka as Protobuf messages.
//
// Pipeline (per tick per symbol):
//   resolveProfileSeed(symbol)              → curated company name/aliases/sector
//     ↓
//   getProfileContext(symbol, seed)         → Finnhub profile+financials (Redis 24h cache)
//     ↓
//   fetchStrategicNews(symbol, seed)        → materiality-bucketed, deduped Google News RSS
//     ↓  category-tagged article array
//   analyzeStrategicSentiment(symbol, ctx)  → rich verdict (score, label, thesis, drivers…)
//     ↓
//   latestSentiment.set(...)                → in-memory cache for the HTTP API
//     ↓
//   publishSentiment(symbol, projection, NewsSentiment)
//     ↓  NewsSentiment Protobuf → Kafka topic: sentiment_signals (schema unchanged)
//
// Configuration (env vars):
//   NEWSDATA_API_KEY           — NewsData.io API key         (required)
//   LLM_API_KEY                — LLM provider API key        (required for scoring)
//   KAFKA_BROKER_URL           — Kafka broker                (default: localhost:9092)
//   REDIS_URL                  — Redis connection string     (default: redis://localhost:6379)
//   SENTIMENT_SYMBOLS          — comma-separated ticker list (default: RELIANCE)
//   SENTIMENT_POLL_INTERVAL_MS — poll cadence in ms         (default: 600000)
//
// Graceful shutdown:
//   SIGINT → disconnectProducer() + redis.quit() + process.exit(0)

import './loadEnv.js';  // MUST be first: loads the repo-root .env (LLM_*, NEWSDATA, etc.)
import { loadNewsSentimentType }                   from './protoLoader.js';
import { resolveProfileSeed }                      from './companyProfiles.js';
import { fetchCompanyProfile, fetchBasicFinancials } from './profile.js';
import { fetchStrategicNews }                      from './googleNewsFetcher.js';
import { analyzeStrategicSentiment }               from './analyzer.js';
import { connectProducer, publishSentiment, disconnectProducer } from './kafkaProducer.js';
import { createClient }                            from 'redis';
import http                                        from 'node:http';
import { metrics }                                 from './metrics.js';

// ── Configuration ─────────────────────────────────────────────────────────────

const SYMBOLS = (process.env.SENTIMENT_SYMBOLS ?? 'RELIANCE')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean);

const POLL_INTERVAL_MS = parseInt(process.env.SENTIMENT_POLL_INTERVAL_MS ?? '600000', 10);

// ── HTTP API for on-demand sentiment lookups ───────────────────────────────
// The Deep_Quant_Agent's `get_news_context` tool calls the Rust Tool_Server,
// which proxies to this HTTP endpoint (SENTIMENT_SERVICE_URL, default
// http://localhost:8090/sentiment). We serve the latest LLM-scored sentiment
// per symbol from an in-memory cache populated by the polling loop, so a
// directional classification is available on demand — not only via Kafka.
const HTTP_PORT = parseInt(process.env.SENTIMENT_HTTP_PORT ?? '8090', 10);

/**
 * In-memory cache of the latest RICH strategic verdict per symbol, served by
 * the HTTP API to the Deep_Quant_Agent's get_news_context tool.
 *
 * @type {Map<string, {
 *   symbol: string,
 *   conviction_score: number,
 *   label: string,
 *   thesis: string,
 *   drivers: Array<{category: string, headline: string, impact: string, weight: number}>,
 *   risks: string[],
 *   horizon: string,
 *   confidence: number,
 *   reasoning_snippet: string,
 *   headlines: string[],
 *   profile: (Object|null),
 *   industry: (string|null),
 *   updated_at: number
 * }>}
 */
const latestSentiment = new Map();

// ── Profile context cache (Redis, long TTL) ───────────────────────────────────
// Company profile + financials change slowly, so we cache them in Redis for ~24h
// to avoid hammering Finnhub every poll cycle. Falls back to a live fetch when
// Redis is unavailable.
const PROFILE_TTL_SECONDS = 86_400; // 24 h
const PROFILE_KEY_PREFIX  = 'sentiment:profile:';

// ── Redis client (for graceful shutdown reference) ────────────────────────────
// cache.js manages its own singleton internally; we create a second reference
// here only to expose .quit() in the SIGINT handler without breaking the cache
// module's internal state.  We import the same env var so both point to the
// same Redis instance.

const REDIS_URL  = process.env.REDIS_URL ?? 'redis://localhost:6379';
let   redisClient = null; // initialised inside run()

// ── getProfileContext ─────────────────────────────────────────────────────────

/**
 * Resolve the company profile + basic financials for a symbol, served from a
 * Redis cache (24 h TTL) when available, else fetched live from Finnhub and
 * cached. Always degrades gracefully: a Redis miss/error simply triggers a live
 * fetch; a Finnhub miss/error yields `{ profile: null, financials: null }`.
 *
 * @param {string} symbol - NSE ticker symbol.
 * @param {{finnhubSymbol: string}} seed - Resolved profile seed.
 * @returns {Promise<{profile: (Object|null), financials: (Object|null)}>}
 */
async function getProfileContext(symbol, seed) {
  const cacheKey = `${PROFILE_KEY_PREFIX}${symbol.toUpperCase()}`;

  // ── Try the Redis cache first ──────────────────────────────────────────────
  if (redisClient) {
    try {
      const cached = await redisClient.get(cacheKey);
      if (cached) {
        const parsed = JSON.parse(cached);
        console.log(`[index] Profile cache HIT for ${symbol}.`);
        return { profile: parsed.profile ?? null, financials: parsed.financials ?? null };
      }
    } catch (err) {
      console.warn(`[index] Profile cache read failed for ${symbol}: ${err.message}`);
      metrics.cacheError('profile_read');
    }
  }

  // ── Cache miss → best-effort live fetch (never throws) ─────────────────────
  let profile = null;
  let financials = null;
  try {
    profile = await fetchCompanyProfile(symbol, seed);
  } catch (err) {
    console.error(`[index] fetchCompanyProfile failed for ${symbol}: ${err.message}`);
  }
  try {
    financials = await fetchBasicFinancials(symbol, seed);
  } catch (err) {
    console.error(`[index] fetchBasicFinancials failed for ${symbol}: ${err.message}`);
  }

  // ── Persist to Redis with a long TTL (profiles change slowly) ──────────────
  if (redisClient) {
    try {
      await redisClient.set(
        cacheKey,
        JSON.stringify({ profile, financials }),
        { EX: PROFILE_TTL_SECONDS }
      );
    } catch (err) {
      console.warn(`[index] Profile cache write failed for ${symbol}: ${err.message}`);
      metrics.cacheError('profile_write');
    }
  }

  return { profile, financials };
}

// ── processTicker ─────────────────────────────────────────────────────────────

/**
 * Runs a single strategic poll cycle for one ticker symbol:
 *   1. Resolve the company profile seed (companyProfiles).
 *   2. Fetch + cache Finnhub profile/financials context (Redis, 24 h TTL).
 *   3. Fetch materiality-bucketed, deduplicated news (googleNewsFetcher).
 *   4. Synthesize a rich strategic verdict (analyzeStrategicSentiment).
 *   5. Store the rich verdict in the in-memory cache for the HTTP API.
 *   6. Publish a backward-compatible projection to Kafka (proto schema unchanged).
 *
 * All errors are caught and logged — a single bad symbol never kills the loop.
 *
 * @param {string}                   symbol       - NSE ticker (e.g. "RELIANCE").
 * @param {import('protobufjs').Type} NewsSentiment - Loaded proto type (injected once).
 * @returns {Promise<void>}
 */
async function processTicker(symbol, NewsSentiment) {
  console.log(`\n\x1b[36m[index]\x1b[0m ── Processing symbol: \x1b[1m\x1b[33m${symbol}\x1b[0m ──`);

  // ── Step 1: Resolve profile seed ───────────────────────────────────────────
  const seed = resolveProfileSeed(symbol);

  // ── Step 2: Profile + financials context (cached, best-effort) ─────────────
  const { profile, financials } = await getProfileContext(symbol, seed);

  // ── Step 3: Fetch strategic, category-tagged news ──────────────────────────
  let categorizedNews;
  try {
    categorizedNews = await fetchStrategicNews(symbol, seed);
  } catch (err) {
    console.error(`\x1b[31m[index] fetchStrategicNews failed for ${symbol}: ${err.message}\x1b[0m`);
    categorizedNews = [];
  }

  console.log(
    `\x1b[36m[index]\x1b[0m \x1b[1m${symbol}\x1b[0m: ${categorizedNews.length} new categorized article(s); ` +
    `profile=${profile ? 'yes' : 'no'} financials=${financials ? 'yes' : 'no'}.`
  );

  // ── Step 4: Synthesize the strategic verdict ───────────────────────────────
  // analyzeStrategicSentiment returns a neutral verdict for empty news without
  // calling the LLM; LLM/transport failures are caught here so the loop survives.
  let verdict;
  try {
    verdict = await analyzeStrategicSentiment(symbol, { profile, financials, categorizedNews });
  } catch (err) {
    console.error(`[index] analyzeStrategicSentiment failed for ${symbol}: ${err.message}`);
    // Not work — see metrics.js. Counting a failure as a beat would let an agent
    // failing every symbol on every cycle report a perfectly fresh heartbeat.
    metrics.classificationCompleted('failed');
    return; // Keep the last good verdict served by the HTTP API.
  }

  // THE WORK POINT. A completed per-symbol classification is the finest-grained
  // unit of real work this agent does, so it is what beats the monitoring
  // heartbeat. `neutral_no_news` is the no-LLM path taken when a symbol has no
  // fresh articles — with 10-minute polling that is the common case, and it does
  // beat: the loop ran and produced a verdict. What it does NOT prove is that
  // news is reaching the agent at all, which is why articles_total and
  // news_fetches_total exist alongside it.
  metrics.classificationCompleted(categorizedNews.length > 0 ? 'scored' : 'neutral_no_news');
  metrics.verdictProduced(verdict.label);

  // ── Step 5: Cache the RICH verdict for the HTTP API ────────────────────────
  // Headline count is capped so the panel has a bounded list, not because more
  // are unavailable — the fetcher gathers up to SENTIMENT_PER_BUCKET per bucket
  // across every bucket. Raised from a hardcoded 5 to 10.
  const maxHeadlines = (() => {
    const n = parseInt(process.env.SENTIMENT_MAX_HEADLINES ?? '10', 10);
    return Number.isFinite(n) && n > 0 ? n : 10;
  })();
  const headlines = categorizedNews.map((a) => a.title).filter(Boolean).slice(0, maxHeadlines);
  const reasoningSnippet = String(verdict.thesis ?? '').slice(0, 150);

  latestSentiment.set(symbol.toUpperCase(), {
    symbol,
    conviction_score:  verdict.conviction_score,
    label:             verdict.label,
    thesis:            verdict.thesis,
    drivers:           verdict.drivers,
    risks:             verdict.risks,
    horizon:           verdict.horizon,
    confidence:        verdict.confidence,
    reasoning_snippet: reasoningSnippet,
    headlines,
    profile,
    industry:          profile?.industry ?? seed.sector ?? null,
    updated_at:        Date.now(),
  });

  // Set from the map's own size rather than incremented, so the gauge cannot
  // drift from reality on a path that forgets to adjust it.
  metrics.setCachedSymbols(latestSentiment.size);

  // ── Step 6: Publish a backward-compatible projection to Kafka ──────────────
  // Map the rich verdict onto the EXISTING proto fields — schema unchanged:
  //   claude_conviction_score ← conviction_score
  //   reasoning_snippet        ← thesis (truncated to 150)
  //   headline                 ← top driver headline (else first news title)
  const topDriverHeadline =
    verdict.drivers?.find((d) => d.headline)?.headline ?? headlines[0] ?? '';

  const kafkaPayload = {
    conviction_score:  verdict.conviction_score,
    reasoning_snippet: reasoningSnippet,
    headline:          topDriverHeadline,
  };

  try {
    await publishSentiment(symbol, kafkaPayload, NewsSentiment);
  } catch (err) {
    // publishSentiment already handles errors internally, but catch here too.
    console.error(`[index] publishSentiment failed for ${symbol}: ${err.message}`);
  }

  console.log(`\x1b[36m[index]\x1b[0m \x1b[32m✅ Cycle complete for \x1b[1m${symbol}\x1b[0m. label=\x1b[33m${verdict.label}\x1b[0m score=\x1b[36m${verdict.conviction_score}\x1b[0m\x1b[32m\x1b[0m`);
}

// ── HTTP API server ─────────────────────────────────────────────────────────

/**
 * Start a minimal HTTP server exposing the latest computed sentiment per symbol.
 *
 * Routes:
 *   GET /sentiment?symbol=SYM  → 200 the RICH strategic verdict object:
 *                                  { symbol, conviction_score, label, thesis, drivers,
 *                                    risks, horizon, confidence, reasoning_snippet,
 *                                    headlines, profile, industry, updated_at }
 *                                404 when no sentiment has been computed yet for SYM
 *                                400 when the symbol query param is missing
 *   GET /health                → 200 { status: "ok", symbols: [...] }
 *
 * The Rust Tool_Server's get_news_context proxy reads the strategic fields
 * (label/thesis/drivers) when present, falling back to a conviction_score→label
 * mapping otherwise; on a non-200 it returns the honest "Unavailable" marker.
 *
 * @returns {import('node:http').Server}
 */
function startSentimentHttpServer(NewsSentiment) {
  // Symbols with an on-demand classification currently in flight, so concurrent
  // requests for the same uncached symbol coalesce onto one run instead of
  // launching a storm of duplicate LLM classifications.
  const onDemandInFlight = new Map(); // symbol → Promise<void>

  // Max time a request waits for a fresh on-demand classification before
  // degrading to 404 (the Rust proxy then serves headlines-only). The
  // classification keeps running in the background so a later request is served
  // from cache.
  const ON_DEMAND_TIMEOUT_MS = parseInt(process.env.SENTIMENT_ON_DEMAND_TIMEOUT_MS ?? '25000', 10);

  /**
   * Classify a symbol on demand (cache miss), coalescing concurrent callers.
   * Never throws — a failure simply leaves the cache unpopulated.
   * @param {string} symbol - Upper-cased NSE ticker.
   * @returns {Promise<void>}
   */
  function classifyOnDemand(symbol) {
    if (onDemandInFlight.has(symbol)) return onDemandInFlight.get(symbol);
    if (!NewsSentiment) return Promise.resolve();
    console.log(`[index] On-demand sentiment classification requested for ${symbol}.`);
    const run = processTicker(symbol, NewsSentiment)
      .catch((err) => {
        console.error(`[index] On-demand classification failed for ${symbol}: ${err.message}`);
      })
      .finally(() => {
        onDemandInFlight.delete(symbol);
      });
    onDemandInFlight.set(symbol, run);
    return run;
  }

  const server = http.createServer(async (req, res) => {
    const sendJson = (status, body) => {
      res.writeHead(status, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(body));
    };
    try {
      const url = new URL(req.url, `http://localhost:${HTTP_PORT}`);

      if (req.method === 'GET' && url.pathname === '/health') {
        metrics.httpRequestCompleted('/health', 200);
        return sendJson(200, { status: 'ok', symbols: [...latestSentiment.keys()] });
      }

      if (req.method === 'GET' && url.pathname === '/sentiment') {
        const symbol = (url.searchParams.get('symbol') ?? '').trim().toUpperCase();
        if (!symbol) {
          metrics.httpRequestCompleted('/sentiment', 400);
          return sendJson(400, { error: 'symbol query parameter is required' });
        }
        let entry = latestSentiment.get(symbol);
        const wasMiss = !entry;
        if (!entry) {
          // Cache miss → classify this symbol on demand rather than serving a
          // permanent 404 for every symbol outside the fixed polling set. Wait
          // up to ON_DEMAND_TIMEOUT_MS for a fresh verdict; if it does not
          // arrive in time, degrade to 404 (the proxy falls back to
          // headlines-only) while the classification finishes in the background.
          const classification = classifyOnDemand(symbol);
          let timer;
          const timeout = new Promise((resolve) => {
            timer = setTimeout(resolve, ON_DEMAND_TIMEOUT_MS);
          });
          await Promise.race([classification, timeout]);
          clearTimeout(timer);
          entry = latestSentiment.get(symbol);
        }
        if (!entry) {
          // Still no classification (in flight or failed) — the proxy treats a
          // non-200 as "Unavailable" and reads the headlines directly.
          //
          // Which of the two it was is worth separating: a `timeout` means the
          // work is still running and a later request will be served from cache,
          // while `failed` means it finished with nothing and the next request
          // will pay the same cost again. The in-flight map is the evidence —
          // classifyOnDemand deletes the entry in its .finally.
          metrics.onDemandCompleted(onDemandInFlight.has(symbol) ? 'timeout' : 'failed');
          metrics.httpRequestCompleted('/sentiment', 404);
          return sendJson(404, { error: `no sentiment computed yet for ${symbol}` });
        }
        if (wasMiss) metrics.onDemandCompleted('served');
        metrics.httpRequestCompleted('/sentiment', 200);
        return sendJson(200, entry);
      }

      // Unrouted paths are deliberately not counted: doing so would mint a
      // metric series from arbitrary request URLs.
      return sendJson(404, { error: 'not found' });
    } catch (err) {
      return sendJson(500, { error: `internal error: ${err.message}` });
    }
  });

  server.on('error', (err) => {
    console.error(`[index] Sentiment HTTP server error: ${err.message}`);
  });

  server.listen(HTTP_PORT, () => {
    console.log(`[index] ✅ Sentiment HTTP API listening on http://localhost:${HTTP_PORT}/sentiment`);
  });

  return server;
}

// ── run ───────────────────────────────────────────────────────────────────────

/**
 * Main entry point:
 *   0. Start the Prometheus surface (before anything that can block or fail).
 *   1. Load the Protobuf schema (once, shared across all publish calls).
 *   2. Connect to Kafka.
 *   3. Connect to Redis (for graceful-shutdown reference).
 *   4. Start the setInterval polling loop immediately + every POLL_INTERVAL_MS.
 *   5. Register SIGINT handler for graceful shutdown.
 *
 * @returns {Promise<void>}
 */
async function run() {
  console.log('╔═══════════════════════════════════════════════════════════╗');
  console.log('║  Sentiment Agent — NLP Polling Loop (Subphases 34-36)    ║');
  console.log('║  LLM · Redis · Kafka Protobuf Pipeline                     ║');
  console.log('╚═══════════════════════════════════════════════════════════╝\n');

  // ── 0. Prometheus surface (:9108) ─────────────────────────────────────────
  // First, deliberately: kafkajs retries a broker connection with backoff before
  // giving up, and during that window this is the difference between Prometheus
  // scraping an honest `idle` and scraping nothing at all — and a failed scrape
  // is indistinguishable from a service that was never deployed.
  //
  // On its own port rather than bolted onto the API below: that one is reachable
  // through the tool-server, this one is for Prometheus alone, and a wedged API
  // server must not take the monitoring surface down with it.
  const metricsServer = metrics.serve();

  // ── 1. Load Protobuf schema ───────────────────────────────────────────────
  console.log('[index] Loading NewsSentiment Protobuf schema...');
  let NewsSentiment;
  try {
    NewsSentiment = await loadNewsSentimentType();
    console.log('[index] ✅ Protobuf schema loaded.');
  } catch (err) {
    console.error(`[index] FATAL: Failed to load Protobuf schema: ${err.message}`);
    process.exit(1);
  }

  // ── 2. Connect Kafka producer ─────────────────────────────────────────────
  console.log('[index] Connecting Kafka producer...');
  try {
    await connectProducer();
    console.log('[index] ✅ Kafka producer connected.');
  } catch (err) {
    console.error(`[index] FATAL: Kafka producer connection failed: ${err.message}`);
    process.exit(1);
  }

  // ── 3. Connect Redis (shutdown reference) ─────────────────────────────────
  console.log('[index] Connecting Redis client...');
  try {
    redisClient = createClient({ url: REDIS_URL });
    redisClient.on('error', (err) => {
      console.error(`[index] Redis client error: ${err.message}`);
    });
    await redisClient.connect();
    console.log('[index] ✅ Redis client connected.');
  } catch (err) {
    // Redis failure is non-fatal for startup; cache.js handles its own connection.
    console.warn(`[index] Redis connection warning: ${err.message}`);
  }

  // ── 3b. Start the on-demand sentiment HTTP API ───────────────────────────
  const httpServer = startSentimentHttpServer(NewsSentiment);

  // ── 4. Poll loop ──────────────────────────────────────────────────────────
  console.log(
    `\n[index] Starting polling loop. Symbols=[${SYMBOLS.join(', ')}]  ` +
    `Interval=${POLL_INTERVAL_MS}ms\n`
  );

  /**
   * Single poll cycle — iterates over all configured symbols sequentially.
   * Errors per symbol are caught inside processTicker; the overall loop
   * continues regardless.
   */
  const pollCycle = async () => {
    console.log(`\n\x1b[36m[index]\x1b[0m \x1b[32m══ Poll cycle started at ${new Date().toISOString()} ══\x1b[0m`);
    for (const symbol of SYMBOLS) {
      await processTicker(symbol, NewsSentiment);
    }
    console.log(`\x1b[36m[index]\x1b[0m \x1b[32m══ Poll cycle complete. Next run in ${POLL_INTERVAL_MS / 1000}s ══\x1b[0m\n`);
  };

  // Run immediately on startup, then on every interval. Arming stall detection
  // here rather than at construction means a process that dies during Kafka or
  // Redis startup reports `idle`, not a stall it never had the chance to avoid.
  metrics.markPollLoopRunning();
  await pollCycle();
  setInterval(pollCycle, POLL_INTERVAL_MS);

  // ── 5. Graceful shutdown ──────────────────────────────────────────────────
  process.on('SIGINT', async () => {
    console.log('\n[index] 🛑 SIGINT received — shutting down gracefully...');

    try {
      httpServer.close();
      console.log('[index] Sentiment HTTP API closed.');
    } catch (err) {
      console.error(`[index] Error closing HTTP server: ${err.message}`);
    }

    try {
      metricsServer?.close();
      console.log('[index] Metrics listener closed.');
    } catch (err) {
      console.error(`[index] Error closing metrics listener: ${err.message}`);
    }

    try {
      await disconnectProducer();
    } catch (err) {
      console.error(`[index] Error disconnecting Kafka producer: ${err.message}`);
    }

    if (redisClient) {
      try {
        await redisClient.quit();
        console.log('[index] Redis client disconnected cleanly.');
      } catch (err) {
        console.error(`[index] Error disconnecting Redis client: ${err.message}`);
      }
    }

    console.log('[index] Goodbye. ✅');
    process.exit(0);
  });
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

run().catch((err) => {
  console.error('[index] Fatal unhandled error:', err);
  process.exit(1);
});
