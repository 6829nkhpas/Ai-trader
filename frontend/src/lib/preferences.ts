// lib/preferences.ts — durable storage for the user's terminal selections.
//
// Everything the user PICKS should still be picked after a refresh: the
// workspace mode, the instrument, the timeframe, the data range, the chart type,
// the F&O underlying/expiry, the split layout and which pane is active, the
// sidebar, the drawing defaults. Before this, exactly one of those (the theme)
// survived a reload; every other selection was in-memory and snapped back to a
// hard-coded default — `INTRADAY`, `RELIANCE`, `10m`, `candlestick`, single view
// — so reopening the tab silently discarded the user's whole setup.
//
// Design notes:
//
// · ONE storage key holding ONE blob, written through `savePreferences`, which
//   merges patches. Two stores contribute slices of it (`useTradeStore` and
//   `useChartUIStore`); if each owned its own key they could not be validated
//   against one another, and the split-view rule below needs exactly that.
//
// · Reads are TOTAL. A malformed, truncated, hand-edited, or version-mismatched
//   blob yields `{}` and the stores keep their defaults. Nothing here throws, and
//   nothing here trusts a stored value: every field is validated against the same
//   allowlist the UI offers, because localStorage is user-writable and a bad enum
//   restored into `activeProfile` would break the workspace switch outright.
//
// · The allowlists carry compile-time exhaustiveness assertions, so widening one
//   of the underlying unions without updating the validator is a type error rather
//   than a value that silently fails to restore.
//
// · SSR-safe: `readPreferences()` returns `{}` on the server so the prerender is
//   deterministic, mirroring `readStoredTheme`. `app/page.tsx` gates the whole
//   terminal behind a `mounted` flag, so restored values are never part of the
//   server-rendered output and cannot cause a hydration mismatch.
//
// NOT persisted, deliberately: transient UI (active drawing tool, cursor,
// selection/hover, open panels, fullscreen — `page.tsx` explicitly clears
// fullscreen on unmount and trapping a user in it across reloads would be a bug),
// derived values, all market data, and anything from `useFeatureStore`, whose
// entitlement flags must stay fail-closed and server-derived.
//
// Also not here: per-symbol drawings and indicators. Those have their own
// versioned, per-symbol mechanism in `charting/workspace.ts`.

import type { ChartMode, ChartTimeframe, TradeProfile } from '../store/useTradeStore';
import type {
  ChartPaneState,
  GhostLineMode,
  MagnetMode,
  PaneId,
} from '../store/useChartUIStore';
import type { ChartType, ChartTypeParams } from '../charting/engines';
import { CHART_TYPES } from '../charting/engines';
import { RANGE_DAYS, type DataRange } from '../utils/chartTypes';

export const PREFERENCES_STORAGE_KEY = 'stratai.preferences';

/** Bump when a field's MEANING changes; a mismatch is treated as no preferences. */
export const PREFERENCES_VERSION = 1;



/** Every user selection that survives a reload. */
export interface TerminalPreferences {
  version: number;
  // ── useTradeStore ────────────────────────────────────────────────────────
  activeProfile: TradeProfile;
  selectedSymbol: string;
  activeTimeframe: ChartTimeframe;
  activeRange: DataRange;
  chartMode: ChartMode;
  fnoUnderlying: string;
  fnoExpiry: string;
  /**
   * The instrument the user last charted IN EACH MODE, so Investor can sit on
   * TCS while Swing sits on INFY and Intraday on RELIANCE — across reloads.
   *
   * Partial on purpose: a missing key means "this mode has no choice yet", which
   * `setActiveProfile` treats differently from a stored one (it carries the
   * current symbol over instead of restoring). Defaulting the absent keys to a
   * literal here would erase that distinction and make a first visit to Swing
   * look like a deliberate pick of `RELIANCE`.
   */
  symbolByProfile: Partial<Record<TradeProfile, string>>;
  // ── useChartUIStore ──────────────────────────────────────────────────────
  chartType: ChartType;
  chartTypeParams: ChartTypeParams;
  ghostLineMode: GhostLineMode;
  splitView: boolean;
  panes: [ChartPaneState, ChartPaneState];
  activePaneId: PaneId;
  sidebarOpen: boolean;
  drawingColor: string;
  magnetMode: MagnetMode;
  drawingsVisible: boolean;
  drawingsLocked: boolean;
}

