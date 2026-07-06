// Feature: professional-charting-suite, Property 21
//
// Property-based test for Property 21: "Imbalance flags exactly the levels
// meeting the configured ratio" (Validates Requirements 6.6).
//
// For any cluster and for any configured ratio in 1.5-20, a price level is
// flagged as an imbalance if and only if the ratio of the larger to the smaller
// of its diagonally-opposed bid and ask volumes is greater than or equal to the
// configured ratio (with this level on the larger side). Imbalance is diagonal
// between vertically adjacent levels (ascending price order):
//   - a buy (ask) imbalance when level.ask >= ratio * below.bid and level.ask > 0;
//   - a sell (bid) imbalance when level.bid >= ratio * above.ask and level.bid > 0.
// "below"/"above" are the previous/next cells in ascending price order; the
// missing neighbour beyond the array edge contributes an opposing volume of 0.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { detectImbalances, MIN_IMBALANCE_RATIO, MAX_IMBALANCE_RATIO } from '@/charting/engines';
import type { FootprintCell } from '@/charting/engines';

const RUNS = 100;

/**
 * Independent reference for the diagonal imbalance rule. Mirrors the design's
 * "larger-to-smaller ratio of diagonally-opposed volumes" definition without
 * reusing the engine's loop. Returns flagged prices in ascending order.
 */
function referenceImbalances(cells: FootprintCell[], ratio: number): number[] {
  if (cells.length === 0) return [];
  const sorted = [...cells].sort((a, b) => a.price - b.price);

  return sorted
    .filter((level, i) => {
      const belowBid = i > 0 ? sorted[i - 1].bid : 0;
      const aboveAsk = i < sorted.length - 1 ? sorted[i + 1].ask : 0;

      // Buy imbalance: this level's ask dominates the bid diagonally below.
      const buy = level.ask > 0 && level.ask >= ratio * belowBid;
      // Sell imbalance: this level's bid dominates the ask diagonally above.
      const sell = level.bid > 0 && level.bid >= ratio * aboveAsk;

      return buy || sell;
    })
    .map((c) => c.price);
}

/** Integer volume so the boundary comparison (level >= ratio * neighbour) is exact. */
const volume = () => fc.integer({ min: 0, max: 200 });

/**
 * A cluster of cells with unique, strictly-ordered prices (distinct tick rows),
 * which is the shape the engine produces. Prices are built from unique integer
 * indices so "immediately below/above" is unambiguous.
 */
const cluster = (): fc.Arbitrary<FootprintCell[]> =>
  fc
    .uniqueArray(fc.integer({ min: -500, max: 500 }), { minLength: 0, maxLength: 25 })
    .chain((indices) =>
      fc.tuple(
        ...indices.map((idx) =>
          fc.record({ bid: volume(), ask: volume() }).map(({ bid, ask }) => ({
            price: idx, // tick row index used directly as a price
            bid,
            ask,
          })),
        ),
      ),
    )
    .map((arr) => arr as FootprintCell[]);

/** Ratios strictly within the accepted, un-clamped range so clamping is identity. */
const ratio = () =>
  fc.double({
    min: MIN_IMBALANCE_RATIO,
    max: MAX_IMBALANCE_RATIO,
    noNaN: true,
    noDefaultInfinity: true,
  });

describe('Property 21: Imbalance flags exactly the levels meeting the configured ratio', () => {
  it('flags exactly the diagonal levels whose dominant volume meets the configured ratio', () => {
    fc.assert(
      fc.property(cluster(), ratio(), (cells, r) => {
        const actual = detectImbalances(cells, r);
        const expected = referenceImbalances(cells, r);

        // detectImbalances returns prices in ascending price order.
        expect(actual).toEqual(expected);
      }),
      { numRuns: RUNS },
    );
  });

  it('input order does not affect the flagged set (engine sorts internally)', () => {
    fc.assert(
      fc.property(cluster(), ratio(), (cells, r) => {
        const shuffled = [...cells].reverse();
        const fromSorted = detectImbalances(cells, r);
        const fromShuffled = detectImbalances(shuffled, r);

        expect(fromShuffled).toEqual(fromSorted);
      }),
      { numRuns: RUNS },
    );
  });
});
