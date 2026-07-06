// Feature: professional-charting-suite, Property 9
//
// Property-based test for Property 9: "Indicator search returns exactly the
// case-insensitive name matches" (Validates Requirement 4.2).
//
// For any search query and the fixed set of available indicators,
// searchIndicators(query) must return EXACTLY the set of indicators whose name
// contains the query case-insensitively — every match included, no non-match
// included. A blank (empty or whitespace-only) query returns the full list.
//
// The test compares searchIndicators against an independent reference filter
// over listIndicators() so it asserts behaviour, not implementation.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { listIndicators, searchIndicators } from '@/charting/engines';
import type { IndicatorDef } from '@/charting/engines';

const RUNS = 100;

/** Reference oracle: indicators whose name contains `query` case-insensitively. */
function expectedMatches(query: string): IndicatorDef[] {
  const q = query.trim().toLowerCase();
  if (q === '') return listIndicators();
  return listIndicators().filter((def) => def.name.toLowerCase().includes(q));
}

/** Stable identity for an indicator definition. */
const idOf = (def: IndicatorDef): string => def.id;

/**
 * Arbitrary queries spanning realistic and adversarial inputs:
 *  - substrings drawn from real indicator names (with random casing),
 *  - arbitrary unicode strings,
 *  - empty / whitespace-only strings (must return everything).
 */
const queryArb: fc.Arbitrary<string> = (() => {
  const names = listIndicators().map((d) => d.name);

  // A substring of a real name, re-cased randomly to exercise case-insensitivity.
  const nameSubstring = fc
    .constantFrom(...names)
    .chain((name) =>
      fc
        .tuple(
          fc.nat({ max: name.length }),
          fc.nat({ max: name.length }),
        )
        .map(([a, b]) => name.slice(Math.min(a, b), Math.max(a, b))),
    )
    .chain((sub) =>
      fc
        .array(fc.boolean(), { minLength: sub.length, maxLength: sub.length })
        .map((flips) =>
          sub
            .split('')
            .map((ch, i) => (flips[i] ? ch.toUpperCase() : ch.toLowerCase()))
            .join(''),
        ),
    );

  const whitespace = fc
    .array(fc.constantFrom(' ', '\t', '\n'), { minLength: 0, maxLength: 5 })
    .map((parts) => parts.join(''));

  return fc.oneof(
    { weight: 5, arbitrary: nameSubstring },
    { weight: 3, arbitrary: fc.string() },
    { weight: 2, arbitrary: whitespace },
  );
})();

describe('Property 9: indicator search returns exactly the case-insensitive name matches', () => {
  it('searchIndicators(query) equals the reference case-insensitive name filter', () => {
    fc.assert(
      fc.property(queryArb, (query) => {
        const actual = searchIndicators(query).map(idOf).sort();
        const expected = expectedMatches(query).map(idOf).sort();

        // Exact set equality: every match present, no non-match present.
        expect(actual).toEqual(expected);

        // Strengthen with the universal characterisation directly against the
        // full indicator set: an indicator is in the result iff its name
        // contains the query (case-insensitive), for non-blank queries.
        const q = query.trim().toLowerCase();
        const resultIds = new Set(actual);
        for (const def of listIndicators()) {
          const nameContains = def.name.toLowerCase().includes(q);
          const present = resultIds.has(def.id);
          if (q === '') {
            expect(present, `blank query must include "${def.id}"`).toBe(true);
          } else {
            expect(
              present,
              `"${def.id}" presence (${present}) must match name-contains "${query}" (${nameContains})`,
            ).toBe(nameContains);
          }
        }
      }),
      { numRuns: RUNS },
    );
  });

  it('blank and whitespace-only queries return the complete indicator list', () => {
    const all = listIndicators().map(idOf).sort();
    for (const blank of ['', ' ', '   ', '\t', '\n', ' \t \n ']) {
      expect(searchIndicators(blank).map(idOf).sort()).toEqual(all);
    }
  });
});
