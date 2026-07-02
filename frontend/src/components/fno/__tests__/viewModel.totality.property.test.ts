// Feature: fno-frontend-section, Property 8
//
// Property 8: The view-model layer is total and never throws.
//
// "For any input — a well-formed payload, an `Unavailable_Marker`, a partial
//  payload, a malformed/garbage object, or missing fields — `toFnoViewState`
//  (and the selectors it calls) returns a valid `FnoViewState` without raising,
//  so a backend error or malformed result becomes a visible error/empty state
//  rather than a crash or freeze."
//
// Validates: Requirements 6.5
//
// `toFnoViewState` is the honest-empty-state boundary: components branch on
// `viewState.kind`, so it MUST be total over the entire input space — not just
// the documented payload shape, but adversarial garbage too. This file feeds it
// (a) fully-arbitrary `fc.anything()` values, (b) hand-crafted adversarial
// objects (missing/garbage fields, hostile getters), and (c) structurally
// well-formed payloads, markers, and partial payloads — and asserts that across
// ALL of them it never throws and always returns a structurally-valid
// `FnoViewState` of one of the three valid kinds.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { toFnoViewState, type FnoViewState } from '@/components/fno/viewModel';

const NUM_RUNS = 200;

// ---------------------------------------------------------------------------
// Structural validator: assert the result is a well-formed FnoViewState with
// the fields its `kind` requires. Returns nothing; throws (failing the test)
// only if the SHAPE is invalid — never as a side effect of calling the selector.
// ---------------------------------------------------------------------------

function assertValidViewState(state: FnoViewState): void {
  expect(state).not.toBeNull();
  expect(typeof state).toBe('object');
  expect(['ready', 'partial', 'unavailable', 'service-error']).toContain(state.kind);

  if (state.kind === 'unavailable') {
    // A non-empty human-readable reason is mandatory; lastSnapshotTs is finite-or-null.
    expect(typeof state.reason).toBe('string');
    expect(state.reason.length).toBeGreaterThan(0);
    expect(state.lastSnapshotTs === null || Number.isFinite(state.lastSnapshotTs)).toBe(true);
  } else if (state.kind === 'service-error') {
    expect(typeof state.detail).toBe('string');
    expect(state.detail.length).toBeGreaterThan(0);
  } else {
    // ready | partial: the structurally-required render fields must be present.
    expect(state.oi).toBeDefined();
    expect(Array.isArray(state.oi.points)).toBe(true);
    expect(state.iv).toBeDefined();
    expect(Array.isArray(state.iv.points)).toBe(true);
    expect(state.hud).toBeDefined();
    expect(typeof state.hud).toBe('object');
    expect(Number.isFinite(state.snapshotTs)).toBe(true);
    expect(['open', 'closed']).toContain(state.marketStatus);
  }
}

// ---------------------------------------------------------------------------
// (a) Fully-arbitrary garbage: anything the runtime can produce.
// ---------------------------------------------------------------------------

const anythingArb = fc.anything({
  withBigInt: true,
  withDate: true,
  withMap: true,
  withSet: true,
  withNullPrototype: true,
  withObjectString: true,
  withTypedArray: true,
});

// ---------------------------------------------------------------------------
// (b) Adversarial near-payloads: objects shaped LIKE a payload/marker but with
// individual fields replaced by garbage, wrong types, or omitted entirely —
// the inputs most likely to slip past a naive guard and throw.
// ---------------------------------------------------------------------------

const garbageLeafArb = fc.oneof(
  fc.constant(null),
  fc.constant(undefined),
  fc.constant(Number.NaN),
  fc.constant(Number.POSITIVE_INFINITY),
  fc.constant(Number.NEGATIVE_INFINITY),
  fc.string(),
  fc.integer(),
  fc.double(),
  fc.boolean(),
  fc.array(fc.anything()),
  fc.object(),
);

/** A "chain" that may be a real array of garbage rows, or not an array at all. */
const garbageChainArb = fc.oneof(
  fc.array(garbageLeafArb, { maxLength: 8 }),
  fc.array(
    fc.record(
      {
        strike: garbageLeafArb,
        ce_oi: garbageLeafArb,
        pe_oi: garbageLeafArb,
        iv: garbageLeafArb,
      },
      { requiredKeys: [] },
    ),
    { maxLength: 8 },
  ),
  garbageLeafArb, // not even an array
);

/** An object with a partial/garbage subset of the documented keys. */
const adversarialObjectArb = fc.record(
  {
    underlying: garbageLeafArb,
    expiry: garbageLeafArb,
    snapshot_ts: garbageLeafArb,
    last_snapshot_ts: garbageLeafArb,
    market_status: garbageLeafArb,
    unavailable: fc.oneof(fc.boolean(), garbageLeafArb),
    reason: garbageLeafArb,
    chain: garbageChainArb,
    analytics: fc.oneof(garbageLeafArb, fc.object()),
    bias: fc.oneof(garbageLeafArb, fc.object()),
  },
  { requiredKeys: [] }, // every key independently present or omitted
);

