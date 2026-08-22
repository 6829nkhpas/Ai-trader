// Feature: professional-charting-suite
//
// WorkspaceStore — workspace serialization, defaults, and persistence.
//
// The workspace payload captures everything needed to restore a symbol's chart
// across sessions: the active chart type and its parameters, the active
// indicators, the drawings (including locked ones), and the oscillator pane
// layout. Serialization is a pair of PURE functions so they can be round-trip
// property-tested (Property 33) and default-tested (Property 34) without any
// I/O. The persistence side (debounced save / restore through the transport
// bridge — SQLite on desktop, `localStorage` on the web — with an in-memory
// fallback when the backend rejects a write) is layered on top of those pure
// functions (Requirements 11.1, 11.2, 11.3, 11.6).

import { CHART_TYPES, type ChartType, type ChartTypeParams } from './engines';
import type { PaneLayout } from './paneManager';
import { bridgeInvoke } from '../lib/bridge';
// Type-only imports keep this module free of a runtime cycle with the store
// (the store imports the runtime persistence helpers from here).
import type { ActiveIndicator, Drawing } from '../store/useChartUIStore';

/**
 * The complete, persisted workspace for a single symbol. `version` is a literal
 * `1` so future migrations can detect and upgrade older blobs. All fields are
 * plain JSON-serializable data (Requirements 1.3, 4.9, 4.10, 5.11, 11.1, 11.2).
 */
export interface WorkspaceState {
  /** Schema version of the persisted blob. */
  version: 1;
  /** The active chart type (Requirement 1.3). */
  chartType: ChartType;
  /** Parameters for the active chart type (Requirement 1.3). */
  chartTypeParams: ChartTypeParams;
  /** The active indicators for this symbol (Requirements 4.9, 4.10). */
  activeIndicators: ActiveIndicator[];
  /** The drawings for this symbol, including locked ones (Requirement 5.11). */
  drawings: Drawing[];
  /** The oscillator pane layout, top→bottom. */
  paneLayout: PaneLayout[];
}

/**
 * The blank-canvas workspace applied when nothing is persisted or a blob is
 * malformed: candlestick chart, no parameters, no indicators, no drawings, no
 * extra panes (Requirements 1.4, 4.11, 11.3).
 */
export const DEFAULT_WORKSPACE: WorkspaceState = {
  version: 1,
  chartType: 'candlestick',
  chartTypeParams: {},
  activeIndicators: [],
  drawings: [],
  paneLayout: [],
};

// ── Pure serialization ────────────────────────────────────────────────

/**
 * Serialize a workspace to a JSON string. Pure; the inverse of
 * {@link deserializeWorkspace} for any valid {@link WorkspaceState}
 * (Property 33 round-trip).
 */
export function serializeWorkspace(state: WorkspaceState): string {
  return JSON.stringify(state);
}

/** Return a fresh, independent copy of {@link DEFAULT_WORKSPACE}. */
function freshDefault(): WorkspaceState {
  return {
    version: 1,
    chartType: 'candlestick',
    chartTypeParams: {},
    activeIndicators: [],
    drawings: [],
    paneLayout: [],
  };
}

/** True when `value` is a non-null, non-array plain object. */
function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Coerce an unknown chart-type field to a known {@link ChartType}. */
function readChartType(value: unknown): ChartType {
  return typeof value === 'string' && (CHART_TYPES as readonly string[]).includes(value)
    ? (value as ChartType)
    : 'candlestick';
}

/** Keep only finite-number entries from an unknown chart-type-params field. */
function readChartTypeParams(value: unknown): ChartTypeParams {
  if (!isObject(value)) return {};
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(value)) {
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = v;
  }
  return out as ChartTypeParams;
}

/**
 * Parse a persisted blob back into a {@link WorkspaceState}. PURE and total: any
 * malformed, absent, or wrong-shaped input yields a fresh {@link DEFAULT_WORKSPACE}
 * rather than throwing (Requirements 1.4, 11.3; Property 34). Well-formed blobs
 * round-trip exactly (Property 33). Per-field validation drops only the
 * offending field, falling back to its default while preserving the rest.
 */
export function deserializeWorkspace(raw: unknown): WorkspaceState {
  if (typeof raw !== 'string' || raw.length === 0) return freshDefault();

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return freshDefault();
  }

  if (!isObject(parsed)) return freshDefault();
  // A version mismatch means the blob predates this schema: start clean.
  if (parsed.version !== 1) return freshDefault();

  return {
    version: 1,
    chartType: readChartType(parsed.chartType),
    chartTypeParams: readChartTypeParams(parsed.chartTypeParams),
    activeIndicators: Array.isArray(parsed.activeIndicators)
      ? (parsed.activeIndicators as ActiveIndicator[])
      : [],
    drawings: Array.isArray(parsed.drawings) ? (parsed.drawings as Drawing[]) : [],
    paneLayout: Array.isArray(parsed.paneLayout) ? (parsed.paneLayout as PaneLayout[]) : [],
  };
}

