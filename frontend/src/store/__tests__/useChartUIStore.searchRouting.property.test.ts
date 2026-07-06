// Feature: terminal-ux-overhaul — Property 6: Search routes to the active pane
//
// Property 6:
//   "For any split state and any selected SearchResult, the selection sets the
//    Active_Pane's symbol and no other pane's."
//
// Validates: Requirements 3.3, 4.4
//
// The F&O-aware Instrument_Search (LeftPanel, task 9.2) returns a discriminated
// SearchResult union (EQ | FNO). When the trader selects a result, the routing
// rule sets ONLY the Active_Pane's symbol via `setPaneSymbol(activePaneId,
// symbolOf(result))` — the sibling pane is untouched (R3.3, R4.4). The symbol
// for a result is `result.kind === 'EQ' ? result.symbol : result.tradingsymbol`.
//
// To keep this a robust store-level property (decoupled from LeftPanel's DOM),
// we model the selection as that single store action and assert the invariant
// directly against `useChartUIStore`. `SearchResult` / `symbolOf` are not
// exported from LeftPanel, so we mirror the (small, pure) shape here.

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';

import {
  useChartUIStore,
  type ChartPaneState,
  type PaneId,
} from '@/store/useChartUIStore';
import type { ChartTimeframe } from '@/store/useTradeStore';
import type { ChartType } from '@/charting/engines';

// ── SearchResult shape (mirrors LeftPanel's module-local union) ─────────
type SearchResult =
  | { kind: 'EQ'; symbol: string; name: string; exchange: string }
  | {
      kind: 'FNO';
      tradingsymbol: string;
      underlying: string;
      expiry: string;
      strike: number | null;
      optionType: 'CE' | 'PE' | 'FUT';
    };

/** The instrument symbol used for charting regardless of result kind (R3.3). */
const symbolOf = (r: SearchResult): string =>
  r.kind === 'EQ' ? r.symbol : r.tradingsymbol;

