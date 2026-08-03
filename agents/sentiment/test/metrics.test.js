// metrics.test.js — contract tests for the sentiment agent's Prometheus surface.
//
// Run with `npm test` (node:test, no test framework dependency — the image is
// built with `npm ci --omit=dev`, so a devDependency here would be absent in
// prod anyway).
//
// These assert the two things that are easy to get wrong and impossible to
// notice from outside: that the heartbeat beats on real work ONLY, and that the
// exported names match what status-api will look for. A rename that silently
// drops this service out of the dashboard would otherwise look exactly like a
// service that was never deployed.

import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';

import { SentimentMetrics, marketSession, _internals } from '../src/metrics.js';

const { Heartbeat, STALL_SECONDS } = _internals;

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Find a rendered series and return its value.
 *
 * Matches on label PAIRS rather than the whole line, because prom-client's label
 * ordering is its own business — an assertion pinned to a full rendered string
 * would break on a library upgrade that reorders them, which is not a fact worth
 * testing.
 *
 * @param {string} text - Rendered exposition.
 * @param {string} name - Metric name.
 * @param {Object<string,string>} [labels] - Label pairs that must be present.
 * @returns {number|null} The value, or null when no such series exists.
 */
function seriesValue(text, name, labels = {}) {
  for (const line of text.split('\n')) {
    if (line.startsWith('#') || !line.startsWith(`${name}{`)) continue;
    const closing = line.indexOf('}');
    const labelPart = line.slice(name.length + 1, closing);
    const ok = Object.entries(labels).every(([k, v]) => labelPart.includes(`${k}="${v}"`));
    if (!ok) continue;
    return Number(line.slice(closing + 1).trim());
  }
  return null;
}

/** A UTC instant for a given IST wall-clock time. IST is UTC+05:30 year-round. */
function istInstant(y, m, d, hh, mm) {
  return new Date(Date.UTC(y, m - 1, d, hh, mm) - (5 * 60 + 30) * 60 * 1000);
}

// ── Market session ───────────────────────────────────────────────────────────

test('market session covers both boundaries of the NSE day', () => {
  // 2026-08-03 is a Monday.
  assert.equal(marketSession(istInstant(2026, 8, 3, 9, 14)), 'closed', 'one minute before the open');
  assert.equal(marketSession(istInstant(2026, 8, 3, 9, 15)), 'open', 'the open itself is in session');
  assert.equal(marketSession(istInstant(2026, 8, 3, 12, 0)), 'open');
  // Inclusive: the closing auction lands on 15:30.
  assert.equal(marketSession(istInstant(2026, 8, 3, 15, 30)), 'open', 'the close itself is in session');
  assert.equal(marketSession(istInstant(2026, 8, 3, 15, 31)), 'closed', 'one minute after the close');
  assert.equal(marketSession(istInstant(2026, 8, 3, 3, 0)), 'closed', 'the small hours');
});

test('market session reads weekends off the IST calendar, not the host timezone', () => {
  assert.equal(marketSession(istInstant(2026, 8, 8, 12, 0)), 'weekend', 'Saturday');
  assert.equal(marketSession(istInstant(2026, 8, 9, 12, 0)), 'weekend', 'Sunday');

  // 23:00 UTC on a Sunday is already Monday 04:30 IST — a naive UTC weekday read
  // would call this a weekend. This is the case the fixed offset exists for.
  assert.equal(marketSession(new Date(Date.UTC(2026, 7, 9, 23, 0))), 'closed');
});

// ── Heartbeat ────────────────────────────────────────────────────────────────

test('an agent that has never worked reports age from start, not zero', () => {
  const hb = new Heartbeat();
  hb.startedMs = Date.now() - 60_000;

  assert.equal(hb.hasWorked(), false);
  assert.ok(hb.lastWorkAgeSeconds() >= 59, 'a boot-and-never-work process must look stale');
  assert.equal(hb.workCount, 0);
});

test('a beat resets the age and counts the work', () => {
  const hb = new Heartbeat();
  hb.startedMs = Date.now() - 60_000;
  hb.beat();

  assert.equal(hb.hasWorked(), true);
  assert.ok(hb.lastWorkAgeSeconds() < 1);
  assert.equal(hb.workCount, 1);
});

test('a backwards clock step cannot read as fresh work', () => {
  const hb = new Heartbeat();
  hb.lastWorkMs = Date.now() + 60_000; // clock jumped back after the beat
  assert.equal(hb.lastWorkAgeSeconds(), 0, 'clamped at 0 rather than going negative');
});

// ── The work point ───────────────────────────────────────────────────────────