// ── Persistence backend ───────────────────────────────────────────────
// Routed through `lib/bridge`, which resolves to the Tauri `save_workspace` /
// `load_workspace` commands (SQLite) on desktop and to `localStorage` in a
// browser. A browser therefore now KEEPS its workspace across reloads; the
// in-memory store below remains the fallback for when the backend rejects a
// write — quota exceeded, private mode, storage disabled (Requirement 11.6).

/** In-memory, per-symbol fallback used when the persistence backend fails. */
const memoryStore = new Map<string, WorkspaceState>();

/**
 * True when a loaded blob carries no stored workspace.
 *
 * `db::load_workspace` maps `QueryReturnedNoRows` to `Ok("{}")` and the browser
 * adapter mirrors that, so `"{}"` is a MISS, not a stored empty workspace. It
 * must not clobber state this session already holds in memory.
 */
function isEmptyBlob(raw: unknown): boolean {
  return typeof raw !== 'string' || raw.trim().length === 0 || raw.trim() === '{}';
}

/** Default debounce window for persistence writes (Requirement 11.6). */
export const SAVE_DEBOUNCE_MS = 500;

// Pending debounce timers keyed by symbol, plus the latest state to flush.
const pendingTimers = new Map<string, ReturnType<typeof setTimeout>>();
const pendingState = new Map<string, WorkspaceState>();

/**
 * Persist a workspace immediately (no debounce). Writes through the persistence
 * backend (SQLite on desktop, `localStorage` on the web); the in-memory copy is
 * always updated first so a failed backend still retains the latest state for
 * the session (Requirements 11.4, 11.6).
 *
 * @returns `true` when the backend accepted the write, `false` when the write
 *          failed and only the in-memory copy holds it (caller may retry).
 */
export async function flushWorkspace(
  symbol: string,
  state: WorkspaceState,
): Promise<boolean> {
  memoryStore.set(symbol, state);
  try {
    await bridgeInvoke('save_workspace', { symbol, stateJson: serializeWorkspace(state) });
    return true;
  } catch {
    // Persist failure: in-memory state is retained; caller retries next change.
    return false;
  }
}

/**
 * Schedule a debounced persistence write for a symbol. Rapid successive calls
 * collapse into a single write after {@link SAVE_DEBOUNCE_MS} of quiet
 * (Requirement 11.6). The latest state always wins. The in-memory store is
 * updated synchronously so reads between debounce windows stay consistent.
 */
export function saveWorkspace(symbol: string, state: WorkspaceState): void {
  memoryStore.set(symbol, state);
  pendingState.set(symbol, state);

  const existing = pendingTimers.get(symbol);
  if (existing) clearTimeout(existing);

  const timer = setTimeout(() => {
    pendingTimers.delete(symbol);
    const latest = pendingState.get(symbol);
    pendingState.delete(symbol);
    if (latest) void flushWorkspace(symbol, latest);
  }, SAVE_DEBOUNCE_MS);

  pendingTimers.set(symbol, timer);
}

/**
 * Restore a symbol's workspace from the persistence backend, falling back to
 * the in-memory session copy when the backend has no row for this symbol or the
 * read fails. Any malformed blob resolves to {@link DEFAULT_WORKSPACE}
 * (Requirements 1.4, 11.3, 11.4).
 */
export async function loadWorkspace(symbol: string): Promise<WorkspaceState> {
  try {
    const raw = await bridgeInvoke('load_workspace', { symbol });
    if (isEmptyBlob(raw)) {
      // Backend miss — prefer state this session already holds over defaults,
      // so drawings whose save failed are not silently discarded on reload.
      return memoryStore.get(symbol) ?? freshDefault();
    }
    const state = deserializeWorkspace(raw);
    memoryStore.set(symbol, state);
    return state;
  } catch {
    // Restore failure: retained session state, else defaults (Requirement 11.4).
    return memoryStore.get(symbol) ?? freshDefault();
  }
}

/**
 * Cancel any pending debounced write for a symbol (or all symbols when omitted)
 * and forget the in-memory copy. Primarily for test isolation.
 */
export function resetWorkspacePersistence(symbol?: string): void {
  if (symbol === undefined) {
    for (const timer of pendingTimers.values()) clearTimeout(timer);
    pendingTimers.clear();
    pendingState.clear();
    memoryStore.clear();
    return;
  }
  const timer = pendingTimers.get(symbol);
  if (timer) clearTimeout(timer);
  pendingTimers.delete(symbol);
  pendingState.delete(symbol);
  memoryStore.delete(symbol);
}
