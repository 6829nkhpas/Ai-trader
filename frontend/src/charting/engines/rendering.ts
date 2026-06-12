// Feature: professional-charting-suite
//
// Pure rendering math shared by the canvas overlays.
//
// Canvas-backed overlays (volume profile, footprint) must size their backing
// store to the display's device pixel ratio so text and lines stay crisp on
// high-DPI screens for ratios 1.0–4.0 (Requirement 12.6). The CSS box is laid
// out in logical pixels; the backing store must hold `cssLength * dpr` physical
// pixels, floored to a whole pixel because a canvas dimension is integral.
//
// Keeping this in one deterministic, side-effect-free helper lets the overlays
// share the exact same scaling and lets the math be unit-tested without a DOM.

/**
 * Compute the integral backing-store length (in physical pixels) for a CSS
 * length rendered at a given device pixel ratio.
 *
 *   backingStoreLength(cssLength, dpr) === Math.floor(cssLength * dpr)
 *
 * The result is clamped to be non-negative so a degenerate (negative) CSS
 * length never yields a negative canvas dimension.
 *
 * @param cssLength The logical (CSS) length in pixels.
 * @param dpr       The device pixel ratio (e.g. 1.0–4.0).
 */
export function backingStoreLength(cssLength: number, dpr: number): number {
  if (!Number.isFinite(cssLength) || !Number.isFinite(dpr)) return 0;
  return Math.max(0, Math.floor(cssLength * dpr));
}
