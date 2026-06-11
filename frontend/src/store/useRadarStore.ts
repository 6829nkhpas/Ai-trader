// store/useRadarStore.ts — Quant Radar state (FEAT-037).
//
// Owns the user's radar watchlist (symbols they chose to track), the
// per-symbol scan results (located patterns + strategies), the radar
// timeframe, and the current on-chart visualization target.
//
// Persistence: the symbol list + timeframe are saved to the local SQLite
// workspace DB (key "__QUANT_RADAR__") and pushed to the Rust background
// worker's registry so live alerts follow the same symbols.

import { create } from 'zustand';
import type { Timeframe } from '../utils/chartTypes';
import {
  scanRadarSymbol,
  type RadarScan,
  type LocatedPattern,
  type LocatedStrategy,
} from '../utils/radarData';

const isTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

const RADAR_PERSIST_KEY = '__QUANT_RADAR__';

// ── Visualization target ──────────────────────────────────────────────────
// A single detection the user clicked to draw on the chart. The overlay hook
// reads this and renders the corresponding marker / zone / level.

export type RadarVizKind = 'pattern' | 'strategy';

export interface RadarVizTarget {
  symbol: string;
  timeframe: Timeframe;
  kind: RadarVizKind;
  pattern?: LocatedPattern;
  strategy?: LocatedStrategy;
}

export interface RadarSymbolState {
  symbol: string;
  scan: RadarScan | null;
  loading: boolean;
  error: string | null;
  lastScanned: number; // epoch ms
}

interface RadarStore {
  /** Whether the radar feature panel is enabled (master toggle). */
  enabled: boolean;
  /** User's chosen radar symbols (upper-cased, unique). */
  symbols: string[];
  /** Timeframe all radar scans run on. */
  timeframe: Timeframe;
  /** Per-symbol scan state, keyed by symbol. */
  scans: Record<string, RadarSymbolState>;
  /** The detection currently visualized on the chart (null = nothing). */
  vizTarget: RadarVizTarget | null;
  /** Whether on-chart visualization is enabled. */
  vizEnabled: boolean;
  /** Auto-rescan interval handle (module-managed). */
  _autoTimer: ReturnType<typeof setInterval> | null;

  setEnabled: (v: boolean) => void;
  setTimeframe: (tf: Timeframe) => void;
  addSymbol: (symbol: string) => void;
  removeSymbol: (symbol: string) => void;
  scanOne: (symbol: string, retriesLeft?: number) => Promise<void>;
  scanAll: () => Promise<void>;
  setVizTarget: (target: RadarVizTarget | null) => void;
  toggleViz: () => void;
  hydrate: () => Promise<void>;
  startAutoScan: (intervalMs?: number) => void;
  stopAutoScan: () => void;
}

// ── Persistence helpers ────────────────────────────────────────────────────

let persistTimer: ReturnType<typeof setTimeout> | null = null;

async function persist(symbols: string[], timeframe: Timeframe) {
  // Debounced SQLite save (mirrors watchlist persistence pattern).
  if (persistTimer) clearTimeout(persistTimer);
  persistTimer = setTimeout(async () => {
    if (!isTauri()) return;
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('save_workspace', {
        symbol: RADAR_PERSIST_KEY,
        stateJson: JSON.stringify({ symbols, timeframe }),
      });
    } catch (e) {
      console.warn('[Radar] persist failed:', e);
    }
  }, 400);
}

/** Push the symbol set to the Rust background worker registry. */
async function syncRegistry(symbols: string[]) {
  if (!isTauri()) return;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('set_radar_symbols', { symbols });
  } catch (e) {
    console.warn('[Radar] registry sync failed:', e);
  }
}

const clean = (s: string) => s.trim().toUpperCase();

// ── Scan in-flight guard ────────────────────────────────────────────────────
// Keyed by `${symbol}|${timeframe}`. Prevents the auto-scan timer, the empty-
// data retry, manual rescans, and scanAll from running overlapping scans for
// the same symbol+timeframe — which previously caused duplicate Kite backfills
// and last-write-wins stale-state overwrites on `scans[sym]`.
const scanInFlight = new Set<string>();

/** Run async tasks with a bounded number in flight at once (preserves
 *  rate-limit friendliness vs an unbounded fan-out, while removing the fully
 *  serial latency of the old 250ms-gap loop). */
async function runBounded<T>(items: T[], limit: number, fn: (item: T) => Promise<void>): Promise<void> {
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (cursor < items.length) {
      const idx = cursor++;
      await fn(items[idx]);
    }
  });
  await Promise.all(workers);
}

