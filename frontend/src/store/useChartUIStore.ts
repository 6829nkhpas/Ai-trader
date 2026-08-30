import { create } from 'zustand';
import { getIndicator, validateParams, clearUnlocked } from '../charting/engines';
import type { IndicatorId, IndicatorParams } from '../charting/engines';
import type { LineStyleSpec } from '../charting/types';
import type { ChartType, ChartTypeParams, StrategyParams } from '../charting/engines';
import {
  saveWorkspace,
  loadWorkspace,
  DEFAULT_WORKSPACE,
  type WorkspaceState,
} from '../charting/workspace';
import { useTradeStore, type ChartTimeframe } from './useTradeStore';
import { readPreferences, savePreferences } from '../lib/preferences';

type CursorMode = 'cross' | 'dot' | 'arrow' | 'eraser';
// Exported so `lib/preferences.ts` can assert its validation allowlist covers
// the whole union — a private type there would make that assertion vacuous.
export type MagnetMode = 'off' | 'weak' | 'strong';
export type GhostLineMode = 'linear' | 'volume' | 'curved' | 'forecast';

// ── Theme persistence ──────────────────────────────────────────────────
// Shared with the inline boot script in `app/layout.tsx`, which reads the SAME
// key to set the `light` class on <html> before first paint. Keep the key and
// the accepted values in sync with that script.
export const THEME_STORAGE_KEY = 'stratai.theme';

/**
 * Read the persisted theme, defaulting to `dark`.
 *
 * Returns `dark` on the server so SSR output is deterministic; the browser
 * reads the real value when the store module is first evaluated client-side.
 */
export function readStoredTheme(): 'light' | 'dark' {
  if (typeof window === 'undefined') return 'dark';
  try {
    return localStorage.getItem(THEME_STORAGE_KEY) === 'light' ? 'light' : 'dark';
  } catch {
    return 'dark'; // private mode / storage disabled
  }
}

function writeStoredTheme(theme: 'light' | 'dark'): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* quota / private mode — the in-memory theme still applies for this session */
  }
}

// ── Split-Chart (Dual-Pane) Model ──────────────────────────────────────
//
// The split-view slice models the Angel-One-style dual-pane chart layout
// (Requirement 4). Each `ChartPaneState` is a fully independent chart — its
// own symbol, timeframe, and chart type — with no cross-pane synchronization
// in this phase (R4.3, R4.8). One pane is the `activePaneId`, the target the
// Instrument_Search and global controls route to (R4.4). `splitView` is off by
// default and is mode-gated: it can only be enabled in the INTRADAY or FNO
// workspace modes (R4.7), enforced at the store boundary in `setSplitView`.

export type PaneId = 'A' | 'B';

export interface ChartPaneState {
  /** Stable identifier for the pane (left = 'A', right = 'B'). */
  id: PaneId;
  /** The instrument this pane charts, independent of the other pane. */
  symbol: string;
  /** The pane's own timeframe selection. */
  timeframe: ChartTimeframe;
  /** The pane's own chart type. */
  chartType: ChartType;
}

/** Workspace modes in which the Split_Chart_View is available (R4.7). */
const SPLIT_ENABLED_PROFILES = ['INTRADAY', 'FNO'] as const;

/** Whether the currently active workspace profile permits split view (R4.7). */
function isSplitAllowed(): boolean {
  const profile = useTradeStore.getState().activeProfile;
  return (SPLIT_ENABLED_PROFILES as readonly string[]).includes(profile);
}

/** Seed a fresh pane. The symbol is left empty — the store-level
 *  `setSplitView(true)` initializer hydrates panes from the active
 *  `selectedSymbol` so the split view always opens with the symbol the user
 *  was just looking at, never a hard-coded placeholder. */
function defaultPane(id: PaneId): ChartPaneState {
  return { id, symbol: '', timeframe: '10m', chartType: 'candlestick' };
}

export type Point = { time: number; price: number };
export type Drawing = {
  id: string;
  tool: string;
  points: Point[];
  color?: string;
  text?: string;
  /** Per-drawing stroke width override (px). Falls back to the tool default. */
  lineWidth?: number;
  /** Per-drawing visibility. When true the drawing is hidden but retained
   *  (distinct from the global `drawingsVisible` flag). */
  hidden?: boolean;
  /** When true, the drawing is immutable and survives a "clear drawings" action. */
  locked?: boolean;
  /** The symbol the drawing belongs to, enabling per-symbol persistence. */
  symbol?: string;
};

