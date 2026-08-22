// profile.test.js — Yahoo Finance context mapping.
//
// Covers the pure mapping only; the network path is exercised against the live
// endpoint before deploy. The mapping is what matters here: these values go
// straight into the LLM prompt via `renderContext`, so a wrong field is not a
// crash — it is a confidently wrong verdict. In particular, the null-vs-guess
// distinction is load-bearing: `renderContext` OMITS null fields, so null means
// "the model is told nothing", while a fabricated 0 would mean "the model is told
// the P/E is zero".

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { extractMeta, toFinancials, toProfile, yahooSymbol } from '../src/profile.js';

// Trimmed from a live response for RELIANCE.NS.
const META = {
  currency: 'INR',
  symbol: 'RELIANCE.NS',
  exchangeName: 'NSI',
  fullExchangeName: 'NSE',
  instrumentType: 'EQUITY',
  longName: 'Reliance Industries Limited',
  shortName: 'RELIANCE INDUSTRIES LTD',
  regularMarketPrice: 1316.0,
  fiftyTwoWeekHigh: 1611.8,
  fiftyTwoWeekLow: 1249.8,
};

const SEED = {
  companyName: 'Reliance Industries',
  sector: 'Energy / Conglomerate',
  finnhubSymbol: 'RELIANCE.NS',
};

// ── yahooSymbol ──────────────────────────────────────────────────────────────

test('yahooSymbol prefers the seed symbol', () => {
  // The curated map already carries the exchange-qualified symbol and Yahoo uses
  // the same `.NS` convention, so no second column was needed.
  assert.equal(yahooSymbol('RELIANCE', SEED), 'RELIANCE.NS');
});

test('yahooSymbol appends the default suffix when the seed has none', () => {
  assert.equal(yahooSymbol('INFY', {}), 'INFY.NS');
  assert.equal(yahooSymbol('INFY', undefined), 'INFY.NS');
});

test('yahooSymbol does not double-suffix an already-qualified symbol', () => {
  assert.equal(yahooSymbol('TCS.NS', {}), 'TCS.NS');
  assert.equal(yahooSymbol('AAPL.O', {}), 'AAPL.O');
});

test('yahooSymbol returns empty for a blank ticker rather than a bare suffix', () => {
  // Requesting ".NS" would 404 on every cycle for no reason.
  assert.equal(yahooSymbol('', {}), '');
  assert.equal(yahooSymbol('   ', {}), '');
});

// ── extractMeta ──────────────────────────────────────────────────────────────

test('extractMeta pulls meta from a well-formed response', () => {
  assert.deepEqual(extractMeta({ chart: { result: [{ meta: META }] } }), META);
});

test('extractMeta returns null for every malformed shape rather than throwing', () => {
  // The poll loop must survive an upstream change; a throw here would take down
  // classification for every symbol.
  for (const body of [
    null,
    undefined,
    {},
    { chart: null },
    { chart: { result: null } },
    { chart: { result: [] } },
    { chart: { result: [{}] } },
    { chart: { result: [{ meta: 'nope' }] } },
    { chart: { error: { code: 'Not Found' } } },
    'not json at all',
  ]) {
    assert.equal(extractMeta(body), null);
  }
});

// ── toProfile ────────────────────────────────────────────────────────────────

test('toProfile maps the fields renderContext consumes', () => {
  const p = toProfile(META, SEED);
  assert.equal(p.name, 'Reliance Industries Limited');
  assert.equal(p.exchange, 'NSE');
  assert.equal(p.country, 'IN');
});

test('toProfile takes industry from the seed, not the response', () => {
  // Yahoo's industry lives behind the crumb-gated quoteSummary endpoint (measured:
  // 401 Invalid Crumb without one), and the curated sector is better for Indian
  // names anyway.
  assert.equal(toProfile(META, SEED).industry, 'Energy / Conglomerate');
  assert.equal(toProfile(META, {}).industry, '');
});

test('toProfile reports marketCap as null rather than guessing', () => {
  // renderContext omits nulls, so the prompt carries no market cap at all. A
  // fabricated number would be reasoned over as though it were real.
  assert.equal(toProfile(META, SEED).marketCap, null);
});

test('toProfile falls back through longName → shortName → seed', () => {
  assert.equal(toProfile({ shortName: 'ACME LTD' }, {}).name, 'ACME LTD');
  assert.equal(toProfile({}, { companyName: 'Acme Industries' }).name, 'Acme Industries');
  assert.equal(toProfile({}, {}).name, '');
});

test('toProfile only claims country IN for rupee-denominated listings', () => {
  assert.equal(toProfile({ ...META, currency: 'USD' }, SEED).country, '');
});

// ── toFinancials ─────────────────────────────────────────────────────────────

test('toFinancials maps the 52-week range and price', () => {
  const f = toFinancials(META);
  assert.equal(f.week52High, 1611.8);
  assert.equal(f.week52Low, 1249.8);
  assert.equal(f.price, 1316.0);
});

test('toFinancials nulls the metrics Yahoo cannot supply', () => {
  // Honest absence. These came from Finnhub, which no longer answers for NSE.
  const f = toFinancials(META);
  assert.equal(f.pe, null);
  assert.equal(f.netProfitMargin, null);
  assert.equal(f.revenueGrowth, null);
});

test('toFinancials coerces numeric strings and rejects junk', () => {
  const f = toFinancials({ fiftyTwoWeekHigh: '1611.8', fiftyTwoWeekLow: 'n/a', regularMarketPrice: null });
  assert.equal(f.week52High, 1611.8);
  assert.equal(f.week52Low, null);
  assert.equal(f.price, null);
});

test('toFinancials rejects non-finite numbers', () => {
  // NaN would render into the prompt as "NaN" and Infinity as "Infinity"; both
  // read to the model as data.
  const f = toFinancials({ fiftyTwoWeekHigh: NaN, fiftyTwoWeekLow: Infinity, regularMarketPrice: 100 });
  assert.equal(f.week52High, null);
  assert.equal(f.week52Low, null);
  assert.equal(f.price, 100);
});

test('toFinancials on an empty meta yields all-null, which the caller treats as no context', () => {
  const f = toFinancials({});
  assert.deepEqual(Object.values(f).filter((v) => v !== null), []);
});
