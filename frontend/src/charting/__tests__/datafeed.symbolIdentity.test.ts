// @vitest-environment node
//
// Why SENSEX charted at ₹80 while the index sits near 77,000.
//
// SENSEX is a BSE index (segment INDICES, token 265). Every chart request goes out
// as `NSE:SENSEX` because the datafeed defaults the exchange to NSE, and NSE's
// instrument master carries only the ETFs that TRACK the index — SENSEXADD,
// SENSEXETF, SENSEXBEES, and a dozen more. `resolveInstrumentToken` then took
// `results[0]` when no exact tradingsymbol matched, so the DSP Sensex ETF was
// charted under the SENSEX name at its own price.
//
// A chart labelled with one instrument while showing another is worse than an empty
// one: it is unfalsifiable from the UI. So a near-miss now resolves to nothing.
//
// Measured against production: `/api/kite/historical?symbol=SENSEX` answered
// `404 Symbol 'SENSEX' not found` while `instrument_token=265` returned 114
// ten-minute candles closing at 76,957.27.
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.hoisted(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL ||= 'http://127.0.0.1:0/api/v1';
  process.env.NEXT_PUBLIC_DASHBOARD_URL ||= 'http://127.0.0.1:0/dashboard';
  process.env.NEXT_PUBLIC_AUTH_URL ||= 'https://auth.test.invalid';
});
const { kiteFetchSpy } = vi.hoisted(() => ({ kiteFetchSpy: vi.fn() }));
vi.mock('@/lib/kiteFetch', () => ({ kiteFetch: kiteFetchSpy }));

import { fetchKiteBatch } from '@/charting/datafeed';

const FROM = new Date(Date.UTC(2026, 7, 27));
const TO = new Date(Date.UTC(2026, 7, 31));

function json(body: unknown, ok = true): Response {
  return { ok, json: async () => body } as unknown as Response;
}

/** The ETF rows an `?q=SENSEX&exchange=NSE` search really returns. */
const NSE_SENSEX_ETFS = [
  { tradingsymbol: 'SENSEXADD', instrument_token: 4508929 },
  { tradingsymbol: 'SENSEXETF', instrument_token: 4921345 },
  { tradingsymbol: 'SENSEXBEES', instrument_token: 2691841 },
];

beforeEach(() => {
  kiteFetchSpy.mockReset();
});

describe('fetchKiteBatch — instrument identity', () => {
  it('charts nothing rather than an ETF that merely matches the name', async () => {
    kiteFetchSpy.mockImplementation(async (url: string) => {
      // The symbol form misses: SENSEX is not an NSE instrument.
      if (url.includes('symbol=SENSEX')) {
        return json({ error: "Symbol 'SENSEX' not found", candles: [] }, false);
      }
      // No NSE quote for it either.
      if (url.startsWith('/quote')) return json({ quotes: [] });
      // The search returns only the ETFs that track the index.
      if (url.startsWith('/instruments')) return json({ results: NSE_SENSEX_ETFS });
      // Reached only if a token was resolved — which is the bug.
      return json({
        candles: [{ time: 1788000000, open: 80, high: 80.5, low: 79.1, close: 80, volume: 12 }],
      });
    });

    const bars = await fetchKiteBatch('SENSEX', '10minute', FROM, TO, 'NSE');

    expect(bars).toEqual([]);
    const tokenCalls = kiteFetchSpy.mock.calls
      .map((c) => String(c[0]))
      .filter((u) => u.includes('instrument_token='));
    expect(tokenCalls, 'no token may be charted for a symbol that did not match').toEqual([]);
  });

  it('still resolves by token when the tradingsymbol matches exactly', async () => {
    // The fallback itself is load-bearing — an F&O contract the symbol form misses
    // must still chart — so this proves the fix narrowed it rather than removed it.
    kiteFetchSpy.mockImplementation(async (url: string) => {
      if (url.includes('symbol=SENSEX') && !url.includes('instrument_token')) {
        return json({ candles: [] });
      }
      if (url.startsWith('/quote')) {
        return json({ quotes: [{ symbol: 'SENSEX', instrument_token: 265 }] });
      }
      if (url.includes('instrument_token=265')) {
        return json({
          candles: [
            { time: 1788170100, open: 76924.93, high: 76957.27, low: 76924.93, close: 76957.27, volume: 0 },
          ],
        });
      }
      return json({ candles: [] });
    });

    const bars = await fetchKiteBatch('SENSEX', '10minute', FROM, TO, 'BSE');

    expect(bars).toHaveLength(1);
    expect(bars[0].close).toBe(76957.27);
  });
});
