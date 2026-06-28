// Feature: terminal-ux-overhaul — Property 4: Split round-trip preserves the active pane
//
// Property 4:
//   "For any pane states, toggling split off then on (or single -> split ->
//    single) preserves the Active_Pane's symbol/timeframe/chart type as the
//    single chart."
//
// Validates: Requirements 4.6
//
// The split slice lives in `useChartUIStore`: `splitView` (off by default),
// `panes: [ChartPaneState, ChartPaneState]` (ids 'A'/'B'), and `activePaneId`.
// `setSplitView` only flips the boolean (mode-gated to INTRADAY/FNO); it must
// NOT mutate the panes or the active-pane designation. Returning to single
// view therefore renders the Active_Pane's `{symbol, timeframe, chartType}`
// unchanged (R4.6).
//
// To exercise the mode-gated `setSplitView(true)` path we set the active
// workspace profile to INTRADAY (split-enabled) in setup.

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';

import {
  useChartUIStore,
  type ChartPaneState,
  type PaneId,
} from '@/store/useChartUIStore';
import { useTradeStore, type ChartTimeframe } from '@/store/useTradeStore';
import type { ChartType } from '@/charting/engines';

const PANE_IDS: PaneId[] = ['A', 'B'];
const SYMBOLS = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'NIFTY 50', 'BANKNIFTY'];
const TIMEFRAMES: ChartTimeframe[] = ['1m', '5m', '10m', '15m', '1h', '1D', '1W'];
const CHART_TYPES: ChartType[] = [
  'candlestick',
  'hollow-candle',
  'ohlc-bar',
  'line',
  'area',
  'baseline',
  'heikin-ashi',
  'renko',
  'kagi',
  'point-figure',
  'line-break',
];

function store() {
  return useChartUIStore.getState();
}

/** Arbitrary independent pane state for a given pane id. */
function paneArb(id: PaneId): fc.Arbitrary<ChartPaneState> {
  return fc.record({
    id: fc.constant(id),
    symbol: fc.constantFrom(...SYMBOLS),
    timeframe: fc.constantFrom(...TIMEFRAMES),
    chartType: fc.constantFrom(...CHART_TYPES),
  });
}

/** Arbitrary pair of panes plus the active-pane designation. */
function splitStateArb() {
  return fc.record({
    paneA: paneArb('A'),
    paneB: paneArb('B'),
    activePaneId: fc.constantFrom(...PANE_IDS),
  });
}

function activePane(): ChartPaneState {
  const s = store();
  return s.panes.find((p) => p.id === s.activePaneId)!;
}

beforeEach(() => {
  // Split is mode-gated to INTRADAY/FNO; pick a split-enabled profile so that
  // `setSplitView(true)` is honoured rather than no-op'd (R4.7).
  useTradeStore.getState().setActiveProfile('INTRADAY');
  useChartUIStore.setState({
    splitView: false,
    activePaneId: 'A',
    panes: [
      { id: 'A', symbol: 'RELIANCE', timeframe: '10m', chartType: 'candlestick' },
      { id: 'B', symbol: 'RELIANCE', timeframe: '10m', chartType: 'candlestick' },
    ],
  });
});

describe('Property 4: split round-trip preserves the active pane', () => {
  it('single -> split -> single preserves the Active_Pane symbol/timeframe/chartType', () => {
    fc.assert(
      fc.property(splitStateArb(), ({ paneA, paneB, activePaneId }) => {
        useChartUIStore.setState({
          splitView: false,
          activePaneId,
          panes: [paneA, paneB],
        });

        const before = { ...activePane() };

        // single -> split -> single
        store().setSplitView(true);
        expect(store().splitView).toBe(true);
        store().setSplitView(false);
        expect(store().splitView).toBe(false);

        // The Active_Pane is still the same pane, with identical settings,
        // ready to render as the single chart (R4.6).
        expect(store().activePaneId).toBe(activePaneId);
        const after = activePane();
        expect(after.symbol).toBe(before.symbol);
        expect(after.timeframe).toBe(before.timeframe);
        expect(after.chartType).toBe(before.chartType);
      }),
      { numRuns: 300 },
    );
  });

  it('split -> off -> on preserves both panes and the active-pane designation', () => {
    fc.assert(
      fc.property(splitStateArb(), ({ paneA, paneB, activePaneId }) => {
        useChartUIStore.setState({
          splitView: true,
          activePaneId,
          panes: [paneA, paneB],
        });

        const panesBefore = store().panes.map((p) => ({ ...p }));

        // Toggle split off then on again.
        store().setSplitView(false);
        store().setSplitView(true);

        expect(store().splitView).toBe(true);
        expect(store().activePaneId).toBe(activePaneId);
        // Every pane survived the toggle untouched.
        expect(store().panes).toEqual(panesBefore);
      }),
      { numRuns: 300 },
    );
  });

  it('repeated split toggling never mutates the Active_Pane settings', () => {
    fc.assert(
      fc.property(
        splitStateArb(),
        fc.array(fc.boolean(), { minLength: 1, maxLength: 20 }),
        ({ paneA, paneB, activePaneId }, toggles) => {
          useChartUIStore.setState({
            splitView: false,
            activePaneId,
            panes: [paneA, paneB],
          });

          const before = { ...activePane() };

          for (const on of toggles) store().setSplitView(on);

          // Regardless of the toggle sequence, the Active_Pane settings persist.
          const after = activePane();
          expect(store().activePaneId).toBe(activePaneId);
          expect(after.symbol).toBe(before.symbol);
          expect(after.timeframe).toBe(before.timeframe);
          expect(after.chartType).toBe(before.chartType);
        },
      ),
      { numRuns: 300 },
    );
  });
});
