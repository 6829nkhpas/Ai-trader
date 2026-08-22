// agentErrorClassifier.test.ts
//
// The point of this module is that it must never state a cause it has not
// evidenced. The tests are therefore mostly about what it must NOT say — the
// previous UI told every failed run to go check an LLM key, including runs where
// no request was ever made, which cost real debugging time on a healthy key.

import { describe, expect, it } from 'vitest';

import { classifyAgentError } from '../agentErrorClassifier';

describe('classifyAgentError', () => {
  it('recognises the RESEARCH plan restriction and does not call it a fault', () => {
    // The exact string the store sets (RESEARCH_LOCKED_MESSAGE). This is the case
    // that was being reported as "your LLM API key is expired".
    const err = classifyAgentError(
      'This is part of the RESEARCH plan. Trade analysis and recommendations are ' +
        'available to subscribers of our SEBI-registered research service.',
    );
    expect(err.kind).toBe('research-locked');
    expect(err.retryable).toBe(false);
    expect(err.explanation).not.toMatch(/API key|quota|rate.?limit/i);
  });

  it('recognises the server-side feature switch', () => {
    const err = classifyAgentError(
      'Deep Quant AI analysis is disabled in this deployment. If you believe you ' +
        'have access to it, contact support — the operator controls this switch, not your plan.',
    );
    expect(err.kind).toBe('feature-disabled');
    expect(err.retryable).toBe(false);
  });

  it('blames the LLM provider ONLY on quota/billing evidence', () => {
    for (const msg of [
      'HTTP 429 rate limit exceeded',
      'You exceeded your current quota',
      'insufficient credit on this account',
      'billing hard limit reached',
    ]) {
      expect(classifyAgentError(msg).kind, msg).toBe('llm-quota');
    }
  });

  it('separates a rejected credential from an exhausted quota', () => {
    // Different fixes: one is "top up", the other is "the key is wrong". Merging
    // them sends you to the wrong page of the provider dashboard.
    expect(classifyAgentError('Invalid API key provided').kind).toBe('llm-auth');
    expect(classifyAgentError('401 Unauthorized').kind).toBe('llm-auth');
  });

  it('recognises a transport failure and says the LLM was not involved', () => {
    for (const msg of [
      'deepquant upstream unreachable: fetch failed',
      'connect ECONNREFUSED 172.18.0.5:8086',
      'HTTP 502 Bad Gateway',
    ]) {
      const err = classifyAgentError(msg);
      expect(err.kind, msg).toBe('upstream-unreachable');
      expect(err.explanation).not.toMatch(/API key|quota/i);
    }
  });

  it('recognises the idle-stream timeout the store produces', () => {
    const err = classifyAgentError(
      'The agent stream stalled — no activity for 120s. The Python agent server may ' +
        'be unreachable or the LLM request stalled. Please retry.',
    );
    expect(err.kind).toBe('timeout');
    expect(err.retryable).toBe(true);
  });

  it('recognises a data shortfall rather than blaming the model', () => {
    // Common and previously very misleading: an illiquid symbol with no candles
    // rendered as an expired-key diagnosis.
    for (const msg of ['no candles available for NIFTY26AUG24000CE', 'insufficient history for this timeframe']) {
      expect(classifyAgentError(msg).kind, msg).toBe('no-data');
    }
  });

  it('recognises cancellation', () => {
    expect(classifyAgentError('AbortError: The operation was aborted').kind).toBe('cancelled');
    expect(classifyAgentError('run cancelled by user').kind).toBe('cancelled');
  });

  it('falls back to unknown and blames nothing', () => {
    const err = classifyAgentError('Something completely unanticipated happened in node X');
    expect(err.kind).toBe('unknown');
    // The whole point: no invented diagnosis.
    expect(err.explanation).not.toMatch(/API key|quota|rate.?limit|unreachable/i);
    // But the raw text is preserved so it is still actionable.
    expect(err.detail).toContain('unanticipated');
  });

  it('handles a failure with no message without inventing one', () => {
    // The old UI printed "Connection refused: Python service port :8086
    // unreachable." here as though it had been observed. It had not.
    for (const empty of [null, undefined, '', '   ']) {
      const err = classifyAgentError(empty);
      expect(err.kind).toBe('unknown');
      expect(err.detail).toBe('No error detail was reported.');
      expect(err.detail).not.toMatch(/8086|Connection refused/);
    }
  });

  it('is total — every input yields a renderable result', () => {
    // The error panel must not be able to throw; that would replace a readable
    // failure with a blank screen.
    for (const weird of [null, undefined, '', '\n\t', '💥', 'a'.repeat(5000), '<script>']) {
      const err = classifyAgentError(weird as string);
      expect(typeof err.title).toBe('string');
      expect(err.title.length).toBeGreaterThan(0);
      expect(typeof err.explanation).toBe('string');
      expect(typeof err.detail).toBe('string');
      expect(typeof err.retryable).toBe('boolean');
    }
  });

  it('prefers the more specific rule when a message could match two', () => {
    // A plan restriction that happens to contain the word "limit" must not be
    // reported as a rate limit. Ordering is load-bearing, so it is pinned.
    const err = classifyAgentError(
      'This is part of the RESEARCH plan — subscription limit applies. See our ' +
        'SEBI-registered research service.',
    );
    expect(err.kind).toBe('research-locked');
  });
});
