// @vitest-environment jsdom
//
// The wiring, not the module. `lib/__tests__/preferences.test.ts` proves the blob
// is parsed and validated correctly; this proves the two stores actually READ it
// on boot and WRITE to it when a selection changes. A perfect preferences module
// that no store consults restores nothing, so these assertions run against the
// real stores.
//
// Both stores read their saved selections at MODULE EVALUATION time (the same
// approach `theme` uses, so the terminal never paints one frame of the defaults
// before rearranging itself). That means the blob has to be in storage before the
// import — hence `resetModules` + dynamic `import()` per case rather than a shared
// top-level import.
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.hoisted(() => {
  // `lib/env.ts` throws at import time when these are unset, taking down any
  // suite that transitively imports a store.
  process.env.NEXT_PUBLIC_API_BASE_URL ||= 'http://127.0.0.1:0/api/v1';
  process.env.NEXT_PUBLIC_DASHBOARD_URL ||= 'http://127.0.0.1:0/dashboard';
  process.env.NEXT_PUBLIC_AUTH_URL ||= 'https://auth.test.invalid';
});

const STORAGE_KEY = 'stratai.preferences';

/** Seed storage, then import both stores fresh so they read it on evaluation. */
async function bootWith(prefs: Record<string, unknown> | null) {
  // Cancel any debounced write still pending from the previous case. The module
  // registry has not been reset yet, so this resolves to the SAME instance that
  // holds the pending timer. Left alone, that timer fires after the clear below
  // and re-seeds storage with the previous case's selections — which the store
  // then reads as this case's saved preferences.
  (await import('../../lib/preferences')).resetPreferences();
  localStorage.clear();
  if (prefs) localStorage.setItem(STORAGE_KEY, JSON.stringify({ version: 1, ...prefs }));
  vi.resetModules();
  const [{ useTradeStore }, { useChartUIStore }] = await Promise.all([
    import('../useTradeStore'),
    import('../useChartUIStore'),
  ]);
  return { useTradeStore, useChartUIStore };
}

function storedPrefs(): Record<string, unknown> {
  return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}');
}

beforeEach(() => {
  vi.useRealTimers();
  localStorage.clear();
});

describe('a returning user boots into their own selections', () => {
  it('restores mode, symbol, timeframe, range, chart mode and the F&O chain', async () => {
    const { useTradeStore } = await bootWith({
      activeProfile: 'SWING',
      selectedSymbol: 'TCS',
      activeTimeframe: '1D',
      activeRange: '5Y',
      chartMode: 'VOLUME_PROFILE',
      fnoUnderlying: 'BANKNIFTY',
      fnoExpiry: '2026-09-29',
      symbolByProfile: { INTRADAY: 'RELIANCE', SWING: 'TCS', INVESTOR: 'HDFCBANK' },
    });

    const s = useTradeStore.getState();
    expect(s.activeProfile).toBe('SWING');
    expect(s.selectedSymbol).toBe('TCS');
    expect(s.activeTimeframe).toBe('1D');
    expect(s.activeRange).toBe('5Y');
    expect(s.chartMode).toBe('VOLUME_PROFILE');
    // Both survive together. Restoring through `setFnoUnderlying` would have
    // wiped the expiry, since that setter clears it as a side effect.
    expect(s.fnoUnderlying).toBe('BANKNIFTY');
    expect(s.fnoExpiry).toBe('2026-09-29');
    // Each mode's own instrument comes back, not just the one the tab closed on.
    expect(s.symbolByProfile).toMatchObject({
      INTRADAY: 'RELIANCE',
      SWING: 'TCS',
      INVESTOR: 'HDFCBANK',
    });
  });

  it('restores the chart type, ghost line, sidebar and the split layout', async () => {
    const { useChartUIStore } = await bootWith({
      activeProfile: 'INTRADAY',
      chartType: 'area',
      chartTypeParams: { brickSize: 12 },
      ghostLineMode: 'linear',
      sidebarOpen: false,
      splitView: true,
      activePaneId: 'B',
      panes: [
        { id: 'A', symbol: 'RELIANCE', timeframe: '5m', chartType: 'line' },
        { id: 'B', symbol: 'INFY', timeframe: '1h', chartType: 'candlestick' },
      ],
      drawingColor: '#123456',
      magnetMode: 'strong',
      drawingsVisible: false,
      drawingsLocked: true,
    });

    const s = useChartUIStore.getState();
    expect(s.chartType).toBe('area');
    expect(s.chartTypeParams).toEqual({ brickSize: 12 });
    expect(s.ghostLineMode).toBe('linear');
    expect(s.sidebarOpen).toBe(false);
    expect(s.splitView).toBe(true);
    expect(s.activePaneId).toBe('B');
    expect(s.drawingColor).toBe('#123456');
    expect(s.magnetMode).toBe('strong');
    expect(s.drawingsVisible).toBe(false);
    expect(s.drawingsLocked).toBe(true);
    // Per-pane symbols survive. Restoring via `setSplitView(true)` would have
    // re-seeded both panes from the active selection and lost these.
    expect(s.panes[0]).toEqual({ id: 'A', symbol: 'RELIANCE', timeframe: '5m', chartType: 'line' });
    expect(s.panes[1].symbol).toBe('INFY');
  });

  it('leaves transient UI at its default rather than restoring it', async () => {
    const { useChartUIStore } = await bootWith({
      // Even if a blob carries these, they are not part of the schema and must
      // not come back. Fullscreen especially: `page.tsx` clears it on unmount and
      // reopening the app inside an overlay would trap the user.
      isFullscreen: true,
      activeDrawingTool: 'trendline',
      showIndicatorManager: true,
      selectedDrawingId: 'abc',
    });

    const s = useChartUIStore.getState();
    expect(s.isFullscreen).toBe(false);
    expect(s.activeDrawingTool).toBeNull();
    expect(s.showIndicatorManager).toBe(false);
    expect(s.selectedDrawingId).toBeNull();
  });
});

