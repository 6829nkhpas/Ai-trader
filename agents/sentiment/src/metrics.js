// metrics.js — Prometheus surface for the Sentiment Agent (:9108).
//
// The Node counterpart of the shared Rust `service-metrics` crate. It cannot
// reuse that crate, so it reimplements its CONTRACT — same metric names, same
// `service` label, same /metrics, /health, /ready routes, same ok|stalled|idle
// readiness JSON — because status-api classifies all nine services through one
// code path. A service reporting `sentiment_heartbeat_age` instead of
// `sentiment_last_work_age_seconds` would drop out of the dashboard as
// "unknown", which reads identically to "not deployed".
//
// WHAT COUNTS AS WORK. The agent polls NewsData.io every
// SENTIMENT_POLL_INTERVAL_MS (default 10 min) and produces one verdict per
// symbol. The heartbeat beats on a completed per-symbol classification — the
// finest-grained real work — not on the poll cycle, so a loop that keeps ticking
// while every symbol throws reads as a stall rather than as health.
//
// A quiet news day DOES beat. `analyzeStrategicSentiment` returns a neutral
// verdict without calling the LLM when a symbol has no fresh articles, and with
// 10-minute polling that is the common case. Withholding the beat there would
// report a stall on any normal afternoon, so the beat means "the loop ran and
// produced a verdict", not "the verdict was interesting".
//
// The cost of that choice, stated plainly: with NEWSDATA_API_KEY unset every
// symbol takes the empty-news path, so the heartbeat stays perfectly fresh while
// the agent learns nothing. The heartbeat is the wrong instrument for that
// failure — `news_fetches_total{outcome="no_api_key"}` and a flat
// `articles_total` are the right ones, and Phase 3 should alert on those rather
// than on staleness. Folding it into the heartbeat would make "stalled" mean two
// different things and leave neither actionable.
//
// SESSION AWARENESS IS DELIBERATELY ABSENT. The Rust crate widens its threshold
// 20x outside NSE hours because a tick pipeline with nothing to consume is
// healthy. That does not transfer: this loop is driven by setInterval, not by the
// market, and news breaks overnight and at weekends. It should be classifying at
// 03:00 exactly as at noon, so `market_session` is reported as context but never
// widens the threshold.

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import client from 'prom-client';

// ── Contract constants ───────────────────────────────────────────────────────

const SERVICE = 'sentiment';
const PREFIX = 'sentiment';
const DEFAULT_METRICS_PORT = 9108;

/**
 * Seconds without a completed classification that counts as a stall.
 *
 * Derived from the poll interval — 2.5 cycles — rather than fixed. One cycle is
 * too tight, since a single slow LLM call trips it, while a hardcoded constant
 * would be silently wrong for anyone tuning SENTIMENT_POLL_INTERVAL_MS, and
 * wrong in the dangerous direction if they lengthen it: the alert would fire on
 * every normal cycle and get muted.
 *
 * The floor guards the opposite mistake. A short interval set for testing would
 * otherwise give a threshold below the duration of one LLM call (legitimately
 * 30s+), leaving the service permanently "stalled".
 */
const POLL_INTERVAL_MS = parseInt(process.env.SENTIMENT_POLL_INTERVAL_MS ?? '600000', 10);
const STALL_SECONDS = Math.max(300, (POLL_INTERVAL_MS / 1000) * 2.5);

/**
 * Read the package version for `build_info`.
 *
 * From package.json rather than `process.env.npm_package_version`, which npm
 * only sets when the process is launched through an npm script — the Dockerfile
 * runs `node src/index.js` directly, so that variable is absent exactly where
 * the label matters most.
 */
function readVersion() {
  try {
    const here = path.dirname(fileURLToPath(import.meta.url));
    const pkg = JSON.parse(fs.readFileSync(path.join(here, '..', 'package.json'), 'utf8'));
    return pkg.version ?? 'unknown';
  } catch {
    return 'unknown';
  }
}

/**
 * Every label value that exists, instantiated at 0 on startup.
 *
 * A counter with no series yet is simply absent from /metrics, and `rate()` over
 * an absent series renders as "no data" — visually identical to a failed scrape.
 * These are precisely the series that stay absent while everything works, so
 * pre-creating them is what makes "zero errors" distinguishable from "this
 * metric never existed".
 */
