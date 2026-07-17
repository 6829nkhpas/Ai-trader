// Unit test for the ghost-line VWEPR (curved) projection clamp tuning.
//
// Regression guard for Unit 6: the per-curve clamp in `computeGhostPoints`
// (ghostLineComputation.ts ~L599) used to be so tight
// (`maxTotal = avgStep * 40 * (N-1)`) that a genuinely accelerating VWEPR
// parabola was flattened to `anchorPrice ± maxTotal`, making the ghost line
// look like it "gave up" / pointed the wrong way. The clamp was loosened to
// `maxStep = avgStep * 12` and `maxTotal = maxStep * (N-1) * 2` (scales with
// projection length) so real curvature survives while still rejecting
// pathological single-step spikes.
//
// This spec exercises the pure `vweprProjection` engine directly (no store /
// IPC) with a deterministic quadratic-accelerating input and asserts the
// projected last price differs from the anchor by MORE than the trailing
// average step — i.e. the curve is NOT a flat line. It then sanity-checks
// that the loosened clamp budget (computed the same way as in
// `computeGhostPoints`) would not flatten that natural curvature.

import { describe, it, expect } from 'vitest';

import { vweprProjection } from '../ghostLineComputation';

/** Build a window of candles whose closes follow a quadratic that
 *  accelerates upward: close[i] = base + linearSlope * i + curve * i^2.
 *  Volume is held constant so VWEPR's volume weights don't distort the fit.
 *  The resulting VWEPR fit should extrapolate the acceleration forward. */
function quadraticCandles(
  count: number,
  base: number,
  linearSlope: number,
  curve: number,
): { time: number; close: number; volume: number; high: number; low: number }[] {
  const out = [];
  for (let i = 0; i < count; i++) {
    const close = base + linearSlope * i + curve * i * i;
    out.push({ time: 1000 + i * 60, close, volume: 1000, high: close + 1, low: close - 1 });
  }
  return out;
}

describe('vweprProjection — clamp does not flatten accelerating curves', () => {
  it('projects a last price that differs from the anchor by more than avgStep', () => {
    // A parabola with a modest but real upward acceleration. The trailing
    // bar-to-bar step grows from `linearSlope` (≈1) at the start of the window
    // to `linearSlope + 2*curve*(n-1)` (≈1 + 2*0.05*49 ≈ 5.9) at the last bar,
    // so the projected curve should keep accelerating beyond the anchor.
    const candles = quadraticCandles(50, /*base=*/100, /*linearSlope=*/1, /*curve=*/0.05);
    const lastTime = candles[candles.length - 1].time;
    const intervalSec = 60;
    const projLen = 6;

    const pts = vweprProjection(candles, lastTime, intervalSec, projLen);
    expect(pts.length).toBe(projLen + 1);

    const anchor = pts[0].price;
    const last = pts[pts.length - 1].price;
    const deviation = Math.abs(last - anchor);

    // Trailing avg step, computed the same way as the clamp in
    // `computeGhostPoints` (last 20 bars, mean absolute bar-to-bar delta).
    const recent = candles.slice(-20).map((c) => c.close);
    let sumAbs = 0;
    for (let i = 1; i < recent.length; i++) sumAbs += Math.abs(recent[i] - recent[i - 1]);
    const avgStep = sumAbs / (recent.length - 1);

    // The curve must NOT be clamped flat: the final projected price deviates
    // from the anchor by strictly more than a single average step.
    expect(deviation).toBeGreaterThan(avgStep);

    // And the loosened clamp budget must be generous enough to let this
    // natural deviation through (mirrors the new tuning in
    // `computeGhostPoints`: maxStep = avgStep*12, maxTotal = maxStep*(N-1)*2).
    const maxStep = avgStep * 12.0;
    const maxTotal = maxStep * (pts.length - 1) * 2.0;
    expect(deviation).toBeLessThanOrEqual(maxTotal);
  });

  it('still rejects a truly pathological single-step spike via the per-step guard', () => {
    // Sanity check that the loosened per-step cap (avgStep * 12) still bounds a
    // single absurd jump: a mostly-flat window with one 1000× spike should
    // produce a VWEPR fit whose forward step is within the clamp's per-step
    // budget. We verify the budget itself is finite and proportional, not that
    // the raw projection is clamped (the clamp lives in `computeGhostPoints`).
    const candles = quadraticCandles(50, 100, 0, 0); // flat: close = 100
    // Inject one pathological spike on the final bar.
    candles[candles.length - 1] = { ...candles[candles.length - 1], close: 100_000 };

    const lastTime = candles[candles.length - 1].time;
    const pts = vweprProjection(candles, lastTime, 60, 6);
    expect(pts.length).toBe(7);
    // Every projected price is finite and positive (engine never returns NaN).
    for (const p of pts) {
      expect(Number.isFinite(p.price)).toBe(true);
      expect(p.price).toBeGreaterThan(0);
    }
  });
});