// ---------------------------------------------------------------------------
// (c) Structurally well-formed inputs: a valid payload, a valid marker, and a
// "partial" payload (a real snapshot whose analytics/bias are all absent).
// ---------------------------------------------------------------------------

const finiteNum = fc.double({ min: -1e9, max: 1e9, noNaN: true, noDefaultInfinity: true });
const numOrNull = fc.oneof(finiteNum, fc.constant(null));

const wellFormedPayloadArb = fc.record({
  underlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
  expiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
  snapshot_ts: fc.integer({ min: 1, max: 2_000_000_000_000 }),
  market_status: fc.constantFrom('open', 'closed'),
  chain: fc.array(
    fc.record({
      strike: fc.integer({ min: 1, max: 200_000 }),
      ce_oi: numOrNull,
      pe_oi: numOrNull,
      ce_price: numOrNull,
      pe_price: numOrNull,
      iv: numOrNull,
    }),
    { minLength: 1, maxLength: 20 },
  ),
  analytics: fc.record({
    spot: numOrNull,
    pcr_oi: numOrNull,
    pcr_volume: numOrNull,
    max_pain: numOrNull,
    oi_buildup: fc.record({
      call: fc.oneof(fc.string(), fc.constant(null)),
      put: fc.oneof(fc.string(), fc.constant(null)),
    }),
    iv_skew: fc.record({ put_minus_call: numOrNull, slope: numOrNull, atm_iv: numOrNull }),
    oi_walls: fc.record({ support: numOrNull, resistance: numOrNull }),
    futures_basis: numOrNull,
  }),
  bias: fc.record(
    {
      options_bias_state: fc.constantFrom('bullish', 'bearish', 'neutral'),
      chain_context: fc.constantFrom('own-chain', 'broad-market'),
      signals: fc.object(),
    },
    { requiredKeys: [] },
  ),
});

const markerArb = fc.record(
  {
    underlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
    expiry: fc.constantFrom('', '2024-12-26'),
    unavailable: fc.constant(true),
    reason: fc.oneof(fc.string(), fc.constant('')),
    last_snapshot_ts: fc.oneof(fc.integer({ min: 1, max: 2_000_000_000_000 }), fc.constant(undefined)),
  },
  { requiredKeys: ['unavailable'] },
);

/** A real snapshot whose analytics + bias are entirely absent => `partial`. */
const partialPayloadArb = fc.record({
  underlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
  expiry: fc.constantFrom('', '2024-12-26'),
  snapshot_ts: fc.integer({ min: 1, max: 2_000_000_000_000 }),
  market_status: fc.constantFrom('open', 'closed'),
  chain: fc.array(
    fc.record({
      strike: fc.integer({ min: 1, max: 200_000 }),
      ce_oi: numOrNull,
      pe_oi: numOrNull,
      iv: numOrNull,
    }),
    { minLength: 1, maxLength: 12 },
  ),
});

// One combined arbitrary covering every category, so a single property body
// asserts totality across the full input space.
const anyInputArb = fc.oneof(
  { weight: 4, arbitrary: anythingArb },
  { weight: 3, arbitrary: adversarialObjectArb },
  { weight: 2, arbitrary: wellFormedPayloadArb },
  { weight: 1, arbitrary: markerArb },
  { weight: 1, arbitrary: partialPayloadArb },
);

describe('Property 8: The view-model layer is total and never throws', () => {
  it('never throws and always returns a valid FnoViewState for ANY input', () => {
    fc.assert(
      fc.property(anyInputArb, (input) => {
        let state: FnoViewState;
        // Calling the selector must not raise — that is the property under test.
        expect(() => {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          state = toFnoViewState(input as any);
        }).not.toThrow();

        // ...and whatever it returns must be a structurally-valid view state.
        // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
        assertValidViewState(state!);
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('collapses pure fc.anything() garbage to a valid (typically unavailable) state without throwing', () => {
    fc.assert(
      fc.property(anythingArb, (input) => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const state = toFnoViewState(input as any);
        assertValidViewState(state);
      }),
      { numRuns: NUM_RUNS },
    );
  });

  it('handles well-formed payloads, markers, and partial payloads as valid states', () => {
    fc.assert(
      fc.property(
        fc.oneof(wellFormedPayloadArb, markerArb, partialPayloadArb),
        (input) => {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const state = toFnoViewState(input as any);
          assertValidViewState(state);
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });

  it('survives objects with hostile getters that throw on field access', () => {
    // A backend/serialization quirk could yield an object whose property access
    // throws; the totality guard must still produce a valid unavailable state.
    const hostile = new Proxy(
      {},
      {
        get() {
          throw new Error('hostile field access');
        },
        has() {
          return true;
        },
      },
    );

    let state: FnoViewState;
    expect(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      state = toFnoViewState(hostile as any);
    }).not.toThrow();
    // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
    assertValidViewState(state!);
  });
});
