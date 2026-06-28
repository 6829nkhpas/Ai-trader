// Feature: fno-frontend-section, Property 11
//
// Property 11: The underlying selector is bounded to configured index
// underlyings.
//
// "For any `FnoChains` payload, the set of options offered by the
//  `Underlying_Selector` is a subset of the configured index underlyings
//  established by F1 (no non-index or unconfigured underlying is ever offered),
//  and the `Expiry_Selector` options are exactly the available expiries for the
//  selected underlying."
//
// Validates: Requirements 2.2, 9.3
//
// The `Underlying_Selector` / `Expiry_Selector` option lists are produced by
// the pure, total `deriveUnderlyingOptions` / `deriveExpiryOptions` selectors
// (extracted from `FnoSection` verbatim — see ../selectors.ts). Testing those
// pure derivations is the most robust way to pin the bounding guarantee without
// mounting the React tree.
//
// Documented input space (the store invariant the component upholds):
//   - `fnoUnderlying` defaults to the configured `'NIFTY 50'` and is only ever
//     reassigned from the very list the selector offers, so the active
//     selection is always one of the configured index underlyings. The
//     generator therefore draws `selectedUnderlying` from `chains.underlyings`.
//   - A separate case exercises the defensive `chains === null` / unconfigured
//     path to confirm the selector still never invents a configured underlying.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { deriveExpiryOptions, deriveUnderlyingOptions } from '@/components/fno/selectors';
import type { FnoChains } from '@/components/fno/viewModel';

const NUM_RUNS = 200;

/**
 * The configured index underlyings established by F1 (the realistic pool) plus
 * a few arbitrary-but-distinct extras, so the property holds for any bounded
 * configured set rather than only the two production indexes.
 */
const underlyingNameArb = fc.oneof(
  { weight: 3, arbitrary: fc.constantFrom('NIFTY 50', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY') },
  { weight: 1, arbitrary: fc.string({ minLength: 1, maxLength: 12 }) },
);

/** An expiry string in the documented `YYYY-MM-DD` shape (plus a few arbitrary). */
const expiryArb = fc.oneof(
  { weight: 3, arbitrary: fc.constantFrom('2024-12-26', '2025-01-30', '2025-02-27', '2025-03-27') },
  { weight: 1, arbitrary: fc.string({ minLength: 1, maxLength: 10 }) },
);

/**
 * A well-formed `FnoChains` payload: a non-empty set of unique configured
 * underlyings, each mapped to its own (possibly empty) unique expiry list, as
 * the bridge's `fno_list_chains` publishes it.
 */
const fnoChainsArb: fc.Arbitrary<FnoChains> = fc
  .uniqueArray(underlyingNameArb, { minLength: 1, maxLength: 5 })
  .chain((underlyings) =>
    fc
      .tuple(
        ...underlyings.map(() =>
          fc.uniqueArray(expiryArb, { minLength: 0, maxLength: 6 }),
        ),
      )
      .map((expiryLists) => {
        const expiries_by_underlying: Record<string, string[]> = {};
        underlyings.forEach((u, i) => {
          expiries_by_underlying[u] = expiryLists[i];
        });
        return { underlyings, expiries_by_underlying } satisfies FnoChains;
      }),
  );

describe('Property 11: the underlying selector is bounded to configured underlyings', () => {
  it('underlying options are a subset of the configured underlyings (no unconfigured underlying offered)', () => {
    fc.assert(
      fc.property(
        fnoChainsArb,
        fc.nat(),
        (chains, pick) => {
          // Store invariant: the active selection is one of the configured
          // underlyings (default NIFTY 50, only ever set from this list).
          const selected = chains.underlyings[pick % chains.underlyings.length];

          const options = deriveUnderlyingOptions(chains, selected);
          const configured = new Set(chains.underlyings);

          // Every offered option is a configured index underlying.
          for (const option of options) {
            expect(configured.has(option)).toBe(true);
          }
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });

  it('offers exactly the configured underlyings when the selection is configured (complete, no drops, no dupes)', () => {
    fc.assert(
      fc.property(
        fnoChainsArb,
        fc.nat(),
        (chains, pick) => {
          const selected = chains.underlyings[pick % chains.underlyings.length];

          const options = deriveUnderlyingOptions(chains, selected);

          // Exactly the configured set, in the published order (no synthesized
          // entries, no dropped entries, no duplicates).
          expect(options).toEqual(chains.underlyings);
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });

  it('expiry options are exactly the available expiries for the selected underlying', () => {
    fc.assert(
      fc.property(
        fnoChainsArb,
        fc.nat(),
        (chains, pick) => {
          const selected = chains.underlyings[pick % chains.underlyings.length];

          const options = deriveExpiryOptions(chains, selected);

          // Exactly the bridge-published expiry list for that underlying —
          // never synthesized, never dropped.
          expect(options).toEqual(chains.expiries_by_underlying[selected]);
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });

  it('an unselected/unknown underlying yields an empty expiry list (never synthesized)', () => {
    fc.assert(
      fc.property(
        fnoChainsArb,
        fc.string({ minLength: 1, maxLength: 12 }),
        (chains, candidate) => {
          // A candidate that is not a configured underlying has no expiries.
          fc.pre(!chains.underlyings.includes(candidate));
          expect(deriveExpiryOptions(chains, candidate)).toEqual([]);
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });

  it('the defensive path never invents a configured underlying when chains are absent/unconfigured', () => {
    fc.assert(
      fc.property(
        // chains is either null (not yet resolved) or a configured set that does
        // NOT contain the active selection.
        fc.oneof(fc.constant(null), fnoChainsArb),
        fc.string({ minLength: 1, maxLength: 12 }),
        (chains, selected) => {
          if (chains !== null) {
            fc.pre(!chains.underlyings.includes(selected));
          }

          const options = deriveUnderlyingOptions(chains, selected);
          const configured = new Set(chains?.underlyings ?? []);

          // The active selection is kept selectable, and every OTHER offered
          // option is a configured underlying — the data never introduces an
          // unconfigured underlying.
          for (const option of options) {
            if (option === selected) continue;
            expect(configured.has(option)).toBe(true);
          }

          // The active selection is always present so it can never dangle.
          if (selected) {
            expect(options).toContain(selected);
          }
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });
});
