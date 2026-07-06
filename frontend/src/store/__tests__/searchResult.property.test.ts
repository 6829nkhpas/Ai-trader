// Feature: terminal-ux-overhaul
//
// Property 8: F&O search results are well-typed and distinguishable
//   "For any query, every returned result is either a well-formed EQ or FNO
//    record, and FNO results carry underlying/expiry/type so they are
//    distinguishable from equities."
//
//   Validates: Requirements 3.1, 3.2, 3.4
//
// This is a frontend contract test for the pure validators/type-guards in
// `@/types/searchResult` that the search UI relies on. We generate arbitrary
// well-formed EQ and FNO records (and some malformed objects) with fast-check
// and assert:
//   - every well-formed EQ/FNO passes `isWellFormedSearchResult` (R3.1)
//   - every well-formed FNO exposes non-empty underlying, expiry and a valid
//     optionType, making it distinguishable from an equity (R3.2, R3.4)
//   - EQ and FNO are never confused — the `kind` tag and FNO-only fields
//     disambiguate them (R3.4)
//   - malformed objects are rejected (no fabricated/ill-typed result leaks)

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  isWellFormedSearchResult,
  isDistinguishableFno,
  isEquityResult,
  isFnoResult,
  isOptionType,
  resultSymbol,
  OPTION_TYPES,
  type SearchResult,
  type EquityResult,
  type FnoResult,
  type OptionType,
} from '@/types/searchResult';

// ── Generators ──────────────────────────────────────────────────────────────

const nonEmptyString = fc.string({ minLength: 1, maxLength: 20 });
const optionType: fc.Arbitrary<OptionType> = fc.constantFrom(...OPTION_TYPES);

/** A well-formed equity result. `name` may be empty (display falls back). */
const eqArb: fc.Arbitrary<EquityResult> = fc.record({
  kind: fc.constant('EQ' as const),
  symbol: nonEmptyString,
  name: fc.string({ maxLength: 30 }),
  exchange: nonEmptyString,
});

/** A well-formed FNO result; strike is a finite number for options or null. */
const fnoArb: fc.Arbitrary<FnoResult> = fc.record({
  kind: fc.constant('FNO' as const),
  tradingsymbol: nonEmptyString,
  underlying: nonEmptyString,
  expiry: nonEmptyString,
  strike: fc.option(fc.double({ min: 0, max: 100000, noNaN: true }), { nil: null }),
  optionType,
});

const searchResultArb: fc.Arbitrary<SearchResult> = fc.oneof(eqArb, fnoArb);

/** Objects that should be rejected by the well-formed guard. */
const malformedArb: fc.Arbitrary<unknown> = fc.oneof(
  // Wrong / missing discriminant
  fc.record({ kind: fc.constant('STOCK'), symbol: nonEmptyString }),
  fc.record({ symbol: nonEmptyString, name: nonEmptyString }),
  // EQ missing or empty required fields
  fc.record({ kind: fc.constant('EQ'), symbol: fc.constant(''), name: nonEmptyString, exchange: nonEmptyString }),
  fc.record({ kind: fc.constant('EQ'), name: nonEmptyString, exchange: nonEmptyString }),
  // FNO with invalid optionType
  fc.record({
    kind: fc.constant('FNO'),
    tradingsymbol: nonEmptyString,
    underlying: nonEmptyString,
    expiry: nonEmptyString,
    strike: fc.constant(null),
    optionType: fc.constant('XX'),
  }),
  // FNO missing underlying/expiry
  fc.record({
    kind: fc.constant('FNO'),
    tradingsymbol: nonEmptyString,
    strike: fc.constant(null),
    optionType,
  }),
  // FNO with non-numeric, non-null strike
  fc.record({
    kind: fc.constant('FNO'),
    tradingsymbol: nonEmptyString,
    underlying: nonEmptyString,
    expiry: nonEmptyString,
    strike: fc.constant('100'),
    optionType,
  }),
  // Primitives / nullish
  fc.constant(null),
  fc.constant(undefined),
  fc.string(),
  fc.integer(),
  fc.boolean(),
);

// ── Property 8 ────────────────────────────────────────────────────────────────

describe('Property 8: F&O search results are well-typed and distinguishable', () => {
  it('every well-formed EQ or FNO record passes the guard (R3.1)', () => {
    fc.assert(
      fc.property(searchResultArb, (r) => {
        expect(isWellFormedSearchResult(r)).toBe(true);
      }),
      { numRuns: 300 },
    );
  });

  it('every FNO result carries non-empty underlying/expiry and a valid type, so it is distinguishable (R3.2, R3.4)', () => {
    fc.assert(
      fc.property(fnoArb, (r) => {
        expect(isDistinguishableFno(r)).toBe(true);
        expect(r.underlying.length).toBeGreaterThan(0);
        expect(r.expiry.length).toBeGreaterThan(0);
        expect(isOptionType(r.optionType)).toBe(true);
        // Strike is a finite number (options) or null (futures) — never NaN.
        expect(r.strike === null || Number.isFinite(r.strike)).toBe(true);
      }),
      { numRuns: 300 },
    );
  });

  it('EQ and FNO are never confused — the kind tag and FNO-only fields disambiguate (R3.4)', () => {
    fc.assert(
      fc.property(searchResultArb, (r) => {
        // Exactly one branch matches.
        expect(isEquityResult(r)).toBe(r.kind === 'EQ');
        expect(isFnoResult(r)).toBe(r.kind === 'FNO');
        expect(isEquityResult(r) && isFnoResult(r)).toBe(false);
        // Only FNO results are distinguishable-as-FNO; equities never are.
        expect(isDistinguishableFno(r)).toBe(r.kind === 'FNO');
        // A routable chart symbol exists for either kind.
        expect(resultSymbol(r).length).toBeGreaterThan(0);
      }),
      { numRuns: 300 },
    );
  });

  it('rejects malformed objects (no ill-typed/fabricated result leaks)', () => {
    fc.assert(
      fc.property(malformedArb, (x) => {
        expect(isWellFormedSearchResult(x)).toBe(false);
      }),
      { numRuns: 300 },
    );
  });

  it('a mixed result list keeps EQ and FNO partitions disjoint and exhaustive', () => {
    fc.assert(
      fc.property(fc.array(searchResultArb, { maxLength: 30 }), (results) => {
        // All results are well-formed.
        expect(results.every(isWellFormedSearchResult)).toBe(true);
        const eq = results.filter(isEquityResult);
        const fno = results.filter(isFnoResult);
        // Partition is exhaustive (every result lands in exactly one bucket).
        expect(eq.length + fno.length).toBe(results.length);
        // Every FNO in the list is distinguishable from an equity.
        expect(fno.every(isDistinguishableFno)).toBe(true);
      }),
      { numRuns: 200 },
    );
  });
});
