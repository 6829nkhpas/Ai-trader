// Feature: fno-frontend-section, Property 1
//
// Property 1: OI profile renders exactly the snapshot's strikes.
//
// "For any chain snapshot payload, `buildOiProfile` produces one point per
//  strike present in the snapshot — the multiset of point strikes equals the
//  snapshot's strike set with no added or synthesized strikes — and each
//  point's `callOi`/`putOi` equals that strike's `ce_oi`/`pe_oi` (preserving
//  `null` as `null`, never as `0`)."
//
// Validates: Requirements 3.1, 3.5
//
// `buildOiProfile` is a pure, total selector over the IPC payload. This file
// exercises ONLY Property 1 (strike fidelity + OI pass-through), with a smart
// generator that constrains chains to the documented input space (finite
// strikes; each `ce_oi`/`pe_oi` is `finite | null`).

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { buildOiProfile, type FnoChainRow, type FnoPayload } from '@/components/fno/viewModel';

const NUM_RUNS = 200;

/** A finite OI value: a non-negative integer (open interest is a count). */
const oiValueArb = fc.integer({ min: 0, max: 50_000_000 });

/** OI leaf in the documented input space: `finite | null` (never fabricated). */
const oiOrNullArb: fc.Arbitrary<number | null> = fc.oneof(
  { weight: 3, arbitrary: oiValueArb },
  { weight: 1, arbitrary: fc.constant<null>(null) },
);

/** A finite, optional price leaf (not read by buildOiProfile but kept realistic). */
const priceOrNullArb: fc.Arbitrary<number | null> = fc.oneof(
  { weight: 3, arbitrary: fc.double({ min: 0, max: 100_000, noNaN: true, noDefaultInfinity: true }) },
  { weight: 1, arbitrary: fc.constant<null>(null) },
);

/** An optional IV leaf (not read by buildOiProfile). */
const ivOrNullArb: fc.Arbitrary<number | null> = fc.oneof(
  { weight: 3, arbitrary: fc.double({ min: 0, max: 5, noNaN: true, noDefaultInfinity: true }) },
  { weight: 1, arbitrary: fc.constant<null>(null) },
);

/**
 * A chain of rows with UNIQUE finite strikes (a real option chain carries one
 * row per strike), each with `ce_oi`/`pe_oi` drawn from `finite | null`. We key
 * uniqueness on `strike` so we can assert the exact per-strike OI mapping.
 */
const chainArb: fc.Arbitrary<FnoChainRow[]> = fc
  .uniqueArray(
    fc.record({
      strike: fc.integer({ min: 1, max: 200_000 }),
      ce_oi: oiOrNullArb,
      pe_oi: oiOrNullArb,
      ce_price: priceOrNullArb,
      pe_price: priceOrNullArb,
      iv: ivOrNullArb,
    }),
    { selector: (row) => row.strike, maxLength: 60 },
  );

/** A minimal-but-complete payload wrapping the generated chain. */
function payloadArb(): fc.Arbitrary<FnoPayload> {
  return fc.record({
    underlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
    expiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
    snapshot_ts: fc.integer({ min: 1, max: 2_000_000_000_000 }),
    market_status: fc.constantFrom('open' as const, 'closed' as const),
    chain: chainArb,
    analytics: fc.record({
      spot: fc.oneof(priceOrNullArb),
      pcr_oi: priceOrNullArb,
      pcr_volume: priceOrNullArb,
      max_pain: priceOrNullArb,
      oi_buildup: fc.record({ call: fc.constant(null), put: fc.constant(null) }),
      iv_skew: fc.record({
        put_minus_call: priceOrNullArb,
        slope: priceOrNullArb,
        atm_iv: priceOrNullArb,
      }),
      oi_walls: fc.record({ support: priceOrNullArb, resistance: priceOrNullArb }),
      futures_basis: priceOrNullArb,
    }),
    bias: fc.record({}),
  }) as fc.Arbitrary<FnoPayload>;
}

describe('Property 1: OI profile renders exactly the snapshot strikes', () => {
  it('emits one point per snapshot strike — multiset equality, no synthesized strikes', () => {
    fc.assert(
      fc.property(payloadArb(), (payload) => {
        const model = buildOiProfile(payload);

        // One point per chain row: same count (no added/dropped strikes).
        expect(model.points.length).toBe(payload.chain.length);

        // Multiset of point strikes equals the snapshot's strike set: compare
        // the two sorted strike lists element-by-element.
        const inputStrikes = payload.chain.map((r) => r.strike).sort((a, b) => a - b);
        const outputStrikes = model.points.map((p) => p.strike);
        expect(outputStrikes).toEqual(inputStrikes);

        // No synthesized strikes: every output strike exists in the input set.
        const inputStrikeSet = new Set(payload.chain.map((r) => r.strike));
        for (const p of model.points) {
          expect(inputStrikeSet.has(p.strike)).toBe(true);
        }
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('points are sorted strictly ascending by strike', () => {
    fc.assert(
      fc.property(payloadArb(), (payload) => {
        const { points } = buildOiProfile(payload);
        for (let i = 1; i < points.length; i++) {
          expect(points[i].strike).toBeGreaterThan(points[i - 1].strike);
        }
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('callOi/putOi equal the strike ce_oi/pe_oi, preserving null as null (never 0)', () => {
    fc.assert(
      fc.property(payloadArb(), (payload) => {
        const model = buildOiProfile(payload);
        const bySourceStrike = new Map(payload.chain.map((r) => [r.strike, r]));

        for (const point of model.points) {
          const source = bySourceStrike.get(point.strike)!;

          // Exact pass-through of the source OI values.
          expect(point.callOi).toBe(source.ce_oi);
          expect(point.putOi).toBe(source.pe_oi);

          // A null source must remain null — never fabricated as 0.
          if (source.ce_oi === null) {
            expect(point.callOi).toBeNull();
            expect(point.callOi).not.toBe(0);
          }
          if (source.pe_oi === null) {
            expect(point.putOi).toBeNull();
            expect(point.putOi).not.toBe(0);
          }
        }
      }),
      { numRuns: NUM_RUNS },
    );
  });
});
