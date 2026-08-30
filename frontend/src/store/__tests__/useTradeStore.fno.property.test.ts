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
//   "For any store state, setActiveProfile(m) leaves the timeframe and the chart
//    mode unchanged, and resolves the symbol to mode m's own instrument."
//
// Validates: Requirements 6.2, 6.4, 7.1
//
// `setActiveProfile(profile)` must NOT clear `activeTimeframe` or `chartMode`.
// `selectedSymbol` used to be in that same untouched set; it is now per-mode
// (`symbolByProfile`), so a switch is expected to move it to the incoming mode's
// instrument. The preservation property still holds in the case it was written
// for — a mode with no instrument of its own yet inherits the current one rather
// than resetting to a default — and that is asserted explicitly below. The F&O
// round-trip further down is unaffected either way: entering F&O leaves the
// originating mode's entry alone, so returning to it restores the same symbol.

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
    // Any subset of modes may already have a remembered instrument.
    symbolByProfile: fc.dictionary(fc.constantFrom(...PROFILES), fc.constantFrom(...SYMBOLS)),
    fnoUnderlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
    fnoExpiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
  });
}

type Seed = ReturnType<ReturnType<typeof storeStateArb>['generate']>['value'];

/**
 * Install `seed`, forcing the store's own invariant that
 * `symbolByProfile[activeProfile]` IS `selectedSymbol`. Both setters and the
 * initialiser maintain it and nothing else writes `selectedSymbol`, so a seed
 * that broke it would describe an unreachable state.
 *
 * Returns the resulting map so a case can assert against what it seeded.
 */
function seedStore(seed: Seed) {
  const symbolByProfile: Partial<Record<TradeProfile, string>> = {
    ...(seed.symbolByProfile as Partial<Record<TradeProfile, string>>),
    [seed.activeProfile]: seed.selectedSymbol,
  };
  useTradeStore.setState({ ...seed, symbolByProfile });
  return symbolByProfile;
}

beforeEach(() => {
  // Reset only the fields these properties exercise back to defaults.
  useTradeStore.setState({
    activeProfile: 'INTRADAY',
    selectedSymbol: 'RELIANCE',
    activeTimeframe: '10m',
    chartMode: 'STANDARD',
    symbolByProfile: { INTRADAY: 'RELIANCE' },
    fnoUnderlying: 'NIFTY 50',
    fnoExpiry: '',
  });
});

describe('Property 2: selecting a mode preserves unrelated state', () => {
  it('setActiveProfile(m) leaves timeframe and chartMode unchanged, and shows m’s instrument', () => {
    fc.assert(
      fc.property(storeStateArb(), fc.constantFrom(...PROFILES), (seed, target) => {
        const seeded = seedStore(seed);

        const symbolBefore = store().selectedSymbol;
        const timeframeBefore = store().activeTimeframe;
        const chartModeBefore = store().chartMode;

        store().setActiveProfile(target);

        // The selected mode is applied (single source of truth)...
        expect(store().activeProfile).toBe(target);
        // ...the view settings are untouched...
        expect(store().activeTimeframe).toBe(timeframeBefore);
        expect(store().chartMode).toBe(chartModeBefore);
        // ...and the chart shows what THIS mode was last left on.
        expect(store().selectedSymbol).toBe(seeded[target] ?? symbolBefore);
      }),
      { numRuns: 300 },
    );
  });

  it('a mode with no instrument of its own inherits the current one instead of resetting', () => {
    fc.assert(
      fc.property(storeStateArb(), fc.constantFrom(...PROFILES), (seed, target) => {
        seedStore(seed);
        // A mode can only be un-visited if it is not the one already active: the
        // active mode's entry IS `selectedSymbol` by invariant.
        fc.pre(target !== store().activeProfile);
        // Wipe the target's memory: this is a user visiting that mode for the
        // first time. None of `SYMBOLS` is an F&O contract, so no underlying
        // substitution applies and a plain carry-over is expected.
        useTradeStore.setState({
          symbolByProfile: { ...store().symbolByProfile, [target]: undefined },
        });
        const symbolBefore = store().selectedSymbol;

        store().setActiveProfile(target);

        // Carried over, NOT reset to the cold-start default — a first visit to
        // Swing should keep charting whatever the user was already looking at.
        expect(store().selectedSymbol).toBe(symbolBefore);
        expect(store().symbolByProfile[target]).toBe(symbolBefore);
      }),
      { numRuns: 300 },
    );
  });

  it('a sequence of mode selections never mutates the view settings', () => {
    fc.assert(
      fc.property(
        storeStateArb(),
        fc.array(fc.constantFrom(...PROFILES), { minLength: 1, maxLength: 20 }),
        (seed, sequence) => {
          seedStore(seed);

          const timeframeBefore = store().activeTimeframe;
          const chartModeBefore = store().chartMode;

          for (const mode of sequence) store().setActiveProfile(mode);

          // Final active profile is the last one selected (mutual exclusivity)...
          expect(store().activeProfile).toBe(sequence[sequence.length - 1]);
          // ...and the view settings survived the whole sequence.
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
        seedStore({ ...seed, activeProfile: priorProfile });

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
        seedStore({ ...seed, activeProfile: priorProfile });

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
