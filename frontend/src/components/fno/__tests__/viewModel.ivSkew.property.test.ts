// Feature: fno-frontend-section, Property 2
//
// Property 2: IV skew excludes null-IV strikes and goes unavailable when empty.
//
// "For any chain snapshot payload, `buildIvSkew` emits a point for exactly the
//  strikes whose IV is finite (each with that strike's IV), excludes every
//  strike whose IV is `null`, and yields an empty point set — which the IV view
//  renders as an `Unavailable_State` — precisely when no strike has a finite IV;
//  adding or removing null-IV strikes never changes the emitted points."
//
// Validates: Requirements 4.1, 4.2, 4.4
//
// `buildIvSkew` is a pure, total selector over the IPC chain snapshot. This
// property pins its IV-by-strike behavior: it plots the implied volatility
// across strikes (R4.1), omits strikes whose IV is null / NaN / ±Infinity /
// missing rather than fabricating a value (R4.2), and produces an empty point
// set — the caller's Unavailable_State signal — exactly when no strike has a
// finite IV (R4.4). We generate chains with a mix of finite-IV and non-finite-IV
// strikes (over unique strikes so the emitted set is unambiguous) and assert all
// four facets, including that injecting/removing null-IV strikes is inert.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { buildIvSkew, type FnoChainRow, type FnoPayload } from '@/components/fno/viewModel';

// The IV values that buildIvSkew MUST drop (treated as N/A, never plotted).
const NON_FINITE_IVS = [null, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY];

/** A chain row carrying a given strike and iv; the OI/price fields are unread by
 *  buildIvSkew, so they are filled with arbitrary placeholders. */
function row(strike: number, iv: number | null): FnoChainRow {
  return { strike, ce_oi: null, pe_oi: null, ce_price: null, pe_price: null, iv };
}

/** Wrap a chain into a minimal FnoPayload (only `chain` and `analytics.spot`
 *  are read by buildIvSkew). */
function payload(chain: FnoChainRow[], spot: number | null = null): FnoPayload {
  return {
    underlying: 'NIFTY 50',
    expiry: '2024-12-26',
    snapshot_ts: 1_734_511_200_000,
    market_status: 'open',
    chain,
    // Only `spot` is consulted (for atmStrike, not points); the rest is inert.
    analytics: {
      spot,
      pcr_oi: null,
      pcr_volume: null,
      max_pain: null,
      oi_buildup: { call: null, put: null },
      iv_skew: { put_minus_call: null, slope: null, atm_iv: null },
      oi_walls: { support: null, resistance: null },
      futures_basis: null,
    },
    bias: {},
  };
}

/** A finite IV value (the kind buildIvSkew MUST keep). */
const finiteIvArb = fc
  .double({ min: -1e6, max: 1e6, noNaN: true, noDefaultInfinity: true })
  .filter((v) => Number.isFinite(v));

/** A non-finite IV value (null / NaN / ±Infinity) that MUST be dropped. */
const nonFiniteIvArb = fc.constantFrom(...NON_FINITE_IVS);

/**
 * Generate a chain over UNIQUE strikes, partitioned into:
 *  - `finite`: rows whose iv is finite (must appear as points), and
 *  - `nullish`: rows whose iv is non-finite (must be dropped),
 * plus the rows in a shuffled order (input order should not matter).
 */
function partitionedChainArb() {
  return fc
    .uniqueArray(fc.double({ min: -50_000, max: 50_000, noNaN: true, noDefaultInfinity: true }), {
      minLength: 0,
      maxLength: 40,
      selector: (s) => s,
    })
    .chain((strikes) =>
      fc
        .tuple(
          // For each strike decide whether it carries a finite or non-finite IV.
          fc.array(fc.boolean(), { minLength: strikes.length, maxLength: strikes.length }),
          fc.array(finiteIvArb, { minLength: strikes.length, maxLength: strikes.length }),
          fc.array(nonFiniteIvArb, { minLength: strikes.length, maxLength: strikes.length }),
        )
        .map(([isFiniteFlags, finiteIvs, nullIvs]) => {
          const finite: FnoChainRow[] = [];
          const nullish: FnoChainRow[] = [];
          strikes.forEach((strike, i) => {
            if (isFiniteFlags[i]) {
              finite.push(row(strike, finiteIvs[i]));
            } else {
              nullish.push(row(strike, nullIvs[i]));
            }
          });
          return { finite, nullish };
        }),
    );
}

/** Expected emitted points: the finite-IV rows, sorted ascending by strike. */
function expectedPoints(finite: FnoChainRow[]) {
  return finite
    .map((r) => ({ strike: r.strike, iv: r.iv as number }))
    .sort((a, b) => a.strike - b.strike);
}

describe('Property 2: IV skew excludes null-IV strikes and goes unavailable when empty', () => {
  it('emits a point for exactly the finite-IV strikes (each with that strike IV), excluding every non-finite IV', () => {
    fc.assert(
      fc.property(partitionedChainArb(), fc.boolean(), ({ finite, nullish }, nullFirst) => {
        // Interleave the finite and nullish rows in an order that does not
        // depend on the partition (input order must not affect the output).
        const chain = nullFirst ? [...nullish, ...finite] : [...finite, ...nullish];

        const model = buildIvSkew(payload(chain));

        // Exactly the finite-IV strikes survive, each carrying its own IV, sorted.
        expect(model.points).toEqual(expectedPoints(finite));

        // Every emitted IV is finite (no NaN / ±Infinity / null leaked through).
        for (const p of model.points) {
          expect(Number.isFinite(p.iv)).toBe(true);
        }

        // No dropped (non-finite-IV) strike appears among the emitted points.
        const emittedStrikes = new Set(model.points.map((p) => p.strike));
        for (const r of nullish) {
          expect(emittedStrikes.has(r.strike)).toBe(false);
        }
      }),
      { numRuns: 200 },
    );
  });

  it('yields empty points precisely when no strike has a finite IV', () => {
    fc.assert(
      fc.property(partitionedChainArb(), ({ finite, nullish }) => {
        const chain = [...finite, ...nullish];
        const model = buildIvSkew(payload(chain));

        // Empty <=> no finite-IV strike exists in the chain.
        expect(model.points.length === 0).toBe(finite.length === 0);
      }),
      { numRuns: 200 },
    );
  });

  it('adding or removing null-IV strikes never changes the emitted points', () => {
    fc.assert(
      fc.property(partitionedChainArb(), ({ finite, nullish }) => {
        // Baseline: only the finite-IV rows.
        const baseline = buildIvSkew(payload([...finite]));
        // With the null-IV rows injected (in either order).
        const withNulls = buildIvSkew(payload([...nullish, ...finite]));
        const withNullsAlt = buildIvSkew(payload([...finite, ...nullish]));

        expect(withNulls.points).toEqual(baseline.points);
        expect(withNullsAlt.points).toEqual(baseline.points);
      }),
      { numRuns: 200 },
    );
  });
});
