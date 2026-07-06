// Feature: fno-frontend-section, Property 4
//
// Property 4: HUD is complete and renders nulls as explicit N/A, never fabricated.
//
// "For any analytics-plus-bias payload, `buildHudModel` exposes every headline
//  field — PCR (OI and volume), max pain, aggregate OI bias (call/put), nearest
//  OI walls, IV-skew summary, futures basis — plus the agent bias state with its
//  driving signals and the chain context (underlying, expiry, own-chain vs
//  broad-market); every field equals the payload value when finite/present or
//  the explicit N/A sentinel (null) when null/omitted, and no null input ever
//  becomes a 0 or any other fabricated number."
//
// Validates: Requirements 5.1, 5.2, 5.3, 5.4, 8.2
//
// `buildHudModel` is a pure, total selector from an IPC payload to the HUD
// view-model. We generate analytics-plus-bias payloads whose every leaf is
// independently either a finite number / valid label, or one of the "should be
// N/A" inputs (null, undefined/omitted, NaN, ±Infinity, or a garbage type). For
// each generated leaf we carry the value alongside its EXPECTED normalized
// result, then assert the built HUD reproduces every expected value exactly,
// exposes every headline field, and never turns a null/garbage input into `0`
// or any fabricated number.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  buildHudModel,
  type FnoPayload,
  type ChainContext,
  type OptionsBiasState,
} from '@/components/fno/viewModel';

// ---------------------------------------------------------------------------
// Leaf arbitraries: each yields { raw, expected } so the test mirrors the
// selector's normalization contract (finiteOrNull / stringOrNull / etc.).
// ---------------------------------------------------------------------------

const BIAS_STATES: OptionsBiasState[] = ['bullish', 'bearish', 'neutral'];
const CHAIN_CONTEXTS: ChainContext[] = ['own-chain', 'broad-market'];

const finiteNumber = fc
  .double({ min: -1e12, max: 1e12, noNaN: true })
  .filter((n) => Number.isFinite(n));

/** A numeric leaf: finite -> itself; null / undefined / NaN / ±Infinity /
 *  non-number garbage -> the explicit N/A sentinel (null), never `0`. */
