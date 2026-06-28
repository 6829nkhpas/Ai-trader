// Feature: fno-frontend-section, Property 9
//
// Property 9: Toggling F&O mode is a round-trip that restores the prior workspace.
//
// "For any store state, applying `toggleFnoMode` twice returns `fnoMode` to its
//  original value, so a non-F&O workspace active before activation is the
//  workspace rendered again after deactivation."
//
// Validates: Requirements 1.3
//
// `toggleFnoMode` is a pure boolean flip on the single source of truth
// (`fnoMode`) held in useTradeStore. The workspace branch in page.tsx renders
// `renderProfileContent()` (driven by the untouched `activeProfile`) whenever
// `fnoMode` is false, so restoring `fnoMode` to its original value restores the
// previously-active non-F&O workspace. We seed the store with an arbitrary
// `fnoMode`/`activeProfile`/`activeTimeframe`/`chartMode` and assert that two
// toggles return `fnoMode` to its starting value (and that the prior workspace
// driver, `activeProfile`, is the one rendered again on round-trip).

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

/** Arbitrary slice of the store state relevant to the F&O toggle round-trip. */
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

describe('Property 9: toggling F&O mode is a round-trip that restores the prior workspace', () => {
  it('two toggles return fnoMode to its original value', () => {
    fc.assert(
      fc.property(storeStateArb(), (seed) => {
        useTradeStore.setState(seed);

        const originalFnoMode = store().fnoMode;
        // The non-F&O workspace is driven by activeProfile; capture it so we can
        // confirm the prior workspace driver is the one rendered again.
        const priorWorkspaceDriver = store().activeProfile;

        store().toggleFnoMode();
        store().toggleFnoMode();

        // Round-trip: fnoMode is restored to its starting value...
        expect(store().fnoMode).toBe(originalFnoMode);
        // ...and the prior non-F&O workspace driver is unchanged, so the same
        // workspace is rendered again after a deactivate-then-reactivate cycle.
        expect(store().activeProfile).toBe(priorWorkspaceDriver);
      }),
      { numRuns: 200 },
    );
  });

  it('a single toggle flips fnoMode (so the round-trip is a genuine there-and-back)', () => {
    fc.assert(
      fc.property(storeStateArb(), (seed) => {
        useTradeStore.setState(seed);

        const before = store().fnoMode;
        store().toggleFnoMode();
        expect(store().fnoMode).toBe(!before);
      }),
      { numRuns: 200 },
    );
  });

  it('any even number of toggles restores the original value; any odd number flips it', () => {
    fc.assert(
      fc.property(storeStateArb(), fc.integer({ min: 0, max: 20 }), (seed, count) => {
        useTradeStore.setState(seed);

        const before = store().fnoMode;
        for (let i = 0; i < count; i++) store().toggleFnoMode();

        const expected = count % 2 === 0 ? before : !before;
        expect(store().fnoMode).toBe(expected);
      }),
      { numRuns: 200 },
    );
  });
});
