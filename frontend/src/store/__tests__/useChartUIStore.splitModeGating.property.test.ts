// Feature: terminal-ux-overhaul
//
// Property 7: Split is mode-gated
//   "For any activeProfile, setSplitView(true) yields a rendered split only
//    when the mode is INTRADAY or FNO; otherwise the control is absent and the
//    view stays single (splitView stays false)."
//
//   Validates: Requirements 4.7
//
// `setSplitView` enforces the mode gate at the store boundary (AD-5):
//   - setSplitView(true) enables split ONLY when activeProfile ∈ {INTRADAY, FNO}
//   - in SWING / INVESTOR, setSplitView(true) is a no-op (view stays single)
//   - setSplitView(false) is always allowed (returning to single view is valid
//     in any mode)
// We drive the gate via useTradeStore.setActiveProfile(profile) and assert the
// resulting splitView flag matches the gate for every profile.

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';

import { useChartUIStore } from '@/store/useChartUIStore';
import { useTradeStore, type TradeProfile } from '@/store/useTradeStore';

const PROFILES: TradeProfile[] = ['INTRADAY', 'SWING', 'INVESTOR', 'FNO'];
const SPLIT_ALLOWED: TradeProfile[] = ['INTRADAY', 'FNO'];

function chartStore() {
  return useChartUIStore.getState();
}

function isSplitAllowed(profile: TradeProfile): boolean {
  return SPLIT_ALLOWED.includes(profile);
}

beforeEach(() => {
  // Start each run from a known single-view state in a split-allowed mode.
  useTradeStore.setState({ activeProfile: 'INTRADAY' });
  useChartUIStore.setState({ splitView: false });
});

describe('Property 7: split is mode-gated', () => {
  it('setSplitView(true) enables split only in INTRADAY or FNO', () => {
    fc.assert(
      fc.property(fc.constantFrom(...PROFILES), (profile) => {
        useChartUIStore.setState({ splitView: false });
        useTradeStore.getState().setActiveProfile(profile);

        chartStore().setSplitView(true);

        // Split turns on only for the gated modes; otherwise it stays single.
        expect(chartStore().splitView).toBe(isSplitAllowed(profile));
      }),
      { numRuns: 200 },
    );
  });

  it('setSplitView(false) returns to single view in every mode', () => {
    fc.assert(
      fc.property(fc.constantFrom(...PROFILES), (profile) => {
        // Force split on (bypassing the gate) so we can verify disabling works
        // regardless of the active mode.
        useChartUIStore.setState({ splitView: true });
        useTradeStore.getState().setActiveProfile(profile);

        chartStore().setSplitView(false);

        // Disabling split is always honored.
        expect(chartStore().splitView).toBe(false);
      }),
      { numRuns: 200 },
    );
  });

  it('repeated setSplitView(true) in a non-gated mode never enables split', () => {
    fc.assert(
      fc.property(
        fc.constantFrom<TradeProfile>('SWING', 'INVESTOR'),
        fc.integer({ min: 1, max: 25 }),
        (profile, attempts) => {
          useChartUIStore.setState({ splitView: false });
          useTradeStore.getState().setActiveProfile(profile);

          for (let i = 0; i < attempts; i += 1) {
            chartStore().setSplitView(true);
            // No number of attempts can flip the gate in an unsupported mode.
            expect(chartStore().splitView).toBe(false);
          }
        },
      ),
      { numRuns: 100 },
    );
  });
});