function numField(): fc.Arbitrary<{ raw: unknown; expected: number | null }> {
  return fc.oneof(
    { weight: 4, arbitrary: finiteNumber.map((n) => ({ raw: n as unknown, expected: n })) },
    { weight: 1, arbitrary: fc.constant({ raw: null as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.constant({ raw: undefined as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.constant({ raw: Number.NaN as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.constant({ raw: Number.POSITIVE_INFINITY as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.constant({ raw: Number.NEGATIVE_INFINITY as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.string().map((s) => ({ raw: s as unknown, expected: null })) },
    { weight: 1, arbitrary: fc.constant({ raw: 0 as unknown, expected: 0 }) }, // genuine zero must pass through
  );
}

/** A string-label leaf (OI buildup): string -> itself; anything else -> null. */
function strField(): fc.Arbitrary<{ raw: unknown; expected: string | null }> {
  return fc.oneof(
    { weight: 4, arbitrary: fc.string().map((s) => ({ raw: s as unknown, expected: s })) },
    { weight: 1, arbitrary: fc.constant({ raw: null as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.constant({ raw: undefined as unknown, expected: null }) },
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
    { weight: 1, arbitrary: fc.constant({ raw: undefined as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.constant({ raw: null as unknown, expected: null }) },
    {
      weight: 1,
      arbitrary: fc.string().map((s) => ({
        raw: s as unknown,
        expected: (BIAS_STATES as string[]).includes(s) ? (s as OptionsBiasState) : null,
      })),
    },
  );
}

/** Chain context: a valid context -> itself; anything else (incl. omitted) -> null. */
function chainContextField(): fc.Arbitrary<{ raw: unknown; expected: ChainContext | null }> {
  return fc.oneof(
    {
      weight: 4,
      arbitrary: fc.constantFrom(...CHAIN_CONTEXTS).map((c) => ({ raw: c as unknown, expected: c })),
    },
    { weight: 1, arbitrary: fc.constant({ raw: undefined as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.constant({ raw: null as unknown, expected: null }) },
    {
      weight: 1,
      arbitrary: fc.string().map((s) => ({
        raw: s as unknown,
        expected: (CHAIN_CONTEXTS as string[]).includes(s) ? (s as ChainContext) : null,
      })),
    },
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
    { weight: 1, arbitrary: fc.constant({ raw: null as unknown, expected: null }) },
    { weight: 1, arbitrary: fc.constant({ raw: undefined as unknown, expected: null }) },
  );
}

/** IV-skew summary: omitted/null -> whole summary is null; object -> nested finite-or-null. */
function ivSkewField(): fc.Arbitrary<{
  raw: unknown;
  expected: { putMinusCall: number | null; slope: number | null; atmIv: number | null } | null;
}> {
  return fc.oneof(
    { weight: 1, arbitrary: fc.constant({ raw: undefined as unknown, expected: null }) },
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

// ---------------------------------------------------------------------------
// Payload arbitrary: an analytics-plus-bias payload assembled from the leaves,
// carrying the expected HUD model alongside the raw payload.
// ---------------------------------------------------------------------------

function payloadArb() {
  return fc
    .record({
      underlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
      expiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
      snapshotTs: fc.integer({ min: 0, max: 2_000_000_000_000 }),
      marketStatus: fc.constantFrom('open', 'closed') as fc.Arbitrary<'open' | 'closed'>,
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
      // Assemble the raw IPC payload (omitting fields whose raw is `undefined`
      // so the selector sees a genuinely absent leaf).
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
        chain: [],
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

      return { payload, expected };
    });
}

// The headline fields the HUD must always expose (R5.1, R5.2, R5.4).
const REQUIRED_HUD_KEYS = [
  'pcrOi',
  'pcrVolume',
  'maxPain',
  'aggregateOiBias',
  'walls',
  'ivSkew',
  'futuresBasis',
  'biasState',
  'biasSignals',
  'context',
] as const;

describe('Property 4: HUD is complete and renders nulls as explicit N/A, never fabricated', () => {
  it('exposes every headline field, mirroring finite/present values and mapping null/omitted to the N/A sentinel', () => {
    fc.assert(
      fc.property(payloadArb(), ({ payload, expected }) => {
        const hud = buildHudModel(payload);

        // Completeness: every headline field is exposed (R5.1, R5.2, R5.4).
        for (const key of REQUIRED_HUD_KEYS) {
          expect(hud).toHaveProperty(key);
        }
        expect(hud.aggregateOiBias).toHaveProperty('call');
        expect(hud.aggregateOiBias).toHaveProperty('put');
        expect(hud.walls).toHaveProperty('support');
        expect(hud.walls).toHaveProperty('resistance');
        expect(hud.context).toHaveProperty('underlying');
        expect(hud.context).toHaveProperty('expiry');
        expect(hud.context).toHaveProperty('chainContext');

        // Fidelity: every field equals the payload value when finite/present,
        // or the explicit N/A sentinel (null) when null/omitted (R5.3, R8.2).
        expect(hud.pcrOi).toBe(expected.pcrOi);
        expect(hud.pcrVolume).toBe(expected.pcrVolume);
        expect(hud.maxPain).toBe(expected.maxPain);
        expect(hud.aggregateOiBias.call).toBe(expected.aggregateOiBias.call);
        expect(hud.aggregateOiBias.put).toBe(expected.aggregateOiBias.put);
        expect(hud.walls.support).toBe(expected.walls.support);
        expect(hud.walls.resistance).toBe(expected.walls.resistance);
        expect(hud.futuresBasis).toBe(expected.futuresBasis);
        expect(hud.biasState).toBe(expected.biasState);
        expect(hud.biasSignals).toEqual(expected.biasSignals);
        expect(hud.ivSkew).toEqual(expected.ivSkew);
        expect(hud.context).toEqual(expected.context);
      }),
      { numRuns: 200 },
    );
  });

  it('never fabricates: a null/omitted/non-finite numeric input becomes the N/A sentinel (null), never 0 or any other number', () => {
    fc.assert(
      fc.property(payloadArb(), ({ payload, expected }) => {
        const hud = buildHudModel(payload);

        // For every numeric headline leaf, when the contract says N/A (null),
        // the HUD must be strictly null — not 0 and not any fabricated number.
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

        // The IV-skew nested leaves, when present, are finite-or-null (never NaN/±Infinity).
        if (hud.ivSkew !== null) {
          for (const leaf of [hud.ivSkew.putMinusCall, hud.ivSkew.slope, hud.ivSkew.atmIv]) {
            if (leaf !== null) expect(Number.isFinite(leaf)).toBe(true);
          }
        }
      }),
      { numRuns: 200 },
    );
  });
});
