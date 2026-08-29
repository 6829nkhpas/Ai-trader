// Feature: professional-charting-suite
//
// Footprint (bid/ask cluster) aggregation engine.
//
// This is the pure computational core extracted from the legacy
// `FootprintChart.tsx` rendering component. Given the canonical candle series
// and the raw order-flow (L2) tick buffer, it produces, per candle, a set of
// price-level cells (bid/ask volume grouped by tick size) plus the derived
// order-flow metrics a footprint chart renders: per-candle Delta, the running
// Cumulative_Delta, the imbalance levels, and the POC. A candle with no live
// order-flow ticks yields NO cells and is flagged `hasOrderFlow: false` — see
// `buildFootprint` for why it must not be filled in.
//
// Everything here is deterministic and side-effect-free so it is a direct
// property-based-testing target. The rendering component keeps only draw logic.
//
// Time-unit contract: `ChartCandle.time` is whole seconds (see
// `canonicalCandles`), while `OrderFlowTick.timestamp` is milliseconds (see the
// store). The engine converts candle times to milliseconds (× 1000) before
// bucketing ticks, matching the legacy component.
//
// Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.9.

import type { OrderFlowTick } from '../../store/useTradeStore';
import type { ChartCandle } from '../types';

/** A single price-level cell within a footprint candle. */
export interface FootprintCell {
  /** Price of the level — always an exact multiple of the tick size. */
  price: number;
  /** Total bid-initiated (selling-into-bid) volume at this level. */
  bid: number;
  /** Total ask-initiated (buying-into-ask) volume at this level. */
  ask: number;
}

/** A fully aggregated footprint candle. */
export interface FootprintCandle {
  /** Candle timestamp (seconds), copied from the source candle. */
  time: number;
  /** Price-level cells, sorted ascending by price. */
  cells: FootprintCell[];
  /** Per-candle Delta: total ask volume minus total bid volume. */
  delta: number;
  /** Sum of all cell volumes (bid + ask) across the candle. */
  totalVolume: number;
  /**
   * Point of Control — the price level with the greatest total volume. Ties
   * are broken by selecting the level closest to the candle's close. `null`
   * only when the candle has no cells.
   */
  poc: number | null;
  /** Price levels flagged as an imbalance (sorted ascending). */
  imbalances: number[];
  /**
   * True when this candle's cells came from real order-flow ticks.
   *
   * False means no ticks fell in this candle's bucket, in which case `cells` is
   * EMPTY and every derived metric is zero/null. It does not mean "estimated" —
   * nothing is estimated. Renderers must branch on this rather than presenting
   * the zeros as a measured balance.
   */
  hasOrderFlow: boolean;
}

/** Options accepted by {@link buildFootprint}. */
export interface BuildFootprintOptions {
  /** Price increment used to group ticks into rows. Must be greater than 0. */
  tickSize: number;
  /**
   * Imbalance ratio threshold. Defaults to {@link DEFAULT_IMBALANCE_RATIO} and
   * is clamped to [{@link MIN_IMBALANCE_RATIO}, {@link MAX_IMBALANCE_RATIO}].
   */
  imbalanceRatio?: number;
}

/** Default imbalance ratio (Requirement 6.6). */
export const DEFAULT_IMBALANCE_RATIO = 3;
/** Minimum configurable imbalance ratio (Requirement 6.6). */
export const MIN_IMBALANCE_RATIO = 1.5;
/** Maximum configurable imbalance ratio (Requirement 6.6). */
export const MAX_IMBALANCE_RATIO = 20;

/** Fallback tick size when an invalid (≤ 0) tick size is supplied. */
const FALLBACK_TICK_SIZE = 1;

/**
 * Clamp the configured imbalance ratio into the accepted 1.5–20 range,
 * defaulting to 3 when the value is missing or non-finite (Requirement 6.6).
 */
function normalizeRatio(ratio: number | undefined): number {
  if (typeof ratio !== 'number' || !Number.isFinite(ratio)) {
    return DEFAULT_IMBALANCE_RATIO;
  }
  if (ratio < MIN_IMBALANCE_RATIO) return MIN_IMBALANCE_RATIO;
  if (ratio > MAX_IMBALANCE_RATIO) return MAX_IMBALANCE_RATIO;
  return ratio;
}

/** Guard against a non-positive or non-finite tick size (Requirement 6.9). */
function normalizeTickSize(tickSize: number): number {
  if (typeof tickSize !== 'number' || !Number.isFinite(tickSize) || tickSize <= 0) {
    return FALLBACK_TICK_SIZE;
  }
  return tickSize;
}

/**
 * Snap a price to its tick-size row index. Using an integer index as the map
 * key (rather than a rounded float) keeps every emitted cell price an exact
 * multiple of the tick size and avoids floating-point key collisions.
 */