describe('a first-time user boots into the defaults', () => {
  it('uses the cold-start defaults when nothing is stored', async () => {
    const { useTradeStore, useChartUIStore } = await bootWith(null);

    expect(useTradeStore.getState().activeProfile).toBe('INTRADAY');
    expect(useTradeStore.getState().selectedSymbol).toBe('RELIANCE');
    expect(useTradeStore.getState().activeTimeframe).toBe('10m');
    expect(useChartUIStore.getState().chartType).toBe('candlestick');
    expect(useChartUIStore.getState().splitView).toBe(false);
    expect(useChartUIStore.getState().sidebarOpen).toBe(true);
  });

  it('ignores a corrupt blob entirely instead of half-restoring', async () => {
    localStorage.setItem(STORAGE_KEY, '{ not json');
    vi.resetModules();
    const { useTradeStore } = await import('../useTradeStore');
    expect(useTradeStore.getState().selectedSymbol).toBe('RELIANCE');
    expect(useTradeStore.getState().activeProfile).toBe('INTRADAY');
  });
});

describe('each mode keeps its own instrument', () => {
  it('remembers a per-mode pick and restores all of them after a reload', async () => {
    const { useTradeStore } = await bootWith(null);
    const t = () => useTradeStore.getState();
    vi.useFakeTimers();

    // The scenario verbatim: TCS in Investor, INFY in Swing, RELIANCE in Intraday.
    t().setActiveProfile('INVESTOR');
    t().setSelectedSymbol('TCS');
    t().setActiveProfile('SWING');
    t().setSelectedSymbol('INFY');
    t().setActiveProfile('INTRADAY');
    t().setSelectedSymbol('RELIANCE');
    vi.runAllTimers();

    expect(storedPrefs().symbolByProfile).toEqual({
      INTRADAY: 'RELIANCE',
      SWING: 'INFY',
      INVESTOR: 'TCS',
    });

    // Reload the tab. Each mode still has its own chart, not one global symbol.
    vi.useRealTimers();
    const { useTradeStore: reloaded } = await bootWith(storedPrefs());
    const r = () => reloaded.getState();

    expect(r().activeProfile).toBe('INTRADAY');
    expect(r().selectedSymbol).toBe('RELIANCE');
    r().setActiveProfile('INVESTOR');
    expect(r().selectedSymbol).toBe('TCS');
    r().setActiveProfile('SWING');
    expect(r().selectedSymbol).toBe('INFY');
  });

  it('does not touch the live candles when a switch lands on the same instrument', async () => {
    const { useTradeStore } = await bootWith(null);
    const t = () => useTradeStore.getState();

    // Swing inherits RELIANCE on its first visit, so both modes now hold it.
    t().setActiveProfile('SWING');
    t().setActiveProfile('INTRADAY');

    const candles = [
      { symbol: 'RELIANCE', start_timestamp_ms: 1, open: 1, high: 1, low: 1, close: 1, volume: 1 },
    ];
    useTradeStore.setState({ ohlcCandles: candles });

    t().setActiveProfile('SWING');

    // Same instrument on both sides of the switch, so nothing downstream is told
    // anything changed: the buffer is the SAME array, not a fresh empty one.
    // This is what stops the chart refetching candles it is already displaying.
    expect(t().ohlcCandles).toBe(candles);
    expect(t().selectedSymbol).toBe('RELIANCE');
  });

  it('does clear the live candles when the switch changes instrument', async () => {
    const { useTradeStore } = await bootWith(null);
    const t = () => useTradeStore.getState();

    t().setActiveProfile('INVESTOR');
    t().setSelectedSymbol('TCS');
    t().setActiveProfile('INTRADAY');
    useTradeStore.setState({
      ohlcCandles: [
        { symbol: 'RELIANCE', start_timestamp_ms: 1, open: 1, high: 1, low: 1, close: 1, volume: 1 },
      ],
    });

    t().setActiveProfile('INVESTOR');

    // The counterpart to the case above: these ticks belong to RELIANCE and must
    // not be stitched onto TCS's chart.
    expect(t().selectedSymbol).toBe('TCS');
    expect(t().ohlcCandles).toEqual([]);
  });

  it('sends an F&O contract’s underlying to an equity mode, not the contract', async () => {
    const { useTradeStore } = await bootWith(null);
    const t = () => useTradeStore.getState();

    t().setActiveProfile('FNO');
    // What `useFnoAutoContract` does on entering F&O.
    t().setSelectedSymbol('RELIANCE26AUGFUT');

    t().setActiveProfile('SWING');

    // Swing has no expiry, strike or chain, so a contract is not a chart it can
    // render. The underlying is.
    expect(t().selectedSymbol).toBe('RELIANCE');

    // And F&O kept the contract for the return trip — this is what the removed
    // `preFnoSymbol` was for, now falling out of per-mode memory.
    t().setActiveProfile('FNO');
    expect(t().selectedSymbol).toBe('RELIANCE26AUGFUT');
  });

  it('hands an equity mode the index’s spot name, not its NFO name', async () => {
    const { useTradeStore } = await bootWith(null);
    const t = () => useTradeStore.getState();

    t().setActiveProfile('FNO');
    t().setSelectedSymbol('NIFTY2670724000CE');

    t().setActiveProfile('INVESTOR');

    // The contract parses to the NFO-side `NIFTY`, but an equity chart needs the
    // Kite tradingsymbol — `NSE:NIFTY` does not resolve, so it would render blank.
    expect(t().selectedSymbol).toBe('NIFTY 50');
  });
});

