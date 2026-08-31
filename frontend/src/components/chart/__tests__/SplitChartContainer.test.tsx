// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/no-explicit-any */

/**
 * Terminal UX Overhaul (Task 5.3) — component tests for the split-chart view.
 *
 * These are component tests (jsdom + React Testing Library), exercising the REAL
 * `SplitChartContainer` + `ChartPane` against the REAL `useChartUIStore`, with the
 * heavy chart child (`MainTerminalChart`) and the `react-resizable-panels` layout
 * primitive MOCKED so jsdom never has to initialize a canvas/chart engine.
 *
 * Asserts (Requirements 4.2, 4.5, 4.6):
 *  1. Two independent panes mount — `data-pane-id` A and B are both present, and
 *     each pane mounts its OWN `MainTerminalChart` instance (mount count = 2),
 *     proving the panes are independent chart instances (R4.2).
 *  2. The Active_Pane indication switches on click — clicking pane B makes B the
 *     active pane (`data-active` toggles and the emerald ring moves to B), and A
 *     becomes inactive (R4.5).
 *  3. Returning to single view uses the active pane's settings — toggling split
 *     off then on (single→split→single round-trip) preserves the Active_Pane's
 *     symbol/timeframe/chartType, so the single chart would render the active
 *     pane's own settings (R4.6, asserted at the store level).
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// ── Mount registry (hoisted so the mock factory can reference it) ─────────────
// A counter that increments once per MainTerminalChart mount. Two panes ⇒ two
// independent chart instances ⇒ a mount count of 2.
const charts = vi.hoisted(() => ({
  mounts: 0,
  reset() {
    this.mounts = 0;
  },
}));

// Mock the heavy chart child with a lightweight stand-in. It renders its
// `timeframe` prop so each pane's independent props are observable, and counts
// its mounts so we can prove two distinct instances are created.
vi.mock('../../MainTerminalChart', async () => {
  const ReactNs = await import('react');
  return {
    __esModule: true,
    default: function MockMainTerminalChart({ symbolOverride, timeframeOverride }: any) {
      ReactNs.useEffect(() => {
        charts.mounts += 1;
      }, []);
      return ReactNs.createElement('div', {
        'data-testid': 'main-chart',
        'data-symbol': String(symbolOverride ?? ''),
        'data-timeframe': String(timeframeOverride ?? ''),
      });
    },
  };
});

// Mock the resizable-panel primitive so the layout renders as plain divs in jsdom.
vi.mock('react-resizable-panels', async () => {
  const ReactNs = await import('react');
  return {
    __esModule: true,
    Group: ({ children }: any) =>
      ReactNs.createElement('div', { 'data-testid': 'group' }, children),
    Panel: ({ children }: any) =>
      ReactNs.createElement('div', { 'data-testid': 'panel' }, children),
    Separator: () => ReactNs.createElement('div', { 'data-testid': 'separator' }),
  };
});

// Import AFTER the mocks are declared so the components pick up the mocked deps.
import SplitChartContainer from '../SplitChartContainer';
import { useChartUIStore } from '../../../store/useChartUIStore';
import { useTradeStore } from '../../../store/useTradeStore';

const ACTIVE_RING = 'ring-emerald-500/70';
const INACTIVE_RING = 'ring-border-default';

/** Reset both stores to a deterministic split-enabled baseline. */
function resetStores() {
  // Split is mode-gated to INTRADAY/FNO (R4.7); seed a split-enabled profile.
  useTradeStore.setState({ activeProfile: 'INTRADAY' });
  useChartUIStore.setState({
    splitView: true,
    activePaneId: 'A',
    panes: [
      { id: 'A', symbol: 'RELIANCE', timeframe: '10m', chartType: 'candlestick' },
      { id: 'B', symbol: 'TCS', timeframe: '1h', chartType: 'line' },
    ],
  });
}

function paneEl(id: 'A' | 'B'): HTMLElement {
  return document.querySelector(`[data-pane-id="${id}"]`) as HTMLElement;
}

