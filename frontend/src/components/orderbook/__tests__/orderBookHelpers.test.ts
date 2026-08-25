// orderBookHelpers.test.ts — mapping Kite REST depth into the rendered book.
//
// These target `buildBookFromKiteDepth`, the seam where the broker's payload shape
// becomes the ladder the user reads. Every failure mode here is silent and
// plausible-looking, which is why it gets tests rather than a manual glance:
//
//   * swapping buy/sell inverts the entire book and still renders neatly;
//   * admitting Kite's zero-priced padding rows makes 0 the best bid, which
//     collapses the spread to the full price of the instrument;
//   * returning an empty book instead of null makes "we cannot see the depth"
//     indistinguishable from "there are no orders", which is a much stronger and
//     usually false claim.

import { describe, expect, it } from 'vitest';

import { buildBookFromKiteDepth, type KiteDepth } from '../orderBookHelpers';

/** A realistic RELIANCE payload: five levels a side, tight spread. */
const DEPTH: KiteDepth = {
  buy: [
    { price: 1304.9, quantity: 250, orders: 3 },
    { price: 1304.8, quantity: 400, orders: 5 },
    { price: 1304.7, quantity: 120, orders: 2 },
    { price: 1304.6, quantity: 800, orders: 9 },
    { price: 1304.5, quantity: 300, orders: 4 },
  ],
  sell: [
    { price: 1305.1, quantity: 180, orders: 2 },
    { price: 1305.2, quantity: 520, orders: 6 },
    { price: 1305.3, quantity: 90, orders: 1 },
    { price: 1305.4, quantity: 640, orders: 7 },
    { price: 1305.5, quantity: 210, orders: 3 },
  ],
};

describe('buildBookFromKiteDepth', () => {
  it("maps Kite's buy side to bids and sell side to asks", () => {
    // The inversion this pins would render perfectly while showing the book
    // upside-down — bids above asks, a negative spread read as positive.
    const book = buildBookFromKiteDepth(DEPTH)!;

    const realBids = book.bids.filter((l) => !l.synthetic);
    const realAsks = book.asks.filter((l) => !l.synthetic);

    expect(realBids[0].price).toBe(1304.9);
    expect(realBids[0].size).toBe(250);
    // Asks are reversed for display (highest at the top of the panel), so the best
    // ask is the LAST real entry.
    expect(realAsks[realAsks.length - 1].price).toBe(1305.1);
  });

  it('computes the spread from the two inside prices', () => {
    const book = buildBookFromKiteDepth(DEPTH)!;
    expect(book.spread).toBeCloseTo(0.2, 5);
    expect(book.midPrice).toBeCloseTo(1305.0, 5);
  });

  it('accumulates the running total down each side', () => {
    const book = buildBookFromKiteDepth(DEPTH)!;
    const realBids = book.bids.filter((l) => !l.synthetic);
    // 250, then 250+400, then +120 …
    expect(realBids[0].total).toBe(250);
    expect(realBids[1].total).toBe(650);
    expect(realBids[2].total).toBe(770);
  });

  it('drops the zero-priced rows Kite pads with outside market hours', () => {
    // THE important one. Kite returns `{price: 0, quantity: 0}` filler when a side
    // is thin or the market is shut. Admitting a 0 makes it the best bid, and the
    // spread becomes the entire price of the instrument — a wildly wrong number
    // rendered with total confidence.
    const padded: KiteDepth = {
      buy: [
        { price: 1304.9, quantity: 250 },
        { price: 0, quantity: 0 },
        { price: 0, quantity: 0 },
      ],
      sell: [
        { price: 1305.1, quantity: 180 },
        { price: 0, quantity: 0 },
      ],
    };
    const book = buildBookFromKiteDepth(padded)!;

    const realBids = book.bids.filter((l) => !l.synthetic);
    const realAsks = book.asks.filter((l) => !l.synthetic);
    expect(realBids).toHaveLength(1);
    expect(realAsks).toHaveLength(1);
    expect(book.spread).toBeCloseTo(0.2, 5);
    expect(realBids.every((l) => l.price > 0)).toBe(true);
  });

  it('returns null — not an empty book — when there is no usable depth', () => {
    // Null lets the caller keep the last ladder and drop the live flag. An empty
    // book would assert "no orders exist", which is a claim we cannot make from a
    // failed or depth-less response.
    expect(buildBookFromKiteDepth(null)).toBeNull();
    expect(buildBookFromKiteDepth(undefined)).toBeNull();
    expect(buildBookFromKiteDepth({})).toBeNull();
    expect(buildBookFromKiteDepth({ buy: [], sell: [] })).toBeNull();
    expect(
      buildBookFromKiteDepth({ buy: [{ price: 0, quantity: 0 }], sell: [{ price: 0, quantity: 0 }] }),
    ).toBeNull();
  });

  it('survives a malformed payload rather than throwing', () => {
    // This runs inside a 2s poll; a throw would take the panel down on one bad
    // response and keep it down.
    const junk = [
      { buy: 'nope', sell: null },
      { buy: [null, undefined, 42, 'x'] },
      { buy: [{ price: NaN, quantity: 10 }, { price: 1304.9, quantity: Infinity }] },
      { sell: [{ price: 1305.1 }] },
    ] as unknown as KiteDepth[];

    for (const d of junk) {
      expect(() => buildBookFromKiteDepth(d)).not.toThrow();
    }
  });

  it('handles a one-sided book without inventing the other side', () => {
    // Real at circuit limits: all bids, no asks. The spread is unknowable, and
    // reporting one would be fabrication.
    const book = buildBookFromKiteDepth({
      buy: [{ price: 1304.9, quantity: 250 }],
      sell: [],
    });
    expect(book).not.toBeNull();
    expect(book!.bids.filter((l) => !l.synthetic)).toHaveLength(1);
    expect(book!.asks.filter((l) => !l.synthetic)).toHaveLength(0);
    expect(book!.spread).toBe(0);
    expect(book!.midPrice).toBe(0);
  });

  it('ignores a NaN or non-finite quantity rather than rendering it', () => {
    const book = buildBookFromKiteDepth({
      buy: [
        { price: 1304.9, quantity: Number.NaN },
        { price: 1304.8, quantity: 400 },
      ],
      sell: [{ price: 1305.1, quantity: 180 }],
    })!;
    const realBids = book.bids.filter((l) => !l.synthetic);
    expect(realBids).toHaveLength(1);
    expect(realBids[0].price).toBe(1304.8);
  });
});