describe('selections are written back as the user makes them', () => {
  it('persists a mode switch and a symbol change', async () => {
    const { useTradeStore } = await bootWith(null);
    vi.useFakeTimers();

    useTradeStore.getState().setActiveProfile('SWING');
    useTradeStore.getState().setSelectedSymbol('infy');
    useTradeStore.getState().setActiveTimeframe('30m');
    vi.runAllTimers();

    expect(storedPrefs()).toMatchObject({
      activeProfile: 'SWING',
      selectedSymbol: 'INFY',
      activeTimeframe: '30m',
    });
  });

  it('persists chart selections from the other store into the SAME blob', async () => {
    const { useTradeStore, useChartUIStore } = await bootWith(null);
    vi.useFakeTimers();

    useTradeStore.getState().setSelectedSymbol('TCS');
    useChartUIStore.getState().setChartType('heikin-ashi');
    useChartUIStore.getState().setSidebarOpen(false);
    vi.runAllTimers();

    // One blob, two contributors — neither erases the other.
    expect(storedPrefs()).toMatchObject({
      selectedSymbol: 'TCS',
      chartType: 'heikin-ashi',
      sidebarOpen: false,
    });
  });

  it('persists a per-pane symbol change', async () => {
    const { useTradeStore, useChartUIStore } = await bootWith(null);
    vi.useFakeTimers();

    useTradeStore.setState({ activeProfile: 'INTRADAY' });
    useChartUIStore.getState().setSplitView(true);
    useChartUIStore.getState().setPaneSymbol('B', 'SBIN');
    useChartUIStore.getState().setActivePane('B');
    vi.runAllTimers();

    const stored = storedPrefs() as { panes?: Array<{ symbol: string }>; activePaneId?: string };
    expect(stored.panes?.[1].symbol).toBe('SBIN');
    expect(stored.activePaneId).toBe('B');
  });

  it('does NOT write on every market tick', async () => {
    const { useTradeStore } = await bootWith(null);
    vi.useFakeTimers();
    const setItem = vi.spyOn(Storage.prototype, 'setItem');

    // The tick buffer lives in the same store and updates many times a second.
    // Without the diff guard in the subscription this would schedule a
    // localStorage write per candle.
    for (let i = 0; i < 50; i += 1) {
      useTradeStore.setState({
        ohlcCandles: [
          { symbol: 'RELIANCE', start_timestamp_ms: i, open: 1, high: 1, low: 1, close: 1, volume: 1 },
        ],
        latencyMs: i,
      });
    }
    vi.runAllTimers();

    expect(setItem).not.toHaveBeenCalled();
    setItem.mockRestore();
  });
});
