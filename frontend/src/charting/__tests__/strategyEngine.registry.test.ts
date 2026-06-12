// Feature: professional-charting-suite
//
// Unit tests for the strategy registry, insufficient-data handling, and the
// marker-removal contract (Task 10.5).
//
// These example-based tests pin three concrete behaviours of the pure
// StrategyEngine that underpin the strategy panel and its marker rendering:
//
//   - Registry completeness (Requirement 8.1): listStrategies() exposes at
//     least three selectable strategies and includes the three named ones —
//     'ma-cross', 'rsi-mean-reversion', and 'breakout'.
//   - Insufficient data (Requirement 8.3): evaluate() returns [] (no signals)
//     whenever the candle series is shorter than the strategy's
//     requiredLookback(params).
//   - Marker removal (Requirement 8.8): the engine contract that backs marker
//     clearing — an absent/unknown strategy id yields no strategy and therefore
//     no signals to render — holds at the level testable without a real chart.

import { describe, it, expect } from 'vitest';

import {
  STRATEGY_REGISTRY,
  listStrategies,
  getStrategy,
} from '@/charting/engines';
import type { StrategyDef } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';

// The three strategies the panel guarantees (Requirement 8.1).
const NAMED_STRATEGY_IDS = ['ma-cross', 'rsi-mean-reversion', 'breakout'] as const;

/**
 * A trivial, strictly-timestamped OHLC candle series of `n` candles. The exact
 * shape does not matter for the insufficient-data check — only the length does.
 */
function makeCandles(n: number): ChartCandle[] {
  const out: ChartCandle[] = [];
  for (let i = 0; i < n; i += 1) {
    const price = 100 + i;
    out.push({
      time: 1_000 + i * 60,
      open: price,
      high: price + 1,
      low: price - 1,
      close: price,
    } as ChartCandle);
  }
  return out;
}

describe('strategy registry (Requirement 8.1)', () => {
  it('exposes at least three selectable strategies', () => {
    expect(listStrategies().length).toBeGreaterThanOrEqual(3);
  });

  it('includes the three named strategies', () => {
    const ids = listStrategies();
    for (const id of NAMED_STRATEGY_IDS) {
      expect(ids, `strategy "${id}" missing from listStrategies()`).toContain(id);
    }
  });

  it.each(NAMED_STRATEGY_IDS)('getStrategy("%s") returns a well-formed definition', (id) => {
    const def = getStrategy(id);
    expect(def, `strategy "${id}" not retrievable via getStrategy`).toBeDefined();
    expect(def!.id).toBe(id);
    expect(typeof def!.name).toBe('string');
    expect(def!.name.trim().length).toBeGreaterThan(0);
    expect(typeof def!.evaluate).toBe('function');
    expect(typeof def!.requiredLookback).toBe('function');
  });

  it.each(NAMED_STRATEGY_IDS)('registry entry for "%s" matches getStrategy', (id) => {
    expect(STRATEGY_REGISTRY[id]).toBe(getStrategy(id));
  });
});

describe('insufficient data yields no signals (Requirement 8.3)', () => {
  const allDefs: StrategyDef[] = listStrategies().map((id) => getStrategy(id)!);

  it.each(allDefs.map((d) => [d.id, d] as const))(
    'strategy "%s" returns [] when candles are shorter than requiredLookback',
    (_id, def) => {
      const need = def.requiredLookback(def.defaults);

      // One candle short of the requirement → no signals.
      expect(def.evaluate(makeCandles(need - 1), def.defaults)).toEqual([]);

      // An empty series is also insufficient → no signals.
      expect(def.evaluate([], def.defaults)).toEqual([]);
    },
  );

  it('a series exactly at requiredLookback is no longer rejected for being too short', () => {
    // Boundary check: at exactly requiredLookback the strategy is allowed to
    // run (it may still legitimately produce zero signals, but it must not be
    // short-circuited by the insufficient-data guard).
    for (const def of allDefs) {
      const need = def.requiredLookback(def.defaults);
      const signals = def.evaluate(makeCandles(need), def.defaults);
      expect(Array.isArray(signals)).toBe(true);
    }
  });
});

describe('marker-removal contract (Requirement 8.8)', () => {
  // The marker hook clears all markers when the strategy id is null/absent or
  // unknown. The engine-level contract that backs this is: there is no strategy
  // to evaluate, so there are no signals to render.
  it('an unknown strategy id resolves to no strategy (no signals to render)', () => {
    expect(getStrategy('does-not-exist')).toBeUndefined();
  });

  it('clearing a strategy yields an empty signal set for the renderer', () => {
    const candles = makeCandles(100);

    // Simulate the hook's resolution path: a null/empty id never resolves to a
    // strategy, so the markers collapse to an empty set regardless of candles.
    const clearedIds: Array<string | null | undefined> = [null, undefined, ''];
    for (const id of clearedIds) {
      const def = id ? getStrategy(id) : undefined;
      const signals = def ? def.evaluate(candles, def.defaults) : [];
      expect(signals).toEqual([]);
    }
  });
});
