// Feature: professional-charting-suite, Property 11
//
// Property 11: Toggling visibility preserves configuration.
//
// "For any active indicator, toggling its visibility off and on again restores
//  it to its original visible value and leaves its parameters, style, paneId,
//  instanceId, and indicatorId unchanged."
//
// Validates: Requirements 4.7
//
// We seed a symbol's active-indicator list with arbitrary instances (arbitrary
// params, style, paneId, and initial visibility) and exercise
// `toggleIndicatorVisible(symbol, instanceId)`. We assert:
//   - a single toggle flips ONLY the `visible` flag and preserves params,
//     style, paneId, instanceId, and indicatorId
//   - toggling twice returns `visible` to its original value and leaves the
//     full configuration byte-for-byte unchanged
//   - other instances in the list are never touched

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';

import {
  useChartUIStore,
  type ActiveIndicator,
} from '@/store/useChartUIStore';
import { listIndicators } from '@/charting/engines';
import type { IndicatorId } from '@/charting/engines';

const ENGINE_IDS = listIndicators().map((d) => d.id) as IndicatorId[];
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

/** Arbitrary per-instance numeric parameter bag. */
function paramsArb() {
  return fc.dictionary(
    fc.string({ minLength: 1, maxLength: 6 }),
    fc.double({ min: -1e6, max: 1e6, noNaN: true }),
    { maxKeys: 5 },
  );
}

/** Arbitrary visual style override. */
function styleArb() {
  return fc.record({
    color: fc.constantFrom('#2962FF', '#FF5722', '#00C853', '#000000'),
    lineWidth: fc.integer({ min: 1, max: 6 }),
    lineStyle: fc.constantFrom('solid' as const, 'dashed' as const, 'dotted' as const),
  });
}

/** Arbitrary active indicator instance with a caller-supplied instanceId. */
function indicatorArb(instanceId: string) {
  return fc.record({
    indicatorId: fc.constantFrom(...ENGINE_IDS),
    params: paramsArb(),
    style: styleArb(),
    visible: fc.boolean(),
    paneId: fc.option(fc.string({ minLength: 1, maxLength: 8 }), { nil: null }),
  }).map((rec): ActiveIndicator => ({ instanceId, ...rec }));
}

/** A non-empty list of instances with unique instanceIds, plus a target index. */
function listAndTargetArb() {
  return fc
    .integer({ min: 1, max: 8 })
    .chain((n) =>
      fc.record({
        list: fc.tuple(...Array.from({ length: n }, (_, i) => indicatorArb(`inst-${i}`))),
        targetIndex: fc.integer({ min: 0, max: n - 1 }),
        symbol: fc.constantFrom(...SYMBOLS),
      }),
    );
}

/** Deep clone for capturing an immutable "before" snapshot. */
function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

describe('Property 11: toggling visibility preserves configuration', () => {
  it('a single toggle flips only `visible` and preserves all other config', () => {
    fc.assert(
      fc.property(listAndTargetArb(), ({ list, targetIndex, symbol }) => {
        resetState();
        const seeded = list as ActiveIndicator[];
        useChartUIStore.setState({ activeIndicators: { [symbol]: clone(seeded) } });

        const before = clone(store().getActiveIndicators(symbol));
        const target = before[targetIndex];

        store().toggleIndicatorVisible(symbol, target.instanceId);

        const after = store().getActiveIndicators(symbol);
        const toggled = after[targetIndex];

        // Only the visible flag flips on the target.
        expect(toggled.visible).toBe(!target.visible);
        expect(toggled.instanceId).toBe(target.instanceId);
        expect(toggled.indicatorId).toBe(target.indicatorId);
        expect(toggled.params).toEqual(target.params);
        expect(toggled.style).toEqual(target.style);
        expect(toggled.paneId).toBe(target.paneId);

        // Every other instance is left byte-for-byte unchanged.
        after.forEach((ind, i) => {
          if (i !== targetIndex) expect(ind).toEqual(before[i]);
        });
      }),
      { numRuns: 100 },
    );
  });

  it('toggling twice restores the original visible value and full config', () => {
    fc.assert(
      fc.property(listAndTargetArb(), ({ list, targetIndex, symbol }) => {
        resetState();
        const seeded = list as ActiveIndicator[];
        useChartUIStore.setState({ activeIndicators: { [symbol]: clone(seeded) } });

        const before = clone(store().getActiveIndicators(symbol));
        const target = before[targetIndex];

        store().toggleIndicatorVisible(symbol, target.instanceId);
        store().toggleIndicatorVisible(symbol, target.instanceId);

        const after = store().getActiveIndicators(symbol);

        // Two toggles return to the original visible value...
        expect(after[targetIndex].visible).toBe(target.visible);
        // ...and the entire list is unchanged.
        expect(after).toEqual(before);
      }),
      { numRuns: 100 },
    );
  });
});
