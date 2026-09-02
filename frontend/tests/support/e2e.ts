// tests/support/e2e.ts
//
// The two things BOTH Find Quant specs need. They live here because each was fixed once in the
// desktop spec and the mobile spec then failed for the very same reason, which is a copy waiting
// to rot rather than a coincidence.

import { expect, test, type Page } from '@playwright/test';

declare global {
  interface Window {
    __stratai_test__?: {
      seedCandles: (input: { symbol: string; timeframe: string; count?: number }) => number;
    };
  }
}

/**
 * Unique per RUN, so a second run of the same suite does not inherit the first run's sessions.
 *
 * Read from the environment when provided, so CI can pin it in the log for a reproducible rerun.
 */
const RUN_NONCE = process.env.E2E_RUN_ID ?? Date.now().toString(36);

/**
 * A token unique to the running test, so each test gets its OWN user.
 *
 * Sessions are per-user and the agent's database lives for the whole Playwright run, so tests sharing
 * an identity share a session list: the tab bar counts leftovers from earlier tests and assertions
 * fail with "locator resolved to 5 elements". Isolating by identity is cheaper than restarting the
 * service per test and removes the coupling outright.
 *
 * The RUN nonce matters as much as the per-test slug. A title-only token is stable ACROSS runs, so
 * re-running against a still-live agent had each test find its own leftovers and count them as tabs —
 * which is precisely how the mobile spec failed with "Expected: 1, Received: 5".
 */
export function tokenForTest(suffix = ''): string {
  const slug = test
    .info()
    .title.toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .slice(0, 40);
  return `e2e-${RUN_NONCE}-${slug}${suffix}`;
}

/**
 * Put synthetic candles in the historical cache, so the FIND button is deterministically enabled.
 *
 * `DeepQuantPanel` renders `#btn-run-deep-quant` as `disabled={!isAnalyzing && !dataReady}`, and
 * `dataReady` is `symbolCandleCount > 0` — candles in `useTradeStore.historicalCache`, which arrive
 * from QuestDB via `useHistoricalData` or from the TradingView datafeed. The fixture runs neither, so
 * nothing populates the cache and the button never enables. Intercepting the HTTP response does not
 * help, because no request is made. See `src/lib/testAffordance.ts` for why seeding the store was
 * chosen over relaxing the gate.
 *
 * Call AFTER navigating: the affordance is attached to the loaded page.
 */
export async function seedCandles(page: Page) {
  // WAITED FOR, not read once. `layout.tsx` sets `window.__ALPHA_TEST_MODE__` in an inline script, but
  // `__stratai_test__` is attached by a `useEffect` in `page.tsx`, so it exists only once React has
  // hydrated. `page.goto` resolves on `load`, which is earlier, and reading immediately returned -1
  // for whichever tests happened to lose the race.
  await expect
    .poll(() => page.evaluate(() => typeof window.__stratai_test__?.seedCandles === 'function'), {
      message:
        'window.__stratai_test__ never appeared. Either the server was not started with ' +
        'ALPHA_TEST_MODE=1, or the BUILD was made without it — `/` is prerendered as static ' +
        'content, so `layout.tsx` reads ALPHA_TEST_MODE at build time and setting it only on ' +
        '`next start` leaves the flag script out of the HTML entirely.',
      timeout: 20_000,
    })
    .toBe(true);

  const seeded = await page.evaluate(() =>
    // RELIANCE / 10m is what `DeepQuantPanel` defaults to (`selectedSymbol || 'RELIANCE'`), and the
    // cache key must be `SYMBOL::TIMEFRAME` for `symbolCandleCount` to find it.
    window.__stratai_test__!.seedCandles({ symbol: 'RELIANCE', timeframe: '10m' }),
  );
  expect(seeded, 'seedCandles wrote no candles').toBeGreaterThan(0);
}
