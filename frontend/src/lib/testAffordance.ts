// lib/testAffordance.ts
//
// A deliberately tiny, test-mode-only hook for seeding client state from an end-to-end test.
//
// WHY THIS EXISTS
// ---------------
// `DeepQuantPanel` disables the FIND button until `dataReady`, which counts candles in
// `useTradeStore.historicalCache`. Those candles come from QuestDB via `useHistoricalData` or the
// TradingView datafeed. The e2e fixture runs neither — no QuestDB, and the charting library is not loaded —
// so nothing ever populates the cache and the button stays disabled. Intercepting the HTTP response does not
// help, because no request is made.
//
// Three options were considered (see the migration plan): seed the store from the test, drive the real chart
// path, or relax the `dataReady` gate under a flag. The third was rejected because it stops the test
// exercising the real enablement rule; the second drags TradingView into the e2e. This is the first.
//
// WHAT IT DELIBERATELY IS NOT
// ---------------------------
// Not "expose the store". The surface is one function that writes candles, so a test cannot reach in and
// mutate arbitrary state — which would let an e2e fake its way past exactly the behaviour it is meant to
// prove. It is also inert unless `window.__ALPHA_TEST_MODE__` is set, which only happens when the server was
// started with `ALPHA_TEST_MODE=1` (see `app/layout.tsx`). In a normal build the flag is absent, so
// `install()` returns immediately and nothing is attached.

import { useTradeStore, type OhlcCandle } from '../store/useTradeStore';

/** The shape a test passes in. Deliberately minimal — the module builds the rest. */
export interface SeedCandlesInput {
  symbol: string;
  timeframe: string;
  count?: number;
}

declare global {
  interface Window {
    __ALPHA_TEST_MODE__?: boolean;
    /** Present ONLY in test mode. See `installTestAffordance`. */
    __stratai_test__?: {
      seedCandles: (input: SeedCandlesInput) => number;
    };
  }
}

/**
 * Write synthetic candles into the historical cache.
 *
 * The cache key is `SYMBOL::TIMEFRAME` because `DeepQuantPanel` scans for keys starting with `${symbol}::`
 * when computing `symbolCandleCount`; a differently-shaped key would be silently ignored and the button
 * would stay disabled with no indication why.
 *
 * Defaults to 240 candles: fewer than 50 makes the panel render its "insufficient data" state instead.
 */
function seedCandles({ symbol, timeframe, count = 240 }: SeedCandlesInput): number {
  const upper = symbol.toUpperCase();
  const start = Date.UTC(2026, 2, 12, 3, 45, 0);
  const candles: OhlcCandle[] = Array.from({ length: count }, (_, i) => {
    const base = 2450 + (i % 20);
    return {
      symbol: upper,
      start_timestamp_ms: start + i * 600_000,
      open: base,
      high: base + 6,
      low: base - 5,
      close: base + 2,
      volume: 100_000 + i,
    };
  });

  useTradeStore.getState().setHistoricalCache(`${upper}::${timeframe}`, candles);
  return candles.length;
}

/**
 * Attach the affordance, but only in test mode.
 *
 * Safe to call more than once. Called from the app shell rather than at module scope so it runs in the
 * browser, after `layout.tsx` has injected the flag.
 */
export function installTestAffordance(): void {
  if (typeof window === 'undefined') return;
  if (!window.__ALPHA_TEST_MODE__) return;
  if (window.__stratai_test__) return;
  window.__stratai_test__ = { seedCandles };
  console.info('[test-mode] window.__stratai_test__.seedCandles is available');
}
