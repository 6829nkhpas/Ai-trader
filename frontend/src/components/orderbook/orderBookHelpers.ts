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

/**
 * Validate an order book that came from OUTSIDE the running program.
 *
 * The component caches the book in `localStorage` and reads it back on mount and
 * on every symbol change. That read used to be `JSON.parse(cached)` inside a
 * try/catch — which guards the PARSE, not the SHAPE. Any entry that parsed
 * successfully was accepted as an `OrderBookState`, so a `{}`, a null-ish object,
 * or an entry written by an earlier version of this schema set `book` to something
 * with no `asks`/`bids`. The very next render then ran `book.asks.filter(...)` and
 * threw `TypeError: Cannot read properties of undefined (reading 'filter')`.
 * Because that happens during render, and the sidebar has no error boundary of its
 * own, it took the whole terminal down — the reported crash when collapsing or
 * re-expanding the order book (which remounts this component).
 *
 * Returns null for anything that is not a usable book, so the caller can fall back
 * to `createEmptyBook()` rather than trusting a cast. Levels are filtered to
 * finite numbers: a `NaN` price would render as "NaN" and a `NaN` size would
 * poison the depth-bar scaling for the whole ladder.
 */
export function parseCachedBook(raw: string | null): OrderBookState | null {
  if (!raw) return null;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;

  const o = parsed as Record<string, unknown>;
  if (!Array.isArray(o.asks) || !Array.isArray(o.bids)) return null;

  const levels = (side: unknown[]): OrderBookLevel[] =>
    side
      .filter(
        (l): l is OrderBookLevel =>
          !!l &&
          typeof l === 'object' &&
          typeof (l as OrderBookLevel).price === 'number' &&
          Number.isFinite((l as OrderBookLevel).price) &&
          typeof (l as OrderBookLevel).size === 'number' &&
          Number.isFinite((l as OrderBookLevel).size),
      )
      .map((l) => ({
        price: l.price,
        size: l.size,
        total: typeof l.total === 'number' && Number.isFinite(l.total) ? l.total : l.size,
        ...(l.synthetic ? { synthetic: true } : {}),
      }));

  const asks = levels(o.asks);
  const bids = levels(o.bids);
  // An entry whose every level was junk is not a book worth restoring.
  if (asks.length === 0 && bids.length === 0) return null;

  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

  return {
    asks,
    bids,
    spread: num(o.spread),
    spreadPct: typeof o.spreadPct === 'string' ? o.spreadPct : '0.000',
    midPrice: num(o.midPrice),
  };
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