// ── Active Indicator Model (Indicator Manager store slice) ─────────────
//
// An `ActiveIndicator` is one configured instance of a registered indicator
// applied to a symbol's chart. It carries a unique `instanceId`, the registry
// `indicatorId`, the per-instance `params`, a `style` override, a `visible`
// flag, and the `paneId` it renders into (null = price pane / overlay).
//
// The slice below keeps the active list per-symbol (`Record<symbol, list>`)
// and enforces the invariants required by Requirements 4.3–4.5, 4.7, 4.8, 4.11.

export type ActiveIndicator = {
  /** Unique id for this active instance (distinct from `indicatorId`). */
  instanceId: string;
  /** The registered indicator this instance is an instance of. */
  indicatorId: IndicatorId;
  /** Per-instance numeric parameters (seeded from the registry defaults). */
  params: IndicatorParams;
  /** Per-instance visual style override. */
  style: LineStyleSpec;
  /** Whether the indicator's output is currently rendered. */
  visible: boolean;
  /** The pane the indicator renders into; null = price pane (overlay). */
  paneId: string | null;
};

/** Maximum number of active indicators allowed per symbol (Requirement 4.5). */
export const MAX_INDICATORS_PER_SYMBOL = 50;

/** Default style applied to a newly added active indicator. */
export const DEFAULT_INDICATOR_STYLE: LineStyleSpec = {
  color: '#2962FF',
  lineWidth: 1,
  lineStyle: 'solid',
};

/** Reason an `addIndicator` call was rejected. */
export type AddIndicatorError = 'duplicate' | 'at-capacity' | 'unknown-indicator';

/** Result of an `addIndicator` call. */
export type AddIndicatorResult =
  | { ok: true; instanceId: string }
  | { ok: false; error: AddIndicatorError; message: string };

/** Result of a `setIndicatorParams` call. */
export type SetIndicatorParamsResult =
  | { ok: true }
  | { ok: false; errorParam: string; message: string };

// Monotonic counter backing unique instance ids. A counter (rather than a
// random source) keeps instance-id generation deterministic and collision-free
// even when many indicators are added in a tight loop.
let indicatorInstanceCounter = 0;
function nextInstanceId(indicatorId: IndicatorId): string {
  indicatorInstanceCounter += 1;
  return `${indicatorId}-${indicatorInstanceCounter}`;
}

/** Shallow structural equality for two indicator parameter bags. */
function sameParams(a: IndicatorParams, b: IndicatorParams): boolean {
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  return aKeys.every((k) => a[k] === b[k]);
}

// Workspace persistence (serialization, debounce, Tauri IPC bridge, and the
// in-memory fallback outside Tauri) lives in `charting/workspace.ts` so the
// serialization functions stay pure and property-testable. The store collects
// the live, persistable slice of state into a `WorkspaceState` and delegates.

/** Drawings created by system visualizations are transient and not persisted. */
function isPersistableDrawing(d: Drawing): boolean {
  return !d.id.startsWith('radar-') && !d.id.startsWith('realtime-pattern-');
}

interface ChartUIState {
  activeCursor: CursorMode;
  activeDrawingTool: string | null;
  magnetMode: MagnetMode;
  drawingsVisible: boolean;
  drawingsLocked: boolean;
  drawings: Drawing[];
  selectedDrawingId: string | null;
  /** The drawing currently under the pointer, used to render a hover state
   *  (Requirement 10.5). Null when the pointer is not over any drawing. */
  hoveredDrawingId: string | null;
  drawingColor: string;

  /** Whether the chart card is rendered as a viewport-filling overlay.
   *  Lifted to the store so peripheral components (e.g. the global
   *  drawing toolbar in TerminalLayout) can hide themselves to avoid
   *  duplicate DOM IDs and unreachable controls underneath the overlay. */
  isFullscreen: boolean;

  // ── Ghost Line Engine ─────────────────────────────────────────────
  /** Which projection engine to render:
   *  'linear' (OLS) · 'volume' (VWLR) · 'curved' (VWEPR) ·
   *  'forecast' (volatility-aware, regime-conditioned EWMA-drift forecaster). */
  ghostLineMode: GhostLineMode;
  /** The quadratic acceleration coefficient from the VWEPR fit.
   *  Positive = accelerating up, negative = accelerating down, ≈0 = linear. */
  accelerationCoefficient: number;

