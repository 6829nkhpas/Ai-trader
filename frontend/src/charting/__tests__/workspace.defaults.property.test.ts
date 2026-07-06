// Feature: professional-charting-suite, Property 34
//
// Property-based test for Property 34: "Absent persisted workspace yields
// defaults" (Validates Requirements 1.4, 4.11, 11.3).
//
// For any symbol with no (or malformed) persisted workspace, deserializing the
// stored blob yields the default workspace: a candlestick chart, no chart-type
// params, an empty active-indicator list, zero drawings, and no extra panes.
//
// `deserializeWorkspace(raw)` is PURE and total: any malformed/absent/
// wrong-shaped input — a non-string, an empty string, an unparseable string,
// a parsed non-object, or an object whose `version` is not `1` — must resolve
// to a value deep-equal to DEFAULT_WORKSPACE. We generate arbitraries across
// all of these malformed-input classes and assert the result equals the
// defaults.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { deserializeWorkspace, DEFAULT_WORKSPACE } from '@/charting/workspace';

const RUNS = 100;

/**
 * Arbitrary inputs that represent an "absent" or malformed persisted workspace.
 * Each class below must be treated as "nothing valid was stored":
 *  - non-string values (undefined, null, numbers, booleans, objects, arrays)
 *  - the empty string
 *  - non-empty strings that are not valid JSON
 *  - strings that parse to JSON but are not plain objects (numbers, arrays,
 *    booleans, null)
 *  - strings that parse to an object whose `version` is not the literal `1`
 */
const absentWorkspace = (): fc.Arbitrary<unknown> =>
  fc.oneof(
    // Non-string raw values.
    fc.constant(undefined),
    fc.constant(null),
    fc.double({ noNaN: true }),
    fc.boolean(),
    fc.object(),
    fc.array(fc.anything()),
    // Empty string.
    fc.constant(''),
    // Non-empty strings that are not valid JSON.
    fc
      .string({ minLength: 1 })
      .filter((s) => {
        try {
          JSON.parse(s);
          return false;
        } catch {
          return true;
        }
      }),
    // Valid JSON strings whose parsed value is not a plain object.
    fc
      .oneof(
        fc.double({ noNaN: true }),
        fc.boolean(),
        fc.constant(null),
        fc.array(fc.anything()),
      )
      .map((v) => JSON.stringify(v)),
    // Valid JSON object strings whose `version` is not 1.
    fc
      .record({
        version: fc
          .anything()
          .filter((v) => v !== 1),
        chartType: fc.constantFrom('candlestick', 'line', 'bogus'),
        drawings: fc.array(fc.anything()),
      })
      .map((o) => JSON.stringify(o)),
  );

describe('Property 34: absent persisted workspace yields defaults', () => {
  it('returns the default workspace for any absent/malformed input', () => {
    fc.assert(
      fc.property(absentWorkspace(), (raw) => {
        expect(deserializeWorkspace(raw)).toEqual(DEFAULT_WORKSPACE);
      }),
      { numRuns: RUNS },
    );
  });
});
