// Feature: professional-charting-suite
//
// Pure drawing geometry, validation, and coordinate-transform helpers.
//
// The Drawing_Engine has two halves: a stateful rendering/interaction layer
// (the existing `useDrawingEngine` / `useDrawingRenderer` / `useDrawingInteraction`
// hooks plus the `useChartUIStore` slice) and the pure, side-effect-free
// geometry/validation primitives in this module. Everything here is a direct
// property-based-testing target:
//
//   - TOOL_REGISTRY  — maps each drawing tool to its required anchor count and
//                      category (Property 12, Requirements 5.1, 5.2, 5.3).
//   - isComplete     — a drawing exists iff its anchors meet the tool's count.
//   - fibLevels      — canonical Fibonacci retracement ratios (Property 17,
//                      Requirement 5.10).
//   - magnetSnap     — snaps to the nearest OHLC within threshold, else to the
//                      pointer (Property 14, Requirement 5.6).
//   - pointToPixel / pixelToPoint — the {time,price} <-> pixel transforms that
//                      round-trip within 1 px (Property 13, Requirement 5.4).
//   - clearUnlocked  — keeps locked drawings, drops unlocked ones (Property 16,
//                      Requirement 5.9).
//
// The transforms are modeled as a linear mapping over an explicit viewport so
// they can be tested in isolation from `lightweight-charts`; the live renderer
// converts stored {time, price} anchors to pixels every frame using the same
// linear relationship, which keeps anchors fixed in data space across pan/zoom.

import type { ChartCandle } from '../types';
import type { Drawing, Point } from '../../store/useChartUIStore';

// ── Tool registry ─────────────────────────────────────────────────────────

/**
 * The drawing categories from Requirement 5.1. Used to group tools in the
 * toolbar and to drive category-specific behavior.
 */
export type DrawingCategory =
  | 'trend-line'
  | 'channel'
  | 'fibonacci'
  | 'shape'
  | 'text'
  | 'projection';

/**
 * Describes how many anchor points a tool needs before it becomes a complete
 * drawing. `'multi'` denotes open-ended tools (path, polyline, disjoint
 * channel) that require at least {@link MULTI_MIN_ANCHORS} anchors but accept
 * more.
 */
export interface ToolSpec {
  tool: string;
  anchorCount: number | 'multi';
  category: DrawingCategory;
}

/** The minimum number of anchors a `'multi'` tool requires to be complete. */
export const MULTI_MIN_ANCHORS = 3;

/**
 * Maps each supported drawing tool to its anchor requirement and category
 * (Requirement 5.1). Tool ids match the kebab-case ids used by the toolbar and
 * `useDrawingEngine`. Single-point tools need 1 anchor, two-point tools need 2,
 * and open-ended tools are `'multi'`.
 */
export const TOOL_REGISTRY: Record<string, ToolSpec> = {
  // ── Trend lines ──────────────────────────────────────────────────────────
  'trendline': { tool: 'trendline', anchorCount: 2, category: 'trend-line' },
  'ray': { tool: 'ray', anchorCount: 2, category: 'trend-line' },
  'extended-line': { tool: 'extended-line', anchorCount: 2, category: 'trend-line' },
  'horizontal-line': { tool: 'horizontal-line', anchorCount: 1, category: 'trend-line' },
  'horizontal-ray': { tool: 'horizontal-ray', anchorCount: 1, category: 'trend-line' },
  'vertical-line': { tool: 'vertical-line', anchorCount: 1, category: 'trend-line' },
  'cross-line': { tool: 'cross-line', anchorCount: 1, category: 'trend-line' },

  // ── Channels ─────────────────────────────────────────────────────────────
  'parallel-channel': { tool: 'parallel-channel', anchorCount: 3, category: 'channel' },
  'regression-trend': { tool: 'regression-trend', anchorCount: 2, category: 'channel' },
  'flat-top-bottom': { tool: 'flat-top-bottom', anchorCount: 3, category: 'channel' },
  'disjoint-channel': { tool: 'disjoint-channel', anchorCount: 'multi', category: 'channel' },

  // ── Fibonacci ────────────────────────────────────────────────────────────
  'fib-retracement': { tool: 'fib-retracement', anchorCount: 2, category: 'fibonacci' },
  'fib-extension': { tool: 'fib-extension', anchorCount: 3, category: 'fibonacci' },
  'fib-channel': { tool: 'fib-channel', anchorCount: 3, category: 'fibonacci' },
  'fib-time-zone': { tool: 'fib-time-zone', anchorCount: 2, category: 'fibonacci' },

  // ── Shapes ───────────────────────────────────────────────────────────────
  'rectangle': { tool: 'rectangle', anchorCount: 2, category: 'shape' },
  'circle': { tool: 'circle', anchorCount: 2, category: 'shape' },
  'ellipse': { tool: 'ellipse', anchorCount: 2, category: 'shape' },
  'triangle-shape': { tool: 'triangle-shape', anchorCount: 3, category: 'shape' },
  'path': { tool: 'path', anchorCount: 'multi', category: 'shape' },
  'polyline': { tool: 'polyline', anchorCount: 'multi', category: 'shape' },

  // ── Text & notes ─────────────────────────────────────────────────────────
  'text': { tool: 'text', anchorCount: 1, category: 'text' },
  'note': { tool: 'note', anchorCount: 1, category: 'text' },

  // ── Projection ───────────────────────────────────────────────────────────
  'long-position': { tool: 'long-position', anchorCount: 2, category: 'projection' },
  'short-position': { tool: 'short-position', anchorCount: 2, category: 'projection' },
  'price-range': { tool: 'price-range', anchorCount: 2, category: 'projection' },
  'date-range': { tool: 'date-range', anchorCount: 2, category: 'projection' },
  'date-price-range': { tool: 'date-price-range', anchorCount: 2, category: 'projection' },
};

