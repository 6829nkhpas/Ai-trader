// Feature: fno-frontend-section, Property 5
//
// Property 5: Partial analytics pass through finite fields and flag nulls.
//
// "For any analytics result mixing finite and `null` fields, `buildHudModel`
//  passes every finite value through unchanged and marks every `null` field as
//  N/A, and `toFnoViewState` tags the result `partial` (rather than `ready` or
//  `unavailable`) whenever at least one analytic field is `null` while a
//  snapshot exists."
//
// Validates: Requirements 8.2, 8.3
//
// `buildHudModel` and `toFnoViewState` are pure, total selectors. We generate
// analytics-plus-bias payloads whose every headline leaf is independently
// either a finite number / valid label, or one of the "should be N/A" inputs
// (null, omitted, NaN, ±Infinity, garbage type) — so each generated payload
// genuinely MIXES finite and null fields. Every leaf carries its EXPECTED
// normalized result so the test can assert exact pass-through / N/A behavior.
//
// To exercise the `toFnoViewState` tagging half, every payload carries a
// non-empty chain with a finite `snapshot_ts`, which is exactly the condition
// under which a snapshot "exists" (chain.length > 0 AND finite snapshot_ts).
// Under that condition the result must be `partial` when at least one analytic
// field is N/A, and `ready` when every analytic field is present — never
// `unavailable`.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  buildHudModel,
  toFnoViewState,
  type FnoPayload,
  type ChainContext,
  type OptionsBiasState,
} from '@/components/fno/viewModel';

// ---------------------------------------------------------------------------
// Leaf arbitraries: each yields { raw, expected } mirroring the selector's
// normalization contract (finiteOrNull / stringOrNull / biasStateOrNull / ...).
// ---------------------------------------------------------------------------

const BIAS_STATES: OptionsBiasState[] = ['bullish', 'bearish', 'neutral'];
const CHAIN_CONTEXTS: ChainContext[] = ['own-chain', 'broad-market'];

const finiteNumber = fc
  .double({ min: -1e12, max: 1e12, noNaN: true, noDefaultInfinity: true })
  .filter((n) => Number.isFinite(n));

/** A numeric leaf: finite -> itself (incl. genuine 0); null / omitted / NaN /
 *  ±Infinity / non-number garbage -> the explicit N/A sentinel (null). */