function tickIndex(price: number, tickSize: number): number {
  return Math.round(price / tickSize);
}

/**
 * Assign each order-flow tick to the candle whose time bucket contains it.
 *
 * Candle start times (seconds → milliseconds) are expected to fall on a regular
 * grid; the interval is inferred from the smallest positive gap between
 * consecutive candle starts. A tick is matched to the candle whose start equals
 * `floor(tick.timestamp / interval) * interval`. Ticks that fall outside every
 * candle bucket are dropped. When the interval cannot be inferred (a single
 * candle), every tick is assigned to that candle.
 *
 * @returns a map from candle index (into `candles`) to the ticks it owns.
 */
function assignTicksToCandles(
  candles: ChartCandle[],
  ticks: OrderFlowTick[],
): Map<number, OrderFlowTick[]> {
  const result = new Map<number, OrderFlowTick[]>();
  if (candles.length === 0 || ticks.length === 0) return result;

  // candleStartMs → candle index.
  const startToIndex = new Map<number, number>();
  const startsMs: number[] = [];
  candles.forEach((c, idx) => {
    const startMs = c.time * 1000;
    startToIndex.set(startMs, idx);
    startsMs.push(startMs);
  });

  // Infer the bucket interval from the smallest positive gap between starts.
  const sorted = [...startsMs].sort((a, b) => a - b);
  let intervalMs = Infinity;
  for (let i = 1; i < sorted.length; i += 1) {
    const gap = sorted[i] - sorted[i - 1];
    if (gap > 0 && gap < intervalMs) intervalMs = gap;
  }

  const singleBucket = !Number.isFinite(intervalMs);

  for (const tick of ticks) {
    if (!tick || !Number.isFinite(tick.timestamp)) continue;

    let candleIdx: number | undefined;
    if (singleBucket) {
      candleIdx = 0;
    } else {
      const bucketStart = Math.floor(tick.timestamp / intervalMs) * intervalMs;
      candleIdx = startToIndex.get(bucketStart);
    }

    if (candleIdx === undefined) continue;
    const bucket = result.get(candleIdx);
    if (bucket) bucket.push(tick);
    else result.set(candleIdx, [tick]);
  }

  return result;
}

/**
 * Build the bid/ask cells for a candle from its live order-flow ticks, grouped
 * by tick size. Returns `null` when there are no ticks so the caller can fall
 * back to a synthetic distribution.
 */
function buildLiveCells(ticks: OrderFlowTick[], tickSize: number): FootprintCell[] | null {
  if (ticks.length === 0) return null;

  // Group by integer tick index so every price is a clean multiple of tickSize.
  const byIndex = new Map<number, { bid: number; ask: number }>();
  for (const tick of ticks) {
    if (!tick || !Number.isFinite(tick.price_level)) continue;
    const idx = tickIndex(tick.price_level, tickSize);
    const cell = byIndex.get(idx) ?? { bid: 0, ask: 0 };
    cell.bid += Number.isFinite(tick.bid_volume) ? tick.bid_volume : 0;
    cell.ask += Number.isFinite(tick.ask_volume) ? tick.ask_volume : 0;
    byIndex.set(idx, cell);
  }

  if (byIndex.size === 0) return null;

  return Array.from(byIndex.entries())
    .map(([idx, v]) => ({ price: idx * tickSize, bid: v.bid, ask: v.ask }))
    .sort((a, b) => a.price - b.price);
}

/**
 * Select the POC for a set of cells: the level with the greatest total volume,
 * breaking ties by proximity to the candle's close, then (for equidistant ties)
 * by the lower price for determinism (Requirement 6.7).
 */
function selectPoc(cells: FootprintCell[], close: number): number | null {
  if (cells.length === 0) return null;

  let bestPrice = cells[0].price;
  let bestVol = cells[0].bid + cells[0].ask;
  let bestDist = Math.abs(cells[0].price - close);

  for (let i = 1; i < cells.length; i += 1) {
    const vol = cells[i].bid + cells[i].ask;
    const dist = Math.abs(cells[i].price - close);

    if (vol > bestVol) {
      bestVol = vol;
      bestPrice = cells[i].price;
      bestDist = dist;
    } else if (vol === bestVol) {
      // Tie on volume → prefer the level closest to the close; on an equal
      // distance prefer the lower price for a deterministic result.
      if (dist < bestDist || (dist === bestDist && cells[i].price < bestPrice)) {
        bestPrice = cells[i].price;
        bestDist = dist;
      }
    }
  }

  return bestPrice;
}

