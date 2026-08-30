// @vitest-environment jsdom
//
// Terminal selection persistence.
//
// `localStorage` is user-writable, so every field read back here is treated as
// untrusted input. The property that matters is not "a saved value round-trips" —
// it is that a value which does NOT validate behaves exactly like an absent one,
// leaving the store's own default in place. Coercing a bad enum to a default
// would turn "we could not read your preference" into "your preference is
// candlestick", and restoring an unknown `activeProfile` would break the
// workspace switch outright.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  PREFERENCES_STORAGE_KEY,
  PREFERENCES_VERSION,
  flushPreferences,
  parsePreferences,
  readPreferences,
  resetPreferences,
  savePreferences,
  type TerminalPreferences,
} from '../preferences';

/** A fully-populated, valid blob. */
function validBlob(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    version: PREFERENCES_VERSION,
    activeProfile: 'FNO',
    selectedSymbol: 'RELIANCE26SEP1290CE',
    activeTimeframe: '15m',
    activeRange: '2Y',
    chartMode: 'FOOTPRINT',
    fnoUnderlying: 'RELIANCE',
    fnoExpiry: '2026-09-29',
    preFnoSymbol: 'RELIANCE',
    chartType: 'area',
    chartTypeParams: { brickSize: 5 },
    ghostLineMode: 'forecast',
    splitView: true,
    panes: [
      { id: 'A', symbol: 'RELIANCE', timeframe: '10m', chartType: 'candlestick' },
      { id: 'B', symbol: 'TCS', timeframe: '1h', chartType: 'line' },
    ],
    activePaneId: 'B',
    sidebarOpen: false,
    drawingColor: '#00FF88',
    magnetMode: 'strong',
    drawingsVisible: false,
    drawingsLocked: true,
    ...overrides,
  });
}

