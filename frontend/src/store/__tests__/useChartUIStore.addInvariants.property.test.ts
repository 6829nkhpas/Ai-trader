// Feature: professional-charting-suite, Property 10
//
// Property 10: Active-indicator add invariants hold.
//
// "For any sequence of add operations on a symbol's active-indicator list, the
//  list never contains duplicate indicator ids and never exceeds 50 entries;
//  any rejected add (duplicate or at-capacity) leaves the list unchanged."
//
// Validates: Requirements 4.3, 4.4, 4.5
//
// We drive `addIndicator(symbol, id)` with arbitrary sequences of engine ids
// (drawn from `listIndicators()`) over one or more symbols and assert, after
// every call, that:
//   - the per-symbol list never exceeds MAX_INDICATORS_PER_SYMBOL (50)
//   - no two active instances share the same indicatorId (duplicate rejection)
//   - instanceIds are unique
//   - a successful add appends exactly one new instance (with a fresh,
//     unique instanceId) to the end of the list and changes nothing else
//   - a rejected add leaves the list byte-for-byte unchanged

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';

import {
  useChartUIStore,
  MAX_INDICATORS_PER_SYMBOL,
  type ActiveIndicator,
} from '@/store/useChartUIStore';
import { listIndicators } from '@/charting/engines';

const ENGINE_IDS = listIndicators().map((d) => d.id);
const SYMBOLS = ['AAPL', 'MSFT', 'BTCUSD'] as const;

function store() {
  return useChartUIStore.getState();
}

function resetState() {
  useChartUIStore.setState({ activeIndicators: {} });
}

beforeEach(() => {
  resetState();
});

/** A sequence of (symbol, indicatorId) add operations. */
function addSequence() {
  return fc.array(
    fc.record({
      symbol: fc.constantFrom(...SYMBOLS),
      id: fc.constantFrom(...ENGINE_IDS),
    }),
    { minLength: 1, maxLength: 60 },
  );
}

function indicatorIds(list: ActiveIndicator[]): string[] {
  return list.map((i) => i.indicatorId);
}

function instanceIds(list: ActiveIndicator[]): string[] {
  return list.map((i) => i.instanceId);
}

function hasUniqueValues(values: string[]): boolean {
  return new Set(values).size === values.length;
}

describe('Property 10: active-indicator add invariants hold', () => {
  it('every add upholds the cap, no-duplicate-id, and unchanged-on-rejection invariants', () => {
    fc.assert(
      fc.property(addSequence(), (ops) => {
        resetState();

        for (const { symbol, id } of ops) {
          const before = store().getActiveIndicators(symbol);
          const beforeLen = before.length;

          const result = store().addIndicator(symbol, id);
          const after = store().getActiveIndicators(symbol);

          // Invariant: the list never exceeds the per-symbol cap.
          expect(after.length).toBeLessThanOrEqual(MAX_INDICATORS_PER_SYMBOL);

          // Invariant: no duplicate indicator ids and no duplicate instance ids.
          expect(hasUniqueValues(indicatorIds(after))).toBe(true);
          expect(hasUniqueValues(instanceIds(after))).toBe(true);

          if (result.ok) {
            // A successful add appends exactly one instance to the end.
            expect(after.length).toBe(beforeLen + 1);
            const added = after[after.length - 1];
            expect(added.instanceId).toBe(result.instanceId);
            expect(added.indicatorId).toBe(id);
            // The new instanceId is unique (was not present before).
            expect(instanceIds(before)).not.toContain(added.instanceId);
            // Every prior instance is left untouched and in order.
            expect(after.slice(0, beforeLen)).toEqual(before);
          } else {
            // A rejected add (duplicate or at-capacity) leaves the list unchanged.
            expect(after).toEqual(before);
            expect(result.error === 'duplicate' || result.error === 'at-capacity').toBe(true);
          }
        }
      }),
      { numRuns: 100 },
    );
  });

  it('re-adding any already-active id is rejected as a duplicate and leaves the list unchanged (Req 4.4)', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...SYMBOLS),
        fc.constantFrom(...ENGINE_IDS),
        (symbol, id) => {
          resetState();

          const first = store().addIndicator(symbol, id);
          expect(first.ok).toBe(true);
          const before = store().getActiveIndicators(symbol);

          const second = store().addIndicator(symbol, id);
          expect(second.ok).toBe(false);
          if (!second.ok) expect(second.error).toBe('duplicate');

          // Unchanged: still exactly the one instance from the first add.
          expect(store().getActiveIndicators(symbol)).toEqual(before);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('at capacity, any further add is rejected and the 50-entry list is unchanged (Req 4.5)', () => {
    fc.assert(
      fc.property(
        fc.constantFrom(...SYMBOLS),
        fc.constantFrom(...ENGINE_IDS),
        (symbol, id) => {
          resetState();

          // Seed the symbol to exactly capacity with distinct-param instances.
          // Periods are pushed far outside any registry default range so the
          // duplicate guard (which runs before the capacity guard) cannot match
          // the add-under-test and we reliably exercise the at-capacity path.
          const seeded: ActiveIndicator[] = Array.from(
            { length: MAX_INDICATORS_PER_SYMBOL },
            (_, i) => ({
              instanceId: `seed-${i}`,
              indicatorId: 'sma',
              params: { period: 10000 + i },
              style: { color: '#fff', lineWidth: 1, lineStyle: 'solid' as const },
              visible: true,
              paneId: null,
            }),
          );
          useChartUIStore.setState({ activeIndicators: { [symbol]: seeded } });
          const before = store().getActiveIndicators(symbol);
          expect(before.length).toBe(MAX_INDICATORS_PER_SYMBOL);

          const result = store().addIndicator(symbol, id);
          expect(result.ok).toBe(false);
          if (!result.ok) expect(result.error).toBe('at-capacity');

          const after = store().getActiveIndicators(symbol);
          expect(after.length).toBe(MAX_INDICATORS_PER_SYMBOL);
          expect(after).toEqual(before);
        },
      ),
      { numRuns: 100 },
    );
  });
});