const CLASSIFY_OUTCOMES = ['scored', 'neutral_no_news', 'failed'];
const NEWS_OUTCOMES = ['ok', 'http_error', 'network_error', 'no_api_key', 'skipped'];
const LLM_OUTCOMES = ['ok', 'http_error', 'network_error', 'parse_error', 'no_api_key'];
/** LLM outcomes that involved a real request, so a latency is meaningful. */
const LLM_TIMED_OUTCOMES = ['ok', 'http_error', 'network_error', 'parse_error'];
const VERDICT_LABELS = ['Bullish', 'Bearish', 'Neutral'];
const PUBLISH_OUTCOMES = ['ok', 'encode_error', 'send_error', 'not_connected'];
const ON_DEMAND_OUTCOMES = ['served', 'timeout', 'failed'];
const CACHE_OPERATIONS = ['dedup_check', 'dedup_mark', 'profile_read', 'profile_write'];
const HTTP_ROUTES = ['/sentiment', '/health'];
const HTTP_STATUSES = ['200', '400', '404', '500'];

/**
 * LLM latency buckets, in seconds. Same range as the Rust services: a completion
 * returning in under a second and one grinding for two minutes must both land
 * somewhere other than `+Inf`.
 */
const LLM_LATENCY_BUCKETS = [0.5, 1, 2, 5, 10, 20, 30, 60, 120];

// ── Heartbeat ────────────────────────────────────────────────────────────────

/**
 * Tracks when the service last completed a unit of real work.
 *
 * Mirrors `service-metrics::Heartbeat`. Before the first beat the age measures
 * from process start rather than sitting at zero — otherwise an agent that boots
 * and never classifies anything reports perfect health forever, which is the
 * exact failure this exists to catch.
 */
class Heartbeat {
  constructor() {
    this.startedMs = Date.now();
    this.lastWorkMs = 0;
    this.workCount = 0;
  }

  /** Record one completed unit of real work. */
  beat() {
    this.lastWorkMs = Date.now();
    this.workCount += 1;
  }

  hasWorked() {
    return this.lastWorkMs !== 0;
  }

  /** Seconds since the last completed work, or since start if there is none. */
  lastWorkAgeSeconds() {
    const reference = this.lastWorkMs === 0 ? this.startedMs : this.lastWorkMs;
    // Math.max guards a backwards clock step: a negative age would read as
    // "just worked" and suppress a real stall.
    return Math.max(0, Date.now() - reference) / 1000;
  }

  uptimeSeconds() {
    return Math.max(0, Date.now() - this.startedMs) / 1000;
  }
}

// ── Market session ───────────────────────────────────────────────────────────

/**
 * Classify an instant against NSE hours (09:15-15:30 IST, weekdays).
 *
 * Reported for context only — unlike the Rust crate it does NOT widen this
 * service's stall threshold. See the module header.
 *
 * @param {Date} [at] - Instant to classify; defaults to now.
 * @returns {'open'|'closed'|'weekend'}
 */
export function marketSession(at = new Date()) {
  // IST is UTC+05:30 year-round — India observes no DST, so a fixed offset is
  // exact rather than an approximation.
  const ist = new Date(at.getTime() + (5 * 60 + 30) * 60 * 1000);

  // getUTC* on the shifted instant reads IST wall-clock without depending on the
  // host timezone, which is UTC in Docker and anything at all on a dev machine.
  const day = ist.getUTCDay();
  if (day === 0 || day === 6) return 'weekend';

  const minutes = ist.getUTCHours() * 60 + ist.getUTCMinutes();
  // Both bounds inclusive: 09:15:00 is open and 15:30:00 is still in session,
  // since the closing auction lands on it.
  return minutes >= 9 * 60 + 15 && minutes <= 15 * 60 + 30 ? 'open' : 'closed';
}

// ── SentimentMetrics ─────────────────────────────────────────────────────────

/**
 * The sentiment agent's Prometheus handles.
 *
 * Construction never throws. On registry failure every method becomes a no-op,
 * so instrumentation cannot take down the service it observes — the same
 * inert-handle degradation the Rust services use, for the same reason: call
 * sites stay unconditional and readable.
 */
