// Feature: professional-charting-suite
//
// Unit tests for the pure `shouldPulseOnRangeChange` helper (ghost-line zoom
// pulse classifier). The helper decides whether a TradingView
// `onVisibleRangeChanged` event should re-project the ghost line.
//
// We pulse ONLY on genuine user zoom, NOT on programmatic auto-scroll on a
// new bar. A new bar already bumps `lastBarTime` (a redraw dep), so pulsing
// here too caused 2–3 concurrent redraws per new bar — the "ghost line
// thrash".
//
// Decision model (see the helper's doc comment):
//   · first event (prev null)                  → bootstrap  → pulse
//   · range WIDTH `to - from` changed          → user zoom  → pulse
//   · width unchanged (auto-scroll / no-op)    → skip

import { describe, it, expect } from 'vitest';

import { shouldPulseOnRangeChange } from '@/hooks/useGhostLine';

describe('shouldPulseOnRangeChange', () => {
  it('pulses on the first event (prev null) to bootstrap the baseline', () => {
    expect(shouldPulseOnRangeChange(null, { from: 100, to: 200 })).toBe(true);
  });

  it('does NOT pulse on programmatic auto-scroll (constant width, both edges slide equally)', () => {
    // The window tracks the latest bar: `from` and `to` both advance by the
    // same delta with the width unchanged — exactly what TradingView does on
    // a new bar while the user is parked at the right edge.
    expect(
      shouldPulseOnRangeChange({ from: 100, to: 200 }, { from: 110, to: 210 }),
    ).toBe(false);
    // Repeated auto-scrolls also stay silent.
    expect(
      shouldPulseOnRangeChange({ from: 110, to: 210 }, { from: 120, to: 220 }),
    ).toBe(false);
    // Sliding left (e.g. loading older history) is the same constant-width
    // move, just negative — also not a user zoom.
    expect(
      shouldPulseOnRangeChange({ from: 110, to: 210 }, { from: 100, to: 200 }),
    ).toBe(false);
  });

  it('does NOT pulse on a constant-width window slide (pan or auto-scroll)', () => {
    // When the width is unchanged the projection length is unchanged, so we
    // never re-project — this is also the signature of programmatic auto-scroll
    // on a new bar (both edges slide forward by one bar). Slide right:
    expect(
      shouldPulseOnRangeChange({ from: 100, to: 200 }, { from: 120, to: 220 }),
    ).toBe(false);
    // Slide left:
    expect(
      shouldPulseOnRangeChange({ from: 100, to: 200 }, { from: 90, to: 190 }),
    ).toBe(false);
  });

  it('pulses when the range width changes (user zoom)', () => {
    // `from` unchanged, width grew (zoomed out).
    expect(
      shouldPulseOnRangeChange({ from: 100, to: 200 }, { from: 100, to: 250 }),
    ).toBe(true);
    // `from` unchanged, width shrank (zoomed in).
    expect(
      shouldPulseOnRangeChange({ from: 100, to: 200 }, { from: 100, to: 150 }),
    ).toBe(true);
    // Both edges moved AND width changed (zoom + pan) — still a user zoom.
    expect(
      shouldPulseOnRangeChange({ from: 100, to: 200 }, { from: 120, to: 250 }),
    ).toBe(true);
  });

  it('does not pulse when nothing moved (width unchanged, edges identical)', () => {
    expect(
      shouldPulseOnRangeChange({ from: 100, to: 200 }, { from: 100, to: 200 }),
    ).toBe(false);
  });
});