test('a completed classification beats the heartbeat; a failed one does not', async () => {
  const m = new SentimentMetrics();
  m.heartbeat.startedMs = Date.now() - 60_000;

  m.classificationCompleted('failed');
  assert.equal(m.heartbeat.workCount, 0, 'a failure is not work');
  assert.ok(
    m.heartbeat.lastWorkAgeSeconds() >= 59,
    'an agent failing every symbol must not report a fresh heartbeat'
  );

  m.classificationCompleted('scored');
  assert.equal(m.heartbeat.workCount, 1);
  assert.ok(m.heartbeat.lastWorkAgeSeconds() < 1);

  const text = await m.render();
  assert.equal(seriesValue(text, 'sentiment_classifications_total', { outcome: 'failed' }), 1);
  assert.equal(seriesValue(text, 'sentiment_classifications_total', { outcome: 'scored' }), 1);
  assert.equal(seriesValue(text, 'sentiment_work_completed_total'), 1);
});

test('a quiet news day still counts as work', () => {
  const m = new SentimentMetrics();
  m.classificationCompleted('neutral_no_news');

  // With 10-minute polling, most cycles find nothing new. Withholding the beat
  // here would report a stall on any normal afternoon.
  assert.equal(m.heartbeat.workCount, 1);
});

// ── Readiness ────────────────────────────────────────────────────────────────

test('readiness moves idle → ok → stalled', () => {
  const m = new SentimentMetrics();

  assert.equal(m.readiness().status, 'idle', 'before the poll loop starts');

  m.markPollLoopRunning();
  m.classificationCompleted('scored');
  assert.equal(m.readiness().status, 'ok');

  // Age past the threshold without touching the clock.
  m.heartbeat.lastWorkMs = Date.now() - (STALL_SECONDS + 60) * 1000;
  assert.equal(m.readiness().status, 'stalled');
});

test('staleness before the loop starts is not a stall', () => {
  const m = new SentimentMetrics();
  m.heartbeat.startedMs = Date.now() - (STALL_SECONDS + 600) * 1000;

  // A process still working through Kafka/Redis startup has not failed at
  // anything yet, and reporting it as stalled would page on every slow boot.
  assert.equal(m.readiness().status, 'idle');
  assert.equal(m.readiness().stalled, false);
});

test('the readiness body carries exactly the fields status-api reads', () => {
  const m = new SentimentMetrics();
  m.markPollLoopRunning();
  m.classificationCompleted('scored');

  const body = m.readinessJson();
  assert.deepEqual(Object.keys(body).sort(), [
    'last_work_age_seconds',
    'market_session',
    'service',
    'stall_threshold_seconds',
    'status',
    'uptime_seconds',
    'work_completed',
    'work_expected',
  ]);
  assert.equal(body.service, 'sentiment');
  assert.equal(body.status, 'ok');
  assert.equal(body.work_expected, true);
  assert.equal(body.work_completed, 1);
  assert.equal(body.stall_threshold_seconds, STALL_SECONDS);
});

// ── Exposition ───────────────────────────────────────────────────────────────

test('the shared contract metrics are all exported under the service label', async () => {
  const m = new SentimentMetrics();
  const text = await m.render();

  for (const name of [
    'sentiment_up',
    'sentiment_uptime_seconds',
    'sentiment_last_work_age_seconds',
    'sentiment_stall_threshold_seconds',
    'sentiment_market_session_open',
    'sentiment_work_expected',
    'sentiment_work_completed_total',
    'sentiment_build_info',
  ]) {
    assert.notEqual(seriesValue(text, name), null, `${name} must be exported`);
  }

  assert.ok(text.includes('service="sentiment"'), 'every series carries the service label');
  // Node-specific: how a blocked event loop shows up while /health still answers.
  assert.ok(text.includes('nodejs_eventloop_lag_seconds'), 'default runtime metrics are collected');
});

test('every error series exists at zero before any error happens', async () => {
  const m = new SentimentMetrics();
  const text = await m.render();

  // `rate()` over a series that does not exist renders as "no data", which looks
  // identical to a failed scrape. Pre-creating them is what makes a genuine zero
  // readable as a zero.
  for (const outcome of ['http_error', 'network_error', 'no_api_key', 'skipped']) {
    assert.equal(seriesValue(text, 'sentiment_news_fetches_total', { outcome }), 0, outcome);
  }
  for (const outcome of ['encode_error', 'send_error', 'not_connected']) {
    assert.equal(seriesValue(text, 'sentiment_publishes_total', { outcome }), 0, outcome);
  }
  for (const outcome of ['parse_error', 'http_error']) {
    assert.equal(seriesValue(text, 'sentiment_llm_calls_total', { outcome }), 0, outcome);
  }
  assert.equal(seriesValue(text, 'sentiment_http_requests_total', { route: '/sentiment', status: '404' }), 0);
});