// ── Allowlists ──────────────────────────────────────────────────────────────
//
// Each is paired with an assertion that it covers its whole union. Adding a
// timeframe or a profile without listing it here fails `tsc` instead of quietly
// refusing to restore.

/** Asserts `Listed` covers every member of `Union`. */
type CoversUnion<Union, Listed> = [Exclude<Union, Listed>] extends [never] ? true : never;

const PROFILES = ['INTRADAY', 'SWING', 'INVESTOR', 'FNO'] as const;
const _profilesCoverUnion: CoversUnion<TradeProfile, (typeof PROFILES)[number]> = true;

const TIMEFRAMES = [
  '1m', '2m', '3m', '4m', '5m',
  '10m', '15m', '30m', '75m', '125m',
  '1h', '1H', '2h', '3h', '4h',
  '1D', '1W', '1M',
] as const;
const _timeframesCoverUnion: CoversUnion<ChartTimeframe, (typeof TIMEFRAMES)[number]> = true;

const GHOST_LINE_MODES = ['linear', 'volume', 'curved', 'forecast'] as const;
const _ghostModesCoverUnion: CoversUnion<GhostLineMode, (typeof GHOST_LINE_MODES)[number]> = true;

const CHART_MODES = ['STANDARD', 'VOLUME_PROFILE', 'FOOTPRINT'] as const;
const _chartModesCoverUnion: CoversUnion<ChartMode, (typeof CHART_MODES)[number]> = true;

const MAGNET_MODES = ['off', 'weak', 'strong'] as const;
const _magnetModesCoverUnion: CoversUnion<MagnetMode, (typeof MAGNET_MODES)[number]> = true;

const PANE_IDS = ['A', 'B'] as const;
const _paneIdsCoverUnion: CoversUnion<PaneId, (typeof PANE_IDS)[number]> = true;

// `RANGE_DAYS` is keyed by DataRange, so its keys ARE the allowlist — no second
// list to drift.
const DATA_RANGES = Object.keys(RANGE_DAYS) as DataRange[];

/** The workspace modes in which split view is permitted (mirrors `setSplitView`). */
const SPLIT_ENABLED_PROFILES: readonly TradeProfile[] = ['INTRADAY', 'FNO'];

// Reference the assertions so `noUnusedLocals` cannot strip them.
void _profilesCoverUnion;
void _timeframesCoverUnion;
void _ghostModesCoverUnion;
void _chartModesCoverUnion;
void _magnetModesCoverUnion;
void _paneIdsCoverUnion;

// ── Field validators ────────────────────────────────────────────────────────

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** `value` when it is one of `allowed`, else undefined (field is skipped). */
function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T | undefined {
  return typeof value === 'string' && (allowed as readonly string[]).includes(value)
    ? (value as T)
    : undefined;
}

/** A non-empty instrument name, upper-cased as the stores keep it. */
function symbolOf(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim().toUpperCase();
  // Bounded so a pathological blob cannot restore a megabyte-long "symbol".
  return trimmed.length > 0 && trimmed.length <= 64 ? trimmed : undefined;
}

/** A possibly-empty string field (F&O expiry / underlying). */
function optionalText(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const trimmed = value.trim();
  return trimmed.length <= 64 ? trimmed : undefined;
}

/**
 * The per-mode instrument map, keeping only entries that pass `symbolOf`.
 *
 * Iterates `PROFILES` rather than the blob's own keys, so unknown keys are
 * dropped by construction — a blob carrying `{"__proto__": ...}` or a thousand
 * junk modes contributes nothing. One rejected entry does not void the rest:
 * a garbled Swing symbol should not cost the user their Investor one.
 */
function symbolByProfileOf(value: unknown): Partial<Record<TradeProfile, string>> | undefined {
  if (!isObject(value)) return undefined;
  const out: Partial<Record<TradeProfile, string>> = {};
  for (const profile of PROFILES) {
    const symbol = symbolOf(value[profile]);
    if (symbol) out[profile] = symbol;
  }
  return out;
}

