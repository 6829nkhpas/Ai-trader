// Feature: fno-frontend-section, Property 3
//
// Property 3: Analytic marker levels surface exactly when non-null.
//
// "For any analytics result, each marker level — OI-profile max pain, OI-wall
//  support, OI-wall resistance, and IV-skew ATM strike — is present in the
//  produced model exactly when its source analytic value is non-null and equals
//  that value, and is absent (no fabricated level) when the source is `null`."
//
// Validates: Requirements 3.2, 3.3, 4.3
//
// The OI-profile markers (`maxPain`, `support`, `resistance`) come directly from
// `buildOiProfile`, which mirrors `analytics.max_pain`, `analytics.oi_walls.support`,
// and `analytics.oi_walls.resistance` through `finiteOrNull` — present (and equal)
// exactly when the source is a finite number, absent (`null`) when the source is
// `null` (or any non-finite/garbage value, so no fabricated level is surfaced).
//
// The IV-skew ATM marker (`atmStrike`) is NOT a verbatim analytics field — there
// is no `atm_strike` leaf in the payload. The implementer derived it as the chain
// strike NEAREST to `analytics.spot` (the standard ATM definition). So this test
// asserts `atmStrike` consistent with that implementation: it is present (a real
// chain strike) exactly when `analytics.spot` is non-null AND at least one valid
// chain strike exists, equals the nearest strike to spot (ties → lower strike),
// and is absent (`null`) when spot is `null`/non-finite or no strike exists.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  buildOiProfile,
  buildIvSkew,
  type FnoPayload,
  type FnoChainRow,
  type NaOr,
} from '@/components/fno/viewModel';

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/**
 * A source analytic leaf: either an explicit `null` (the N/A sentinel), a
 * finite number, or a non-finite "garbage" value (NaN / ±Infinity) that the
 * selectors must also treat as "no level" so a fabricated marker never leaks.
 */
function analyticLeafArb(): fc.Arbitrary<unknown> {
  return fc.oneof(
    { weight: 3, arbitrary: fc.constant(null) },
    {
      weight: 5,
      arbitrary: fc.double({ min: -1e9, max: 1e9, noNaN: true, noDefaultInfinity: true }),
    },
    { weight: 1, arbitrary: fc.constantFrom(NaN, Infinity, -Infinity) },
  );
}

/** A finite strike value (the kind a real chain row carries). */
function strikeArb(): fc.Arbitrary<number> {
  return fc.double({ min: 1000, max: 60000, noNaN: true, noDefaultInfinity: true });
}

/** One chain row with an arbitrary (possibly null/garbage) strike + OI/price/iv. */
function chainRowArb(): fc.Arbitrary<FnoChainRow> {
  return fc.record({
    strike: strikeArb(),
    ce_oi: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
    pe_oi: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
    ce_price: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
    pe_price: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
    iv: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
  });
}

/** A full payload whose marker sources and chain/spot vary across the space. */
function payloadArb(): fc.Arbitrary<FnoPayload> {
  return fc.record({
    underlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
    expiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
    snapshot_ts: fc.integer({ min: 0, max: 2_000_000_000_000 }),
    market_status: fc.constantFrom('open', 'closed') as fc.Arbitrary<'open' | 'closed'>,
    chain: fc.array(chainRowArb(), { maxLength: 12 }),
    analytics: fc.record({
      spot: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
      pcr_oi: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
      pcr_volume: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
      max_pain: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
      oi_buildup: fc.record({
        call: fc.constantFrom(null, 'short_buildup', 'long_buildup') as fc.Arbitrary<NaOr<string>>,
        put: fc.constantFrom(null, 'long_unwinding', 'short_covering') as fc.Arbitrary<NaOr<string>>,
      }),
      iv_skew: fc.record({
        put_minus_call: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
        slope: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
        atm_iv: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
      }),
      oi_walls: fc.record({
        support: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
        resistance: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
      }),
      futures_basis: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
    }),
    bias: fc.constant({}),
  }) as fc.Arbitrary<FnoPayload>;
}