export const useRadarStore = create<RadarStore>((set, get) => ({
  enabled: true,
  symbols: [],
  timeframe: '10m',
  scans: {},
  vizTarget: null,
  vizEnabled: true,
  _autoTimer: null,

  setEnabled: (v) => set({ enabled: v }),

  setTimeframe: (tf) => {
    set({ timeframe: tf });
    persist(get().symbols, tf);
    // Re-scan everything on the new timeframe.
    void get().scanAll();
  },

  addSymbol: (symbol) => {
    const sym = clean(symbol);
    if (!sym) return;
    const { symbols } = get();
    if (symbols.includes(sym)) return;
    const updated = [...symbols, sym];
    set((state) => ({
      symbols: updated,
      scans: {
        ...state.scans,
        [sym]: { symbol: sym, scan: null, loading: true, error: null, lastScanned: 0 },
      },
    }));
    persist(updated, get().timeframe);
    void syncRegistry(updated);
    void get().scanOne(sym);
  },

  removeSymbol: (symbol) => {
    const sym = clean(symbol);
    const { symbols, vizTarget } = get();
    const updated = symbols.filter((s) => s !== sym);
    set((state) => {
      const scans = { ...state.scans };
      delete scans[sym];
      return {
        symbols: updated,
        scans,
        // Clear any visualization tied to the removed symbol.
        vizTarget: vizTarget?.symbol === sym ? null : vizTarget,
      };
    });
    persist(updated, get().timeframe);
    void syncRegistry(updated);
  },

  scanOne: async (symbol, retriesLeft = 2) => {
    const sym = clean(symbol);
    const tf = get().timeframe;
    const key = `${sym}|${tf}`;

    // Drop overlapping scans for the same symbol+timeframe (auto-scan vs
    // retry vs manual rescan). The retry path re-invokes scanOne only after
    // the previous scan has settled, so legitimate retries still proceed.
    if (scanInFlight.has(key)) {
      return;
    }
    scanInFlight.add(key);

    set((state) => ({
      scans: {
        ...state.scans,
        [sym]: {
          ...(state.scans[sym] ?? { symbol: sym, scan: null, lastScanned: 0 }),
          symbol: sym,
          loading: true,
          error: null,
        },
      },
    }));

    try {
      // Single native call: fetch candles from QuestDB + locate detections,
      // all in Rust. No browser fetch.
      const scan = await scanRadarSymbol(sym, tf);

      if (scan === null) {
        // Only happens outside the Tauri desktop app.
        set((state) => ({
          scans: {
            ...state.scans,
            [sym]: {
              symbol: sym, scan: null, loading: false,
              error: 'Open the desktop app to scan', lastScanned: Date.now(),
            },
          },
        }));
        return;
      }

      // Guard against a timeframe switch mid-scan: if the user changed the
      // radar timeframe while this scan was in flight, discard the stale result.
      if (get().timeframe !== tf) {
        return;
      }

      set((state) => ({
        scans: {
          ...state.scans,
          [sym]: {
            symbol: sym,
            scan,
            loading: false,
            error: scan.candle_count === 0 ? 'No candle data yet — warming up' : null,
            lastScanned: Date.now(),
          },
        },
      }));

      // First scan of a freshly-added symbol/timeframe triggers the backend's
      // proactive Kite backfill into QuestDB. That data may land just after
      // this call returns, so retry a couple of times shortly to pick it up
      // without making the user wait for the next auto-scan cycle. Bounded by
      // `retriesLeft` so it never loops forever when a symbol simply has no
      // data available.
      if (scan.candle_count === 0 && retriesLeft > 0) {
        setTimeout(() => {
          const stillTracked = get().symbols.includes(sym);
          const stillEmpty = (get().scans[sym]?.scan?.candle_count ?? 0) === 0;
          if (stillTracked && stillEmpty && get().timeframe === tf) {
            void get().scanOne(sym, retriesLeft - 1);
          }
        }, 4000);
      }
    } catch (e: any) {
      // Backend error (e.g. pool not ready, no data for this timeframe).
      // Surface the real reason instead of a generic message.
      const raw = typeof e === 'string' ? e : e?.message ?? String(e);
      set((state) => ({
        scans: {
          ...state.scans,
          [sym]: {
            symbol: sym,
            scan: null,
            loading: false,
            error: raw,
            lastScanned: Date.now(),
          },
        },
      }));
    } finally {
      scanInFlight.delete(key);
    }
  },

  scanAll: async () => {
    const { symbols } = get();
    // Bounded concurrency: scan a few symbols at a time. Faster than the old
    // fully-serial 250ms-gap loop for multi-symbol watchlists, while still
    // capping simultaneous Kite backfills to stay within broker rate limits.
    // The per-symbol in-flight guard in scanOne dedups any overlap with the
    // auto-scan timer or manual rescans.
    await runBounded(symbols, 3, (sym) => get().scanOne(sym));
  },

  setVizTarget: (target) => set({ vizTarget: target, vizEnabled: true }),

  toggleViz: () => set((s) => ({ vizEnabled: !s.vizEnabled })),

  hydrate: async () => {
    if (!isTauri()) return;
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const json = await invoke<string>('load_workspace', { symbol: RADAR_PERSIST_KEY });
      if (json && json !== '{}') {
        const parsed = JSON.parse(json) as { symbols?: string[]; timeframe?: Timeframe };
        const symbols = Array.isArray(parsed.symbols) ? parsed.symbols.map(clean) : [];
        const timeframe = parsed.timeframe ?? '10m';
        set((state) => ({
          symbols,
          timeframe,
          scans: symbols.reduce((acc, sym) => {
            acc[sym] = { symbol: sym, scan: null, loading: false, error: null, lastScanned: 0 };
            return acc;
          }, { ...state.scans } as Record<string, RadarSymbolState>),
        }));
        await syncRegistry(symbols);
        void get().scanAll();
      }
    } catch (e) {
      console.warn('[Radar] hydrate failed:', e);
    }
  },

  startAutoScan: (intervalMs = 90_000) => {
    const existing = get()._autoTimer;
    if (existing) clearInterval(existing);
    const timer = setInterval(() => {
      const { enabled, symbols } = get();
      if (enabled && symbols.length > 0) void get().scanAll();
    }, intervalMs);
    set({ _autoTimer: timer });
  },

  stopAutoScan: () => {
    const existing = get()._autoTimer;
    if (existing) clearInterval(existing);
    set({ _autoTimer: null });
  },
}));
