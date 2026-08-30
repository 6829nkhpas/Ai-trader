// @vitest-environment jsdom

/**
 * Terminal UX Overhaul (Task 9.5) — component tests for the F&O-aware
 * Instrument_Search UI in `LeftPanel`.
 *
 * These are component tests (jsdom + React Testing Library) exercising the REAL
 * `LeftPanel` against the REAL `useTradeStore` / `useChartUIStore`, with the
 * Tauri `invoke` boundary and the `/kite/quote` fetch MOCKED so the search
 * dropdown can be driven with controlled results (including a rejection to test
 * the error state). The heavy bottom-section children (sentiment / consensus /
 * pattern HUDs) are stubbed so the test stays focused on the search dropdown.
 *
 * Asserts:
 *  1. Distinct EQ vs FNO rows render — an EQ result shows its symbol/name; an
 *     FNO result shows its tradingsymbol, a CE/PE/FUT type badge, and the
 *     underlying·expiry·strike meta (R3.4).
 *  2. When `search_instruments` returns `[]`, an explicit "No instruments found"
 *     state is shown rather than a stale/fabricated result (R3.5).
 *  3. When `search_instruments` rejects, the explicit error message is shown and
 *     the previously charted instrument (`selectedSymbol`) is NOT changed (R3.5).
 *  4. Equity-only queries still work and route the selection to the chart (R3.6).
 *
 * _Requirements: 3.4, 3.5, 3.6_
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// ── Controllable `bridgeInvoke` boundary ─────────────────────────────────────
// `search_instruments` returns whatever the current test installs (or rejects);
// `load_workspace` (used by hydrateWatchlist) returns an empty workspace so no
// default symbols are seeded; everything else resolves to undefined.
//
// Mocked at `lib/bridge` — the transport chokepoint the panel actually calls —
// so the assertions hold for both the desktop IPC and the website HTTP path.
const ipc = vi.hoisted(() => ({
  search: async (_query: string): Promise<unknown[]> => [],
}));

vi.mock('../../../lib/bridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../lib/bridge')>()),
  bridgeInvoke: vi.fn(async (cmd: string, args?: { query?: string }) => {
    if (cmd === 'search_instruments') return ipc.search(args?.query ?? '');
    // Return a controlled, non-colliding persisted watchlist so hydrateWatchlist
    // does NOT seed the default NIFTY-50 blue chips (which include RELIANCE and
    // would collide with the dropdown rows under test).
    if (cmd === 'load_workspace') {
      return JSON.stringify([{ symbol: 'WLITEM', token: 1, name: 'Watch Item', sector: 'EQ' }]);
    }
    return undefined;
  }),
}));

// Stub the heavy bottom-section children — unrelated to the search dropdown.
vi.mock('../left-panel/LiveAssetHUD', () => ({ __esModule: true, default: () => null }));
vi.mock('../left-panel/SentimentBlock', () => ({ __esModule: true, default: () => null }));
vi.mock('../../quant/deep-quant/MultiTfPatternsView', () => ({ __esModule: true, default: () => null }));

import LeftPanel from '../LeftPanel';
import { useTradeStore } from '../../../store/useTradeStore';
import { useChartUIStore } from '../../../store/useChartUIStore';

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

const EQ_RESULT: SearchResult = { kind: 'EQ', symbol: 'RELIANCE', name: 'Reliance Industries', exchange: 'NSE' };
const FNO_RESULT: SearchResult = {
  kind: 'FNO',
  tradingsymbol: 'NIFTY24DEC24000CE',
  underlying: 'NIFTY',
  expiry: '24DEC',
  strike: 24000,
  optionType: 'CE',
};

/** Install the result set the next `search_instruments` call resolves with. */
function setSearchResults(results: SearchResult[]) {
  ipc.search = async () => results;
}

/** Make the next `search_instruments` call reject (backend error/timeout). */
function setSearchRejects() {
  ipc.search = async () => {
    throw new Error('backend unavailable');
  };
}

function resetStores() {
  useTradeStore.setState({ watchlist: [], selectedSymbol: 'RELIANCE' });
  useChartUIStore.setState({
    splitView: false,
    activePaneId: 'A',
    panes: [
      { id: 'A', symbol: 'PANEA_INIT', timeframe: '10m', chartType: 'candlestick' },
      { id: 'B', symbol: 'PANEB_INIT', timeframe: '1h', chartType: 'line' },
    ],
  });
}

function typeQuery(value: string) {
  const input = screen.getByLabelText('Search symbols');
  fireEvent.focus(input);
  fireEvent.change(input, { target: { value } });
}

/**
 * The search-results region, once the 400ms debounce has fired and the dropdown
 * has rendered.
 *
 * Every assertion about a result row must be scoped through this. The panel
 * renders `selectedSymbol` in its "No Technical Data for <symbol>" placeholder,
 * so an unscoped `findByText('RELIANCE')` matches that placeholder instantly and
 * never waits for the dropdown at all.
 */
function searchResults(): Promise<HTMLElement> {
  return screen.findByRole('group', { name: 'Search results' });
}