/** The chart instance rendered INSIDE a given pane. */
function chartIn(pane: HTMLElement): HTMLElement {
  return pane.querySelector('[data-testid="main-chart"]') as HTMLElement;
}

beforeEach(() => {
  charts.reset();
  resetStores();
});

afterEach(() => cleanup());

describe('SplitChartContainer — dual-pane render (R4.2)', () => {
  it('mounts two independent panes, each with its own chart instance', () => {
    render(<SplitChartContainer mode="INTRADAY" />);

    // Both panes are present in the DOM.
    const a = paneEl('A');
    const b = paneEl('B');
    expect(a).toBeInTheDocument();
    expect(b).toBeInTheDocument();

    // Exactly two independent MainTerminalChart instances mounted (one per pane).
    const chartInstances = screen.getAllByTestId('main-chart');
    expect(chartInstances).toHaveLength(2);
    expect(charts.mounts).toBe(2);

    // Each pane drives its OWN independent symbol + timeframe into its chart
    // instance (proving panes can chart different stocks at once — R4.3).
    //
    // Asserted PER PANE rather than as an unordered list over both charts. The
    // pane used to also print its symbol in a header, which is what pinned each
    // setting to a specific pane; the header was deliberately removed so the
    // chart gets the full pane height, so the binding is checked where it now
    // lives — on the chart instance each pane actually renders.
    expect(chartIn(a)).toHaveAttribute('data-symbol', 'RELIANCE');
    expect(chartIn(a)).toHaveAttribute('data-timeframe', '10m');
    expect(chartIn(b)).toHaveAttribute('data-symbol', 'TCS');
    expect(chartIn(b)).toHaveAttribute('data-timeframe', '1h');
  });

  it('renders no chrome of its own — the chart fills the pane', () => {
    render(<SplitChartContainer mode="INTRADAY" />);

    // The panes are intentionally header-free. This is the guard against a
    // per-pane toolbar creeping back in and stealing chart height.
    for (const id of ['A', 'B'] as const) {
      const pane = paneEl(id);
      expect(pane.querySelectorAll('button')).toHaveLength(0);
      // Its only child subtree is the chart.
      expect(pane).toContainElement(chartIn(pane));
    }
  });
});

// The panes carry independent settings (R4.3), but the controls that change them
// are NOT inside the pane — the pane is chrome-free by design, and the command bar
// / left panel route a change to whichever pane is active. So the input to these
// tests is the per-pane store setter (the same one those controls call), and the
// assertion is that the change lands on one pane only and is reflected in that
// pane's own chart instance.
describe('SplitChartContainer — per-pane settings are independent (R4.3)', () => {
  it('changing pane B timeframe leaves pane A timeframe untouched', () => {
    render(<SplitChartContainer mode="INTRADAY" />);

    act(() => {
      useChartUIStore.getState().setPaneTimeframe('B', '15m');
    });

    const panes = useChartUIStore.getState().panes;
    expect(panes.find((p) => p.id === 'B')?.timeframe).toBe('15m');
    // Pane A keeps its own timeframe — the two panes are independent.
    expect(panes.find((p) => p.id === 'A')?.timeframe).toBe('10m');

    // And each pane's chart re-rendered with only its own timeframe.
    expect(chartIn(paneEl('B'))).toHaveAttribute('data-timeframe', '15m');
    expect(chartIn(paneEl('A'))).toHaveAttribute('data-timeframe', '10m');
  });

  it('changing pane A chart type leaves pane B chart type untouched', () => {
    render(<SplitChartContainer mode="INTRADAY" />);

    act(() => {
      useChartUIStore.getState().setPaneChartType('A', 'area');
    });

    const panes = useChartUIStore.getState().panes;
    expect(panes.find((p) => p.id === 'A')?.chartType).toBe('area');
    // Pane B keeps its own chart type.
    expect(panes.find((p) => p.id === 'B')?.chartType).toBe('line');
  });

  it('changing one pane symbol leaves the sibling pane charting its own', () => {
    render(<SplitChartContainer mode="INTRADAY" />);

    act(() => {
      useChartUIStore.getState().setPaneSymbol('B', 'INFY');
    });

    // This is the property the removed header used to make visible: two panes,
    // two different instruments, at the same time.
    expect(chartIn(paneEl('B'))).toHaveAttribute('data-symbol', 'INFY');
    expect(chartIn(paneEl('A'))).toHaveAttribute('data-symbol', 'RELIANCE');
  });
});