export class SentimentMetrics {
  constructor() {
    this.heartbeat = new Heartbeat();
    /**
     * Whether the service currently has anything to do. Unlike the tool-server
     * (idle by design), this agent has a fixed poll loop, so work is always
     * expected once that loop runs. Set by `markPollLoopRunning()` rather than
     * here, so a process dying during startup is not reported as stalled when it
     * never got as far as polling.
     */
    this.workExpected = false;
    this.inner = null;

    try {
      this.inner = this.#build();
    } catch (err) {
      console.error(
        `[metrics] Registry construction failed — metrics disabled, service continues: ${err.message}`
      );
    }
  }

  /** @returns {Object} the built handles. Throws on any registry error. */
  #build() {
    // A private registry, not prom-client's global one: two SentimentMetrics
    // instances (a test alongside the singleton) would otherwise collide on
    // duplicate metric names and throw at construction.
    const registry = new client.Registry();
    registry.setDefaultLabels({ service: SERVICE });

    // process_* / nodejs_* — event-loop lag is the Node-specific signal the Rust
    // services have no equivalent for, and it is how a blocked loop shows up
    // while the process still answers /health.
    client.collectDefaultMetrics({ register: registry });

    const gauge = (name, help) => new client.Gauge({ name, help, registers: [registry] });
    const counter = (name, help, labelNames = []) =>
      new client.Counter({ name, help, labelNames, registers: [registry] });

    const up = gauge(
      `${PREFIX}_up`,
      '1 while the metrics listener is serving. Absence of the metric — a failed scrape — is the real down signal.'
    );
    up.set(1);

    const buildInfo = new client.Gauge({
      name: `${PREFIX}_build_info`,
      help: 'Always 1; carries the package version as a label so a deploy shows up as a series change.',
      labelNames: ['version'],
      registers: [registry],
    });
    buildInfo.labels(readVersion()).set(1);

