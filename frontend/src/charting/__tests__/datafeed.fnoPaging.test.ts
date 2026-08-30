// @vitest-environment node
//
// Why an F&O chart rendered "No data here" while equities charted fine.
//
// Kite caps a single `/instruments/historical` request at a per-interval number of
// days, so `fetchKiteBatch` splits the window TradingView asks for into pages. It
// also stops early once a page comes back empty, to avoid fanning out requests for
// history that does not exist.
//
// Those two behaviours were safe together only because the pages were walked
// OLDEST first — which they were, and that was the bug. TradingView's opening
// window spans months; an option contract is listed weeks before expiry. So the
// oldest page predates the contract's existence, Kite correctly answers `[]`, and
// the loop gave up on its first batch having never asked for the present. Verified
// against the live proxy: RELIANCE26SEP1080CE returns 100 ten-minute candles for
// Aug 25-30 2026 and 0 for Feb 1 - Mar 2 2026.
//
// A cash symbol never tripped it, because it has bars in every page — which is
// exactly why this needs its own test rather than relying on the equity path.
import { describe, expect, it, vi, beforeEach } from 'vitest';

vi.hoisted(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL ||= 'http://127.0.0.1:0/api/v1';
  process.env.NEXT_PUBLIC_DASHBOARD_URL ||= 'http://127.0.0.1:0/dashboard';
  process.env.NEXT_PUBLIC_AUTH_URL ||= 'https://auth.test.invalid';
});

const { kiteFetchSpy } = vi.hoisted(() => ({ kiteFetchSpy: vi.fn() }));
vi.mock('@/lib/kiteFetch', () => ({ kiteFetch: kiteFetchSpy }));

import { fetchKiteBatch } from '@/charting/datafeed';

/** A Kite historical response body. */
function candlesResponse(times: number[]) {
  return {
    ok: true,
    json: async () => ({
      candles: times.map((t) => ({ time: t, open: 100, high: 101, low: 99, close: 100, volume: 5 })),
    }),
  } as unknown as Response;
}

const LISTED_ON = Date.UTC(2026, 7, 20) / 1000; // 2026-08-20, in seconds

/**
 * Stand in for Kite: return candles only for pages that overlap the window during
 * which the contract actually traded. Everything earlier is empty, as it is in
 * reality.
 */
function respondAsIfListedRecently() {
  kiteFetchSpy.mockImplementation(async (url: string) => {
    const from = /from=(\d{4}-\d{2}-\d{2})/.exec(url)?.[1];
    const to = /to=(\d{4}-\d{2}-\d{2})/.exec(url)?.[1];
    if (!from || !to) return candlesResponse([]);
    const toSec = Date.parse(`${to}T00:00:00Z`) / 1000;
    return toSec >= LISTED_ON ? candlesResponse([LISTED_ON + 600, LISTED_ON + 1200]) : candlesResponse([]);
  });
}

describe('fetchKiteBatch — a recently listed contract over a wide window', () => {
  beforeEach(() => {
    kiteFetchSpy.mockReset();
  });

  it('returns the recent candles even though the oldest page is empty', async () => {
    respondAsIfListedRecently();

    // 10minute pages in 30-day slices, so this window is 5+ pages and only the
    // last one has data — the exact shape that used to return nothing.
    const bars = await fetchKiteBatch(
      'RELIANCE26SEP1080CE',
      '10minute',
      new Date('2026-03-01T00:00:00Z'),
      new Date('2026-08-30T00:00:00Z'),
      'NFO',
      '10m',
    );

    expect(bars.length).toBeGreaterThan(0);
    expect(kiteFetchSpy).toHaveBeenCalled();
  });

  it('requests the newest page before the oldest one', async () => {
    respondAsIfListedRecently();
    await fetchKiteBatch(
      'RELIANCE26SEP1080CE',
      '10minute',
      new Date('2026-03-01T00:00:00Z'),
      new Date('2026-08-30T00:00:00Z'),
      'NFO',
      '10m',
    );

    // Page order is what makes the early exit safe: it must mean "stop going
    // further back", not "give up before reaching the present".
    const firstTo = /to=(\d{4}-\d{2}-\d{2})/.exec(String(kiteFetchSpy.mock.calls[0][0]))?.[1];
    expect(firstTo).toBe('2026-08-30');
  });

  it('still stops early instead of walking every page back', async () => {
    respondAsIfListedRecently();
    await fetchKiteBatch(
      'RELIANCE26SEP1080CE',
      '10minute',
      new Date('2026-03-01T00:00:00Z'),
      new Date('2026-08-30T00:00:00Z'),
      'NFO',
      '10m',
    );

    // 2026-03-01 → 2026-08-30 in 30-day slices is 7 pages, walked newest-first in
    // batches of 3. Batch 1 contains the page with data, batch 2 is entirely empty
    // and trips the exit — so the 7th (oldest) page is never requested. Counted as
    // distinct page windows rather than raw calls, because an empty page also
    // issues a token-resolution fallback request.
    const windows = new Set(
      kiteFetchSpy.mock.calls
        .map(([url]) => String(url))
        .filter((url) => url.includes('/historical?symbol='))
        .map((url) => /from=(\d{4}-\d{2}-\d{2})/.exec(url)?.[1]),
    );
    expect(windows.size).toBe(6);
    expect(windows.has('2026-03-01')).toBe(false);
  });

  it('returns nothing when the contract never traded in the window', async () => {
    kiteFetchSpy.mockResolvedValue(candlesResponse([]));
    const bars = await fetchKiteBatch(
      'RELIANCE26SEP9999CE',
      '10minute',
      new Date('2026-08-01T00:00:00Z'),
      new Date('2026-08-30T00:00:00Z'),
      'NFO',
      '10m',
    );
    // A genuinely untraded strike stays empty — the fix must not invent bars.
    expect(bars).toEqual([]);
  });
});