describe('SplitChartContainer — active-pane indication switches on click (R4.5)', () => {
  it('starts with A active and B inactive', () => {
    render(<SplitChartContainer mode="INTRADAY" />);

    const a = paneEl('A');
    const b = paneEl('B');

    expect(a).toHaveAttribute('data-active', 'true');
    expect(b).toHaveAttribute('data-active', 'false');
    expect(a).toHaveClass(ACTIVE_RING);
    expect(b).toHaveClass(INACTIVE_RING);
  });

  it('clicking pane B makes B active and A inactive (indication + store)', () => {
    render(<SplitChartContainer mode="INTRADAY" />);

    fireEvent.click(paneEl('B'));

    // The store's Active_Pane updated.
    expect(useChartUIStore.getState().activePaneId).toBe('B');

    // The visual indication moved to B (data-active flag + emerald ring).
    const a = paneEl('A');
    const b = paneEl('B');
    expect(b).toHaveAttribute('data-active', 'true');
    expect(a).toHaveAttribute('data-active', 'false');
    expect(b).toHaveClass(ACTIVE_RING);
    expect(a).toHaveClass(INACTIVE_RING);

    // Clicking back to A switches the indication again.
    fireEvent.click(paneEl('A'));
    expect(useChartUIStore.getState().activePaneId).toBe('A');
    expect(paneEl('A')).toHaveAttribute('data-active', 'true');
    expect(paneEl('B')).toHaveAttribute('data-active', 'false');
  });
});

describe('SplitChartContainer — single-view round-trip uses active pane settings (R4.6)', () => {
  it('preserves the Active_Pane symbol/timeframe/chartType across split→single→split', () => {
    render(<SplitChartContainer mode="INTRADAY" />);

    // Make B the Active_Pane; B carries its own independent settings.
    fireEvent.click(paneEl('B'));
    const store = useChartUIStore.getState();
    expect(store.activePaneId).toBe('B');

    const activeBefore = store.panes.find((p) => p.id === 'B')!;
    const snapshot = { ...activeBefore };

    // Return to single view (split off), then back to split view (split on).
    // Single view renders the Active_Pane's settings as the sole chart, so the
    // active pane's symbol/timeframe/chartType must survive the round-trip.
    // Wrapped in act(): these setters re-render the mounted container, and an
    // unwrapped update logged a React act() warning on every run.
    act(() => useChartUIStore.getState().setSplitView(false));
    expect(useChartUIStore.getState().splitView).toBe(false);

    act(() => useChartUIStore.getState().setSplitView(true));
    expect(useChartUIStore.getState().splitView).toBe(true);

    const afterState = useChartUIStore.getState();
    // The Active_Pane designation is preserved.
    expect(afterState.activePaneId).toBe('B');
    // The Active_Pane's own settings are intact — these are what the single
    // chart uses on return to single view (R4.6).
    const activeAfter = afterState.panes.find((p) => p.id === afterState.activePaneId)!;
    expect(activeAfter.symbol).toBe(snapshot.symbol);
    expect(activeAfter.timeframe).toBe(snapshot.timeframe);
    expect(activeAfter.chartType).toBe(snapshot.chartType);

    // The sibling pane's settings are likewise untouched (panes independent).
    const sibling = afterState.panes.find((p) => p.id === 'A')!;
    expect(sibling.symbol).toBe('RELIANCE');
    expect(sibling.timeframe).toBe('10m');
    expect(sibling.chartType).toBe('candlestick');
  });
});