  // ── Chart Control-Bar State (lifted from ChartSurface) ────────────
  /** The active chart-type (candlestick, line, heikin-ashi, etc.). */
  chartType: ChartType;
  /** Numeric parameters for parametric chart types (renko, kagi, etc.). */
  chartTypeParams: ChartTypeParams;
  /** The currently applied strategy id, or null when none is applied. */
  activeStrategyId: string | null;
  /** Numeric parameters for the applied strategy. */
  strategyParams: StrategyParams;
  /** Whether the indicator-manager panel is visible. */
  showIndicatorManager: boolean;
  /** Whether the drawing Layers panel is visible. */
  showLayersPanel: boolean;

  // ── Split-Chart (Dual-Pane) State (Requirement 4) ──────────────────
  /** Whether the chart area is split into two independent panes (off by default). */
  splitView: boolean;
  /** The two independent chart panes; index 0 = 'A', index 1 = 'B'. */
  panes: [ChartPaneState, ChartPaneState];
  /** The pane that search/global controls target (the Active_Pane). */
  activePaneId: PaneId;
  sidebarOpen: boolean;
  setSidebarOpen: (open: boolean) => void;
  setActiveCursor: (cursor: CursorMode) => void;
  setActiveDrawingTool: (tool: string | null) => void;
  setMagnetMode: (mode: MagnetMode) => void;
  toggleDrawingsVisible: () => void;
  toggleDrawingsLocked: () => void;
  addDrawing: (drawing: Drawing) => void;
  updateDrawing: (id: string, updates: Partial<Drawing>) => void;
  updateDrawingPoints: (id: string, points: Point[]) => void;
  removeDrawing: (id: string) => void;
  setSelectedDrawing: (id: string | null) => void;
  /** Set the drawing under the pointer for hover highlighting (Req 10.5). */
  setHoveredDrawing: (id: string | null) => void;
  /** Lock or unlock a single drawing, making it immutable (Req 5.7). */
  toggleDrawingLock: (id: string) => void;
  /** Toggle a single drawing's visibility without deleting it. */
  toggleDrawingHidden: (id: string) => void;
  /** Set a single drawing's stroke width (px). */
  setDrawingLineWidth: (id: string, width: number) => void;
  /** Duplicate a drawing (slightly offset) and place the clone on top. */
  duplicateDrawing: (id: string) => void;
  /** Z-order controls (array order = paint order; last = front). */
  bringDrawingToFront: (id: string) => void;
  sendDrawingToBack: (id: string) => void;
  bringDrawingForward: (id: string) => void;
  sendDrawingBackward: (id: string) => void;
  clearDrawings: () => void;
  setDrawingColor: (color: string) => void;
  setGhostLineMode: (mode: GhostLineMode) => void;
  setAccelerationCoefficient: (value: number) => void;
  setIsFullscreen: (value: boolean) => void;
  toggleFullscreen: () => void;
  setChartType: (type: ChartType) => void;
  setChartTypeParams: (params: ChartTypeParams) => void;
  setActiveStrategyId: (id: string | null) => void;
  setStrategyParams: (params: StrategyParams) => void;
  setShowIndicatorManager: (value: boolean | ((prev: boolean) => boolean)) => void;
  toggleIndicatorManager: () => void;
  setShowLayersPanel: (value: boolean) => void;
  toggleLayersPanel: () => void;

  // ── Split-Chart (Dual-Pane) Actions (Requirement 4) ────────────────
  /** Enable/disable split view. Enabling is a no-op unless the active
   *  workspace profile is INTRADAY or FNO (mode-gating, R4.7). */
  setSplitView: (on: boolean) => void;
  /** Designate which pane is the Active_Pane (R4.4, R4.5). */
  setActivePane: (id: PaneId) => void;
  /** Set a single pane's symbol without affecting the other pane (R4.3, R4.8). */
  setPaneSymbol: (id: PaneId, symbol: string) => void;
  /** Set a single pane's timeframe without affecting the other pane (R4.3, R4.8). */
  setPaneTimeframe: (id: PaneId, tf: ChartTimeframe) => void;
  /** Set a single pane's chart type without affecting the other pane (R4.3, R4.8). */
  setPaneChartType: (id: PaneId, t: ChartType) => void;
  // ── Workspace Persistence ──────────────────────────────────────────
  loadWorkspaceFromDB: (symbol: string) => Promise<void>;
  saveWorkspaceToDB: (symbol: string) => Promise<void>;