function boolOf(value: unknown): boolean | undefined {
  return typeof value === 'boolean' ? value : undefined;
}

/** A CSS hex colour. Anything else is dropped rather than fed to the renderer. */
function hexColorOf(value: unknown): string | undefined {
  return typeof value === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(value) ? value : undefined;
}

/** Finite-number-valued params only, matching `workspace.ts::readChartTypeParams`. */
function chartTypeParamsOf(value: unknown): ChartTypeParams | undefined {
  if (!isObject(value)) return undefined;
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(value)) {
    if (typeof v === 'number' && Number.isFinite(v)) out[k] = v;
  }
  return out as ChartTypeParams;
}

/** One pane, or undefined when any part of it fails validation. */
function paneOf(value: unknown, expectedId: PaneId): ChartPaneState | undefined {
  if (!isObject(value)) return undefined;
  const timeframe = oneOf(value.timeframe, TIMEFRAMES);
  const chartType = oneOf(value.chartType, CHART_TYPES as readonly ChartType[]);
  if (!timeframe || !chartType) return undefined;
  // A pane's symbol is legitimately empty until split view seeds it.
  const symbol = typeof value.symbol === 'string' ? value.symbol.trim().toUpperCase() : '';
  if (symbol.length > 64) return undefined;
  // The id is positional, not trusted from the blob — panes[0] is always 'A'.
  return { id: expectedId, symbol, timeframe, chartType };
}

/** Both panes, or undefined when either fails (a half-valid pair is not usable). */
function panesOf(value: unknown): [ChartPaneState, ChartPaneState] | undefined {
  if (!Array.isArray(value) || value.length !== 2) return undefined;
  const a = paneOf(value[0], 'A');
  const b = paneOf(value[1], 'B');
  return a && b ? [a, b] : undefined;
}

// ── Read ────────────────────────────────────────────────────────────────────

/**
 * Assign `key` from `raw` when the validator accepts it.
 *
 * Skipping rather than defaulting is the point: an absent key must leave the
 * store's own default in place, and a REJECTED key must behave identically to an
 * absent one. Writing a default here instead would turn "we could not read your
 * preference" into "your preference is candlestick".
 */
function take<K extends keyof TerminalPreferences>(
  out: Partial<TerminalPreferences>,
  raw: Record<string, unknown>,
  key: K,
  validate: (value: unknown) => TerminalPreferences[K] | undefined,
): void {
  const accepted = validate(raw[key]);
  if (accepted !== undefined) out[key] = accepted;
}

/**
 * Parse the stored preferences blob into a validated partial.
 *
 * Total: returns `{}` on the server, when storage is unavailable, when the blob
 * is absent/unparseable/wrong-shaped, or when its version does not match.
 */
export function parsePreferences(raw: unknown): Partial<TerminalPreferences> {
  if (typeof raw !== 'string' || raw.length === 0) return {};

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  if (!isObject(parsed)) return {};
  if (parsed.version !== PREFERENCES_VERSION) return {};

  const out: Partial<TerminalPreferences> = {};

  take(out, parsed, 'activeProfile', (v) => oneOf(v, PROFILES));
  take(out, parsed, 'selectedSymbol', symbolOf);
  take(out, parsed, 'activeTimeframe', (v) => oneOf(v, TIMEFRAMES));
  take(out, parsed, 'activeRange', (v) => oneOf(v, DATA_RANGES));
  take(out, parsed, 'chartMode', (v) => oneOf(v, CHART_MODES));
  take(out, parsed, 'fnoUnderlying', optionalText);
  take(out, parsed, 'fnoExpiry', optionalText);
  take(out, parsed, 'symbolByProfile', symbolByProfileOf);

  take(out, parsed, 'chartType', (v) => oneOf(v, CHART_TYPES as readonly ChartType[]));
  take(out, parsed, 'chartTypeParams', chartTypeParamsOf);
  take(out, parsed, 'ghostLineMode', (v) => oneOf(v, GHOST_LINE_MODES));
  take(out, parsed, 'splitView', boolOf);
  take(out, parsed, 'panes', panesOf);
  take(out, parsed, 'activePaneId', (v) => oneOf(v, PANE_IDS));
  take(out, parsed, 'sidebarOpen', boolOf);
  take(out, parsed, 'drawingColor', hexColorOf);
  take(out, parsed, 'magnetMode', (v) => oneOf(v, MAGNET_MODES));
  take(out, parsed, 'drawingsVisible', boolOf);
  take(out, parsed, 'drawingsLocked', boolOf);

  // Cross-field rule, enforced HERE so it cannot be bypassed by restoring the
  // two stores independently: split view only exists in INTRADAY and F&O
  // (`useChartUIStore.setSplitView` refuses to enable it elsewhere). A blob
  // pairing `splitView: true` with `SWING` describes a state the UI cannot
  // produce, and restoring it would render a split workspace that the toggle
  // then refuses to re-enable once turned off.
  if (out.splitView && out.activeProfile && !SPLIT_ENABLED_PROFILES.includes(out.activeProfile)) {
    out.splitView = false;
  }

  return out;
}

