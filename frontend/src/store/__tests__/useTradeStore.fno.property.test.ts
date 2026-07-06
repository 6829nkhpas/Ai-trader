// Feature: terminal-ux-overhaul — F&O is a peer Workspace_Mode (activeProfile === 'FNO')
//
// This file is the rewrite of the legacy fno-frontend-section round-trip test.
// The legacy `fnoMode` boolean / `toggleFnoMode` were removed (AD-1, R6.3): F&O
// is now a peer `TradeProfile` ('FNO') and `setActiveProfile` is the single
// entry. The two properties exercised here are:
//
// Property 4 (store-level mode round-trip portion):
//   "Switching to FNO and back to the prior profile preserves unrelated state."
//   The full Property 4 (split active-pane round-trip) lives in the
//   useChartUIStore split-slice tests; here we cover the store-level mode
//   round-trip — entering F&O and returning to the originating profile restores
//   the active profile and leaves selectedSymbol / timeframe / chartMode intact.
//
// Property 2: Selecting a mode preserves unrelated state.
//   "For any store state, setActiveProfile(m) leaves selectedSymbol, the
//    timeframe, and the chart mode unchanged."
//
// Validates: Requirements 6.2, 6.4, 7.1
//
// `setActiveProfile(profile)` only assigns `activeProfile`; it must NOT clear
// `selectedSymbol`, `activeTimeframe`, or `chartMode`. We seed the store with an
// arbitrary profile/symbol/timeframe/chartMode and assert these invariants hold
// across single selections and full F&O round-trips.

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';

import {
  useTradeStore,
  type TradeProfile,
  type ChartTimeframe,
} from '@/store/useTradeStore';

const PROFILES: TradeProfile[] = ['INTRADAY', 'SWING', 'INVESTOR', 'FNO'];
const NON_FNO_PROFILES: TradeProfile[] = ['INTRADAY', 'SWING', 'INVESTOR'];
const TIMEFRAMES: ChartTimeframe[] = ['1m', '5m', '10m', '15m', '1h', '1D', '1W'];
const CHART_MODES = ['STANDARD', 'VOLUME_PROFILE', 'FOOTPRINT'] as const;
const SYMBOLS = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'NIFTY 50'];

function store() {
  return useTradeStore.getState();
}

/** Arbitrary slice of the store state relevant to the mode-selection properties. */
function storeStateArb() {
  return fc.record({
    activeProfile: fc.constantFrom(...PROFILES),
    selectedSymbol: fc.constantFrom(...SYMBOLS),
    activeTimeframe: fc.constantFrom(...TIMEFRAMES),
    chartMode: fc.constantFrom(...CHART_MODES),
    fnoUnderlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
    fnoExpiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
  });
}

beforeEach(() => {
  // Reset only the fields these properties exercise back to defaults.
  useTradeStore.setState({
    activeProfile: 'INTRADAY',
    selectedSymbol: 'RELIANCE',
    activeTimeframe: '10m',
    chartMode: 'STANDARD',
    fnoUnderlying: 'NIFTY 50',
    fnoExpiry: '',
  });
});

describe('Property 2: selecting a mode preserves unrelated state', () => {
  it('setActiveProfile(m) leaves selectedSymbol, timeframe, and chartMode unchanged', () => {
    fc.assert(
      fc.property(storeStateArb(), fc.constantFrom(...PROFILES), (seed, target) => {
        useTradeStore.setState(seed);

        const symbolBefore = store().selectedSymbol;
        const timeframeBefore = store().activeTimeframe;
        const chartModeBefore = store().chartMode;

        store().setActiveProfile(target);

        // The selected mode is applied (single source of truth)...
        expect(store().activeProfile).toBe(target);
        // ...and unrelated state is untouched.
        expect(store().selectedSymbol).toBe(symbolBefore);
        expect(store().activeTimeframe).toBe(timeframeBefore);
        expect(store().chartMode).toBe(chartModeBefore);
      }),
      { numRuns: 300 },
    );
  });

  it('a sequence of mode selections never mutates unrelated state', () => {
    fc.assert(
      fc.property(
        storeStateArb(),
        fc.array(fc.constantFrom(...PROFILES), { minLength: 1, maxLength: 20 }),
        (seed, sequence) => {
          useTradeStore.setState(seed);

          const symbolBefore = store().selectedSymbol;
          const timeframeBefore = store().activeTimeframe;
          const chartModeBefore = store().chartMode;

          for (const mode of sequence) store().setActiveProfile(mode);

          // Final active profile is the last one selected (mutual exclusivity)...
          expect(store().activeProfile).toBe(sequence[sequence.length - 1]);
          // ...and unrelated state survived the whole sequence.
          expect(store().selectedSymbol).toBe(symbolBefore);
          expect(store().activeTimeframe).toBe(timeframeBefore);
          expect(store().chartMode).toBe(chartModeBefore);
        },
      ),
      { numRuns: 300 },
    );
  });
});

describe('Property 4 (store-level): switching to FNO and back preserves the prior profile and unrelated state', () => {
  it('prior profile -> FNO -> prior profile restores activeProfile and leaves symbol/timeframe/chartMode intact', () => {
    fc.assert(
      fc.property(storeStateArb(), fc.constantFrom(...NON_FNO_PROFILES), (seed, priorProfile) => {
        // Start from a concrete non-F&O profile so the round-trip is genuine.
        useTradeStore.setState({ ...seed, activeProfile: priorProfile });

        const symbolBefore = store().selectedSymbol;
        const timeframeBefore = store().activeTimeframe;
        const chartModeBefore = store().chartMode;

        // Enter F&O, then return to the originating profile.
        store().setActiveProfile('FNO');
        expect(store().activeProfile).toBe('FNO');

        store().setActiveProfile(priorProfile);

        // Round-trip: the prior workspace mode is restored...
        expect(store().activeProfile).toBe(priorProfile);
        // ...and unrelated state is unchanged across the round-trip.
        expect(store().selectedSymbol).toBe(symbolBefore);
        expect(store().activeTimeframe).toBe(timeframeBefore);
        expect(store().chartMode).toBe(chartModeBefore);
      }),
      { numRuns: 300 },
    );
  });

  it('entering and leaving F&O does not clear the F&O chain selectors either', () => {
    fc.assert(
      fc.property(storeStateArb(), fc.constantFrom(...NON_FNO_PROFILES), (seed, priorProfile) => {
        useTradeStore.setState({ ...seed, activeProfile: priorProfile });

        const underlyingBefore = store().fnoUnderlying;
        const expiryBefore = store().fnoExpiry;

        store().setActiveProfile('FNO');
        store().setActiveProfile(priorProfile);

        // setActiveProfile must not touch the F&O chain selectors.
        expect(store().fnoUnderlying).toBe(underlyingBefore);
        expect(store().fnoExpiry).toBe(expiryBefore);
      }),
      { numRuns: 200 },
    );
  });
});