test('LLM latency is observed only when a request was actually issued', async () => {
  const m = new SentimentMetrics();

  m.llmCallCompleted('no_api_key');          // never left the process
  m.llmCallCompleted('http_error', 4.2);     // the provider answered and refused
  m.llmCallCompleted('ok', 1.5);

  const text = await m.render();
  assert.equal(seriesValue(text, 'sentiment_llm_calls_total', { outcome: 'no_api_key' }), 1);
  assert.equal(
    seriesValue(text, 'sentiment_llm_duration_seconds_count', { outcome: 'ok' }),
    1,
    'a successful call is timed'
  );
  assert.equal(
    seriesValue(text, 'sentiment_llm_duration_seconds_count', { outcome: 'http_error' }),
    1,
    'a refusal is timed too — a provider that hangs must be distinguishable from one that refuses instantly'
  );
  assert.equal(
    seriesValue(text, 'sentiment_llm_duration_seconds_count', { outcome: 'no_api_key' }),
    null,
    'a missing key has no duration to report'
  );
});

test('unknown label values are folded rather than minting new series', async () => {
  const m = new SentimentMetrics();

  m.verdictProduced('Ecstatic');                     // not a real verdict label
  m.httpRequestCompleted('/../../etc/passwd', 200);  // not a real route
  m.cacheError('made_up_operation');

  const text = await m.render();
  assert.equal(seriesValue(text, 'sentiment_verdicts_total', { label: 'Neutral' }), 1);
  assert.equal(seriesValue(text, 'sentiment_verdicts_total', { label: 'Ecstatic' }), null);
  assert.ok(!text.includes('etc/passwd'), 'arbitrary request paths cannot become label values');
  assert.ok(!text.includes('made_up_operation'));
});

test('a registry failure leaves the handle inert instead of throwing', () => {
  const m = new SentimentMetrics();
  m.inner = null; // simulate a construction failure

  // Every call site is unconditional, so a broken registry must never be able to
  // take down the service it is observing.
  assert.doesNotThrow(() => {
    m.classificationCompleted('scored');
    m.newsFetchCompleted('ok');
    m.llmCallCompleted('ok', 1);
    m.publishCompleted('ok');
    m.articlesCollected(3, 2);
    m.setCachedSymbols(4);
    m.verdictProduced('Bullish');
    m.onDemandCompleted('served');
    m.httpRequestCompleted('/sentiment', 200);
    m.cacheError('dedup_check');
  });
  assert.equal(m.serve(), null, 'no listener without a registry');
});

// ── The listener ─────────────────────────────────────────────────────────────

test('serve() answers /metrics, /health and /ready', async (t) => {
  const previous = process.env.METRICS_PORT;
  process.env.METRICS_PORT = '0'; // let the OS pick a free port

  const m = new SentimentMetrics();
  const server = m.serve();
  t.after(() => {
    server.close();
    if (previous === undefined) delete process.env.METRICS_PORT;
    else process.env.METRICS_PORT = previous;
  });

  await once(server, 'listening');
  const base = `http://127.0.0.1:${server.address().port}`;

  const metricsRes = await fetch(`${base}/metrics`);
  assert.equal(metricsRes.status, 200);
  assert.match(metricsRes.headers.get('content-type'), /text\/plain/);
  assert.ok((await metricsRes.text()).includes('sentiment_last_work_age_seconds'));

  const healthRes = await fetch(`${base}/health`);
  assert.equal(healthRes.status, 200);
  assert.equal((await healthRes.json()).status, 'idle');

  const notFound = await fetch(`${base}/nope`);
  assert.equal(notFound.status, 404);

  // A stalled service is running, so /health stays 200 — restarting it on a
  // stall would turn a visible problem into a crash loop. /ready is the one that
  // fails, which is what makes the stall actionable without being self-inflicted.
  m.markPollLoopRunning();
  m.heartbeat.lastWorkMs = Date.now() - (STALL_SECONDS + 60) * 1000;

  const stalledHealth = await fetch(`${base}/health`);
  assert.equal(stalledHealth.status, 200);
  assert.equal((await stalledHealth.json()).status, 'stalled');

  const stalledReady = await fetch(`${base}/ready`);
  assert.equal(stalledReady.status, 503);
  assert.equal((await stalledReady.json()).status, 'stalled');
});
