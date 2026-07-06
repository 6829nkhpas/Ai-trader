// Feature: terminal-ux-overhaul
//
// Property 5: Panes are independent
//   "For any pane-state pair, setting one pane's symbol/timeframe/chart type
//    never changes the other pane's."
//
// Validates: Requirements 4.3, 4.8
//
// The split slice (AD-3/AD-4) holds two fully-independent ChartPaneState
// records in `panes: [A, B]`. The per-pane setters `setPaneSymbol`,
// `setPaneTimeframe`, and `setPaneChartType` each target a single pane by id.
// This property drives arbitrary updates against pane A and asserts pane B is
// byte-for-byte unchanged (and the symmetric case: updates to B leave A
// untouched). Independence holds across arbitrary sequences of mixed updates
// too: only the targeted pane ever changes on any step.

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';

import {
  useChartUIStore,
  type ChartPaneState,
  type PaneId,
} from '@/store/useChartUIStore';
import type { ChartTimeframe } from '@/store/useTradeStore';
import type { ChartType } from '@/charting/engines';

const SYMBOLS = ['RELIANCE', 'TCS', 'INFY', 'NIFTY 50', 'BANKNIFTY', 'HDFCBANK'] as const;
const TIMEFRAMES: ChartTimeframe[] = [
  '1m', '2m', '5m', '10m', '15m', '30m', '1h', '1H', '4h', '1D', '1W', '1M',
];
const CHART_TYPES: ChartType[] = [
  'candlestick', 'hollow-candle', 'ohlc-bar', 'line', 'area', 'baseline',
  'heikin-ashi', 'renko', 'kagi', 'point-figure', 'line-break',
];
const PANE_IDS: PaneId[] = ['A', 'B'];

function store() {
  return useChartUIStore.getState();
}

/** Arbitrary fully-populated pane state for a given fixed id. */
function paneArb(id: PaneId) {
  return fc.record({
    id: fc.constant(id),
    symbol: fc.constantFrom(...SYMBOLS),
    timeframe: fc.constantFrom(...TIMEFRAMES),
    chartType: fc.constantFrom(...CHART_TYPES),
  });
}

/** Seed both panes with arbitrary independent states. */
function seedPanes(a: ChartPaneState, b: ChartPaneState) {
  useChartUIStore.setState({ panes: [a, b] });
}

/** Read the current pane state by id. */
function paneById(id: PaneId): ChartPaneState {
  return store().panes.find((p) => p.id === id)!;
}

/** A single arbitrary pane-update operation against a chosen pane. */
type Update =
  | { kind: 'symbol'; id: PaneId; value: string }
  | { kind: 'timeframe'; id: PaneId; value: ChartTimeframe }
  | { kind: 'chartType'; id: PaneId; value: ChartType };

function updateArb() {
  return fc.oneof(
    fc.record({
      kind: fc.constant('symbol' as const),
      id: fc.constantFrom(...PANE_IDS),
      value: fc.constantFrom(...SYMBOLS),
    }),
    fc.record({
      kind: fc.constant('timeframe' as const),
      id: fc.constantFrom(...PANE_IDS),
      value: fc.constantFrom(...TIMEFRAMES),
    }),
    fc.record({
      kind: fc.constant('chartType' as const),
      id: fc.constantFrom(...PANE_IDS),
      value: fc.constantFrom(...CHART_TYPES),
    }),
  );
}

function applyUpdate(u: Update) {
  switch (u.kind) {
    case 'symbol':
      store().setPaneSymbol(u.id, u.value);
      break;
    case 'timeframe':
      store().setPaneTimeframe(u.id, u.value);
      break;
    case 'chartType':
      store().setPaneChartType(u.id, u.value);
      break;
  }
}

/** The pane id that an update does NOT target (the sibling to assert on). */
function sibling(id: PaneId): PaneId {
  return id === 'A' ? 'B' : 'A';
}

beforeEach(() => {
  useChartUIStore.setState({
    panes: [
      { id: 'A', symbol: 'RELIANCE', timeframe: '10m', chartType: 'candlestick' },
      { id: 'B', symbol: 'RELIANCE', timeframe: '10m', chartType: 'candlestick' },
    ],
    activePaneId: 'A',
  });
});

describe('Property 5: panes are independent', () => {
  it('setting pane A symbol/timeframe/chartType never changes pane B', () => {
    fc.assert(
      fc.property(paneArb('A'), paneArb('B'), (a, b) => {
        seedPanes(a, b);
        const bBefore = paneById('B');

        // Drive all three single-pane setters against A.
        store().setPaneSymbol('A', a.symbol === 'TCS' ? 'INFY' : 'TCS');
        store().setPaneTimeframe('A', a.timeframe === '1D' ? '5m' : '1D');
        store().setPaneChartType('A', a.chartType === 'line' ? 'area' : 'line');

        // Pane B is byte-for-byte unchanged.
        expect(paneById('B')).toEqual(bBefore);
      }),
      { numRuns: 200 },
    );
  });

  it('setting pane B symbol/timeframe/chartType never changes pane A', () => {
    fc.assert(
      fc.property(paneArb('A'), paneArb('B'), (a, b) => {
        seedPanes(a, b);
        const aBefore = paneById('A');

        store().setPaneSymbol('B', b.symbol === 'TCS' ? 'INFY' : 'TCS');
        store().setPaneTimeframe('B', b.timeframe === '1D' ? '5m' : '1D');
        store().setPaneChartType('B', b.chartType === 'line' ? 'area' : 'line');

        expect(paneById('A')).toEqual(aBefore);
      }),
      { numRuns: 200 },
    );
  });

  it('across any sequence of mixed updates, only the targeted pane ever changes', () => {
    fc.assert(
      fc.property(
        paneArb('A'),
        paneArb('B'),
        fc.array(updateArb(), { minLength: 1, maxLength: 30 }),
        (a, b, ops) => {
          seedPanes(a, b);

          for (const op of ops) {
            const other = sibling(op.id);
            const otherBefore = paneById(other);

            applyUpdate(op as Update);

            // The sibling pane is untouched by an update to `op.id`.
            expect(paneById(other)).toEqual(otherBefore);

            // The targeted pane reflects exactly the requested field change,
            // keeping its id and untouched fields stable.
            const target = paneById(op.id);
            expect(target.id).toBe(op.id);
            if (op.kind === 'symbol') expect(target.symbol).toBe(op.value);
            if (op.kind === 'timeframe') expect(target.timeframe).toBe(op.value);
            if (op.kind === 'chartType') expect(target.chartType).toBe(op.value);
          }
        },
      ),
      { numRuns: 200 },
    );
  });
});