  // ── Indicator Manager (per-symbol active indicators) ───────────────
  /** Active indicators keyed by symbol; unknown symbols are an empty list. */
  activeIndicators: Record<string, ActiveIndicator[]>;
  /** Read the active-indicator list for a symbol (empty for unknown symbols). */
  getActiveIndicators: (symbol: string) => ActiveIndicator[];
  /** Add an indicator instance; rejects duplicates and at-capacity adds. */
  addIndicator: (symbol: string, id: IndicatorId) => AddIndicatorResult;
  /** Remove an indicator instance by its instance id. */
  removeIndicator: (symbol: string, instanceId: string) => void;
  /** Update an instance's params after validation against its paramSpec. */
  setIndicatorParams: (
    symbol: string,
    instanceId: string,
    params: IndicatorParams,
  ) => SetIndicatorParamsResult;
  /** Update an instance's visual style (partial merge). */
  setIndicatorStyle: (
    symbol: string,
    instanceId: string,
    style: Partial<LineStyleSpec>,
  ) => void;
  /** Flip an instance's visibility without discarding its configuration. */
  toggleIndicatorVisible: (symbol: string, instanceId: string) => void;
  theme: 'light' | 'dark';
  setTheme: (theme: 'light' | 'dark') => void;
  toggleTheme: () => void;
}

/**
 * The user's saved chart selections, read once at module evaluation.
 *
 * Same reasoning as `useTradeStore`: restoring in an effect would paint one frame
 * of the defaults first, so the chart would mount as a single candlestick pane and
 * then rearrange itself. `{}` on the server keeps the prerender deterministic.
 *
 * `splitView` is already cross-checked against the restored `activeProfile` inside
 * `parsePreferences`, so it cannot come back `true` in a mode where `setSplitView`
 * would refuse to re-enable it.
 */
const savedChartPrefs = readPreferences();

