// Feature: professional-charting-suite, Property 3
//
// Property-based test for parameter validation (task 1.3).
//
// Property 3: Invalid parameters are rejected and last valid values are retained.
//   For any engine parameter (chart-type, indicator, or strategy) and for any
//   value that is non-numeric, of the wrong type, or outside its declared
//   range, validation rejects the value, returns an error identifying the
//   offending parameter, and the previously valid parameter set and its
//   computed output are left unchanged. In-range values of the correct type are
//   accepted unchanged.
//
// Validates: Requirements 1.6, 2.5, 8.6

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { validateNumeric, validateParams } from '@/charting/engines';
import type { NumericRange } from '@/charting/types';

const RUNS = { numRuns: 100 };

// A non-empty parameter name. The name is what validation must echo back in
// `errorParam` on rejection.
const paramNameArb = fc.string({ minLength: 1, maxLength: 12 });

// An arbitrary valid declared range. Integer ranges have integer bounds; float
// ranges have finite bounds. Bounds are kept modest so derived out-of-range
// offsets remain exactly representable.
const intRangeArb: fc.Arbitrary<NumericRange> = fc
  .record({ min: fc.integer({ min: -1000, max: 1000 }), span: fc.nat({ max: 5000 }) })
  .map(({ min, span }) => ({ min, max: min + span, integer: true }));

const floatRangeArb: fc.Arbitrary<NumericRange> = fc
  .record({
    min: fc.double({ min: -1000, max: 1000, noNaN: true, noDefaultInfinity: true }),
    span: fc.double({ min: 0, max: 5000, noNaN: true, noDefaultInfinity: true }),
  })
  .map(({ min, span }) => ({ min, max: min + span, integer: false }));

const rangeArb: fc.Arbitrary<NumericRange> = fc.oneof(intRangeArb, floatRangeArb);

// A value guaranteed to lie inside the given range and satisfy its type.
function inRangeArb(range: NumericRange): fc.Arbitrary<number> {
  return range.integer
    ? fc.integer({ min: range.min, max: range.max })
    : fc.double({ min: range.min, max: range.max, noNaN: true, noDefaultInfinity: true });
}

// A range paired with a value that is valid for it.
const rangeWithValidValue = rangeArb.chain((range) =>
  inRangeArb(range).map((value) => ({ range, value })),
);

// A range paired with a value strictly outside it (below min or above max).
const rangeWithOutOfRange = rangeArb.chain((range) =>
  fc
    .record({
      offset: fc.double({ min: 0.001, max: 1_000_000, noNaN: true, noDefaultInfinity: true }),
      below: fc.boolean(),
    })
    .map(({ offset, below }) => {
      const delta = range.integer ? Math.max(1, Math.round(offset)) : offset;
      const value = below ? range.min - delta : range.max + delta;
      return { range, value };
    }),
);

// Values that are not numbers at all.
const nonNumberArb: fc.Arbitrary<unknown> = fc.oneof(
  fc.string(),
  fc.boolean(),
  fc.constant(null),
  fc.constant(undefined),
  fc.object(),
  fc.array(fc.integer()),
  fc.bigInt(),
);

// Numeric values that are not finite.
const nonFiniteArb = fc.constantFrom(Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY);

describe('Property 3: invalid parameters are rejected and last valid values are retained', () => {
  it('accepts any in-range value of the correct type and returns it unchanged', () => {
    fc.assert(
      fc.property(paramNameArb, rangeWithValidValue, (name, { range, value }) => {
        const result = validateNumeric(value, range, name);
        expect(result.ok).toBe(true);
        if (result.ok) expect(result.value).toBe(value);
      }),
      RUNS,
    );
  });

  it('rejects any out-of-range value and names the offending parameter', () => {
    fc.assert(
      fc.property(paramNameArb, rangeWithOutOfRange, (name, { range, value }) => {
        const result = validateNumeric(value, range, name);
        expect(result.ok).toBe(false);
        if (!result.ok) expect(result.errorParam).toBe(name);
      }),
      RUNS,
    );
  });

  it('rejects any non-numeric (wrong-type) value and names the offending parameter', () => {
    fc.assert(
      fc.property(paramNameArb, rangeArb, nonNumberArb, (name, range, value) => {
        const result = validateNumeric(value, range, name);
        expect(result.ok).toBe(false);
        if (!result.ok) expect(result.errorParam).toBe(name);
      }),
      RUNS,
    );
  });

  it('rejects NaN and non-finite values and names the offending parameter', () => {
    fc.assert(
      fc.property(paramNameArb, rangeArb, nonFiniteArb, (name, range, value) => {
        const result = validateNumeric(value, range, name);
        expect(result.ok).toBe(false);
        if (!result.ok) expect(result.errorParam).toBe(name);
      }),
      RUNS,
    );
  });

  it('rejects a non-integer value when the range requires an integer', () => {
    // Constrain to integer ranges with room for a fractional value strictly inside.
    const wideIntRange = intRangeArb.filter((r) => r.max - r.min >= 1);
    fc.assert(
      fc.property(paramNameArb, wideIntRange, (name, range) => {
        const fractional = range.min + 0.5; // strictly inside, not an integer
        const result = validateNumeric(fractional, range, name);
        expect(result.ok).toBe(false);
        if (!result.ok) expect(result.errorParam).toBe(name);
      }),
      RUNS,
    );
  });

  // A spec of uniquely-named parameters, each with a range and a valid value.
  const specEntriesArb = fc.uniqueArray(
    fc.tuple(
      paramNameArb,
      rangeArb.chain((range) => inRangeArb(range).map((value) => ({ range, value }))),
    ),
    { minLength: 1, maxLength: 6, selector: ([name]) => name },
  );

  it('validateParams accepts a fully valid bag and returns only the spec keys', () => {
    fc.assert(
      fc.property(specEntriesArb, (entries) => {
        const spec: Record<string, NumericRange> = {};
        const params: Record<string, unknown> = { __extra__: 'ignored' };
        const expected: Record<string, number> = {};
        for (const [name, { range, value }] of entries) {
          spec[name] = range;
          params[name] = value;
          expected[name] = value;
        }
        const result = validateParams(params, spec);
        expect(result).toEqual({ ok: true, value: expected });
      }),
      RUNS,
    );
  });

  it('validateParams rejects an invalid update, names the parameter, and retains the last valid set', () => {
    const invalidValueArb = fc.oneof(nonNumberArb, nonFiniteArb);
    fc.assert(
      fc.property(
        specEntriesArb,
        fc.nat(),
        invalidValueArb,
        (entries, idxSeed, invalidValue) => {
          const spec: Record<string, NumericRange> = {};
          const lastValid: Record<string, number> = {};
          for (const [name, { range, value }] of entries) {
            spec[name] = range;
            lastValid[name] = value;
          }

          const names = Object.keys(spec);
          const target = names[idxSeed % names.length];

          // The trader attempts to update one parameter to an invalid value.
          const attempted: Record<string, unknown> = { ...lastValid, [target]: invalidValue };
          const snapshot = { ...attempted };

          const result = validateParams(attempted, spec);

          // Rejected, identifying exactly the offending parameter (it is the
          // only invalid one, so the first short-circuit failure is on it).
          expect(result.ok).toBe(false);
          if (!result.ok) expect(result.errorParam).toBe(target);

          // The caller would keep `lastValid` since the update was rejected;
          // validation itself is pure and must not mutate the attempted bag.
          expect(attempted).toEqual(snapshot);
        },
      ),
      RUNS,
    );
  });
});