beforeEach(() => {
  localStorage.clear();
  resetPreferences();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('parsePreferences — a good blob restores every selection', () => {
  it('round-trips the full set', () => {
    const prefs = parsePreferences(validBlob());

    expect(prefs).toMatchObject<Partial<TerminalPreferences>>({
      activeProfile: 'FNO',
      selectedSymbol: 'RELIANCE26SEP1290CE',
      activeTimeframe: '15m',
      activeRange: '2Y',
      chartMode: 'FOOTPRINT',
      fnoUnderlying: 'RELIANCE',
      fnoExpiry: '2026-09-29',
      preFnoSymbol: 'RELIANCE',
      chartType: 'area',
      ghostLineMode: 'forecast',
      splitView: true,
      activePaneId: 'B',
      sidebarOpen: false,
      drawingColor: '#00FF88',
      magnetMode: 'strong',
      drawingsVisible: false,
      drawingsLocked: true,
    });
    expect(prefs.panes?.[0]).toEqual({
      id: 'A',
      symbol: 'RELIANCE',
      timeframe: '10m',
      chartType: 'candlestick',
    });
    expect(prefs.panes?.[1].symbol).toBe('TCS');
  });

  it('upper-cases the symbol the way the store keeps it', () => {
    expect(parsePreferences(validBlob({ selectedSymbol: ' tcs ' })).selectedSymbol).toBe('TCS');
  });

  it('keeps a false/empty selection rather than treating it as absent', () => {
    // `sidebarOpen: false` and `fnoExpiry: ''` are real choices. A truthiness
    // check would silently drop them back to the default (open / nearest expiry).
    const prefs = parsePreferences(validBlob({ sidebarOpen: false, fnoExpiry: '' }));
    expect(prefs.sidebarOpen).toBe(false);
    expect(prefs.fnoExpiry).toBe('');
  });
});

describe('parsePreferences — an unusable blob yields no preferences at all', () => {
  it('returns {} for every malformed shape rather than throwing', () => {
    for (const bad of [
      undefined,
      null,
      42,
      '',
      'not json',
      '[]',
      '"a string"',
      '{}', // no version
      JSON.stringify({ version: 999, activeProfile: 'FNO' }), // future schema
      JSON.stringify({ version: PREFERENCES_VERSION - 1, activeProfile: 'FNO' }), // older schema
    ]) {
      expect(parsePreferences(bad)).toEqual({});
    }
  });
});

describe('parsePreferences — a rejected field is indistinguishable from an absent one', () => {
  it('drops an unknown enum instead of coercing it to a default', () => {
    // The important half: `activeProfile` is absent from the result, so the store
    // keeps its own 'INTRADAY'. If this returned 'INTRADAY' the caller could not
    // tell a real choice from a rejected one — and `renderProfileContent` would
    // be handed a mode it cannot switch on.
    for (const field of [
      ['activeProfile', 'DAYTRADE'],
      ['activeTimeframe', '7m'],
      ['activeRange', '10Y'],
      ['chartMode', 'HEATMAP'],
      ['chartType', 'spaghetti'],
      ['ghostLineMode', 'psychic'],
      ['magnetMode', 'magnetic'],
      ['activePaneId', 'C'],
    ] as const) {
      const prefs = parsePreferences(validBlob({ [field[0]]: field[1] }));
      expect(prefs).not.toHaveProperty(field[0]);
      // The rest of the blob still restores — one bad field is not fatal.
      expect(prefs.selectedSymbol).toBe('RELIANCE26SEP1290CE');
    }
  });

  it('drops wrong-typed booleans and colours', () => {
    const prefs = parsePreferences(
      validBlob({ sidebarOpen: 'yes', drawingsLocked: 1, drawingColor: 'javascript:alert(1)' }),
    );
    expect(prefs).not.toHaveProperty('sidebarOpen');
    expect(prefs).not.toHaveProperty('drawingsLocked');
    expect(prefs).not.toHaveProperty('drawingColor');
  });

  it('bounds the symbol so a pathological blob cannot restore a huge string', () => {
    expect(parsePreferences(validBlob({ selectedSymbol: 'X'.repeat(500) }))).not.toHaveProperty(
      'selectedSymbol',
    );
    // Empty is not a symbol either — the store's default must win.
    expect(parsePreferences(validBlob({ selectedSymbol: '   ' }))).not.toHaveProperty(
      'selectedSymbol',
    );
  });

  it('keeps only finite numeric chart-type params', () => {
    const prefs = parsePreferences(
      validBlob({ chartTypeParams: { brickSize: 5, bad: 'x', worse: null, nope: Infinity } }),
    );
    expect(prefs.chartTypeParams).toEqual({ brickSize: 5 });
  });

  it('rejects a half-valid pane pair outright', () => {
    // One unusable pane makes the pair unusable: restoring pane A next to a
    // defaulted pane B would silently move the user's second chart.
    const prefs = parsePreferences(
      validBlob({
        panes: [
          { id: 'A', symbol: 'RELIANCE', timeframe: '10m', chartType: 'candlestick' },
          { id: 'B', symbol: 'TCS', timeframe: 'not-a-timeframe', chartType: 'line' },
        ],
      }),
    );
    expect(prefs).not.toHaveProperty('panes');
  });

  it('ignores the stored pane id and keys panes positionally', () => {
    // panes[0] is pane A by definition. A swapped blob must not produce two 'B's.
    const prefs = parsePreferences(
      validBlob({
        panes: [
          { id: 'B', symbol: 'RELIANCE', timeframe: '10m', chartType: 'candlestick' },
          { id: 'B', symbol: 'TCS', timeframe: '1h', chartType: 'line' },
        ],
      }),
    );
    expect(prefs.panes?.map((p) => p.id)).toEqual(['A', 'B']);
  });

  it('rejects a pane array that is not exactly two panes', () => {
    for (const panes of [[], [{ id: 'A', symbol: '', timeframe: '10m', chartType: 'line' }], 'x']) {
      expect(parsePreferences(validBlob({ panes }))).not.toHaveProperty('panes');
    }
  });
});

describe('parsePreferences — split view cannot be restored into a mode that forbids it', () => {
  it('forces splitView off for SWING and INVESTOR', () => {
    // `useChartUIStore.setSplitView` refuses to ENABLE split outside INTRADAY/FNO,
    // so a blob pairing splitView:true with SWING describes a state the UI cannot
    // produce. Restoring it would render a split workspace whose toggle then
    // refuses to bring it back once turned off.
    for (const profile of ['SWING', 'INVESTOR'] as const) {
      const prefs = parsePreferences(validBlob({ activeProfile: profile, splitView: true }));
      expect(prefs.activeProfile).toBe(profile);
      expect(prefs.splitView).toBe(false);
    }
  });

  it('leaves splitView on for the two modes that allow it', () => {
    for (const profile of ['INTRADAY', 'FNO'] as const) {
      expect(
        parsePreferences(validBlob({ activeProfile: profile, splitView: true })).splitView,
      ).toBe(true);
    }
  });
});

describe('savePreferences — two stores share one blob', () => {
  it('merges patches instead of replacing, so neither store erases the other', () => {
    vi.useFakeTimers();

    // useTradeStore's slice, then useChartUIStore's.
    savePreferences({ activeProfile: 'FNO', selectedSymbol: 'TCS' });
    savePreferences({ chartType: 'area', sidebarOpen: false });
    vi.runAllTimers();

    const stored = readPreferences();
    expect(stored.activeProfile).toBe('FNO');
    expect(stored.selectedSymbol).toBe('TCS');
    expect(stored.chartType).toBe('area');
    expect(stored.sidebarOpen).toBe(false);
  });

  it('debounces a burst into a single write', () => {
    vi.useFakeTimers();
    const setItem = vi.spyOn(Storage.prototype, 'setItem');

    savePreferences({ selectedSymbol: 'A' });
    savePreferences({ selectedSymbol: 'B' });
    savePreferences({ selectedSymbol: 'C' });
    expect(setItem).not.toHaveBeenCalled(); // nothing written yet
    vi.runAllTimers();

    expect(setItem).toHaveBeenCalledTimes(1);
    expect(readPreferences().selectedSymbol).toBe('C'); // last write wins
    setItem.mockRestore();
  });

  it('flushes a pending write when the tab goes away', () => {
    vi.useFakeTimers();
    savePreferences({ selectedSymbol: 'SBIN' });
    // Nothing written yet — still inside the debounce window.
    expect(localStorage.getItem(PREFERENCES_STORAGE_KEY)).toBeNull();

    // A selection made in the final 300ms before a close/reload must not be lost.
    window.dispatchEvent(new Event('pagehide'));

    expect(readPreferences().selectedSymbol).toBe('SBIN');
  });

  it('always stamps the current version so an older reader rejects it', () => {
    savePreferences({ selectedSymbol: 'TCS' });
    flushPreferences();
    const raw = JSON.parse(localStorage.getItem(PREFERENCES_STORAGE_KEY) as string);
    expect(raw.version).toBe(PREFERENCES_VERSION);
  });

  it('survives storage being full without throwing', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('QuotaExceededError');
    });
    // A failed persist must not break the session — the selection still applies
    // in memory, it just will not survive the reload.
    expect(() => {
      savePreferences({ selectedSymbol: 'TCS' });
      flushPreferences();
    }).not.toThrow();
    setItem.mockRestore();
  });
});

describe('readPreferences', () => {
  it('returns {} when nothing has been saved', () => {
    expect(readPreferences()).toEqual({});
  });

  it('returns {} when localStorage throws (private mode)', () => {
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('SecurityError');
    });
    expect(readPreferences()).toEqual({});
    getItem.mockRestore();
  });
});