// ---------------------------------------------------------------------------
// Oracles (independent re-derivation of the expected marker semantics)
// ---------------------------------------------------------------------------

/** "non-null" in the selector's sense: a finite number survives, all else is N/A. */
function expectedLevel(source: unknown): NaOr<number> {
  return typeof source === 'number' && Number.isFinite(source) ? source : null;
}

/** Independent oracle for the ATM strike: nearest chain strike to spot, ties → lower. */
function expectedAtmStrike(payload: FnoPayload): NaOr<number> {
  const spot = expectedLevel(payload.analytics?.spot);
  if (spot === null) return null;

  const validStrikes = (Array.isArray(payload.chain) ? payload.chain : [])
    .filter((r) => r != null && typeof r.strike === 'number' && Number.isFinite(r.strike))
    .map((r) => r.strike);
  if (validStrikes.length === 0) return null;

  let nearest = validStrikes[0];
  let best = Math.abs(nearest - spot);
  for (const s of validStrikes) {
    const d = Math.abs(s - spot);
    if (d < best || (d === best && s < nearest)) {
      nearest = s;
      best = d;
    }
  }
  return nearest;
}

// ---------------------------------------------------------------------------
// Property 3
// ---------------------------------------------------------------------------

describe('Property 3: analytic marker levels surface exactly when non-null', () => {
  it('maxPain surfaces exactly when analytics.max_pain is non-null and equals it', () => {
    fc.assert(
      fc.property(payloadArb(), (payload) => {
        const { maxPain } = buildOiProfile(payload);
        const expected = expectedLevel(payload.analytics?.max_pain);

        expect(maxPain).toBe(expected);
        // presence iff source non-null; absence (null) is never a fabricated level
        expect(maxPain === null).toBe(expected === null);
      }),
      { numRuns: 200 },
    );
  });

  it('OI-wall support surfaces exactly when oi_walls.support is non-null and equals it', () => {
    fc.assert(
      fc.property(payloadArb(), (payload) => {
        const { support } = buildOiProfile(payload);
        const expected = expectedLevel(payload.analytics?.oi_walls?.support);

        expect(support).toBe(expected);
        expect(support === null).toBe(expected === null);
      }),
      { numRuns: 200 },
    );
  });

  it('OI-wall resistance surfaces exactly when oi_walls.resistance is non-null and equals it', () => {
    fc.assert(
      fc.property(payloadArb(), (payload) => {
        const { resistance } = buildOiProfile(payload);
        const expected = expectedLevel(payload.analytics?.oi_walls?.resistance);

        expect(resistance).toBe(expected);
        expect(resistance === null).toBe(expected === null);
      }),
      { numRuns: 200 },
    );
  });

  it('IV-skew atmStrike surfaces a real chain strike exactly when spot is non-null and a strike exists', () => {
    fc.assert(
      fc.property(payloadArb(), (payload) => {
        const { atmStrike } = buildIvSkew(payload);
        const expected = expectedAtmStrike(payload);

        // Equals the nearest chain strike to spot (ties → lower strike).
        expect(atmStrike).toBe(expected);

        const spotPresent = expectedLevel(payload.analytics?.spot) !== null;
        const hasStrike = (Array.isArray(payload.chain) ? payload.chain : []).some(
          (r) => r != null && typeof r.strike === 'number' && Number.isFinite(r.strike),
        );

        // Present exactly when spot is non-null AND at least one valid strike exists.
        expect(atmStrike !== null).toBe(spotPresent && hasStrike);

        // When present, it is one of the snapshot's actual strikes (never fabricated).
        if (atmStrike !== null) {
          expect(payload.chain.map((r) => r.strike)).toContain(atmStrike);
        }
      }),
      { numRuns: 200 },
    );
  });
});
