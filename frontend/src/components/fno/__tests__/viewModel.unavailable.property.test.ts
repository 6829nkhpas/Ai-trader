// Feature: fno-frontend-section, Property 6
//
// Property 6: Unavailable and empty results map to an explained Unavailable_State.
//
// "For any `Unavailable_Marker` or empty-chain payload, `toFnoViewState` returns
//  `kind: 'unavailable'` carrying a non-empty human-readable reason and no
//  chart/HUD models — never a `ready`/`partial` state and never fabricated data."
//
// Validates: Requirements 6.4, 8.1
//
// `toFnoViewState` is the honest-empty-state boundary (AD-5). Two input families
// must always collapse to `kind: 'unavailable'`:
//   1. An explicit `Unavailable_Marker` (`unavailable === true`) emitted by the
//      bridge when no snapshot/analytic exists.
//   2. An empty-chain payload — one whose `chain` is missing or empty (or whose
//      `snapshot_ts` is non-finite), so no snapshot actually exists even though
//      the object otherwise looks like a payload.
//
// In both cases the returned state must:
//   - carry `kind: 'unavailable'`,
//   - carry a non-empty, human-readable `reason` string (the marker's own reason
//     when present, else a synthesized one),
//   - expose ONLY `{ kind, reason, lastSnapshotTs }` and NO chart/HUD models
//     (no `oi`/`iv`/`hud`/`snapshotTs`/`marketStatus`), so a fabricated zero can
//     never reach a chart or HUD field,
//   - never be tagged `ready` or `partial`.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  toFnoViewState,
  type FnoPayload,
  type FnoUnavailableMarker,
  type FnoChainRow,
  type NaOr,
} from '@/components/fno/viewModel';

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** An analytic leaf: explicit `null`, a finite number, or non-finite garbage. */
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

/** A populated chain row (used only to PROVE empty-chain detection, never to fill). */
function chainRowArb(): fc.Arbitrary<FnoChainRow> {
  return fc.record({
    strike: fc.double({ min: 1000, max: 60000, noNaN: true, noDefaultInfinity: true }),
    ce_oi: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
    pe_oi: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
    ce_price: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
    pe_price: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
    iv: analyticLeafArb() as fc.Arbitrary<NaOr<number>>,
  });
}

/** A full analytics block whose every leaf varies across the space. */
function analyticsArb() {
  return fc.record({
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
  });
}

/**
 * An explicit `Unavailable_Marker`: `unavailable === true`, with a reason that is
 * sometimes present (and possibly empty/blank, so the selector must synthesize a
 * non-empty one) and an optional `last_snapshot_ts`.
 */
function markerArb(): fc.Arbitrary<FnoUnavailableMarker> {
  return fc.record(
    {
      underlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY', ''),
      expiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
      unavailable: fc.constant(true as const),
      // reason may be a real string, an empty/blank string, or omitted entirely.
      reason: fc.oneof(
        fc.constant('no chain snapshot available for NIFTY 50 / 2024-12-26'),
        fc.constant(''),
        fc.constant('   '),
        fc.string(),
      ),
      last_snapshot_ts: fc.option(fc.integer({ min: 0, max: 2_000_000_000_000 }), {
        nil: undefined,
      }),
    },
    { requiredKeys: ['underlying', 'expiry', 'unavailable'] },
  ) as fc.Arbitrary<FnoUnavailableMarker>;
}

/**
 * An empty-chain payload: shaped like a real payload but with NO snapshot —
 * either an empty/missing `chain` array, and/or a non-finite `snapshot_ts`.
 * Crucially `unavailable` is NOT set, so the only signal is the absent snapshot.
 */
function emptyChainPayloadArb(): fc.Arbitrary<FnoPayload> {
  return fc.record(
    {
      underlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY', ''),
      expiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
      // Mix finite and non-finite timestamps; an empty chain is unavailable regardless.
      snapshot_ts: fc.oneof(
        fc.integer({ min: 0, max: 2_000_000_000_000 }),
        fc.constantFrom(NaN, Infinity, -Infinity),
      ) as fc.Arbitrary<number>,
      market_status: fc.constantFrom('open', 'closed') as fc.Arbitrary<'open' | 'closed'>,
      // The empty/missing chain is the defining trait of this family.
      chain: fc.oneof(
        fc.constant([] as FnoChainRow[]),
        fc.constant(undefined as unknown as FnoChainRow[]),
      ),
      analytics: analyticsArb(),
      bias: fc.constant({}),
    },
    { requiredKeys: ['underlying', 'expiry', 'snapshot_ts', 'market_status', 'analytics', 'bias'] },
  ) as fc.Arbitrary<FnoPayload>;
}

// Either family is a valid Property-6 input.
function unavailableInputArb(): fc.Arbitrary<FnoPayload | FnoUnavailableMarker> {
  return fc.oneof(markerArb(), emptyChainPayloadArb());
}

// ---------------------------------------------------------------------------
// Property 6
// ---------------------------------------------------------------------------

describe('Property 6: unavailable and empty results map to an explained Unavailable_State', () => {
  it('maps any Unavailable_Marker to an unavailable state with a non-empty reason and no models', () => {
    fc.assert(
      fc.property(markerArb(), (marker) => {
        const state = toFnoViewState(marker);

        // Always unavailable — never ready/partial.
        expect(state.kind).toBe('unavailable');
        if (state.kind !== 'unavailable') return; // narrows the type for the asserts below

        // Non-empty human-readable reason.
        expect(typeof state.reason).toBe('string');
        expect(state.reason.trim().length).toBeGreaterThan(0);

        // No fabricated chart/HUD models leaked onto the state.
        expect(state).not.toHaveProperty('oi');
        expect(state).not.toHaveProperty('iv');
        expect(state).not.toHaveProperty('hud');
        expect(state).not.toHaveProperty('snapshotTs');
        expect(state).not.toHaveProperty('marketStatus');

        // lastSnapshotTs is finite-or-null (never NaN/±Infinity, never undefined).
        expect(
          state.lastSnapshotTs === null || Number.isFinite(state.lastSnapshotTs),
        ).toBe(true);
      }),
      { numRuns: 200 },
    );
  });

  it('maps any empty-chain payload to an unavailable state with a non-empty reason and no models', () => {
    fc.assert(
      fc.property(emptyChainPayloadArb(), (payload) => {
        const state = toFnoViewState(payload);

        expect(state.kind).toBe('unavailable');
        if (state.kind !== 'unavailable') return;

        expect(typeof state.reason).toBe('string');
        expect(state.reason.trim().length).toBeGreaterThan(0);

        expect(state).not.toHaveProperty('oi');
        expect(state).not.toHaveProperty('iv');
        expect(state).not.toHaveProperty('hud');
        expect(state).not.toHaveProperty('snapshotTs');
        expect(state).not.toHaveProperty('marketStatus');

        expect(
          state.lastSnapshotTs === null || Number.isFinite(state.lastSnapshotTs),
        ).toBe(true);
      }),
      { numRuns: 200 },
    );
  });

  it('never returns a ready/partial state for any unavailable-or-empty input', () => {
    fc.assert(
      fc.property(unavailableInputArb(), (input) => {
        const state = toFnoViewState(input);
        expect(state.kind).not.toBe('ready');
        expect(state.kind).not.toBe('partial');
        expect(state.kind).toBe('unavailable');
      }),
      { numRuns: 200 },
    );
  });
});
