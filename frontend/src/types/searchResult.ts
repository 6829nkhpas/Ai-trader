// Feature: terminal-ux-overhaul
//
// Shared contract for the F&O-aware instrument search (task 9.x). The
// `search_instruments` Tauri command (task 9.1, Rust) returns a typed
// discriminated union so the UI can render equities and NFO contracts
// distinctly and route a selection to the active chart pane.
//
// This module is the single source of truth for that contract on the
// frontend, plus the pure validators / type-guards the UI relies on:
//   - `isWellFormedSearchResult` — narrows an arbitrary value to SearchResult
//   - `isDistinguishableFno`     — an FNO result carries underlying/expiry/type
//                                  so it can be told apart from an equity
//
// Requirements: 3.1 (equities + FNO returned), 3.2 (filter by
// underlying/expiry/strike/type), 3.4 (FNO clearly distinguished from equity).

/** The option/contract type carried by every FNO result. */
export type OptionType = 'CE' | 'PE' | 'FUT';

/** The valid option types, exported for generators and runtime checks. */
export const OPTION_TYPES: readonly OptionType[] = ['CE', 'PE', 'FUT'] as const;

/** An equity (NSE/BSE cash) or index search result. */
export interface EquityResult {
  kind: 'EQ';
  symbol: string;
  name: string;
  exchange: string;
  /**
   * Kite's segment for the row: `INDICES` for an index, the exchange code
   * (`NSE` / `BSE`) for a tradable scrip.
   *
   * Carried so an index can be recognised by what the exchange says it is rather
   * than by matching its name against a hand-written list — see `isIndex`. NSE
   * publishes 136 index rows and BSE 73, so any list is wrong the moment the
   * exchange adds one.
   *
   * Optional: desktop's `search_instruments` predates the field, and a result
   * without it is still a valid equity result.
   */
  segment?: string;
}

/** An NFO option (CE/PE) or future (FUT) search result. */
export interface FnoResult {
  kind: 'FNO';
  tradingsymbol: string;
  underlying: string;
  expiry: string;
  /** Strike is present for options (CE/PE) and null for futures (FUT). */
  strike: number | null;
  optionType: OptionType;
}

/**
 * A single instrument-search result. A discriminated union on `kind` so
 * equities and NFO contracts can be rendered and routed distinctly (R3.1, R3.4).
 */
export type SearchResult = EquityResult | FnoResult;

/** True when `v` is a non-empty string. */
function isNonEmptyString(v: unknown): v is string {
  return typeof v === 'string' && v.length > 0;
}

/** True when `value` is a valid OptionType ('CE' | 'PE' | 'FUT'). */
export function isOptionType(value: unknown): value is OptionType {
  return typeof value === 'string' && (OPTION_TYPES as readonly string[]).includes(value);
}

/** True when `x` is a well-formed equity result. */
export function isEquityResult(x: unknown): x is EquityResult {
  if (typeof x !== 'object' || x === null) return false;
  const r = x as Record<string, unknown>;
  return (
    r.kind === 'EQ' &&
    isNonEmptyString(r.symbol) &&
    typeof r.name === 'string' &&
    isNonEmptyString(r.exchange)
  );
}

/** True when `x` is a well-formed FNO result. */
export function isFnoResult(x: unknown): x is FnoResult {
  if (typeof x !== 'object' || x === null) return false;
  const r = x as Record<string, unknown>;
  return (
    r.kind === 'FNO' &&
    isNonEmptyString(r.tradingsymbol) &&
    isNonEmptyString(r.underlying) &&
    isNonEmptyString(r.expiry) &&
    (r.strike === null || (typeof r.strike === 'number' && Number.isFinite(r.strike))) &&
    isOptionType(r.optionType)
  );
}

/**
 * The contract the UI relies on: `x` is a well-formed `EQ` or `FNO` record.
 * Anything else (missing tag, missing/empty fields, wrong field types,
 * unknown `kind`) is rejected (R3.1).
 */
export function isWellFormedSearchResult(x: unknown): x is SearchResult {
  return isEquityResult(x) || isFnoResult(x);
}

/**
 * An FNO result is *distinguishable* from an equity when it carries a
 * non-empty underlying, a non-empty expiry, and a valid optionType — the
 * fields the UI shows to tell an option/future apart from a cash symbol
 * (R3.2, R3.4). Equities never carry these fields, so the `kind` tag plus
 * these FNO-only fields fully disambiguate the two.
 */
export function isDistinguishableFno(r: SearchResult): r is FnoResult {
  return (
    r.kind === 'FNO' &&
    isNonEmptyString(r.underlying) &&
    isNonEmptyString(r.expiry) &&
    isOptionType(r.optionType)
  );
}

/** The chart/watchlist symbol for a result, regardless of kind. */
export function resultSymbol(r: SearchResult): string {
  return r.kind === 'EQ' ? r.symbol : r.tradingsymbol;
}