    const handles = {
      registry,
      up,
      buildInfo,

      // ── Shared contract ────────────────────────────────────────────────────
      uptime: gauge(
        `${PREFIX}_uptime_seconds`,
        'Seconds since process start. A resetting value means crash-looping.'
      ),
      lastWorkAge: gauge(
        `${PREFIX}_last_work_age_seconds`,
        'Seconds since the last completed symbol classification. The primary working-vs-failing signal; compare against sentiment_stall_threshold_seconds.'
      ),
      stallThreshold: gauge(
        `${PREFIX}_stall_threshold_seconds`,
        'Age above which this service is stalled. Derived from SENTIMENT_POLL_INTERVAL_MS (2.5 cycles) and NOT widened off-session: news breaks overnight and this agent should be working then.'
      ),
      session: gauge(
        `${PREFIX}_market_session_open`,
        "1 during NSE hours (09:15-15:30 IST, weekdays) else 0. Context only — it does not affect this service's stall threshold."
      ),
      workExpectedGauge: gauge(
        `${PREFIX}_work_expected`,
        '1 once the poll loop is running, 0 during startup. Staleness is only a stall while this is 1.'
      ),
      workTotal: counter(
        `${PREFIX}_work_completed_total`,
        'Symbol classifications completed since start. Its rate is the throughput signal; a flat line is a stall.'
      ),

      // ── Classification ────────────────────────────────────────────────────
      classifications: counter(
        `${PREFIX}_classifications_total`,
        'Per-symbol classification attempts by outcome. `neutral_no_news` is the cheap path taken when a symbol has no fresh articles — a success, not a degradation.',
        ['outcome']
      ),
      verdicts: counter(
        `${PREFIX}_verdicts_total`,
        'Verdicts by label. A distribution pinned entirely to Neutral usually means the analyzer is falling back rather than genuinely reading neutral news.',
        ['label']
      ),

      // ── News ingestion ────────────────────────────────────────────────────
      newsFetches: counter(
        `${PREFIX}_news_fetches_total`,
        'NewsData.io bucket queries by outcome. The free tier is ~200 credits/day, so a sustained http_error rate usually means exhausted quota rather than an outage. `skipped` is a bucket with no usable query (SECTOR_MACRO without a known sector) and is normal. `no_api_key` is the one exception to the per-query unit: no query is issued at all, so it counts once per symbol per cycle.',
        ['outcome']
      ),
      articles: counter(
        `${PREFIX}_articles_total`,
        'New, deduplicated articles accepted for scoring. Read next to llm_calls_total: articles arriving with no LLM calls means a broken analyzer, while neither moving is just a quiet news day.'
      ),
      articlesDeduped: counter(
        `${PREFIX}_articles_deduped_total`,
        'Articles skipped as already seen, in-cycle or via the 24h Redis window. If this falls to zero while articles_total keeps rising, dedup has broken and the agent is re-spending LLM credits on news it already scored.'
      ),
      cacheErrors: counter(
        `${PREFIX}_cache_errors_total`,
        'Redis operations that failed, by operation. A dedup_check failure is treated as "not seen", so a Redis outage shows up here first and as duplicate LLM spend second.',
        ['operation']
      ),

      // ── LLM ───────────────────────────────────────────────────────────────
      llmCalls: counter(
        `${PREFIX}_llm_calls_total`,
        'LLM classification calls by outcome. Only made when a symbol has fresh articles, so a low count is normal. `parse_error` means the provider answered but not with JSON — usually a model change rather than an outage.',
        ['outcome']
      ),
      llmLatency: new client.Histogram({
        name: `${PREFIX}_llm_duration_seconds`,
        help: 'LLM request latency, labelled by outcome so a provider hanging until timeout is distinguishable from one refusing instantly. Only observed when a request was actually issued.',
        labelNames: ['outcome'],
        buckets: LLM_LATENCY_BUCKETS,
        registers: [registry],
      }),

      // ── Downstream ────────────────────────────────────────────────────────
      publishes: counter(
        `${PREFIX}_publishes_total`,
        'Kafka publishes by outcome. Failures leave the verdict cached and still served over HTTP, so they are invisible to the tool-server while every Kafka consumer goes blind. `encode_error` is proto drift; `send_error` is the broker.',
        ['outcome']
      ),
      cachedSymbols: gauge(
        `${PREFIX}_cached_symbols`,
        'Symbols with a verdict in the in-memory cache — what the HTTP API can answer without an on-demand classification.'
      ),
      httpRequests: counter(
        `${PREFIX}_http_requests_total`,
        'HTTP API requests by route and status. The 404s on /sentiment are the interesting ones: they are what makes the tool-server fall back to headlines-only.',
        ['route', 'status']
      ),
      onDemand: counter(
        `${PREFIX}_on_demand_total`,
        'Cache-miss requests by outcome — per REQUEST, not per classification: concurrent callers for one uncached symbol coalesce onto a single run and are counted separately, which is the useful reading, since each one is a caller that waited. `timeout` means the caller gave up and got headlines-only while the work continued in the background.',
        ['outcome']
      ),
    };

    // Instantiate every label series at zero — see the label-set block above.
    for (const o of CLASSIFY_OUTCOMES) handles.classifications.labels(o).inc(0);
    for (const l of VERDICT_LABELS) handles.verdicts.labels(l).inc(0);
    for (const o of NEWS_OUTCOMES) handles.newsFetches.labels(o).inc(0);
    for (const o of LLM_OUTCOMES) handles.llmCalls.labels(o).inc(0);
    for (const o of LLM_TIMED_OUTCOMES) handles.llmLatency.labels(o);
    for (const o of PUBLISH_OUTCOMES) handles.publishes.labels(o).inc(0);
    for (const o of ON_DEMAND_OUTCOMES) handles.onDemand.labels(o).inc(0);
    for (const op of CACHE_OPERATIONS) handles.cacheErrors.labels(op).inc(0);
    for (const r of HTTP_ROUTES) {
      for (const s of HTTP_STATUSES) handles.httpRequests.labels(r, s).inc(0);
    }

