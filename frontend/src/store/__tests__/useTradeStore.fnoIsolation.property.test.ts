// Feature: fno-frontend-section, Property 10
//
// Property 10: Toggling F&O mode never alters the existing profile state.
//
// "For any store state, invoking `toggleFnoMode` (activating or deactivating)
//  leaves `activeProfile`, `activeTimeframe`, and `chartMode` unchanged, so the
//  existing Intraday/Swing/Investor profiles and their charts are never
//  disturbed by the F&O toggle."
//
// Validates: Requirements 9.4
//
// `toggleFnoMode` (and its sibling `setFnoMode`) are deliberately scoped to the
// single `fnoMode` boolean source of truth. They must never write to the
// existing profile/workspace state — `activeProfile`, `activeTimeframe`, or
// `chartMode`. We seed the store with arbitrary values for those fields plus an
// arbitrary starting `fnoMode`, invoke the toggle (and, for thoroughness, the
// explicit setter), and assert the three profile fields are byte-for-byte
// unchanged while only `fnoMode` flips.

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';

import {
  useTradeStore,
  type TradeProfile,
  type ChartTimeframe,
} from '@/store/useTradeStore';

const PROFILES: TradeProfile[] = ['INTRADAY', 'SWING', 'INVESTOR'];
const TIMEFRAMES: ChartTimeframe[] = ['1m', '5m', '10m', '15m', '1h', '1D', '1W'];
const CHART_MODES = ['STANDARD', 'VOLUME_PROFILE', 'FOOTPRINT'] as const;

function store() {
  return useTradeStore.getState();
}

/** Arbitrary slice of the store state relevant to toggle isolation. */
function storeStateArb() {
  return fc.record({
    fnoMode: fc.boolean(),
    activeProfile: fc.constantFrom(...PROFILES),
    activeTimeframe: fc.constantFrom(...TIMEFRAMES),
    chartMode: fc.constantFrom(...CHART_MODES),
    fnoUnderlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
    fnoExpiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
  });
}

beforeEach(() => {
  // Reset only the fields this property exercises back to defaults.
  useTradeStore.setState({
    fnoMode: false,
    activeProfile: 'INTRADAY',
    activeTimeframe: '10m',
    chartMode: 'STANDARD',
    fnoUnderlying: 'NIFTY 50',
    fnoExpiry: '',
  });
});

describe('Property 10: toggling F&O mode never alters the existing profile state', () => {
  it('a single toggle leaves activeProfile/activeTimeframe/chartMode unchanged', () => {
    fc.assert(
      fc.property(storeStateArb(), (seed) => {
        useTradeStore.setState(seed);

        const before = {
          activeProfile: store().activeProfile,
          activeTimeframe: store().activeTimeframe,
          chartMode: store().chartMode,
          fnoMode: store().fnoMode,
        };

        store().toggleFnoMode();

        // The F&O toggle flips ONLY fnoMode...
        expect(store().fnoMode).toBe(!before.fnoMode);
        // ...and never disturbs the existing profile workspace state.
        expect(store().activeProfile).toBe(before.activeProfile);
        expect(store().activeTimeframe).toBe(before.activeTimeframe);
        expect(store().chartMode).toBe(before.chartMode);
      }),
      { numRuns: 200 },
    );
  });

  it('toggling to a specific target (activate or deactivate) preserves profile state', () => {
    fc.assert(
      fc.property(storeStateArb(), (seed) => {
        useTradeStore.setState(seed);

        const before = {
          activeProfile: store().activeProfile,
          activeTimeframe: store().activeTimeframe,
          chartMode: store().chartMode,
        };

        // Whether the toggle is an activation (false -> true) or a
        // deactivation (true -> false), the profile fields are untouched.
        store().toggleFnoMode();

        expect(store().activeProfile).toBe(before.activeProfile);
        expect(store().activeTimeframe).toBe(before.activeTimeframe);
        expect(store().chartMode).toBe(before.chartMode);
      }),
      { numRuns: 200 },
    );
  });

  it('repeated toggles never alter the profile state across many flips', () => {
    fc.assert(
      fc.property(storeStateArb(), fc.integer({ min: 0, max: 20 }), (seed, count) => {
        useTradeStore.setState(seed);

        const before = {
          activeProfile: store().activeProfile,
          activeTimeframe: store().activeTimeframe,
          chartMode: store().chartMode,
        };

        for (let i = 0; i < count; i++) store().toggleFnoMode();

        // No number of F&O toggles ever leaks into the profile workspace state.
        expect(store().activeProfile).toBe(before.activeProfile);
        expect(store().activeTimeframe).toBe(before.activeTimeframe);
        expect(store().chartMode).toBe(before.chartMode);
      }),
      { numRuns: 200 },
    );
  });

  it('setFnoMode (the explicit setter) also leaves profile state untouched', () => {
    fc.assert(
      fc.property(storeStateArb(), fc.boolean(), (seed, target) => {
        useTradeStore.setState(seed);

        const before = {
          activeProfile: store().activeProfile,
          activeTimeframe: store().activeTimeframe,
          chartMode: store().chartMode,
        };

        store().setFnoMode(target);

        expect(store().fnoMode).toBe(target);
        expect(store().activeProfile).toBe(before.activeProfile);
        expect(store().activeTimeframe).toBe(before.activeTimeframe);
        expect(store().chartMode).toBe(before.chartMode);
      }),
      { numRuns: 200 },
    );
  });
});
