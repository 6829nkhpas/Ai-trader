// Feature: fno-frontend-section, Property 7
//
// Property 7: Market-closed payloads show the most recent snapshot or an honest
// unavailable.
//
// "For any payload whose `market_status` is closed, `toFnoViewState` yields a
//  `ready`/`partial` state carrying that snapshot's `snapshotTs` when a snapshot
//  is present, and yields `kind: 'unavailable'` (optionally carrying
//  `lastSnapshotTs`) when no snapshot exists — never implying live data and never
//  fabricating a snapshot."
//
// Validates: Requirements 8.4
//
// `toFnoViewState` collapses a single IPC payload (or Unavailable_Marker) into
// one tagged FnoViewState. A snapshot is considered present only when the chain
// carries at least one row AND `snapshot_ts` is a finite number. This test
// drives the closed-market branch with both shapes:
//
//   (a) snapshot present  -> expect kind ∈ {ready, partial}, marketStatus
//       'closed', and snapshotTs equal to the payload's finite snapshot_ts (the
//       most recent snapshot is shown, never a live indicator).
//   (b) no snapshot        -> expect kind 'unavailable' with a non-empty reason
//       and lastSnapshotTs carrying the prior timestamp when one exists, else
//       null — never a fabricated ready/partial state.
//
// In every case the market is closed, so the result must NEVER imply live data:
// a non-unavailable state always reports marketStatus 'closed'.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  toFnoViewState,
  type FnoPayload,
  type FnoChainRow,
  type NaOr,
} from '@/components/fno/viewModel';

// ---------------------------------------------------------------------------
// Generators
// ---------------------------------------------------------------------------

/** A finite numeric analytic leaf or the explicit N/A sentinel (null). */
function leafArb(): fc.Arbitrary<NaOr<number>> {
  return fc.oneof(
    { weight: 2, arbitrary: fc.constant(null) },
    {
      weight: 5,
      arbitrary: fc.double({
        min: -1e9,
        max: 1e9,
        noNaN: true,
        noDefaultInfinity: true,
      }) as fc.Arbitrary<NaOr<number>>,
    },
  );
}

/** One chain row with a finite strike and possibly-null OI/price/iv leaves. */
function chainRowArb(): fc.Arbitrary<FnoChainRow> {
  return fc.record({
    strike: fc.double({ min: 1000, max: 60000, noNaN: true, noDefaultInfinity: true }),
    ce_oi: leafArb(),
    pe_oi: leafArb(),
    ce_price: leafArb(),
    pe_price: leafArb(),
    iv: leafArb(),
  });
}

/** A complete analytics object whose leaves vary across finite/null. */
function analyticsArb() {
  return fc.record({
    spot: leafArb(),
    pcr_oi: leafArb(),
    pcr_volume: leafArb(),
    max_pain: leafArb(),
    oi_buildup: fc.record({
      call: fc.constantFrom(null, 'short_buildup', 'long_buildup') as fc.Arbitrary<NaOr<string>>,
      put: fc.constantFrom(null, 'long_unwinding', 'short_covering') as fc.Arbitrary<NaOr<string>>,
    }),
    iv_skew: fc.record({
      put_minus_call: leafArb(),
      slope: leafArb(),
      atm_iv: leafArb(),
    }),
    oi_walls: fc.record({ support: leafArb(), resistance: leafArb() }),
    futures_basis: leafArb(),
  });
}

/** A closed-market payload WITH a snapshot: non-empty chain + finite snapshot_ts. */
function closedWithSnapshotArb(): fc.Arbitrary<FnoPayload> {
  return fc.record({
    underlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
    expiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
    snapshot_ts: fc.integer({ min: 1, max: 2_000_000_000_000 }),
    market_status: fc.constant('closed') as fc.Arbitrary<'closed'>,
    chain: fc.array(chainRowArb(), { minLength: 1, maxLength: 12 }),
    analytics: analyticsArb(),
    bias: fc.constant({}),
  }) as fc.Arbitrary<FnoPayload>;
}

/**
 * A closed-market payload WITHOUT a usable snapshot: either an empty chain, or a
 * non-finite/absent snapshot_ts. `lastTs` (when finite) should survive as
 * `lastSnapshotTs`.
 */
