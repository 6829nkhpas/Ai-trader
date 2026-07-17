// Feature: ghost-line-stability
//
// Unit tests for the projection-interval resolution in `ghostLineComputation`.
// The ghost line must step on the *display* timeframe grid, not the Kite base
// interval of the stored bars. For aggregated timeframes (`2m` stores 1-minute
// bars, `75m` stores 15-minute bars, etc.) the inferred bar gap would otherwise
// produce 2–5× too many points off the displayed bar grid → jitter.

import { describe, it, expect } from 'vitest';

import { resolveIntervalSec } from '../ghostLineComputation';

/** Build a synthetic lookback of `count` bars spaced `stepSec` seconds apart,
 *  ending at `lastTimeSec`. Mirrors the shape of the stored candle array. */
function makeBars(count: number, stepSec: number, lastTimeSec: number) {
  const bars: { time: number }[] = [];
  for (let i = count - 1; i >= 0; i--) bars.push({ time: lastTimeSec - i * stepSec });
  return bars;
}

describe('resolveIntervalSec', () => {
  it('uses the display timeframe (2m = 120s) even when stored bars are 1-minute-spaced', () => {
    // A 2m chart stores 1-minute bars (Kite base interval), so the inferred
    // bar gap would be 60s. The display timeframe map must win → 120s.
    const lookback = makeBars(60, 60, 1_000_000);
    expect(resolveIntervalSec('2m', lookback)).toBe(120);
  });

  it('uses the display timeframe (75m = 4500s) even when stored bars are 15-minute-spaced', () => {
    // A 75m chart stores 15-minute bars (Kite base interval), so the inferred
    // bar gap would be 900s. The display timeframe map must win → 4500s.
    const lookback = makeBars(60, 900, 1_000_000);
    expect(resolveIntervalSec('75m', lookback)).toBe(4500);
  });

  it('does NOT fall back to the inferred 1-minute bar gap for 2m', () => {
    const lookback = makeBars(60, 60, 1_000_000);
    expect(resolveIntervalSec('2m', lookback)).not.toBe(60);
  });

  it('does NOT fall back to the inferred 15-minute bar gap for 75m', () => {
    const lookback = makeBars(60, 900, 1_000_000);
    expect(resolveIntervalSec('75m', lookback)).not.toBe(900);
  });

  it('falls back to the inferred bar interval when the timeframe is missing from the map', () => {
    // Unknown timeframe string → map has no entry → use the inferred bar gap.
    const lookback = makeBars(60, 180, 1_000_000); // 3-minute bars
    expect(resolveIntervalSec('unknown-tf', lookback)).toBe(180);
  });

  it('falls back to a 60s default when the map is missing and the bar gap cannot be inferred', () => {
    const lookback = makeBars(2, 60, 1_000_000); // < 3 bars → inferBarIntervalSec returns 0
    expect(resolveIntervalSec('unknown-tf', lookback)).toBe(60);
  });

  it('handles aggregated higher timeframes (2h, 4h, 1W, 1M)', () => {
    // 2h stores 1-hour bars (3600s) but must project at 7200s.
    expect(resolveIntervalSec('2h', makeBars(40, 3600, 1_000_000))).toBe(7200);
    // 4h stores 1-hour bars but must project at 14400s.
    expect(resolveIntervalSec('4h', makeBars(40, 3600, 1_000_000))).toBe(14400);
    // 1W stores daily bars but must project at 604800s.
    expect(resolveIntervalSec('1W', makeBars(40, 86400, 1_000_000))).toBe(604800);
    // 1M stores daily bars but must project at 2592000s.
    expect(resolveIntervalSec('1M', makeBars(40, 86400, 1_000_000))).toBe(2592000);
  });
});