beforeEach(() => {
  resetStores();
  ipc.search = async () => [];
  // Mock the /kite/quote fetch the watchlist polls on mount.
  global.fetch = vi.fn(async () => ({
    ok: true,
    json: async () => ({ quotes: [] }),
  })) as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('LeftPanel — symbol selection routes to the active pane in split view (R4.4)', () => {
  it('clicking a watchlist row in split view sets the ACTIVE pane symbol, not the global selected symbol', async () => {
    // Split view ON, pane B is the active pane.
    useChartUIStore.setState({ splitView: true, activePaneId: 'B' });
    render(<LeftPanel />);

    // The watchlist hydrates from the mocked load_workspace (one WLITEM row).
    const row = await screen.findByText('WLITEM');

    const selectedBefore = useTradeStore.getState().selectedSymbol;
    fireEvent.click(row);

    // The selection routed to the ACTIVE pane (B), leaving the sibling pane and
    // the global selected symbol untouched — this is the split-view fix.
    expect(useChartUIStore.getState().panes.find((p) => p.id === 'B')?.symbol).toBe('WLITEM');
    expect(useChartUIStore.getState().panes.find((p) => p.id === 'A')?.symbol).toBe('PANEA_INIT');
    expect(useTradeStore.getState().selectedSymbol).toBe(selectedBefore);
  });

  it('clicking a search result in split view sets the ACTIVE pane symbol', async () => {
    useChartUIStore.setState({ splitView: true, activePaneId: 'A' });
    setSearchResults([EQ_RESULT]);
    render(<LeftPanel />);

    typeQuery('RELI');
    // Scoped to the results region on purpose. A bare findByText('RELIANCE')
    // matched the consensus placeholder ("No Technical Data for RELIANCE", which
    // renders `selectedSymbol` — 'RELIANCE' in this fixture) and resolved
    // IMMEDIATELY, before the 400ms search debounce had even fired. The test then
    // clicked a non-interactive span and asserted on a dropdown that had not
    // rendered yet.
    const eqRow = await within(await searchResults()).findByText('RELIANCE');
    const selectedBefore = useTradeStore.getState().selectedSymbol;
    fireEvent.click(eqRow);

    await waitFor(() => {
      expect(useChartUIStore.getState().panes.find((p) => p.id === 'A')?.symbol).toBe('RELIANCE');
    });
    // Global single-view symbol is NOT touched while in split view.
    expect(useTradeStore.getState().selectedSymbol).toBe(selectedBefore);
  });
});

describe('LeftPanel search — distinct EQ vs FNO rows (R3.4)', () => {
  it('renders an equity row (symbol + name) and an F&O row (tradingsymbol + meta + CE badge) distinctly', async () => {
    setSearchResults([EQ_RESULT, FNO_RESULT]);
    render(<LeftPanel />);

    typeQuery('NIFTY');

    // Scoped to the results region — see `searchResults`. Unscoped, 'RELIANCE'
    // resolved off the consensus placeholder before the debounce fired, so the
    // company-name assertion below ran against a dropdown that did not exist.
    const results = within(await searchResults());

    // Equity row: symbol + company name.
    expect(await results.findByText('RELIANCE')).toBeInTheDocument();
    expect(results.getByText('Reliance Industries')).toBeInTheDocument();

    // F&O row: the full trading symbol is shown.
    expect(results.getByText('NIFTY24DEC24000CE')).toBeInTheDocument();

    // F&O row carries the distinguishing underlying·expiry·strike meta so it is
    // visually distinct from an equity (R3.4).
    expect(results.getByText(/NIFTY · 24DEC · 24000/)).toBeInTheDocument();

    // F&O type badge (CE/PE/FUT) renders — there should be exactly one CE badge.
    const ceBadges = results.getAllByText('CE');
    expect(ceBadges.length).toBeGreaterThanOrEqual(1);
  });
});

describe('LeftPanel search — explicit no-results state (R3.5)', () => {
  it('shows "No instruments found" when the command returns an empty list', async () => {
    setSearchResults([]);
    render(<LeftPanel />);

    typeQuery('ZZZZ');

    expect(await screen.findByText('No instruments found')).toBeInTheDocument();
  });
});

describe('LeftPanel search — error state retains the charted instrument (R3.5)', () => {
  it('shows the error message and does NOT change the selected symbol when the command rejects', async () => {
    setSearchRejects();
    render(<LeftPanel />);

    const before = useTradeStore.getState().selectedSymbol;
    typeQuery('NIFTY');

    expect(await screen.findByText('Search failed — please try again')).toBeInTheDocument();

    // No fabricated results, and the previously charted instrument is retained.
    expect(screen.queryByText('No instruments found')).toBeNull();
    expect(useTradeStore.getState().selectedSymbol).toBe(before);
  });
});

describe('LeftPanel search — equity-only query still works (R3.6)', () => {
  it('renders an equity result and routes the selection to the single chart', async () => {
    setSearchResults([EQ_RESULT]);
    useTradeStore.setState({ selectedSymbol: 'TCS' });
    render(<LeftPanel />);

    typeQuery('RELI');

    const eqRow = await screen.findByText('RELIANCE');
    // No F&O contract should leak into an equity-only result set.
    expect(screen.queryByText('NIFTY24DEC24000CE')).toBeNull();

    // Selecting the equity routes it to the single charted symbol (R3.3/R3.6).
    fireEvent.click(eqRow);

    await waitFor(() => {
      expect(useTradeStore.getState().selectedSymbol).toBe('RELIANCE');
    });
    // Single view → the selection sets selectedSymbol, not a split pane: pane A
    // keeps its own symbol untouched (no setPaneSymbol routing in single view).
    expect(useChartUIStore.getState().panes.find((p) => p.id === 'A')?.symbol).toBe('PANEA_INIT');
  });
});
