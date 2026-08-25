export interface OrderBookLevel {
  price: number;
  size: number;
  total: number;
  synthetic?: boolean;
}

export interface OrderBookState {
  asks: OrderBookLevel[];
  bids: OrderBookLevel[];
  spread: number;
  spreadPct: string;
  midPrice: number;
}

export const LEVEL_COUNT = 10;
export const PADDED_LEVEL_COUNT = 14;

export function createEmptyBook(): OrderBookState {
  return {
    asks: [],
    bids: [],
    spread: 0,
    spreadPct: '0.000',
    midPrice: 0,
  };
}

export function depthPercent(size: number, maxSize: number): number {
  return Math.min((size / maxSize) * 100, 100);
}

export function formatSize(size: number): string {
  if (size >= 1000) {
    return size.toLocaleString('en-IN', { maximumFractionDigits: 0 });
  }
  return size >= 100 ? Math.round(size).toString() : size.toFixed(1);
}

export function inferStep(prices: number[]): number {
  const diffs: number[] = [];
  for (let i = 1; i < prices.length; i++) {
    const d = Math.abs(prices[i] - prices[i - 1]);
    if (d > 1e-9) diffs.push(d);
  }
  if (diffs.length === 0) return 0.05;
  diffs.sort((a, b) => a - b);
  return diffs[Math.floor(diffs.length / 2)];
}

export function extendLadder(
  levels: OrderBookLevel[],
  step: number,
  dir: 1 | -1,
  target: number,
): OrderBookLevel[] {
  if (levels.length === 0 || levels.length >= target) return levels;
  const out = [...levels];
  let last = out[out.length - 1];
  let runningTotal = last.total;
  let size = Math.max(1, last.size);
  for (let i = out.length; i < target; i++) {
    const price = parseFloat((last.price + dir * step).toFixed(2));
    size = Math.max(1, Math.round(size * 1.1));
    runningTotal = parseFloat((runningTotal + size).toFixed(2));
    const level: OrderBookLevel = { price, size, total: runningTotal, synthetic: true };
    out.push(level);
    last = level;
  }
  return out;
}

/**
 * One depth level as Kite's REST `/quote` returns it.
 *
 * Kite gives `{ depth: { buy: [...], sell: [...] } }` with five entries a side.
 * "buy" is the BID side and "sell" is the ASK side — worth stating because
 * mixing them up inverts the whole book and still renders plausibly.
 */
export interface KiteDepthLevel {
  price: number;
  quantity: number;
  orders?: number;
}

export interface KiteDepth {
  buy?: KiteDepthLevel[];
  sell?: KiteDepthLevel[];
}

/**
 * Build the book from a Kite REST depth payload.
 *
 * Returns null when the payload carries no usable level, rather than an empty
 * book: an empty ladder renders identically to "no bids in the market", so a
 * missing or malformed response must be distinguishable from a genuinely empty
 * one. The caller keeps the previous book and its own live/stale flag.
 *
 * Zero-priced levels are dropped. Kite pads the ladder with `{price: 0,
 * quantity: 0}` entries outside market hours, and a 0 would otherwise become the
 * best bid — collapsing the spread calculation to the full price of the
 * instrument.
 */
export function buildBookFromKiteDepth(depth: KiteDepth | null | undefined): OrderBookState | null {
  if (!depth) return null;

  const clean = (side: KiteDepthLevel[] | undefined) =>
    (Array.isArray(side) ? side : []).filter(
      (l) =>
        l &&
        typeof l.price === 'number' &&
        Number.isFinite(l.price) &&
        l.price > 0 &&
        typeof l.quantity === 'number' &&
        Number.isFinite(l.quantity),
    );

  const buy = clean(depth.buy);
  const sell = clean(depth.sell);
  if (buy.length === 0 && sell.length === 0) return null;

  return buildBookFromDepth(
    buy.map((l) => l.price),
    buy.map((l) => l.quantity),
    sell.map((l) => l.price),
    sell.map((l) => l.quantity),
  );
}

export function buildBookFromDepth(
  bidPrices: number[],
  bidSizes: number[],
  askPrices: number[],
  askSizes: number[],
): OrderBookState {
  const asks: OrderBookLevel[] = [];
  const bids: OrderBookLevel[] = [];

  let askRunningTotal = 0;
  const askCount = Math.min(askPrices.length, LEVEL_COUNT);
  for (let i = 0; i < askCount; i++) {
    const price = askPrices[i];
    const size = askSizes[i] || 0;
    askRunningTotal += size;
    asks.push({ price, size, total: parseFloat(askRunningTotal.toFixed(2)) });
  }

  let bidRunningTotal = 0;
  const bidCount = Math.min(bidPrices.length, LEVEL_COUNT);
  for (let i = 0; i < bidCount; i++) {
    const price = bidPrices[i];
    const size = bidSizes[i] || 0;
    bidRunningTotal += size;
    bids.push({ price, size, total: parseFloat(bidRunningTotal.toFixed(2)) });
  }

  const bestAsk = asks.length > 0 ? asks[0].price : 0;
  const bestBid = bids.length > 0 ? bids[0].price : 0;
  const spread = bestAsk > 0 && bestBid > 0 ? parseFloat((bestAsk - bestBid).toFixed(2)) : 0;
  const spreadPct = bestAsk > 0 ? ((spread / bestAsk) * 100).toFixed(3) : '0.000';
  const midPrice = bestAsk > 0 && bestBid > 0 ? parseFloat(((bestAsk + bestBid) / 2).toFixed(2)) : 0;

  const askLadder = extendLadder(asks, inferStep(askPrices), 1, PADDED_LEVEL_COUNT);
  const bidLadder = extendLadder(bids, inferStep(bidPrices), -1, PADDED_LEVEL_COUNT);

  askLadder.reverse();

  return { asks: askLadder, bids: bidLadder, spread, spreadPct, midPrice };
}