export const useChartUIStore = create<ChartUIState>((set, get) => ({
  // Transient by design — the active tool, the current selection, and which
  // panels are open all start fresh. `isFullscreen` especially: `page.tsx` clears
  // it on unmount, and restoring it would reopen the app in an overlay the user
  // has to find their way out of.
  activeCursor: 'cross',
  activeDrawingTool: null,
  selectedDrawingId: null,
  hoveredDrawingId: null,
  accelerationCoefficient: 0.0,
  isFullscreen: false,
  showIndicatorManager: false,
  showLayersPanel: false,
  activeStrategyId: null,
  strategyParams: {},
  drawings: [],
  activeIndicators: {},

  // Restored selections. Assigned as initial values rather than replayed through
  // the setters: `setSplitView(true)` re-seeds pane symbols from the active
  // selection, which would overwrite the per-pane symbols being restored here.
  magnetMode: savedChartPrefs.magnetMode ?? 'off',
  drawingsVisible: savedChartPrefs.drawingsVisible ?? true,
  drawingsLocked: savedChartPrefs.drawingsLocked ?? false,
  drawingColor: savedChartPrefs.drawingColor ?? '#FF5722',
  ghostLineMode: savedChartPrefs.ghostLineMode ?? 'curved',
  chartType: savedChartPrefs.chartType ?? 'candlestick',
  chartTypeParams: savedChartPrefs.chartTypeParams ?? {},
  splitView: savedChartPrefs.splitView ?? false,
  panes: savedChartPrefs.panes ?? [defaultPane('A'), defaultPane('B')],
  activePaneId: savedChartPrefs.activePaneId ?? 'A',
  sidebarOpen: savedChartPrefs.sidebarOpen ?? true,
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  setActiveCursor: (cursor) => set({ activeCursor: cursor, activeDrawingTool: null }),
  setActiveDrawingTool: (tool) => set({ activeDrawingTool: tool, selectedDrawingId: null }),
  setMagnetMode: (mode) => set({ magnetMode: mode }),
  toggleDrawingsVisible: () => set((state) => ({ drawingsVisible: !state.drawingsVisible })),
  toggleDrawingsLocked: () => set((state) => ({ drawingsLocked: !state.drawingsLocked })),
  addDrawing: (drawing) =>
    set((state) => ({ drawings: [...state.drawings, drawing] })),
  updateDrawing: (id, updates) => set((state) => ({
    drawings: state.drawings.map((d) => (d.id === id ? { ...d, ...updates } : d))
  })),
  updateDrawingPoints: (id, points) =>
    set((state) => ({
      // Locked drawings are immutable: reject geometry edits (Requirement 5.7).
      drawings: state.drawings.map((d) =>
        d.id === id && !d.locked ? { ...d, points } : d,
      ),
    })),
  removeDrawing: (id) =>
    set((state) => {
      // Locked drawings cannot be deleted (Requirement 5.7); leave state intact.
      const target = state.drawings.find((d) => d.id === id);
      if (target?.locked) return state;
      return {
        drawings: state.drawings.filter((d) => d.id !== id),
        selectedDrawingId: state.selectedDrawingId === id ? null : state.selectedDrawingId,
        hoveredDrawingId: state.hoveredDrawingId === id ? null : state.hoveredDrawingId,
      };
    }),
  setSelectedDrawing: (id) => set({ selectedDrawingId: id }),
  setHoveredDrawing: (id) =>
    set((state) => (state.hoveredDrawingId === id ? state : { hoveredDrawingId: id })),
  toggleDrawingLock: (id) =>
    set((state) => ({
      drawings: state.drawings.map((d) =>
        d.id === id ? { ...d, locked: !d.locked } : d,
      ),
    })),
  toggleDrawingHidden: (id) =>
    set((state) => ({
      drawings: state.drawings.map((d) =>
        d.id === id ? { ...d, hidden: !d.hidden } : d,
      ),
    })),
  setDrawingLineWidth: (id, width) =>
    set((state) => {
      const w = Math.max(1, Math.min(4, Math.round(width)));
      return {
        drawings: state.drawings.map((d) =>
          d.id === id ? { ...d, lineWidth: w } : d,
        ),
      };
    }),
  // Clone a drawing with a small time/price nudge and place the copy on top
  // (end of the array = front). The clone is selected so it can be moved.
  duplicateDrawing: (id) =>
    set((state) => {
      const src = state.drawings.find((d) => d.id === id);
      if (!src) return state;
      const newId = `draw-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
      const priceNudge =
        src.points.length > 0 ? Math.abs(src.points[0].price) * 0.004 || 0.5 : 0;
      const clone: Drawing = {
        ...src,
        id: newId,
        locked: false,
        points: src.points.map((p) => ({ time: p.time, price: +(p.price - priceNudge).toFixed(2) })),
      };
      return { drawings: [...state.drawings, clone], selectedDrawingId: newId };
    }),
  // Z-order: the array order is the paint order, so the last entry renders on
  // top. "Front" = move to the end; "back" = move to the start.
  bringDrawingToFront: (id) =>
    set((state) => {
      const idx = state.drawings.findIndex((d) => d.id === id);
      if (idx === -1 || idx === state.drawings.length - 1) return state;
      const next = [...state.drawings];
      const [d] = next.splice(idx, 1);
      next.push(d);
      return { drawings: next };
    }),
  sendDrawingToBack: (id) =>
    set((state) => {
      const idx = state.drawings.findIndex((d) => d.id === id);
      if (idx <= 0) return state;
      const next = [...state.drawings];
      const [d] = next.splice(idx, 1);
      next.unshift(d);
      return { drawings: next };
    }),
  bringDrawingForward: (id) =>
    set((state) => {
      const idx = state.drawings.findIndex((d) => d.id === id);
      if (idx === -1 || idx === state.drawings.length - 1) return state;
      const next = [...state.drawings];
      [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
      return { drawings: next };
    }),
  sendDrawingBackward: (id) =>
    set((state) => {
      const idx = state.drawings.findIndex((d) => d.id === id);
      if (idx <= 0) return state;
      const next = [...state.drawings];
      [next[idx], next[idx - 1]] = [next[idx - 1], next[idx]];
      return { drawings: next };
    }),
  // Clear removes only unlocked drawings, retaining locked ones (Requirement 5.9).
  clearDrawings: () =>
    set((state) => {
      const kept = clearUnlocked(state.drawings);
      const keptIds = new Set(kept.map((d) => d.id));
      return {
        drawings: kept,
        selectedDrawingId:
          state.selectedDrawingId && keptIds.has(state.selectedDrawingId)
            ? state.selectedDrawingId
            : null,
        hoveredDrawingId:
          state.hoveredDrawingId && keptIds.has(state.hoveredDrawingId)
            ? state.hoveredDrawingId
            : null,
      };
    }),
  setDrawingColor: (color) => set({ drawingColor: color }),
  setGhostLineMode: (mode) => set({ ghostLineMode: mode }),
  setAccelerationCoefficient: (value) => set({ accelerationCoefficient: value }),
  setIsFullscreen: (value) => set({ isFullscreen: value }),
  toggleFullscreen: () => set((s) => ({ isFullscreen: !s.isFullscreen })),
  setChartType: (type) => set({ chartType: type }),
  setChartTypeParams: (params) => set({ chartTypeParams: params }),
  setActiveStrategyId: (id) => set({ activeStrategyId: id, strategyParams: {} }),
  setStrategyParams: (params) => set({ strategyParams: params }),
  setShowIndicatorManager: (value) =>
    set((s) => ({
      showIndicatorManager:
        typeof value === 'function' ? value(s.showIndicatorManager) : value,
    })),
  toggleIndicatorManager: () =>
    set((s) => ({ showIndicatorManager: !s.showIndicatorManager })),
  setShowLayersPanel: (value) => set({ showLayersPanel: value }),
  toggleLayersPanel: () => set((s) => ({ showLayersPanel: !s.showLayersPanel })),

  // ── Split-Chart (Dual-Pane) Actions ────────────────────────────────

  /**
   * Toggle the dual-pane split view. Enabling is mode-gated: when the active
   * workspace profile is not INTRADAY or FNO, `setSplitView(true)` is a no-op
   * and the view stays single (Requirement 4.7). Disabling is always allowed
   * (returning to single view is valid in any mode).
   *
   * On enable, panes are seeded from the currently active symbol so the split
   * view opens showing the same instrument the user was just viewing, rather
   * than a hard-coded placeholder. The active pane keeps the symbol verbatim;
   * the sibling pane also starts with it (the user can then pick a different
   * symbol per pane via search / watchlist routing).
   */
  setSplitView: (on) =>
    set((state) => {
      if (on && !isSplitAllowed()) return state;
      if (!on) return { splitView: on };

      // Enabling: seed panes from the active selection so split view never
      // falls back to a placeholder symbol.
      const activeSymbol = useTradeStore.getState().selectedSymbol || '';
      const seed = activeSymbol || state.panes[0]?.symbol || state.panes[1]?.symbol || '';
      return {
        splitView: on,
        panes: [
          { ...state.panes[0], symbol: state.panes[0].symbol || seed },
          { ...state.panes[1], symbol: state.panes[1].symbol || seed },
        ] as [ChartPaneState, ChartPaneState],
      };
    }),

  /** Designate the Active_Pane that search/global controls target (R4.4). */
  setActivePane: (id) => set({ activePaneId: id }),

  /** Update one pane's symbol, leaving the sibling pane untouched (R4.3, R4.8). */
  setPaneSymbol: (id, symbol) =>
    set((state) => ({
      panes: state.panes.map((p) => (p.id === id ? { ...p, symbol } : p)) as [
        ChartPaneState,
        ChartPaneState,
      ],
    })),

  /** Update one pane's timeframe, leaving the sibling pane untouched (R4.3, R4.8). */
  setPaneTimeframe: (id, tf) =>
    set((state) => ({
      panes: state.panes.map((p) => (p.id === id ? { ...p, timeframe: tf } : p)) as [
        ChartPaneState,
        ChartPaneState,
      ],
    })),

  /** Update one pane's chart type, leaving the sibling pane untouched (R4.3, R4.8). */
  setPaneChartType: (id, t) =>
    set((state) => ({
      panes: state.panes.map((p) => (p.id === id ? { ...p, chartType: t } : p)) as [
        ChartPaneState,
        ChartPaneState,
      ],
    })),

  // ── Workspace Persistence Actions ──────────────────────────────────

  /**
   * Load a symbol's persisted workspace and hydrate the live store from it.
   * Restores the per-symbol drawings and active indicators. Outside Tauri this
   * resolves from the in-memory session store; a missing or malformed blob
   * yields {@link DEFAULT_WORKSPACE} (Requirements 1.4, 11.3, 11.4).
   */
  loadWorkspaceFromDB: async (symbol: string) => {
    try {
      const ws: WorkspaceState = await loadWorkspace(symbol);
      const drawings = Array.isArray(ws.drawings) ? ws.drawings : [];
      set((state) => ({
        drawings,
        selectedDrawingId: null,
        hoveredDrawingId: null,
        activeIndicators: {
          ...state.activeIndicators,
          [symbol]: Array.isArray(ws.activeIndicators) ? ws.activeIndicators : [],
        },
      }));
    } catch (err) {
      // Restore failure: keep on-screen state, fall back to defaults silently.
      console.warn(`[Workspace] Failed to load workspace for ${symbol}:`, err);
      set((state) => ({
        activeIndicators: {
          ...state.activeIndicators,
          [symbol]: DEFAULT_WORKSPACE.activeIndicators,
        },
      }));
    }
  },

  /**
   * Persist the current workspace for a symbol via the debounced writer. The
   * full {@link WorkspaceState} (chart type + params, active indicators,
   * persistable drawings, pane layout) is collected and handed off; transient
   * system drawings are filtered out. Outside Tauri the state is retained in
   * the in-memory session store (Requirements 4.9, 4.10, 5.11, 11.6).
   */
  saveWorkspaceToDB: async (symbol: string) => {
    const { drawings, activeIndicators } = get();
    const ws: WorkspaceState = {
      ...DEFAULT_WORKSPACE,
      drawings: drawings.filter(isPersistableDrawing),
      activeIndicators: activeIndicators[symbol] ?? [],
    };
    saveWorkspace(symbol, ws);
  },

  // ── Indicator Manager Actions ──────────────────────────────────────

  /**
   * Read the active-indicator list for a symbol. Unknown symbols resolve to an
   * empty list rather than `undefined` (Requirement 4.11), so callers can treat
   * the result as an array unconditionally.
   */
  getActiveIndicators: (symbol) => get().activeIndicators[symbol] ?? [],

  /**
   * Add an indicator instance to a symbol's active list using the registry
   * defaults for params. Enforces:
   *  - unknown-indicator rejection (id not in the registry)
   *  - duplicate rejection: an instance with the same indicatorId AND identical
   *    params already exists for the symbol (Requirement 4.4)
   *  - 50-entry cap per symbol (Requirement 4.5)
   * Rejected adds leave the existing list unchanged. Unknown symbols start from
   * an empty list (Requirement 4.11).
   */
  addIndicator: (symbol, id) => {
    const def = getIndicator(id);
    if (!def) {
      return { ok: false, error: 'unknown-indicator', message: `Unknown indicator: ${id}` };
    }

    const list = get().activeIndicators[symbol] ?? [];
    const params: IndicatorParams = { ...def.defaults };

    const isDuplicate = list.some(
      (ind) => ind.indicatorId === id && sameParams(ind.params, params),
    );
    if (isDuplicate) {
      return { ok: false, error: 'duplicate', message: `${def.name} is already active` };
    }

    if (list.length >= MAX_INDICATORS_PER_SYMBOL) {
      return {
        ok: false,
        error: 'at-capacity',
        message: `Maximum of ${MAX_INDICATORS_PER_SYMBOL} indicators reached`,
      };
    }

    const instance: ActiveIndicator = {
      instanceId: nextInstanceId(id),
      indicatorId: id,
      params,
      style: { ...DEFAULT_INDICATOR_STYLE },
      visible: true,
      paneId: null,
    };

    set((state) => ({
      activeIndicators: {
        ...state.activeIndicators,
        [symbol]: [...(state.activeIndicators[symbol] ?? []), instance],
      },
    }));

    return { ok: true, instanceId: instance.instanceId };
  },

  /** Remove an indicator instance from a symbol's active list (Requirement 4.8). */
  removeIndicator: (symbol, instanceId) =>
    set((state) => {
      const list = state.activeIndicators[symbol];
      if (!list) return {};
      return {
        activeIndicators: {
          ...state.activeIndicators,
          [symbol]: list.filter((ind) => ind.instanceId !== instanceId),
        },
      };
    }),

  /**
   * Update an instance's params after validating them against the indicator's
   * `paramSpec`. Invalid values are rejected and the existing params are
   * retained unchanged (Requirement 4 / validation), returning the offending
   * parameter name.
   */
  setIndicatorParams: (symbol, instanceId, params) => {
    const list = get().activeIndicators[symbol] ?? [];
    const target = list.find((ind) => ind.instanceId === instanceId);
    if (!target) {
      return { ok: false, errorParam: '', message: 'Indicator instance not found' };
    }

    const def = getIndicator(target.indicatorId);
    if (!def) {
      return { ok: false, errorParam: '', message: 'Unknown indicator' };
    }

    const result = validateParams(params, def.paramSpec);
    if (!result.ok) {
      return { ok: false, errorParam: result.errorParam, message: result.message };
    }

    set((state) => ({
      activeIndicators: {
        ...state.activeIndicators,
        [symbol]: (state.activeIndicators[symbol] ?? []).map((ind) =>
          ind.instanceId === instanceId ? { ...ind, params: result.value } : ind,
        ),
      },
    }));

    return { ok: true };
  },

  /** Update an instance's visual style with a partial merge (Requirement 4.8). */
  setIndicatorStyle: (symbol, instanceId, style) =>
    set((state) => {
      const list = state.activeIndicators[symbol];
      if (!list) return {};
      return {
        activeIndicators: {
          ...state.activeIndicators,
          [symbol]: list.map((ind) =>
            ind.instanceId === instanceId ? { ...ind, style: { ...ind.style, ...style } } : ind,
          ),
        },
      };
    }),

  /**
   * Flip an instance's `visible` flag while preserving every other field
   * (params, style, paneId), so toggling visibility never discards the
   * indicator's configuration (Requirement 4.7).
   */
  toggleIndicatorVisible: (symbol, instanceId) =>
    set((state) => {
      const list = state.activeIndicators[symbol];
      if (!list) return {};
      return {
        activeIndicators: {
          ...state.activeIndicators,
          [symbol]: list.map((ind) =>
            ind.instanceId === instanceId ? { ...ind, visible: !ind.visible } : ind,
          ),
        },
      };
    }),

  theme: readStoredTheme(),
  setTheme: (theme) => {
    set({ theme });
    if (typeof document !== 'undefined') {
      if (theme === 'light') {
        document.documentElement.classList.add('light');
      } else {
        document.documentElement.classList.remove('light');
      }
    }
    // Persist so a reload keeps the user's choice. Previously `theme` was
    // in-memory only, so every refresh snapped back to dark — and because the
    // TradingView widget restores its OWN saved chart properties from
    // localStorage, the shell went dark while the candles stayed light. The
    // inline boot script in `app/layout.tsx` reads this same key before first
    // paint so there is no flash.
    writeStoredTheme(theme);
  },
  toggleTheme: () => {
    const nextTheme = get().theme === 'dark' ? 'light' : 'dark';
    get().setTheme(nextTheme);
  },
}));

// ── Selection persistence ─────────────────────────────────────────────────
//
// The chart-selection half of the shared preferences blob. `savePreferences`
// merges, so this and `useTradeStore`'s subscription cannot erase each other.
//
// `panes` is compared field-by-field rather than by reference because every
// per-pane setter rebuilds the array, so a reference check would schedule a write
// on unrelated state changes.
useChartUIStore.subscribe((state, prev) => {
  const samePanes =
    state.panes[0].symbol === prev.panes[0].symbol &&
    state.panes[0].timeframe === prev.panes[0].timeframe &&
    state.panes[0].chartType === prev.panes[0].chartType &&
    state.panes[1].symbol === prev.panes[1].symbol &&
    state.panes[1].timeframe === prev.panes[1].timeframe &&
    state.panes[1].chartType === prev.panes[1].chartType;
  if (
    samePanes &&
    state.chartType === prev.chartType &&
    state.chartTypeParams === prev.chartTypeParams &&
    state.ghostLineMode === prev.ghostLineMode &&
    state.splitView === prev.splitView &&
    state.activePaneId === prev.activePaneId &&
    state.sidebarOpen === prev.sidebarOpen &&
    state.drawingColor === prev.drawingColor &&
    state.magnetMode === prev.magnetMode &&
    state.drawingsVisible === prev.drawingsVisible &&
    state.drawingsLocked === prev.drawingsLocked
  ) {
    return;
  }
  savePreferences({
    chartType: state.chartType,
    chartTypeParams: state.chartTypeParams,
    ghostLineMode: state.ghostLineMode,
    splitView: state.splitView,
    panes: state.panes,
    activePaneId: state.activePaneId,
    sidebarOpen: state.sidebarOpen,
    drawingColor: state.drawingColor,
    magnetMode: state.magnetMode,
    drawingsVisible: state.drawingsVisible,
    drawingsLocked: state.drawingsLocked,
  });
});
