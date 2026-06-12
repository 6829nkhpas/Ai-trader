// Feature: professional-charting-suite
//
// Setup smoke test: verifies the charting module structure is in place and
// that Vitest + fast-check are correctly configured for the module. This does
// not test engine behavior (engines arrive in later tasks); it guards the
// foundation that every later task builds on.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import type {
  ChartCandle,
  LinePoint,
  LineStyleSpec,
  NumericRange,
  ValidationResult,
} from '@/charting/types';

describe('charting module foundation', () => {
  it('exposes the shared value types as usable shapes', () => {
    const candle: ChartCandle = { time: 1, open: 10, high: 12, low: 9, close: 11 };
    const point: LinePoint = { time: 1, value: 11 };
    const style: LineStyleSpec = { color: '#22c55e', lineWidth: 1, lineStyle: 'solid' };
    const range: NumericRange = { min: 1, max: 5000, integer: true };
    const ok: ValidationResult<number> = { ok: true, value: 14 };
    const bad: ValidationResult<number> = {
      ok: false,
      errorParam: 'period',
      message: 'out of range',
    };

    expect(candle.close).toBe(11);
    expect(point.value).toBe(11);
    expect(style.lineStyle).toBe('solid');
    expect(range.integer).toBe(true);
    expect(ok.ok).toBe(true);
    expect(bad.ok).toBe(false);
  });

  it('runs fast-check property checks against the module', () => {
    // A trivial but real property to confirm fast-check executes under Vitest.
    fc.assert(
      fc.property(fc.double({ noNaN: true }), fc.double({ noNaN: true }), (a, b) => {
        const point: LinePoint = { time: a, value: b };
        return point.time === a && point.value === b;
      }),
      { numRuns: 100 },
    );
  });
});
