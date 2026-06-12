// Feature: professional-charting-suite
//
// Pure, deterministic helpers for the wheel-zoom clamp (Requirement 10.6).
//
// Requirement 10.6: when the trader scrolls the mouse wheel over the chart, the
// time axis zooms centered on the cursor's time position, constrained so that
// no fewer than 5 candles and no more than 5,000 candles are visible.
//
// These helpers operate on `lightweight-charts` *logical* ranges — a `{from,to}`
// pair of fractional bar indices whose span (`to - from`) is the number of
// visible candles. The renderer subscribes to logical-range changes and feeds
// each range through `clampVisibleRange` before re-applying it, so the visible
// candle count can never escape the [5, 5000] bound regardless of how the user
// zooms. The functions are pure so Property 30 (task 12.5) can exercise them
// directly without a chart instance.

/** Minimum number of candles the time axis may show (Requirement 10.6). */
export const MIN_VISIBLE_CANDLES = 5;

/** Maximum number of candles the time axis may show (Requirement 10.6). */
export const MAX_VISIBLE_CANDLES = 5000;

/**
 * Clamp a visible candle count into the inclusive [5, 5000] bound.
 *
 * Non-finite inputs (NaN, ±Infinity) collapse to the minimum so a corrupt range
 * never propagates. The result is always a finite number within the bound.
 */
export function clampVisibleCandleCount(count: number): number {
  if (!Number.isFinite(count)) return MIN_VISIBLE_CANDLES;
  if (count < MIN_VISIBLE_CANDLES) return MIN_VISIBLE_CANDLES;
  if (count > MAX_VISIBLE_CANDLES) return MAX_VISIBLE_CANDLES;
  return count;
}

/** A `lightweight-charts` logical range: fractional bar indices. */
export interface LogicalRange {
  from: number;
  to: number;
}

/**
 * Clamp a logical range so its span (visible candle count) stays within
 * [5, 5000], preserving the range's center so cursor-centered zoom behavior is
 * retained (Requirement 10.6).
 *
 * When the incoming span is already within bounds the original range is
 * returned unchanged. When it is out of bounds the span is clamped and the
 * range is rebuilt symmetrically around its midpoint. Degenerate/non-finite
 * inputs fall back to a minimum-width range anchored at the (finite) `from`,
 * or at 0 when `from` is itself non-finite.
 */
export function clampVisibleRange(from: number, to: number): LogicalRange {
  const span = to - from;
  const clampedSpan = clampVisibleCandleCount(span);

  // Already within bounds (and finite) — return the range untouched.
  if (Number.isFinite(span) && clampedSpan === span) {
    return { from, to };
  }

  const center = (from + to) / 2;
  if (Number.isFinite(center)) {
    const half = clampedSpan / 2;
    return { from: center - half, to: center + half };
  }

  // `from`/`to` were not both finite — anchor a minimum-width range at a finite
  // edge so the caller always receives a usable, in-bounds range.
  const anchor = Number.isFinite(from) ? from : 0;
  return { from: anchor, to: anchor + clampedSpan };
}
