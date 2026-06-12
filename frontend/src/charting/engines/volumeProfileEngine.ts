// Feature: professional-charting-suite
//
// Volume-profile binning engine.
//
// This is the pure computational core extracted from the legacy
// `VolumeProfileOverlay.tsx` rendering component. Given the candle series for a
// profile range (visible range, session, or fixed range) and the matching
// per-candle volume bars, it bins traded volume into a configurable number of
// price-level rows and derives the Point of Control (POC) and Value_Area
// (VAH/VAL) for that range.
//
// Everything here is deterministic and side-effect-free so it is a direct
// property-based-testing target (Properties 23, 24, 25). The rendering
// component keeps only draw logic.
//
// Requirements: 7.1, 7.2, 7.3, 7.4, 7.6, 7.9, 7.10.

import type { ChartCandle, VolumeBar } from '../types';

/** The three supported profile ranges (Requirement 7.1). */
export type ProfileRange = 'visible' | 'session' | 'fixed';

/**
 * Describes the range the profile is computed over.
 *
 *  - `visible` / `session`: the caller supplies the already-sliced candles for
 *    that range (the engine bins whatever it is given).
 *  - `fixed`: the engine filters the supplied candles to the inclusive
 *    `[start, end]` time span between two anchors. The range is invalid when
 *    `end <= start` (Requirement 7.10).
 */
export type ProfileRangeSpec =
  | { kind: 'visible' | 'session' }
  | { kind: 'fixed'; start: number; end: number };

/** A single horizontal price-level row of the profile. */
export interface ProfileRow {
  /** Inclusive lower price edge of the row. */
  priceLow: number;
  /** Exclusive (top-most: inclusive) upper price edge of the row. */
  priceHigh: number;
  /** Total traded volume binned into this row. */
  volume: number;
  /** True when this row is part of the Value_Area. */
  inValueArea: boolean;
}

/** A fully computed volume profile for a range. */
export interface VolumeProfile {
  /** Price-level rows ordered ascending by price (index 0 = lowest price). */
  rows: ProfileRow[];
  /**
   * Point of Control — the representative (center) price of the single
   * greatest-volume row. `null` when total volume is zero (Requirement 7.9).
   */
  poc: number | null;
  /** Value_Area High — upper price edge of the value-area set, or `null`. */
  vah: number | null;
  /** Value_Area Low — lower price edge of the value-area set, or `null`. */
  val: number | null;
  /** Sum of all row volumes (the total traded volume over the range). */
  totalVolume: number;
}

/** Options accepted by {@link buildProfile}. */
export interface BuildProfileOptions {
  /**
   * Number of price-level rows. Defaults to {@link DEFAULT_PROFILE_ROWS} and is
   * clamped to [{@link MIN_PROFILE_ROWS}, {@link MAX_PROFILE_ROWS}]
   * (Requirement 7.2).
   */
  rows?: number;
  /**
   * Value_Area target percentage. Defaults to
   * {@link DEFAULT_VALUE_AREA_PERCENT} and is clamped to
   * [{@link MIN_VALUE_AREA_PERCENT}, {@link MAX_VALUE_AREA_PERCENT}]
   * (Requirement 7.4).
   */
  valuePercent?: number;
  /** The range to profile over. Defaults to `{ kind: 'visible' }`. */
  range?: ProfileRangeSpec;
  /**
   * The previously computed profile. Returned unchanged when a fixed range is
   * rejected as invalid (Requirement 7.10).
   */
  previousProfile?: VolumeProfile | null;
}

/** Default number of profile rows (Requirement 7.2). */
export const DEFAULT_PROFILE_ROWS = 24;
/** Minimum configurable row count (Requirement 7.2). */
export const MIN_PROFILE_ROWS = 1;
/** Maximum configurable row count (Requirement 7.2). */
export const MAX_PROFILE_ROWS = 1000;

/** Default Value_Area percentage (Requirement 7.4). */
export const DEFAULT_VALUE_AREA_PERCENT = 70;
/** Minimum configurable Value_Area percentage (Requirement 7.4). */
export const MIN_VALUE_AREA_PERCENT = 1;
/** Maximum configurable Value_Area percentage (Requirement 7.4). */
export const MAX_VALUE_AREA_PERCENT = 100;

/**
 * Normalize the requested row count: default to 24 when missing/non-finite,
 * round to an integer, and clamp into the accepted 1–1000 range.
 */
