// Feature: professional-charting-suite, Property 31
//
// Property-based test for Property 31: "Values are formatted to the
// instrument's configured precision" (Validates Requirements 10.1, 10.2).
//
// For any numeric OHLC or indicator value and for any configured decimal
// precision, the crosshair readout string produced by `formatValue` is the
// value rounded and formatted to exactly that number of decimal places.
//
// `formatValue` clamps the requested precision to the range Number.toFixed
// accepts (0..100), so the property is stated against that clamped precision:
//
//   - the returned string has exactly `clampedPrecision` digits after the
//     decimal point (and matches `value.toFixed(clampedPrecision)`); and
//   - parsing the string back to a number reproduces the input rounded to that
//     precision (round-trip numerically close to the input).

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { formatValue, DEFAULT_PRICE_PRECISION } from '@/charting/crosshair';

const RUNS = 100;

/**
 * Mirror of the (private) clamp in crosshair.ts: a precision request is
 * truncated toward zero and constrained to the 0..100 range Number.toFixed
 * accepts; a non-finite request falls back to the default price precision.
 */
function clampPrecision(precision: number): number {
  if (!Number.isFinite(precision)) return DEFAULT_PRICE_PRECISION;
  const p = Math.trunc(precision);
  if (p < 0) return 0;
  if (p > 100) return 100;
  return p;
}

/** Count the digits after the decimal point in a formatted decimal string. */
function decimalPlaces(s: string): number {
  const dot = s.indexOf('.');
  return dot === -1 ? 0 : s.length - dot - 1;
}

describe('Property 31: values formatted to configured precision', () => {
  it('formats a finite value to exactly the clamped precision number of decimals', () => {
    fc.assert(
      fc.property(
        // Arbitrary finite values across the realistic range of instrument
        // prices and indicator values. Bounded to |x| < 1e21 because that is
        // the magnitude at which Number.toFixed is spec-defined to switch to
        // exponential notation — a regime no real OHLC/indicator value reaches.
        fc.double({
          noNaN: true,
          noDefaultInfinity: true,
          min: -1e15,
          max: 1e15,
        }),
        // Arbitrary precision requests, including out-of-range ones to exercise clamping.
        fc.integer({ min: -10, max: 110 }),
        (value, precision) => {
          const clamped = clampPrecision(precision);
          const formatted = formatValue(value, precision);

          // Matches the reference toFixed formatting exactly.
          expect(formatted).toBe(value.toFixed(clamped));

          // Exactly `clamped` decimal places.
          expect(decimalPlaces(formatted)).toBe(clamped);

          // Round-trips numerically close to the input at that precision.
          const parsed = Number(formatted);
          expect(Number.isFinite(parsed)).toBe(true);
          // The maximum representable rounding error at `clamped` decimals.
          const tol = 0.5 * Math.pow(10, -clamped);
          // Allow generous slack for floating-point scale of large magnitudes.
          const scale = Math.max(1, Math.abs(value));
          expect(Math.abs(parsed - value)).toBeLessThanOrEqual(
            tol + 1e-9 * scale,
          );
        },
      ),
      { numRuns: RUNS },
    );
  });
});