/**
 * Resolve the required anchor count for a tool. Unknown tools and `'multi'`
 * tools both resolve to {@link MULTI_MIN_ANCHORS}; unknown tools are treated as
 * open-ended so a stray id never silently completes with a single anchor.
 */
function requiredAnchors(tool: string): number {
  const spec = TOOL_REGISTRY[tool];
  if (!spec) return MULTI_MIN_ANCHORS;
  return spec.anchorCount === 'multi' ? MULTI_MIN_ANCHORS : spec.anchorCount;
}

/**
 * Determine whether the placed anchors complete the given tool's drawing
 * (Requirement 5.2, 5.3). A drawing is complete iff the number of anchors meets
 * the tool's required count; placing fewer anchors (a cancellation) is not
 * complete and must not produce a drawing.
 *
 * @param tool    the drawing tool id (see {@link TOOL_REGISTRY})
 * @param anchors the anchors placed so far
 */
export function isComplete(tool: string, anchors: Point[]): boolean {
  return anchors.length >= requiredAnchors(tool);
}

// ── Fibonacci ───────────────────────────────────────────────────────────────

/**
 * The canonical Fibonacci retracement ratios (Requirement 5.10).
 */
export const FIB_RATIOS: readonly number[] = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0];

/**
 * Compute the Fibonacci retracement levels between two price anchors
 * (Requirement 5.10, Property 17). Each level is the price at the corresponding
 * canonical ratio of the anchored price range, linearly interpolated so ratio 0
 * maps to `p1` and ratio 1.0 maps to `p2`.
 *
 * @param p1 the first (0.0) price anchor
 * @param p2 the second (1.0) price anchor
 */
export function fibLevels(p1: number, p2: number): { ratio: number; price: number }[] {
  const range = p2 - p1;
  return FIB_RATIOS.map((ratio) => ({ ratio, price: p1 + range * ratio }));
}

// ── Magnet snapping ───────────────────────────────────────────────────────

/**
 * Snap an anchor to a candle's OHLC under magnet mode (Requirement 5.6,
 * Property 14).
 *
 * The pixel distance from the pointer's price to each of the candle's open,
 * high, low, and close is `|pointer.price - value| * pxPerPrice`. When the
 * nearest such value is within `thresholdPx` pixels, the anchor's price snaps
 * to that value; otherwise the anchor stays at the exact pointer coordinates.
 *
 * @param pointer     the raw pointer position in data coordinates
 * @param candle      the candle nearest the pointer
 * @param pxPerPrice  pixels per unit price (the price scale's vertical density)
 * @param thresholdPx the snap radius in pixels (default 10)
 */
export function magnetSnap(
  pointer: Point,
  candle: ChartCandle,
  pxPerPrice: number,
  thresholdPx = 10,
): Point {
  const candidates = [candle.open, candle.high, candle.low, candle.close];

  let bestValue = pointer.price;
  let bestDistPx = Infinity;
  for (const value of candidates) {
    const distPx = Math.abs(pointer.price - value) * pxPerPrice;
    if (distPx < bestDistPx) {
      bestDistPx = distPx;
      bestValue = value;
    }
  }

  if (bestDistPx <= thresholdPx) {
    return { time: pointer.time, price: bestValue };
  }

  return { time: pointer.time, price: pointer.price };
}

// ── Coordinate transforms ───────────────────────────────────────────────────

/** A pixel position within the chart canvas (origin top-left). */
export interface Pixel {
  x: number;
  y: number;
}

/**
 * The linear viewport mapping data coordinates to pixels. `timeFrom`/`timeTo`
 * are the times at the left (`x = 0`) and right (`x = width`) edges, and
 * `priceMin`/`priceMax` are the prices at the bottom (`y = height`) and top
 * (`y = 0`) edges. A viewport is valid when `timeTo !== timeFrom`,
 * `priceMax !== priceMin`, `width > 0`, and `height > 0`.
 */
export interface CoordinateViewport {
  timeFrom: number;
  timeTo: number;
  priceMin: number;
  priceMax: number;
  width: number;
  height: number;
}

/**
 * Convert a `{time, price}` anchor to pixel coordinates under a viewport
 * (Requirement 5.4). Time increases left-to-right; price increases
 * bottom-to-top (so larger prices map to smaller `y`).
 */
export function pointToPixel(point: Point, vp: CoordinateViewport): Pixel {
  const x = ((point.time - vp.timeFrom) / (vp.timeTo - vp.timeFrom)) * vp.width;
  const y = ((vp.priceMax - point.price) / (vp.priceMax - vp.priceMin)) * vp.height;
  return { x, y };
}

/**
 * Convert pixel coordinates back to a `{time, price}` anchor under a viewport
 * (Requirement 5.4). Inverse of {@link pointToPixel}, so a value round-trips
 * back to its original time and price.
 */
export function pixelToPoint(pixel: Pixel, vp: CoordinateViewport): Point {
  const time = vp.timeFrom + (pixel.x / vp.width) * (vp.timeTo - vp.timeFrom);
  const price = vp.priceMax - (pixel.y / vp.height) * (vp.priceMax - vp.priceMin);
  return { time, price };
}

// ── Clearing ──────────────────────────────────────────────────────────────

/**
 * Return only the locked drawings, dropping every unlocked one (Requirement
 * 5.9, Property 16). This is the pure core of the "clear drawings" action: the
 * result is exactly the subset of `drawings` whose `locked` flag is truthy.
 *
 * @param drawings the current drawing set
 */
export function clearUnlocked(drawings: Drawing[]): Drawing[] {
  return drawings.filter((d) => d.locked === true);
}