function normalizeRows(rows: number | undefined): number {
  if (typeof rows !== 'number' || !Number.isFinite(rows)) {
    return DEFAULT_PROFILE_ROWS;
  }
  const rounded = Math.round(rows);
  if (rounded < MIN_PROFILE_ROWS) return MIN_PROFILE_ROWS;
  if (rounded > MAX_PROFILE_ROWS) return MAX_PROFILE_ROWS;
  return rounded;
}

/**
 * Normalize the Value_Area percentage: default to 70 when missing/non-finite
 * and clamp into the accepted 1–100 range.
 */
function normalizeValuePercent(pct: number | undefined): number {
  if (typeof pct !== 'number' || !Number.isFinite(pct)) {
    return DEFAULT_VALUE_AREA_PERCENT;
  }
  if (pct < MIN_VALUE_AREA_PERCENT) return MIN_VALUE_AREA_PERCENT;
  if (pct > MAX_VALUE_AREA_PERCENT) return MAX_VALUE_AREA_PERCENT;
  return pct;
}

/**
 * Build an empty profile of exactly `rows` rows spanning a degenerate price
 * range. Used when the range contains no candles / no price extent so the
 * caller can still render an empty-profile indication (Requirement 7.9) while
 * the row-count invariant (Requirement 7.2) is preserved.
 */
function emptyProfile(rows: number, price = 0): VolumeProfile {
  return {
    rows: Array.from({ length: rows }, () => ({
      priceLow: price,
      priceHigh: price,
      volume: 0,
      inValueArea: false,
    })),
    poc: null,
    vah: null,
    val: null,
    totalVolume: 0,
  };
}

/**
 * Compute the contiguous Value_Area around the POC.
 *
 * Starting from `pocIndex`, the area grows outward one row at a time, always
 * absorbing the larger of the two adjacent rows (preferring the lower row on a
 * tie, matching the legacy overlay), until the accumulated volume reaches the
 * target percentage of total volume (Requirement 7.4). The returned indices are
 * inclusive bounds into `rowVolumes`.
 *
 * When total volume is zero the area collapses to the POC index itself.
 *
 * @param rowVolumes  per-row volumes ordered ascending by price.
 * @param pocIndex    index of the Point of Control row.
 * @param valuePercent target percentage (1–100) of total volume.
 */
export function valueArea(
  rowVolumes: number[],
  pocIndex: number,
  valuePercent: number,
): { loIndex: number; hiIndex: number } {
  const n = rowVolumes.length;
  if (n === 0) return { loIndex: 0, hiIndex: 0 };

  const clampedPoc = Math.min(Math.max(pocIndex, 0), n - 1);
  let lo = clampedPoc;
  let hi = clampedPoc;

  let total = 0;
  for (const v of rowVolumes) total += v;
  if (total <= 0) return { loIndex: lo, hiIndex: hi };

  const pct = normalizeValuePercent(valuePercent);
  const target = total * (pct / 100);

  let acc = rowVolumes[clampedPoc];

  while (acc < target && (lo > 0 || hi < n - 1)) {
    const below = lo > 0 ? rowVolumes[lo - 1] : -Infinity;
    const above = hi < n - 1 ? rowVolumes[hi + 1] : -Infinity;

    if (below === -Infinity && above === -Infinity) break;

    // Absorb the larger adjacent row; on a tie prefer the lower (below) row.
    if (below >= above) {
      lo -= 1;
      acc += rowVolumes[lo];
    } else {
      hi += 1;
      acc += rowVolumes[hi];
    }
  }

  return { loIndex: lo, hiIndex: hi };
}

/**
 * Bin traded volume into price-level rows over a profile range and derive the
 * POC and Value_Area.
 *
 * Each candle's volume (looked up from `volumes` by matching time) is spread
 * across the price rows its high–low span touches, splitting evenly across the
 * touched rows so that every candle's full volume is conserved
 * (Requirement 7.2/7.6). The function always returns exactly the configured
 * number of rows.
 *
 * Range handling (Requirement 7.1):
 *  - `visible` / `session`: the supplied candles are binned as-is.
 *  - `fixed`: candles are filtered to the inclusive `[start, end]` time span.
 *    An invalid fixed range (`end <= start`) is rejected and the previously
 *    computed profile is returned unchanged (Requirement 7.10).
 *
 * The function is pure: it never mutates its inputs.
 *
 * @param candles candle series for the range (sorted ascending by time).
 * @param volumes per-candle volume bars (matched to candles by time).
 * @param opts    row count, value-area percentage, range, and prior profile.
 */
