// The one shape translation on the web path.
//
// Every other `/api/*` handler is a byte-for-byte proxy, but the sentiment
// service speaks `StrategicVerdict` (conviction 1-100, 50 = neutral — see
// `agents/sentiment/src/analyzer.js:426`) while the UI consumes `SentimentPayload`
// (score -100..+100). The desktop translation lives in `commands/sentiment.rs`;
// these tests pin the browser twin to the same arithmetic and thresholds.

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  GET,
  convictionToScore,
  scoreToImpact,
  scoreToLabel,
  toSentimentPayload,
} from '../sentiment/route';

describe('convictionToScore', () => {
  it('maps the service scale onto the UI scale', () => {
    expect(convictionToScore(50)).toBe(0); // neutral midpoint
    expect(convictionToScore(100)).toBe(100);
    expect(convictionToScore(1)).toBe(-98);
    expect(convictionToScore(75)).toBe(50);
  });

  it('clamps out-of-range and treats a non-number as neutral', () => {
    expect(convictionToScore(1000)).toBe(100);
    expect(convictionToScore(-1000)).toBe(-100);
    for (const bad of [undefined, null, 'high', NaN, Infinity]) {
      expect(convictionToScore(bad)).toBe(0);
    }
  });
});

describe('label and impact thresholds', () => {
  it('uses the same +/-20 bands as commands/sentiment.rs', () => {
    expect(scoreToLabel(21)).toBe('Bullish');
    expect(scoreToLabel(20)).toBe('Neutral');
    expect(scoreToLabel(-20)).toBe('Neutral');
    expect(scoreToLabel(-21)).toBe('Bearish');
    expect(scoreToImpact(21)).toBe('positive');
    expect(scoreToImpact(0)).toBe('neutral');
    expect(scoreToImpact(-21)).toBe('negative');
  });
});

describe('toSentimentPayload', () => {
  it('prefers a driver headline, then the headline list', () => {
    const withDriver = toSentimentPayload('TCS', {
      conviction_score: 80,
      drivers: [{ headline: '  Order book at record high  ' }],
      headlines: ['Something else'],
    });
    expect(withDriver.top_headline).toBe('Order book at record high');

    const withoutDriver = toSentimentPayload('TCS', {
      conviction_score: 80,
      drivers: [{ impact: 'positive' }],
      headlines: ['Something else'],
    });
    expect(withoutDriver.top_headline).toBe('Something else');
  });

  it('never fabricates a headline, and says so plainly when there is none', () => {
    const bare = toSentimentPayload('INFY', { conviction_score: 50 });
    expect(bare.headlines).toEqual([]);
    expect(bare.top_headline).toBe('No notable headline for INFY.');
    expect(bare.score).toBe(0);
    expect(bare.label).toBe('Neutral');
  });

  it('keeps the upstream label when it sends one', () => {
    const out = toSentimentPayload('TCS', { conviction_score: 90, label: 'Strongly Bullish' });
    expect(out.label).toBe('Strongly Bullish');
    expect(out.impact).toBe('positive');
  });

  it('drops junk from the headline list rather than rendering blanks', () => {
    const out = toSentimentPayload('TCS', {
      conviction_score: 60,
      headlines: ['Real one', '', '   ', null as unknown as string, 42 as unknown as string],
    });
    expect(out.headlines).toEqual(['Real one']);
  });

  it('falls back to the requested symbol when upstream omits it', () => {
    expect(toSentimentPayload('SBIN', { conviction_score: 50 }).symbol).toBe('SBIN');
    expect(toSentimentPayload('SBIN', { conviction_score: 50, symbol: 'SBIN-EQ' }).symbol).toBe(
      'SBIN-EQ',
    );
  });
});

// ── The two very different reasons a verdict can be missing ─────────────────
//
// The upstream answers 404 both while a classification is still running and
// after one finished with nothing. Collapsing them into "try again shortly" is
// how a permanently 429'd LLM provider spent its outage being reported as work in
// progress: the panel promised a verdict that could not arrive, and the actual
// fault was invisible to everyone reading the UI.
describe('GET — a missing verdict reports which kind of missing it is', () => {
  const upstreamResponding = (status: number, body: unknown) =>
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    );

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('passes the real cause through when the attempt already failed', async () => {
    upstreamResponding(404, {
      error: 'no sentiment computed yet for RELIANCE',
      still_running: false,
      reason: 'sentiment classification failed: LLM HTTP 429 usage limit reached',
    });

    const res = await GET(new Request('http://localhost/api/sentiment?symbol=RELIANCE'));
    expect(res.status).toBe(503);
    const body = (await res.json()) as { error: string };
    expect(body.error).toContain('429');
    expect(body.error).not.toContain('try again shortly');
  });

  it('still says "try again" while the classification is genuinely running', async () => {
    upstreamResponding(404, {
      error: 'no sentiment computed yet for RELIANCE',
      still_running: true,
    });

    const res = await GET(new Request('http://localhost/api/sentiment?symbol=RELIANCE'));
    expect(res.status).toBe(503);
    const body = (await res.json()) as { error: string };
    expect(body.error).toContain('try again shortly');
  });

  it('degrades to the generic message when the upstream body carries no reason', async () => {
    upstreamResponding(404, { error: 'no sentiment computed yet for RELIANCE' });

    const res = await GET(new Request('http://localhost/api/sentiment?symbol=RELIANCE'));
    expect(res.status).toBe(503);
    expect(((await res.json()) as { error: string }).error).toContain('try again shortly');
  });
});
