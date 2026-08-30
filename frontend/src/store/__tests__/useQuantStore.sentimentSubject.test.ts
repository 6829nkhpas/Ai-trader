// @vitest-environment node
//
// AI News Sentiment for F&O contracts.
//
// The reported bug: charting RELIANCE26AUG1290CE showed "0 headlines · Neutral ·
// No notable headline for RELIANCE26AUG1290CE". Nothing was broken in the
// classifier — the store was asking about the wrong instrument. News is
// published about a company, never about one option contract, so the lookup
// matched nothing and an absence of data was rendered as a neutral verdict.
//
// Two things are proved here, and they are deliberately separate:
//
//  1. the mapping itself (contract -> underlying), including the equity tickers
//     that must NOT be rewritten, and
//  2. that the *store action* actually applies it — the assertion is on the
//     transport spy, because a helper that resolves correctly but is never
//     reached from `loadSentimentForSymbol` fixes nothing. This test fails if the
//     resolution is bypassed at the call site.
import { describe, it, expect, beforeEach, vi } from 'vitest';

// `useQuantStore` -> `useAuthStore` -> `lib/env.ts`, which throws when these are
// unset. Must be assigned before the import bindings are evaluated.
vi.hoisted(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL ||= 'http://127.0.0.1:0/api/v1';
  process.env.NEXT_PUBLIC_DASHBOARD_URL ||= 'http://127.0.0.1:0/dashboard';
  process.env.NEXT_PUBLIC_AUTH_URL ||= 'https://auth.test.invalid';
});

// `lib/bridge` is the single transport chokepoint (Tauri IPC on desktop, HTTP on
// the web), so spying there proves the property on both transports at once. The
// spy echoes the requested symbol back in the payload, exactly as the real
// `/api/sentiment` route does.
const { invokeSpy } = vi.hoisted(() => ({
  invokeSpy: vi.fn(async (_cmd: string, args?: Record<string, unknown>) => ({
    symbol: String(args?.symbol ?? ''),
    score: 42,
    label: 'Bullish',
    top_headline: 'Reliance posts record quarterly profit',
    impact: 'positive',
    headlines: ['Reliance posts record quarterly profit'],
  })),
}));
vi.mock('@/lib/bridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/bridge')>()),
  bridgeInvoke: invokeSpy,
  bridgeListen: vi.fn(async () => () => {}),
}));

import { useQuantStore, sentimentSubject } from '@/store/useQuantStore';

/** The symbol `loadSentimentForSymbol` actually put on the wire. */
function requestedSymbols(): string[] {
  return invokeSpy.mock.calls
    .filter(([cmd]) => cmd === 'fetch_symbol_sentiment')
    .map(([, args]) => String(args?.symbol ?? ''));
}

describe('sentimentSubject — the instrument news is published about', () => {
  it('resolves option and future contracts to their underlying', () => {
    expect(sentimentSubject('RELIANCE26AUG1290CE')).toBe('RELIANCE');
    expect(sentimentSubject('RELIANCE24DEC2500PE')).toBe('RELIANCE');
    expect(sentimentSubject('NIFTY2670724000CE')).toBe('NIFTY');
    expect(sentimentSubject('BANKNIFTY26AUG52000PE')).toBe('BANKNIFTY');
    expect(sentimentSubject('BANKNIFTY24DECFUT')).toBe('BANKNIFTY');
    expect(sentimentSubject('FINNIFTY26AUG23500CE')).toBe('FINNIFTY');
    expect(sentimentSubject('MIDCPNIFTY26AUG12000CE')).toBe('MIDCPNIFTY');
  });

  it('leaves cash and index-spot symbols exactly as they are', () => {
    // These lookups already worked; the fix must not move them.
    expect(sentimentSubject('RELIANCE')).toBe('RELIANCE');
    expect(sentimentSubject('TCS')).toBe('TCS');
    expect(sentimentSubject('NIFTY 50')).toBe('NIFTY 50');
    expect(sentimentSubject('M&M')).toBe('M&M');
  });

  it('does not truncate equity tickers that merely contain a digit', () => {
    // The whole reason resolution is gated on `isFnoSymbol`: cutting at the first
    // digit unconditionally would ask for news about "V" and "A".
    expect(sentimentSubject('V2RETAIL')).toBe('V2RETAIL');
    expect(sentimentSubject('A2ZINFRA')).toBe('A2ZINFRA');
    expect(sentimentSubject('3MINDIA')).toBe('3MINDIA');
    // Ends in "CE" but has no digit — Action Construction Equipment, an equity.
    expect(sentimentSubject('ACE')).toBe('ACE');
  });
});

describe('loadSentimentForSymbol applies the resolution', () => {
  beforeEach(() => {
    invokeSpy.mockClear();
    // A stale cache entry would short-circuit the fetch and make the transport
    // assertions vacuous.
    useQuantStore.setState({
      sentimentCache: {},
      activeSentiment: null,
      sentimentError: null,
      isFetchingSentiment: false,
    });
  });

  it('requests the underlying, not the contract', async () => {
    await useQuantStore.getState().loadSentimentForSymbol('RELIANCE26AUG1290CE');

    expect(requestedSymbols()).toEqual(['RELIANCE']);
    // And the panel is told whose news this is, so it can disclose the subject
    // instead of captioning RELIANCE's headlines with the contract's name.
    expect(useQuantStore.getState().activeSentiment?.symbol).toBe('RELIANCE');
    expect(useQuantStore.getState().sentimentError).toBeNull();
  });

  it('caches by underlying, so sibling strikes share one request', async () => {
    const store = useQuantStore.getState();
    await store.loadSentimentForSymbol('RELIANCE26AUG1290CE');
    await store.loadSentimentForSymbol('RELIANCE26AUG1300PE');
    await store.loadSentimentForSymbol('RELIANCE26SEP1400CE');

    expect(requestedSymbols()).toEqual(['RELIANCE']);
    expect(Object.keys(useQuantStore.getState().sentimentCache)).toEqual(['RELIANCE']);
  });

  it('still fetches an equity under its own name', async () => {
    await useQuantStore.getState().loadSentimentForSymbol('TCS');
    expect(requestedSymbols()).toEqual(['TCS']);
  });

  it('refreshSentimentForSymbol resolves too', async () => {
    // `fetchDeepAnalysis` force-refreshes through this path, so the contract
    // symbol reaches it as well.
    await useQuantStore.getState().refreshSentimentForSymbol('BANKNIFTY24DECFUT');
    expect(requestedSymbols()).toEqual(['BANKNIFTY']);
  });
});
