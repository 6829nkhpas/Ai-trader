// Feature: terminal-ux-overhaul
//
// Property 1: Workspace modes are mutually exclusive
//   "For any sequence of setActiveProfile calls, exactly one TradeProfile
//    (including FNO) is active, and selecting a mode deselects all others."
//   Validates: Requirements 1.2, 1.4
//
// Property 3: No second source of truth for F&O
//   "For any state, whether the F&O workspace is active is determined solely by
//    activeProfile === 'FNO'; no separate fnoMode flag exists."
//   Validates: Requirements 1.4, 6.3
//
// Isolation: switching workspace modes leaves the unrelated chart state
//   (activeTimeframe, chartMode, selectedSymbol) intact.
//   Validates: Requirements 6.2, 6.3, 6.4
//
// This rewrites the former fno-frontend-section "Property 10" test, which was
// written against the now-removed `fnoMode`/`toggleFnoMode`/`setFnoMode` flag.
// Under the unified model (AD-1), F&O is a peer `TradeProfile` and
// `setActiveProfile` is the only entry. Mutual exclusivity is intrinsic to a
// single enum field, and there is no second boolean source of truth.

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';

import {
  useTradeStore,
  type TradeProfile,
  type ChartTimeframe,
} from '@/store/useTradeStore';

const PROFILES: TradeProfile[] = ['INTRADAY', 'SWING', 'INVESTOR', 'FNO'];
const TIMEFRAMES: ChartTimeframe[] = ['1m', '5m', '10m', '15m', '1h', '1D', '1W'];
const CHART_MODES = ['STANDARD', 'VOLUME_PROFILE', 'FOOTPRINT'] as const;
const SYMBOLS = ['RELIANCE', 'TCS', 'INFY', 'NIFTY 50', 'BANKNIFTY'];

function store() {
  return useTradeStore.getState();
}

/** Arbitrary slice of the store state relevant to mode isolation. */
function storeStateArb() {
  return fc.record({
    activeProfile: fc.constantFrom(...PROFILES),
    activeTimeframe: fc.constantFrom(...TIMEFRAMES),
    chartMode: fc.constantFrom(...CHART_MODES),
    selectedSymbol: fc.constantFrom(...SYMBOLS),
    fnoUnderlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
    fnoExpiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
  });
}

beforeEach(() => {
  // Reset only the fields this property exercises back to defaults.
  useTradeStore.setState({
    activeProfile: 'INTRADAY',
    activeTimeframe: '10m',
    chartMode: 'STANDARD',
    selectedSymbol: 'RELIANCE',
    fnoUnderlying: 'NIFTY 50',
    fnoExpiry: '',
  });
});

describe('Property 1: workspace modes are mutually exclusive', () => {
  it('after any single setActiveProfile, exactly that mode is active', () => {
    fc.assert(
      fc.property(storeStateArb(), fc.constantFrom(...PROFILES), (seed, target) => {
        useTradeStore.setState(seed);

        store().setActiveProfile(target);

        // The selected mode is active...
        expect(store().activeProfile).toBe(target);
        // ...and it is exactly one of the known modes (a single enum value can
        // never represent two active modes at once).
        expect(PROFILES).toContain(store().activeProfile);
        // Exactly one mode equals the active value; all others are deselected.
        const activeCount = PROFILES.filter((p) => p === store().activeProfile).length;
        expect(activeCount).toBe(1);
      }),
      { numRuns: 200 },
    );
  });

  it('for any sequence of setActiveProfile calls, the last selected mode is the sole active mode', () => {
    fc.assert(
      fc.property(
        storeStateArb(),
        fc.array(fc.constantFrom(...PROFILES), { minLength: 1, maxLength: 25 }),
        (seed, sequence) => {
          useTradeStore.setState(seed);

          for (const mode of sequence) {
            store().setActiveProfile(mode);
            // Invariant after every step: exactly the just-selected mode is active.
            expect(store().activeProfile).toBe(mode);
          }

          // The final active mode is the last one selected — no residual mode.
          const last = sequence[sequence.length - 1];
          expect(store().activeProfile).toBe(last);
          const activeCount = PROFILES.filter((p) => p === store().activeProfile).length;
          expect(activeCount).toBe(1);
        },
      ),
      { numRuns: 200 },
    );
  });
});

describe('Property 3: no second source of truth for F&O', () => {
  it('F&O-active is determined solely by activeProfile === FNO', () => {
    fc.assert(
      fc.property(fc.constantFrom(...PROFILES), (target) => {
        store().setActiveProfile(target);

        const fnoActive = store().activeProfile === 'FNO';
        expect(fnoActive).toBe(target === 'FNO');
      }),
      { numRuns: 200 },
    );
  });

  it('the store state carries no legacy fnoMode flag (and no fno toggles)', () => {
    fc.assert(
      fc.property(fc.constantFrom(...PROFILES), (target) => {
        store().setActiveProfile(target);

        const state = store() as Record<string, unknown>;
        // No second boolean source of truth.
        expect('fnoMode' in state).toBe(false);
        // The legacy actions are gone too — only setActiveProfile drives F&O.
        expect('setFnoMode' in state).toBe(false);
        expect('toggleFnoMode' in state).toBe(false);
      }),
      { numRuns: 50 },
    );
  });
});

describe('Isolation: switching modes leaves unrelated chart state intact', () => {
  it('a single setActiveProfile leaves timeframe/chartMode/selectedSymbol unchanged', () => {
    fc.assert(
      fc.property(storeStateArb(), fc.constantFrom(...PROFILES), (seed, target) => {
        useTradeStore.setState(seed);

        const before = {
          activeTimeframe: store().activeTimeframe,
          chartMode: store().chartMode,
          selectedSymbol: store().selectedSymbol,
        };

        store().setActiveProfile(target);

        // Switching workspace mode never disturbs the chart state.
        expect(store().activeTimeframe).toBe(before.activeTimeframe);
        expect(store().chartMode).toBe(before.chartMode);
        expect(store().selectedSymbol).toBe(before.selectedSymbol);
      }),
      { numRuns: 200 },
    );
  });

  it('any sequence of mode switches leaves timeframe/chartMode/selectedSymbol unchanged', () => {
    fc.assert(
      fc.property(
        storeStateArb(),
        fc.array(fc.constantFrom(...PROFILES), { minLength: 0, maxLength: 25 }),
        (seed, sequence) => {
          useTradeStore.setState(seed);

          const before = {
            activeTimeframe: store().activeTimeframe,
            chartMode: store().chartMode,
            selectedSymbol: store().selectedSymbol,
          };

          for (const mode of sequence) store().setActiveProfile(mode);

          // No number of mode switches ever leaks into the chart state.
          expect(store().activeTimeframe).toBe(before.activeTimeframe);
          expect(store().chartMode).toBe(before.chartMode);
          expect(store().selectedSymbol).toBe(before.selectedSymbol);
        },
      ),
      { numRuns: 200 },
    );
  });
});