    return handles;
  }

  // ── Recording ──────────────────────────────────────────────────────────────

  /**
   * A symbol classification finished.
   *
   * THE WORK POINT. Beats only on a real per-symbol result, never on a poll
   * cycle, so a loop that keeps ticking while every symbol throws reads as a
   * stall rather than as health.
   *
   * @param {'scored'|'neutral_no_news'|'failed'} outcome
   */
  classificationCompleted(outcome) {
    if (!this.inner) return;
    this.inner.classifications.labels(outcome).inc();
    // A failure is not work. Counting it would let an agent failing every symbol
    // on every cycle report a perfectly fresh heartbeat.
    if (outcome !== 'failed') {
      this.heartbeat.beat();
      this.inner.workTotal.inc();
    }
  }

  /**
   * A verdict was produced.
   * @param {string} label - Bullish|Bearish|Neutral; anything else folds into
   *   Neutral rather than minting a series from model output.
   */
  verdictProduced(label) {
    if (!this.inner) return;
    this.inner.verdicts.labels(VERDICT_LABELS.includes(label) ? label : 'Neutral').inc();
  }

  /**
   * One NewsData.io bucket query finished.
   * @param {'ok'|'http_error'|'network_error'|'no_api_key'|'skipped'} outcome
   */
  newsFetchCompleted(outcome) {
    if (!this.inner) return;
    this.inner.newsFetches.labels(outcome).inc();
  }

  /**
   * Articles accepted for scoring, and articles dropped as duplicates.
   * @param {number} accepted
   * @param {number} deduped
   */
  articlesCollected(accepted, deduped) {
    if (!this.inner) return;
    if (accepted > 0) this.inner.articles.inc(accepted);
    if (deduped > 0) this.inner.articlesDeduped.inc(deduped);
  }

  /**
   * A Redis operation failed.
   * @param {'dedup_check'|'dedup_mark'|'profile_read'|'profile_write'} operation
   */
  cacheError(operation) {
    if (!this.inner) return;
    if (!CACHE_OPERATIONS.includes(operation)) return;
    this.inner.cacheErrors.labels(operation).inc();
  }

  /**
   * An LLM classification call finished.
   * @param {'ok'|'http_error'|'network_error'|'parse_error'|'no_api_key'} outcome
   * @param {number|null} [seconds] - Request latency; omitted when no request was
   *   issued, since a duration for a missing API key measures nothing.
   */
  llmCallCompleted(outcome, seconds = null) {
    if (!this.inner) return;
    this.inner.llmCalls.labels(outcome).inc();
    if (seconds !== null && LLM_TIMED_OUTCOMES.includes(outcome)) {
      this.inner.llmLatency.labels(outcome).observe(seconds);
    }
  }

  /**
   * A Kafka publish resolved.
   * @param {'ok'|'encode_error'|'send_error'|'not_connected'} outcome
   */
  publishCompleted(outcome) {
    if (!this.inner) return;
    this.inner.publishes.labels(outcome).inc();
  }

  /**
   * Report the verdict cache size.
   *
   * Set from the map's own size rather than incremented, so it cannot drift from
   * reality on a path that forgets to adjust it.
   *
   * @param {number} count
   */
  setCachedSymbols(count) {
    if (!this.inner) return;
    this.inner.cachedSymbols.set(count);
  }

  /**
   * An HTTP API request was served.
   * @param {string} route - A known route; anything else is dropped rather than
   *   building an unbounded label series out of arbitrary request paths.
   * @param {number} status
   */
  httpRequestCompleted(route, status) {
    if (!this.inner) return;
    if (!HTTP_ROUTES.includes(route)) return;
    this.inner.httpRequests.labels(route, String(status)).inc();
  }

  /**
   * An on-demand classification resolved.
   * @param {'served'|'timeout'|'failed'} outcome
   */
  onDemandCompleted(outcome) {
    if (!this.inner) return;
    this.inner.onDemand.labels(outcome).inc();
  }

  /** Mark the poll loop as running, arming stall detection. */
  markPollLoopRunning() {
    this.workExpected = true;
  }

  // ── Reporting ──────────────────────────────────────────────────────────────

  /** Pull heartbeat and clock values into the sampled gauges. */
  #refresh() {
    if (!this.inner) return;
    const i = this.inner;
    i.up.set(1);
    i.uptime.set(this.heartbeat.uptimeSeconds());
    i.lastWorkAge.set(this.heartbeat.lastWorkAgeSeconds());
    i.stallThreshold.set(STALL_SECONDS);
    i.session.set(marketSession() === 'open' ? 1 : 0);
    i.workExpectedGauge.set(this.workExpected ? 1 : 0);
  }

  /**
   * The service's own answer to "am I working?".
   * @returns {{status: 'ok'|'stalled'|'idle', stalled: boolean,
   *   workExpected: boolean, ageSeconds: number, thresholdSeconds: number,
   *   session: string, workCompleted: number, uptimeSeconds: number}}
   */
  readiness() {
    const age = this.heartbeat.lastWorkAgeSeconds();
    const stalled = this.workExpected && age > STALL_SECONDS;
    return {
      status: stalled ? 'stalled' : this.workExpected ? 'ok' : 'idle',
      stalled,
      workExpected: this.workExpected,
      ageSeconds: age,
      thresholdSeconds: STALL_SECONDS,
      session: marketSession(),
      workCompleted: this.heartbeat.workCount,
      uptimeSeconds: this.heartbeat.uptimeSeconds(),
    };
  }

  /** The /health and /ready body — field-for-field the Rust services' shape. */
  readinessJson() {
    const r = this.readiness();
    return {
      service: SERVICE,
      status: r.status,
      market_session: r.session,
      work_expected: r.workExpected,
      last_work_age_seconds: Number(r.ageSeconds.toFixed(1)),
      stall_threshold_seconds: Number(r.thresholdSeconds.toFixed(1)),
      work_completed: r.workCompleted,
      uptime_seconds: Number(r.uptimeSeconds.toFixed(1)),
    };
  }

  /** Render the registry in Prometheus text format. */
  async render() {
    if (!this.inner) return '';
    this.#refresh();
    return this.inner.registry.metrics();
  }

  /**
   * Start the /metrics, /health and /ready listener.
   *
   * Separate from the agent's own :8090 API on purpose: that port is reachable
   * through the tool-server, while this one is for Prometheus alone. It also
   * means a wedged API server does not take the monitoring surface down with it.
   *
   * @returns {import('node:http').Server|null}
   */
  serve() {
    if (!this.inner) return null;
    const port = parseInt(process.env.METRICS_PORT ?? String(DEFAULT_METRICS_PORT), 10);

    const server = http.createServer(async (req, res) => {
      try {
        const route = (req.url ?? '/').split('?')[0];

        if (route === '/metrics') {
          const body = await this.render();
          res.writeHead(200, { 'Content-Type': this.inner.registry.contentType });
          return res.end(body);
        }

        if (route === '/health' || route === '/ready') {
          const body = this.readinessJson();
          // /health answers 200 whenever the process is alive: a stalled service
          // is running, and letting Docker restart it on a stall would turn a
          // visible problem into a crash loop. /ready is the one that fails, so
          // a stall stays actionable without being self-inflicted.
          const code = route === '/ready' && body.status === 'stalled' ? 503 : 200;
          res.writeHead(code, { 'Content-Type': 'application/json' });
          return res.end(JSON.stringify(body));
        }

        res.writeHead(404, { 'Content-Type': 'application/json' });
        return res.end(JSON.stringify({ error: 'not found' }));
      } catch (err) {
        res.writeHead(500, { 'Content-Type': 'application/json' });
        return res.end(JSON.stringify({ error: `metrics error: ${err.message}` }));
      }
    });

    server.on('error', (err) => {
      // Never fatal. A port clash must not stop the agent doing its job.
      console.error(`[metrics] Listener error: ${err.message}`);
    });

    server.listen(port, () => {
      console.log(`[metrics] ✅ Prometheus surface on http://localhost:${port}/metrics`);
    });

    // Do not hold the event loop open on this socket alone: if the agent's own
    // work is finished, the process should still be free to exit.
    server.unref();
    return server;
  }
}

/**
 * The process-wide handle.
 *
 * A singleton rather than a value threaded through every signature, because the
 * failure detail worth recording lives deep inside strategicFetcher and
 * analyzer — an HTTP 429 from NewsData.io is only distinguishable from a socket
 * timeout at the catch site, and passing a handle down to reach it would rewrite
 * the signature of every module in the pipeline for one counter each.
 */
export const metrics = new SentimentMetrics();

// Exported for tests.
export const _internals = { Heartbeat, STALL_SECONDS, DEFAULT_METRICS_PORT };
