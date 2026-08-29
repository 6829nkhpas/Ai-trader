//
// Regression guard: the projected curve must not be quantized into a staircase.
//
// Reported as "the ghost line isn't a curve, it makes a ladder". The cause was
// price quantization, in two places that compounded:
//
//   1. Every engine rounded each projected point with `+expr.toFixed(2)`.
//   2. The curve clamp in `computeGhostPoints` then accumulated through the
//      ROUNDED value — `prev = points[i].price` — so each step was re-snapped to
//      one paisa.
//
// On a shallow curve the true per-step delta is smaller than 0.01, so rounding
// collapsed runs of points onto the SAME price and then jumped by a whole paisa:
// flat, flat, flat, riser — a literal staircase. And because a curved projection
// is drawn as joined 2-point segments, those flats and risers render as the rungs
// of a ladder rather than a line.
//
// These tests exercise the pure engines directly (no store, no chart IPC) with a
// deliberately shallow input, where the artefact is unmissable: at ₹1308 with a
// slope of ~0.004/bar, 2dp rounding would produce visible flat runs.
//
// The invariant asserted is monotonic STRICTNESS, not smoothness in a visual
// sense: for a strictly-increasing underlying trend, consecutive projected prices
// must be strictly increasing. A staircase violates that (it has equal
// neighbours); a real curve does not.

import { describe, it, expect } from 'vitest';

import { vweprProjection, forecastProjection } from '../ghostLineComputation';

/** A gently rising series — the case where 2dp rounding used to bite. */
function shallowCandles(
  count: number,
  base: number,
  slopePerBar: number,
): { time: number; close: number; volume: number; high: number; low: number }[] {
  const out = [];
  for (let i = 0; i < count; i++) {
    const close = base + slopePerBar * i;
    out.push({ time: 1000 + i * 60, close, volume: 1000, high: close + 0.5, low: close - 0.5 });
  }
  return out;
}

/** Count adjacent pairs whose price is byte-identical — the staircase's flats. */
function flatRunCount(points: { price: number }[]): number {
  let flats = 0;
  for (let i = 1; i < points.length; i++) {
    if (points[i].price === points[i - 1].price) flats++;
  }
  return flats;
}

describe('projection engines — no paisa-quantized staircase', () => {
  it('vwepr keeps sub-paisa resolution on a shallow rise', () => {
    // 0.004/bar is well under the 0.01 rounding quantum, so every consecutive
    // pair used to collapse to the same 2dp value.
    const candles = shallowCandles(50, 1308.0, 0.004);
    const points = vweprProjection(candles, 1_700_000_000, 60, 20);

    expect(points.length).toBeGreaterThan(2);
    expect(flatRunCount(points)).toBe(0);

    // Strictly increasing, which a staircase cannot be.
    for (let i = 1; i < points.length; i++) {
      expect(points[i].price).toBeGreaterThan(points[i - 1].price);
    }
  });

  it('vwepr prices are not all multiples of 0.01', () => {
    // The direct fingerprint of the bug: every price landing exactly on a paisa
    // boundary means something rounded them.
    const candles = shallowCandles(50, 1308.0, 0.004);
    const points = vweprProjection(candles, 1_700_000_000, 60, 20);

    const allOnPaisaGrid = points.every(
      (p) => Math.abs(p.price * 100 - Math.round(p.price * 100)) < 1e-9,
    );
    expect(allOnPaisaGrid).toBe(false);
  });

  it('forecast engine likewise keeps sub-paisa resolution', () => {
    const candles = shallowCandles(50, 1308.0, 0.004);
    const points = forecastProjection(candles, 1_700_000_000, 60, 20);

    if (points.length > 2) {
      expect(flatRunCount(points)).toBe(0);
    }
  });

  it('still resolves a curve when the whole move is under one paisa', () => {
    // The pathological end of the range: a 20-bar projection whose total drift is
    // ~0.008 — less than a single paisa. Rounding rendered this as ONE flat line,
    // i.e. no projection at all. It must still come out as distinct points.
    const candles = shallowCandles(50, 1308.0, 0.0004);
    const points = vweprProjection(candles, 1_700_000_000, 60, 20);

    expect(points.length).toBeGreaterThan(2);
    const first = points[0].price;
    const last = points[points.length - 1].price;
    expect(last).not.toBe(first);
    expect(flatRunCount(points)).toBe(0);
  });
});