const PANE_IDS: PaneId[] = ['A', 'B'];
const EQ_SYMBOLS = ['RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'NIFTY 50', 'BANKNIFTY'];
const UNDERLYINGS = ['NIFTY', 'BANKNIFTY', 'RELIANCE', 'TCS', 'FINNIFTY'];
const EXPIRIES = ['2024-01-25', '2024-02-29', '2024-03-28'];
const OPTION_TYPES: ('CE' | 'PE' | 'FUT')[] = ['CE', 'PE', 'FUT'];
const TIMEFRAMES: ChartTimeframe[] = ['1m', '5m', '10m', '15m', '1h', '1D', '1W'];
const CHART_TYPES: ChartType[] = [
  'candlestick', 'hollow-candle', 'ohlc-bar', 'line', 'area', 'baseline',
  'heikin-ashi', 'renko', 'kagi', 'point-figure', 'line-break',
];

function store() {
  return useChartUIStore.getState();
}

/** Arbitrary independent pane state for a given pane id. */
function paneArb(id: PaneId): fc.Arbitrary<ChartPaneState> {
  return fc.record({
    id: fc.constant(id),
    symbol: fc.constantFrom(...EQ_SYMBOLS),
    timeframe: fc.constantFrom(...TIMEFRAMES),
    chartType: fc.constantFrom(...CHART_TYPES),
  });
}

/** Arbitrary EQ search result. */
const eqResultArb: fc.Arbitrary<SearchResult> = fc.record({
  kind: fc.constant('EQ' as const),
  symbol: fc.constantFrom(...EQ_SYMBOLS),
  name: fc.string(),
  exchange: fc.constantFrom('NSE', 'BSE'),
});

/** Arbitrary FNO search result (CE/PE carry a strike; FUT may be null). */
const fnoResultArb: fc.Arbitrary<SearchResult> = fc
  .record({
    underlying: fc.constantFrom(...UNDERLYINGS),
    expiry: fc.constantFrom(...EXPIRIES),
    optionType: fc.constantFrom(...OPTION_TYPES),
    strike: fc.oneof(
      fc.integer({ min: 10000, max: 50000 }),
      fc.constant(null),
    ),
  })
  .map(({ underlying, expiry, optionType, strike }) => {
    const effectiveStrike = optionType === 'FUT' ? null : strike;
    const tradingsymbol =
      optionType === 'FUT'
        ? `${underlying}${expiry}FUT`
        : `${underlying}${expiry}${effectiveStrike ?? 0}${optionType}`;
    return {
      kind: 'FNO' as const,
      tradingsymbol,
      underlying,
      expiry,
      strike: effectiveStrike,
      optionType,
    };
  });

/** Arbitrary SearchResult covering both EQ and FNO variants. */
const searchResultArb: fc.Arbitrary<SearchResult> = fc.oneof(eqResultArb, fnoResultArb);

/** Read the current pane state by id. */
function paneById(id: PaneId): ChartPaneState {
  return store().panes.find((p) => p.id === id)!;
}

/** The pane id that is NOT the active pane (the sibling to assert on). */
function sibling(id: PaneId): PaneId {
  return id === 'A' ? 'B' : 'A';
}

beforeEach(() => {
  useChartUIStore.setState({
    splitView: false,
    activePaneId: 'A',
    panes: [
      { id: 'A', symbol: 'RELIANCE', timeframe: '10m', chartType: 'candlestick' },
      { id: 'B', symbol: 'RELIANCE', timeframe: '10m', chartType: 'candlestick' },
    ],
  });
});

describe('Property 6: search routes to the active pane', () => {
  it('selecting a result sets ONLY the active pane symbol; the sibling is unchanged', () => {
    fc.assert(
      fc.property(
        paneArb('A'),
        paneArb('B'),
        fc.constantFrom<PaneId>(...PANE_IDS),
        fc.boolean(),
        searchResultArb,
        (a, b, activePaneId, splitOn, result) => {
          // Arbitrary split state + arbitrary active pane designation.
          useChartUIStore.setState({
            splitView: splitOn,
            activePaneId,
            panes: [a, b],
          });

          const other = sibling(activePaneId);
          const siblingBefore = { ...paneById(other) };

          // Model the LeftPanel selection: route the result to the active pane.
          store().setPaneSymbol(activePaneId, symbolOf(result));

          // The active pane's symbol equals the result's chartable symbol.
          expect(paneById(activePaneId).symbol).toBe(symbolOf(result));

          // No other pane's symbol (or any field) changed.
          expect(paneById(other)).toEqual(siblingBefore);
        },
      ),
      { numRuns: 300 },
    );
  });

  it('routing FNO selections uses tradingsymbol; EQ selections use symbol', () => {
    fc.assert(
      fc.property(fc.constantFrom<PaneId>(...PANE_IDS), searchResultArb, (activePaneId, result) => {
        useChartUIStore.setState({ activePaneId });
        store().setPaneSymbol(activePaneId, symbolOf(result));

        const expected = result.kind === 'EQ' ? result.symbol : result.tradingsymbol;
        expect(paneById(activePaneId).symbol).toBe(expected);
      }),
      { numRuns: 200 },
    );
  });

  it('across a sequence of selections, only the (then-)active pane symbol ever changes', () => {
    fc.assert(
      fc.property(
        paneArb('A'),
        paneArb('B'),
        fc.array(
          fc.record({ active: fc.constantFrom<PaneId>(...PANE_IDS), result: searchResultArb }),
          { minLength: 1, maxLength: 25 },
        ),
        (a, b, ops) => {
          useChartUIStore.setState({ activePaneId: 'A', panes: [a, b] });

          for (const { active, result } of ops) {
            store().setActivePane(active);
            const other = sibling(active);
            const siblingBefore = { ...paneById(other) };

            store().setPaneSymbol(active, symbolOf(result));

            expect(paneById(active).symbol).toBe(symbolOf(result));
            expect(paneById(other)).toEqual(siblingBefore);
          }
        },
      ),
      { numRuns: 200 },
    );
  });
});
