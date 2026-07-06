// Feature: professional-charting-suite
//
// Unit tests for the pure parameter-validation utilities (task 1.2).
// These cover specific examples and edge cases: non-numeric, wrong-type,
// out-of-range, integer-constraint, and boundary values, plus the offending
// parameter name reported on failure. The universal property is exercised
// separately by the property test (task 1.3).

import { describe, it, expect } from 'vitest';

import { validateNumeric, validateParams } from '@/charting/engines';
import type { NumericRange } from '@/charting/types';

const intRange: NumericRange = { min: 1, max: 999_999, integer: true };
const floatRange: NumericRange = { min: 0.1, max: 10.0, integer: false };

describe('validateNumeric', () => {
  it('accepts a value inside the range', () => {
    const result = validateNumeric(14, intRange, 'period');
    expect(result).toEqual({ ok: true, value: 14 });
  });

  it('accepts inclusive boundary values', () => {
    expect(validateNumeric(1, intRange, 'period').ok).toBe(true);
    expect(validateNumeric(999_999, intRange, 'period').ok).toBe(true);
    expect(validateNumeric(0.1, floatRange, 'stdDev').ok).toBe(true);
    expect(validateNumeric(10.0, floatRange, 'stdDev').ok).toBe(true);
  });

  it('rejects values below the minimum and names the parameter', () => {
    const result = validateNumeric(0, intRange, 'period');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errorParam).toBe('period');
  });

  it('rejects values above the maximum', () => {
    const result = validateNumeric(1_000_000, intRange, 'boxSize');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errorParam).toBe('boxSize');
  });

  it('rejects non-integer values when the range requires an integer', () => {
    const result = validateNumeric(14.5, intRange, 'period');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errorParam).toBe('period');
  });

  it('accepts fractional values when integer is not required', () => {
    expect(validateNumeric(2.5, floatRange, 'stdDev').ok).toBe(true);
  });

  it.each([
    ['string', '14'],
    ['boolean', true],
    ['null', null],
    ['undefined', undefined],
    ['object', {}],
    ['array', [1]],
    ['bigint', BigInt(14)],
  ])('rejects %s as a non-numeric type', (_label, value) => {
    const result = validateNumeric(value, intRange, 'rows');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errorParam).toBe('rows');
  });

  it.each([
    ['NaN', Number.NaN],
    ['Infinity', Number.POSITIVE_INFINITY],
    ['-Infinity', Number.NEGATIVE_INFINITY],
  ])('rejects %s', (_label, value) => {
    const result = validateNumeric(value, floatRange, 'multiplier');
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errorParam).toBe('multiplier');
  });
});

describe('validateParams', () => {
  const spec: Record<string, NumericRange> = {
    period: { min: 1, max: 5_000, integer: true },
    stdDev: { min: 0.1, max: 10.0, integer: false },
  };

  it('accepts a fully valid parameter bag and returns only spec keys', () => {
    const result = validateParams({ period: 20, stdDev: 2, extra: 99 }, spec);
    expect(result).toEqual({ ok: true, value: { period: 20, stdDev: 2 } });
  });

  it('rejects the bag and names the first offending parameter', () => {
    const result = validateParams({ period: 0, stdDev: 2 }, spec);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errorParam).toBe('period');
  });

  it('rejects a missing required parameter', () => {
    const result = validateParams({ period: 20 }, spec);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errorParam).toBe('stdDev');
  });

  it('rejects a wrong-type parameter value', () => {
    const result = validateParams({ period: '20', stdDev: 2 }, spec);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.errorParam).toBe('period');
  });

  it('accepts an empty spec', () => {
    expect(validateParams({ anything: 1 }, {})).toEqual({ ok: true, value: {} });
  });
});