function closedWithoutSnapshotArb() {
  // The shape that defeats "hasSnapshot": empty chain OR non-finite snapshot_ts.
  const emptyChain = fc.record({
    kind: fc.constant('emptyChain' as const),
    chain: fc.constant([] as FnoChainRow[]),
    snapshot_ts: fc.integer({ min: 1, max: 2_000_000_000_000 }) as fc.Arbitrary<number>,
  });
  const badTs = fc.record({
    kind: fc.constant('badTs' as const),
    chain: fc.array(chainRowArb(), { minLength: 0, maxLength: 6 }),
    // snapshot_ts is non-finite / absent so no snapshot can be claimed.
    snapshot_ts: fc.constantFrom(NaN, Infinity, -Infinity, undefined) as fc.Arbitrary<number>,
  });

  return fc
    .record({
      underlying: fc.constantFrom('NIFTY 50', 'BANKNIFTY'),
      expiry: fc.constantFrom('', '2024-12-26', '2025-01-30'),
      shape: fc.oneof(emptyChain, badTs),
      market_status: fc.constant('closed') as fc.Arbitrary<'closed'>,
      analytics: analyticsArb(),
    })
    .map((g) => {
      const payload: Record<string, unknown> = {
        underlying: g.underlying,
        expiry: g.expiry,
        market_status: g.market_status,
        chain: g.shape.chain,
        analytics: g.analytics,
        bias: {},
      };
      // Only set snapshot_ts when it is defined (mirror a genuinely absent field).
      if (g.shape.snapshot_ts !== undefined) {
        payload.snapshot_ts = g.shape.snapshot_ts;
      }
      // The expected lastSnapshotTs: the finite snapshot_ts (empty-chain case)
      // or null (the bad/absent-ts case yields no finite prior timestamp).
      const expectedLastTs =
        typeof g.shape.snapshot_ts === 'number' && Number.isFinite(g.shape.snapshot_ts)
          ? g.shape.snapshot_ts
          : null;
      return { payload: payload as unknown as FnoPayload, expectedLastTs };
    });
}

// ---------------------------------------------------------------------------
// Property 7
// ---------------------------------------------------------------------------

describe('Property 7: market-closed payloads show the most recent snapshot or an honest unavailable', () => {
  it('closed + snapshot present -> ready/partial carrying that snapshotTs and marketStatus "closed" (never implies live data)', () => {
    fc.assert(
      fc.property(closedWithSnapshotArb(), (payload) => {
        const state = toFnoViewState(payload);

        // A snapshot exists, so the state must render it — not collapse to
        // unavailable and not fabricate.
        expect(state.kind === 'ready' || state.kind === 'partial').toBe(true);

        if (state.kind === 'ready' || state.kind === 'partial') {
          // The most recent snapshot is carried verbatim (no fabricated ts).
          expect(state.snapshotTs).toBe(payload.snapshot_ts);
          // Market is closed: must report 'closed', never imply live data.
          expect(state.marketStatus).toBe('closed');
        }
      }),
      { numRuns: 200 },
    );
  });

  it('closed + no snapshot -> unavailable with a non-empty reason and lastSnapshotTs (prior ts or null), never a fabricated snapshot', () => {
    fc.assert(
      fc.property(closedWithoutSnapshotArb(), ({ payload, expectedLastTs }) => {
        const state = toFnoViewState(payload);

        // No usable snapshot -> honest unavailable, never ready/partial.
        expect(state.kind).toBe('unavailable');

        if (state.kind === 'unavailable') {
          // Honest reason explaining the unavailable state.
          expect(typeof state.reason).toBe('string');
          expect(state.reason.trim().length).toBeGreaterThan(0);

          // lastSnapshotTs carries the prior timestamp when one exists, else null.
          expect(state.lastSnapshotTs).toBe(expectedLastTs);

          // The unavailable state never exposes a live/ready snapshot field.
          expect(state).not.toHaveProperty('snapshotTs');
          expect(state).not.toHaveProperty('marketStatus');
        }
      }),
      { numRuns: 200 },
    );
  });
});
