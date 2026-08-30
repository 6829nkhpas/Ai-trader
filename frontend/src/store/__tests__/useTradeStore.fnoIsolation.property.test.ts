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
//   (activeTimeframe, chartMode) intact, and leaves every OTHER mode's
//   instrument intact.
//   Validates: Requirements 6.2, 6.3, 6.4
//
// `selectedSymbol` was originally part of that "unchanged" set, on the reading
// that a mode switch must not disturb the chart at all. It is now PER-MODE: each
// workspace remembers its own instrument (Investor on TCS while Swing is on INFY),
// so a switch is expected to move `selectedSymbol` to the incoming mode's symbol.
// The isolation property survives in a sharper form — a switch must not disturb
// any mode's stored instrument except by design — which is what these now assert.
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
    // Any subset of modes may already have a remembered instrument — including
    // none, which is a first-run user.
    symbolByProfile: fc.dictionary(fc.constantFrom(...PROFILES), fc.constantFrom(...SYMBOLS)),
    fnoUnderlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
    fnoExpiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
  });
}

type Seed = ReturnType<ReturnType<typeof storeStateArb>['generate']>['value'];

/**
 * Install `seed`, forcing the one invariant the store maintains for itself:
 * `symbolByProfile[activeProfile]` IS `selectedSymbol`. The store's initialiser
 * and both setters guarantee it, and no production code writes `selectedSymbol`
 * outside them — so a seed that disagreed would test a state the app cannot
 * reach, and its "failures" would say nothing about the app.
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
  // Reset only the fields this property exercises back to defaults.
  useTradeStore.setState({
    activeProfile: 'INTRADAY',
    activeTimeframe: '10m',
    chartMode: 'STANDARD',
    selectedSymbol: 'RELIANCE',
    symbolByProfile: { INTRADAY: 'RELIANCE' },
    fnoUnderlying: 'NIFTY 50',
    fnoExpiry: '',
  });
});

describe('Property 1: workspace modes are mutually exclusive', () => {
  it('after any single setActiveProfile, exactly that mode is active', () => {
    fc.assert(
      fc.property(storeStateArb(), fc.constantFrom(...PROFILES), (seed, target) => {
        seedStore(seed);

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
          seedStore(seed);

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

        const state = store() as unknown as Record<string, unknown>;
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
  it('a single setActiveProfile leaves timeframe/chartMode unchanged', () => {
    fc.assert(
      fc.property(storeStateArb(), fc.constantFrom(...PROFILES), (seed, target) => {
        seedStore(seed);

        const before = {
          activeTimeframe: store().activeTimeframe,
          chartMode: store().chartMode,
        };

        store().setActiveProfile(target);

        // The mode carries no timeframe or chart-type of its own: those are the
        // user's current view settings and a workspace switch must not rewrite them.
        expect(store().activeTimeframe).toBe(before.activeTimeframe);
        expect(store().chartMode).toBe(before.chartMode);
      }),
      { numRuns: 200 },
    );
  });

  it('a single setActiveProfile leaves every OTHER mode’s instrument untouched', () => {
    fc.assert(
      fc.property(storeStateArb(), fc.constantFrom(...PROFILES), (seed, target) => {
        const before = seedStore(seed);

        store().setActiveProfile(target);

        // Only the mode being entered may gain or keep an entry. Every other
        // mode's remembered instrument is none of this transition's business —
        // this is what stops a switch through Intraday from clobbering the
        // instrument Investor is parked on.
        for (const p of PROFILES) {
          if (p === target) continue;
          expect(store().symbolByProfile[p]).toBe(before[p]);
        }
      }),
      { numRuns: 200 },
    );
  });

  it('for any sequence of mode switches, the chart shows the active mode’s own instrument', () => {
    fc.assert(
      fc.property(
        storeStateArb(),
        fc.array(fc.constantFrom(...PROFILES), { minLength: 0, maxLength: 25 }),
        (seed, sequence) => {
          const before = seedStore(seed);

          const before2 = {
            activeTimeframe: store().activeTimeframe,
            chartMode: store().chartMode,
          };

          for (const mode of sequence) {
            store().setActiveProfile(mode);
            // The core invariant, after EVERY step: `selectedSymbol` is exactly
            // the active mode's entry. It is a projection of the map, never an
            // independent value that could drift out of step with it.
            expect(store().selectedSymbol).toBe(store().symbolByProfile[store().activeProfile]);
          }

          // No number of mode switches leaks into the view settings.
          expect(store().activeTimeframe).toBe(before2.activeTimeframe);
          expect(store().chartMode).toBe(before2.chartMode);

          // Modes the sequence never entered keep their instrument exactly.
          for (const p of PROFILES) {
            if (sequence.includes(p)) continue;
            expect(store().symbolByProfile[p]).toBe(before[p]);
          }
        },
      ),
      { numRuns: 200 },
    );
  });

  it('a mode round trip comes back to the instrument that mode was left on', () => {
    fc.assert(
      fc.property(
        storeStateArb(),
        fc.constantFrom(...PROFILES),
        fc.constantFrom(...SYMBOLS),
        (seed, other, pick) => {
          seedStore(seed);
          const home = store().activeProfile;
          fc.pre(other !== home);

          store().setSelectedSymbol(pick);
          store().setActiveProfile(other);
          store().setActiveProfile(home);

          // Leaving a mode and returning restores what was on screen, whatever
          // the detour did. None of the seeded symbols is an F&O contract, so no
          // underlying substitution is in play here.
          expect(store().selectedSymbol).toBe(pick.toUpperCase());
        },
      ),
      { numRuns: 200 },
    );
  });
});