function numField(): fc.Arbitrary<{ raw: unknown; expected: number | null }> {
  return fc.oneof(
    { weight: 5, arbitrary: finiteNumber.map((n) => ({ raw: n as unknown, expected: n })) },
    { weight: 1, arbitrary: fc.constant({ raw: 0 as unknown, expected: 0 }) }, // genuine zero passes through
    { weight: 3, arbitrary: fc.constant({ raw: null as unknown, expected: null }) },
    { weight: 2, arbitrary: fc.constant({ raw: undefined as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.constant({ raw: Number.NaN as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.constant({ raw: Number.POSITIVE_INFINITY as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.constant({ raw: Number.NEGATIVE_INFINITY as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.string().map((s) => ({ raw: s as unknown, expected: null })) },
  );
}

/** A string-label leaf (OI buildup): string -> itself; anything else -> null. */
function strField(): fc.Arbitrary<{ raw: unknown; expected: string | null }> {
  return fc.oneof(
    { weight: 4, arbitrary: fc.string().map((s) => ({ raw: s as unknown, expected: s })) },
    { weight: 2, arbitrary: fc.constant({ raw: null as unknown, expected: null }) },
    { weight: 2, arbitrary: fc.constant({ raw: undefined as unknown, expected: null }) },
    { weight: 1, arbitrary: finiteNumber.map((n) => ({ raw: n as unknown, expected: null })) },
  );
}

/** Agent bias state: a valid state -> itself; anything else (incl. omitted) -> null. */
function biasStateField(): fc.Arbitrary<{ raw: unknown; expected: OptionsBiasState | null }> {
  return fc.oneof(
    {
      weight: 4,
      arbitrary: fc.constantFrom(...BIAS_STATES).map((s) => ({ raw: s as unknown, expected: s })),
    },
    { weight: 2, arbitrary: fc.constant({ raw: undefined as unknown, expected: null }) },
    { weight: 2, arbitrary: fc.constant({ raw: null as unknown, expected: null }) },
  );
}

/** Chain context: a valid context -> itself; anything else (incl. omitted) -> null. */
function chainContextField(): fc.Arbitrary<{ raw: unknown; expected: ChainContext | null }> {
  return fc.oneof(
    {
      weight: 4,
      arbitrary: fc.constantFrom(...CHAIN_CONTEXTS).map((c) => ({ raw: c as unknown, expected: c })),
    },
    { weight: 2, arbitrary: fc.constant({ raw: undefined as unknown, expected: null }) },
    { weight: 2, arbitrary: fc.constant({ raw: null as unknown, expected: null }) },
  );
}

/** Driving signals: a plain object -> itself; null / omitted -> null. */
function signalsField(): fc.Arbitrary<{ raw: unknown; expected: Record<string, unknown> | null }> {
  return fc.oneof(
    {
      weight: 3,
      arbitrary: fc
        .dictionary(fc.string(), fc.oneof(finiteNumber, fc.string(), fc.boolean(), fc.constant(null)))
        .map((o) => ({ raw: o as unknown, expected: o as Record<string, unknown> })),
    },
    { weight: 2, arbitrary: fc.constant({ raw: null as unknown, expected: null }) },
    { weight: 2, arbitrary: fc.constant({ raw: undefined as unknown, expected: null }) },
  );
}

/** IV-skew summary: omitted/null -> whole summary is null; object -> nested finite-or-null. */
function ivSkewField(): fc.Arbitrary<{
  raw: unknown;
  expected: { putMinusCall: number | null; slope: number | null; atmIv: number | null } | null;
}> {
  return fc.oneof(
    { weight: 2, arbitrary: fc.constant({ raw: undefined as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.constant({ raw: null as unknown, expected: null }) },
    {
      weight: 4,
      arbitrary: fc
        .record({ pmc: numField(), slope: numField(), atm: numField() })
        .map(({ pmc, slope, atm }) => ({
          raw: { put_minus_call: pmc.raw, slope: slope.raw, atm_iv: atm.raw } as unknown,
          expected: { putMinusCall: pmc.expected, slope: slope.expected, atmIv: atm.expected },
        })),
    },
  );
}

/** A non-empty chain so a snapshot "exists" for toFnoViewState (chain.length > 0). */
function nonEmptyChainArb() {
  return fc.array(
    fc.record({
      strike: fc.double({ min: 1000, max: 60000, noNaN: true, noDefaultInfinity: true }),
      ce_oi: numField().map((f) => f.raw),
      pe_oi: numField().map((f) => f.raw),
      ce_price: numField().map((f) => f.raw),
      pe_price: numField().map((f) => f.raw),
      iv: numField().map((f) => f.raw),
    }),
    { minLength: 1, maxLength: 8 },
  );
}

// ---------------------------------------------------------------------------
// Payload arbitrary: an analytics-plus-bias payload mixing finite and null
// leaves, carrying the expected HUD model and whether ANY analytic is null.
// ---------------------------------------------------------------------------

function payloadArb() {
  return fc
    .record({
      underlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
      expiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
      // finite snapshot_ts so a snapshot is considered to exist (with chain length > 0)
      snapshotTs: fc.integer({ min: 1, max: 2_000_000_000_000 }),
      marketStatus: fc.constantFrom('open', 'closed') as fc.Arbitrary<'open' | 'closed'>,
      chain: nonEmptyChainArb(),
      pcrOi: numField(),
      pcrVolume: numField(),
      maxPain: numField(),
      buildCall: strField(),
      buildPut: strField(),
      support: numField(),
      resistance: numField(),
      futuresBasis: numField(),
      spot: numField(),
      ivSkew: ivSkewField(),
      biasState: biasStateField(),
      chainContext: chainContextField(),
      signals: signalsField(),
    })
    .map((g) => {
      // Assemble the raw IPC payload, omitting fields whose raw is `undefined`
      // so the selector sees a genuinely absent leaf.
      const analytics: Record<string, unknown> = {
        spot: g.spot.raw,
        pcr_oi: g.pcrOi.raw,
        pcr_volume: g.pcrVolume.raw,
        max_pain: g.maxPain.raw,
        oi_buildup: { call: g.buildCall.raw, put: g.buildPut.raw },
        oi_walls: { support: g.support.raw, resistance: g.resistance.raw },
        futures_basis: g.futuresBasis.raw,
      };
      if (g.ivSkew.raw !== undefined) {
        analytics.iv_skew = g.ivSkew.raw;
      }

      const bias: Record<string, unknown> = {};
      if (g.biasState.raw !== undefined) bias.options_bias_state = g.biasState.raw;
      if (g.chainContext.raw !== undefined) bias.chain_context = g.chainContext.raw;
      if (g.signals.raw !== undefined) bias.signals = g.signals.raw;

      const payload = {
        underlying: g.underlying,
        expiry: g.expiry,
        snapshot_ts: g.snapshotTs,
        market_status: g.marketStatus,
        chain: g.chain,
        analytics,
        bias,
      } as unknown as FnoPayload;

      const expected = {
        pcrOi: g.pcrOi.expected,
        pcrVolume: g.pcrVolume.expected,
        maxPain: g.maxPain.expected,
        aggregateOiBias: { call: g.buildCall.expected, put: g.buildPut.expected },
        walls: { support: g.support.expected, resistance: g.resistance.expected },
        ivSkew: g.ivSkew.expected,
        futuresBasis: g.futuresBasis.expected,
        biasState: g.biasState.expected,
        biasSignals: g.signals.expected,
        context: {
          underlying: g.underlying,
          expiry: g.expiry,
          chainContext: g.chainContext.expected,
        },
      };

      // Mirror the selector's `hudHasNullAnalytic`: at least one analytic leaf
      // is the N/A sentinel (null) => the snapshot-bearing payload is `partial`.
      const ivSkewHasNull =
        expected.ivSkew === null ||
        expected.ivSkew.putMinusCall === null ||
        expected.ivSkew.slope === null ||
        expected.ivSkew.atmIv === null;

      const anyAnalyticNull =
        expected.pcrOi === null ||
        expected.pcrVolume === null ||
        expected.maxPain === null ||
        expected.futuresBasis === null ||
        expected.aggregateOiBias.call === null ||
        expected.aggregateOiBias.put === null ||
        expected.walls.support === null ||
        expected.walls.resistance === null ||
        expected.biasState === null ||
        expected.biasSignals === null ||
        expected.context.chainContext === null ||
        ivSkewHasNull;

      return { payload, expected, anyAnalyticNull };
    });
}

describe('Property 5: partial analytics pass through finite fields and flag nulls', () => {
  it('buildHudModel passes every finite value through unchanged and marks every null field as N/A', () => {
    fc.assert(
      fc.property(payloadArb(), ({ payload, expected }) => {
        const hud = buildHudModel(payload);

        // Finite values pass through unchanged; null/omitted/non-finite -> N/A (null).
        expect(hud.pcrOi).toBe(expected.pcrOi);
        expect(hud.pcrVolume).toBe(expected.pcrVolume);
        expect(hud.maxPain).toBe(expected.maxPain);
        expect(hud.futuresBasis).toBe(expected.futuresBasis);
        expect(hud.aggregateOiBias.call).toBe(expected.aggregateOiBias.call);
        expect(hud.aggregateOiBias.put).toBe(expected.aggregateOiBias.put);
        expect(hud.walls.support).toBe(expected.walls.support);
        expect(hud.walls.resistance).toBe(expected.walls.resistance);
        expect(hud.biasState).toBe(expected.biasState);
        expect(hud.biasSignals).toEqual(expected.biasSignals);
        expect(hud.ivSkew).toEqual(expected.ivSkew);
        expect(hud.context).toEqual(expected.context);

        // Never fabricated: a numeric leaf flagged N/A is strictly null (not 0).
        const numericLeaves: Array<[number | null, number | null]> = [
          [expected.pcrOi, hud.pcrOi],
          [expected.pcrVolume, hud.pcrVolume],
          [expected.maxPain, hud.maxPain],
          [expected.walls.support, hud.walls.support],
          [expected.walls.resistance, hud.walls.resistance],
          [expected.futuresBasis, hud.futuresBasis],
        ];
        for (const [exp, actual] of numericLeaves) {
          if (exp === null) {
            expect(actual).toBeNull();
          } else {
            expect(actual).toBe(exp);
            expect(Number.isFinite(actual as number)).toBe(true);
          }
        }
      }),
      { numRuns: 200 },
    );
  });

  it('toFnoViewState tags `partial` when at least one analytic field is null (and `ready` otherwise), never `unavailable`, while a snapshot exists', () => {
    fc.assert(
      fc.property(payloadArb(), ({ payload, anyAnalyticNull }) => {
        const view = toFnoViewState(payload);

        // A snapshot exists (non-empty chain + finite snapshot_ts), so the
        // result is never `unavailable`.
        expect(view.kind).not.toBe('unavailable');

        // `partial` iff at least one analytic field is N/A; `ready` when all present.
        expect(view.kind).toBe(anyAnalyticNull ? 'partial' : 'ready');

        // Either way the snapshot timestamp is carried through unchanged.
        if (view.kind === 'partial' || view.kind === 'ready') {
          expect(view.snapshotTs).toBe(payload.snapshot_ts);
        }
      }),
      { numRuns: 200 },
    );
  });
});