export function buildProfile(
  candles: ChartCandle[],
  volumes: VolumeBar[],
  opts: BuildProfileOptions = {},
): VolumeProfile {
  const rows = normalizeRows(opts.rows);
  const valuePercent = normalizeValuePercent(opts.valuePercent);
  const range = opts.range ?? { kind: 'visible' };

  // ── Range selection ──────────────────────────────────────────────────────
  let ranged = candles;
  if (range.kind === 'fixed') {
    // Reject an invalid fixed range and retain the prior profile (Req 7.10).
    if (!(Number.isFinite(range.start) && Number.isFinite(range.end) && range.end > range.start)) {
      return opts.previousProfile ?? emptyProfile(rows);
    }
    ranged = candles.filter((c) => c.time >= range.start && c.time <= range.end);
  }

  // ── Price extent over the range ───────────────────────────────────────────
  let minP = Infinity;
  let maxP = -Infinity;
  for (const c of ranged) {
    if (Number.isFinite(c.low) && c.low < minP) minP = c.low;
    if (Number.isFinite(c.high) && c.high > maxP) maxP = c.high;
  }

  // No valid price extent (empty range) → empty profile, still `rows` rows.
  if (!Number.isFinite(minP) || !Number.isFinite(maxP)) {
    return emptyProfile(rows);
  }

  // O(1) volume lookup keyed by candle time.
  const volMap = new Map<number, number>();
  for (const v of volumes) {
    if (Number.isFinite(v.value)) volMap.set(v.time, v.value);
  }

  const binVolumes = new Array<number>(rows).fill(0);
  const priceRange = maxP - minP;
  const binSize = priceRange / rows;

  // Map a price to a row index, clamped into [0, rows - 1].
  const binIndex = (price: number): number => {
    if (binSize <= 0) return 0; // degenerate (flat) range → single row
    const idx = Math.floor((price - minP) / binSize);
    if (idx < 0) return 0;
    if (idx > rows - 1) return rows - 1;
    return idx;
  };

  // ── Distribute each candle's volume across the rows it spans ───────────────
  for (const c of ranged) {
    const vol = volMap.get(c.time) ?? 0;
    if (vol <= 0) continue;

    const lo = binIndex(c.low);
    const hi = binIndex(c.high);

    if (lo === hi) {
      binVolumes[lo] += vol;
    } else {
      const span = hi - lo + 1;
      const share = vol / span;
      let distributed = 0;
      for (let i = lo; i < hi; i += 1) {
        binVolumes[i] += share;
        distributed += share;
      }
      // Assign the remainder to the final row so each candle's volume is
      // conserved exactly despite floating-point division.
      binVolumes[hi] += vol - distributed;
    }
  }

  let totalVolume = 0;
  for (const v of binVolumes) totalVolume += v;

  // ── Zero traded volume → empty-profile indication, no markers (Req 7.9) ────
  if (totalVolume <= 0) {
    return {
      rows: binVolumes.map((_, i) => ({
        priceLow: minP + i * binSize,
        priceHigh: minP + (i + 1) * binSize,
        volume: 0,
        inValueArea: false,
      })),
      poc: null,
      vah: null,
      val: null,
      totalVolume: 0,
    };
  }

  // ── POC: the single greatest-volume row (lowest index on a tie) ────────────
  let pocIndex = 0;
  let pocVol = binVolumes[0];
  for (let i = 1; i < rows; i += 1) {
    if (binVolumes[i] > pocVol) {
      pocVol = binVolumes[i];
      pocIndex = i;
    }
  }

  // ── Value_Area expansion from the POC ──────────────────────────────────────
  const { loIndex, hiIndex } = valueArea(binVolumes, pocIndex, valuePercent);

  const profileRows: ProfileRow[] = binVolumes.map((volume, i) => ({
    priceLow: minP + i * binSize,
    priceHigh: minP + (i + 1) * binSize,
    volume,
    inValueArea: i >= loIndex && i <= hiIndex,
  }));

  const pocRow = profileRows[pocIndex];
  const poc = (pocRow.priceLow + pocRow.priceHigh) / 2;
  const val = profileRows[loIndex].priceLow;
  const vah = profileRows[hiIndex].priceHigh;

  return {
    rows: profileRows,
    poc,
    vah,
    val,
    totalVolume,
  };
}