/** Read and validate the stored preferences. `{}` on the server or on any fault. */
export function readPreferences(): Partial<TerminalPreferences> {
  if (typeof window === 'undefined') return {};
  try {
    return parsePreferences(localStorage.getItem(PREFERENCES_STORAGE_KEY));
  } catch {
    return {}; // private mode / storage disabled
  }
}

// ── Write ───────────────────────────────────────────────────────────────────

/**
 * The blob as it currently stands, so a patch from one store cannot drop the
 * other store's fields. Seeded from storage on first use.
 */
let current: Partial<TerminalPreferences> | null = null;
let writeTimer: ReturnType<typeof setTimeout> | null = null;
let unloadHooked = false;

/** Debounce window. Selections arrive in bursts (mode switch, then symbol). */
export const PREFERENCES_WRITE_DEBOUNCE_MS = 300;

function flush(): void {
  writeTimer = null;
  if (typeof window === 'undefined' || current === null) return;
  try {
    localStorage.setItem(
      PREFERENCES_STORAGE_KEY,
      JSON.stringify({ ...current, version: PREFERENCES_VERSION }),
    );
  } catch {
    /* quota / private mode — the in-memory selections still apply this session */
  }
}

/**
 * Merge a patch into the stored preferences and schedule a debounced write.
 *
 * Merge, not replace: `useTradeStore` and `useChartUIStore` each own a slice of
 * one blob, so a replacing write from either would erase the other's.
 */
export function savePreferences(patch: Partial<TerminalPreferences>): void {
  if (typeof window === 'undefined') return;
  if (current === null) current = readPreferences();
  current = { ...current, ...patch };
  if (writeTimer) clearTimeout(writeTimer);
  writeTimer = setTimeout(flush, PREFERENCES_WRITE_DEBOUNCE_MS);

  // Close the debounce hole: a selection made in the last 300ms before the tab
  // goes away would otherwise never reach storage. `pagehide` fires for closes,
  // reloads, and navigations (including the iOS back/forward cache, where
  // `beforeunload` does not), which is the whole set we care about.
  //
  // Attached on first save rather than at import so merely loading this module —
  // on the server, or in a test — registers nothing.
  if (!unloadHooked) {
    unloadHooked = true;
    window.addEventListener('pagehide', flushPreferences);
  }
}

/** Write any pending patch immediately. */
export function flushPreferences(): void {
  if (writeTimer) clearTimeout(writeTimer);
  flush();
}

/**
 * Drop the stored preferences and any pending write.
 *
 * Used by tests. Deliberately NOT called on sign-out: these are per-device UI
 * selections, not session data, and the watchlist already outlives a logout
 * through the same storage. Clearing one and not the other would be arbitrary.
 */
export function resetPreferences(): void {
  if (writeTimer) clearTimeout(writeTimer);
  writeTimer = null;
  current = null;
  if (typeof window === 'undefined') return;
  try {
    localStorage.removeItem(PREFERENCES_STORAGE_KEY);
  } catch {
    /* nothing to clear */
  }
}
