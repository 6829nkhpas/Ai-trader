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

type CursorMode = 'cross' | 'dot' | 'arrow' | 'eraser';
type MagnetMode = 'off' | 'weak' | 'strong';
export type GhostLineMode = 'linear' | 'curved';

/** The OHLC of the candle currently under the crosshair, or null when the
 *  pointer is off the chart / over empty space. Raw numeric values (unformatted)
 *  so any consumer (e.g. the header readout) can format to its own precision. */
export type HoverOhlc = {
  open: number;
  high: number;
  low: number;
  close: number;
  time: number;
} | null;

export type Point = { time: number; price: number };
export type Drawing = {
  id: string;
  tool: string;
  points: Point[];
  color?: string;
  text?: string;
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

  // ── Ghost Line Dual-Engine ────────────────────────────────────────
  /** Which regression engine to render: 'linear' (OLS) or 'curved' (VWEPR). */
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
  /** OHLC of the candle under the crosshair (null when not hovering a candle).
   *  Written by the crosshair controller, read by the header OHLC readout so it
   *  updates live as the user hovers candles (Requirement 10.1). */
  hoverOhlc: HoverOhlc;

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
  /** Publish (or clear) the crosshair-hovered candle's OHLC. */
  setHoverOhlc: (value: HoverOhlc) => void;

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
}

export const useChartUIStore = create<ChartUIState>((set, get) => ({
  activeCursor: 'cross',
  activeDrawingTool: null,
  magnetMode: 'off',
  drawingsVisible: true,
  drawingsLocked: false,
  drawings: [],
  selectedDrawingId: null,
  hoveredDrawingId: null,
  drawingColor: '#FF5722',
  ghostLineMode: 'curved',
  accelerationCoefficient: 0.0,
  isFullscreen: false,
  chartType: 'candlestick',
  chartTypeParams: {},
  activeStrategyId: null,
  strategyParams: {},
  showIndicatorManager: false,
  hoverOhlc: null,
  activeIndicators: {},
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
  // Equality-gated so repeated crosshair moves over the same candle (or over
  // empty space) keep the same `hoverOhlc` reference and never re-render the
  // header. Returning `{}` is a no-op merge that leaves the slice identity intact.
  setHoverOhlc: (value) =>
    set((s) => {
      const prev = s.hoverOhlc;
      if (value === null) return prev === null ? {} : { hoverOhlc: null };
      if (
        prev &&
        prev.time === value.time &&
        prev.open === value.open &&
        prev.high === value.high &&
        prev.low === value.low &&
        prev.close === value.close
      ) {
        return {};
      }
      return { hoverOhlc: value };
    }),

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
}));