/**
 * Identify the price levels that qualify as an Imbalance (Requirement 6.6).
 *
 * Imbalance is diagonal between vertically adjacent levels. For a level `P`:
 *   - a buy (ask) imbalance occurs when `P.ask` dominates the bid of the level
 *     immediately below it, i.e. `P.ask >= ratio * below.bid` and `P.ask > 0`;
 *   - a sell (bid) imbalance occurs when `P.bid` dominates the ask of the level
 *     immediately above it, i.e. `P.bid >= ratio * above.ask` and `P.bid > 0`.
 *
 * "Immediately below/above" is the previous/next cell in ascending price order.
 * A level is flagged when either diagonal qualifies — equivalently, when the
 * ratio of the larger to the smaller of its diagonally-opposed volumes is at
 * least `ratio`, with this level on the larger side. When the opposing volume
 * is zero and this level's volume is positive, the ratio is treated as infinite
 * and the level is flagged.
 *
 * @param cells price-level cells (any order; sorted internally).
 * @param ratio imbalance threshold; clamped to the accepted 1.5–20 range.
 */
export function detectImbalances(cells: FootprintCell[], ratio: number): number[] {
  if (cells.length === 0) return [];

  const r = normalizeRatio(ratio);
  const sorted = [...cells].sort((a, b) => a.price - b.price);
  const flagged: number[] = [];

  for (let i = 0; i < sorted.length; i += 1) {
    const level = sorted[i];
    const below = sorted[i - 1];
    const above = sorted[i + 1];

    const belowBid = below ? below.bid : 0;
    const aboveAsk = above ? above.ask : 0;

    const buyImbalance = level.ask > 0 && level.ask >= r * belowBid;
    const sellImbalance = level.bid > 0 && level.bid >= r * aboveAsk;

    if (buyImbalance || sellImbalance) {
      flagged.push(level.price);
    }
  }

  return flagged;
}

/**
 * Aggregate a candle series and its order-flow ticks into footprint candles.
 *
 * For each candle, ticks that fall in its time bucket are grouped by tick size
 * into bid/ask cells. Per-candle Delta, total volume, POC, and imbalance levels
 * are derived from those cells.
 *
 * A candle with NO ticks yields no cells and `hasOrderFlow: false`.
 *
 * This used to synthesise a bid/ask distribution instead: volume spread across
 * the high-low range on a bell curve peaking at the mid price, with a base of 100
 * units per level and the bid/ask split tilted by the candle's direction. Nothing
 * about it was measured. Worse, every order-flow metric a footprint chart exists
 * to show — Delta, total volume, POC, the imbalance levels, and the running
 * Cumulative_Delta built on top of them — was then derived from those invented
 * cells, so the whole chart read as real order flow. The only marker was a small
 * "≈ EST" that the renderer drew solely when a candle happened to be wider than
 * 24px. Order flow that nobody traded is not order flow.
 *
 * The function is pure: it never mutates its inputs and the output order
 * matches the input candle order.
 *
 * @param candles canonical candle series (sorted ascending, de-duplicated).
 * @param ticks   raw order-flow ticks for the active symbol.
 * @param opts    tick size (> 0) and imbalance ratio (1.5–20, default 3).
 */
export function buildFootprint(
  candles: ChartCandle[],
  ticks: OrderFlowTick[],
  opts: BuildFootprintOptions,
): FootprintCandle[] {
  const tickSize = normalizeTickSize(opts.tickSize);
  const ratio = normalizeRatio(opts.imbalanceRatio);

  const ticksByCandle = assignTicksToCandles(candles, ticks);

  return candles.map((candle, idx) => {
    const candleTicks = ticksByCandle.get(idx) ?? [];
    const liveCells = buildLiveCells(candleTicks, tickSize);

    const hasOrderFlow = liveCells !== null;
    const cells = liveCells ?? [];

    let bidTotal = 0;
    let askTotal = 0;
    for (const cell of cells) {
      bidTotal += cell.bid;
      askTotal += cell.ask;
    }

    return {
      time: candle.time,
      cells,
      delta: askTotal - bidTotal,
      totalVolume: bidTotal + askTotal,
      poc: selectPoc(cells, candle.close),
      imbalances: detectImbalances(cells, ratio),
      hasOrderFlow,
    };
  });
}

/**
 * Compute the running Cumulative_Delta over a footprint series, beginning at
 * zero from the leftmost candle (Requirement 6.5). The returned array has the
 * same length as `fps`; entry `i` is the sum of per-candle deltas from index 0
 * through `i`, so the final entry equals the sum of all deltas.
 */
export function cumulativeDelta(fps: FootprintCandle[]): number[] {
  const out: number[] = [];
  let running = 0;
  for (const fp of fps) {
    running += fp.delta;
    out.push(running);
  }
  return out;
}
