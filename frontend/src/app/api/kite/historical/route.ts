// Mock Kite /historical endpoint for E2E testing (ALPHA_TEST_MODE)
//
// Generates deterministic synthetic OHLC candles for any NSE symbol.
// The base price is derived from the symbol name so different symbols
// render visually distinct charts. This replaces the real Kite Historical
// API which is unavailable in test mode (Next.js rewrites are disabled).
//
// Response format matches the aggregator's /api/kite/historical response:
//   { candles: [ { time, open, high, low, close, volume }, ... ] }
import { NextResponse } from 'next/server';

function symToBasePrice(symbol: string): number {
  // Deterministic price derived from symbol bytes → 500–5000 range
  let hash = 0;
  for (let i = 0; i < symbol.length; i++) {
    hash = (hash * 31 + symbol.charCodeAt(i)) & 0xffffffff;
  }
  return 500 + (Math.abs(hash) % 4500);
}

function generateMockCandles(
  symbol: string,
  interval: string,
  fromStr: string,
  toStr: string,
): { time: number; open: number; high: number; low: number; close: number; volume: number }[] {
  const from = fromStr ? new Date(fromStr).getTime() : Date.now() - 60 * 24 * 60 * 60 * 1000;
  const to   = toStr   ? new Date(toStr).getTime()   : Date.now();

  // Determine bucket size in ms from interval string
  const intervalMs: Record<string, number> = {
    minute:    60_000,
    '3minute': 3 * 60_000,
    '5minute': 5 * 60_000,
    '10minute':10 * 60_000,
    '15minute':15 * 60_000,
    '30minute':30 * 60_000,
    '60minute':60 * 60_000,
    day:       24 * 60 * 60_000,
  };
  const bucketMs = intervalMs[interval] ?? intervalMs['10minute'];

  // Skip non-trading hours for sub-day intervals
  const isIntraday = bucketMs < 24 * 60 * 60_000;

  let price = symToBasePrice(symbol);
  const candles = [];

  // Seeded pseudo-random for reproducibility
  let seed = price;
  const rand = () => {
    seed = (seed * 1664525 + 1013904223) & 0xffffffff;
    return (seed >>> 0) / 0xffffffff;
  };

  for (let t = from; t < to; t += bucketMs) {
    if (isIntraday) {
      const hour = new Date(t).getUTCHours() + 5; // IST offset approximation
      const min  = new Date(t).getUTCMinutes();
      // NSE: 09:15–15:30 IST = 03:45–10:00 UTC
      const minuteOfDay = hour * 60 + min;
      if (minuteOfDay < 225 || minuteOfDay > 600) continue; // outside market hours
    }

    const move    = (rand() - 0.49) * price * 0.003; // ±0.3% per bar
    const open    = price;
    const close   = Math.max(1, +(price + move).toFixed(2));
    const spread  = price * (0.002 + rand() * 0.003);
    const high    = +(Math.max(open, close) + spread * rand()).toFixed(2);
    const low     = +(Math.min(open, close) - spread * rand()).toFixed(2);
    const volume  = Math.round(50000 + rand() * 200000);

    candles.push({
      time: Math.floor(t / 1000), // seconds (lightweight-charts format)
      open,
      high,
      low,
      close,
      volume,
    });

    price = close; // random walk
  }

  return candles;
}

export async function GET(request: Request) {
  if (!process.env.ALPHA_TEST_MODE) {
    return NextResponse.json({ error: 'Service unavailable' }, { status: 503 });
  }

  const { searchParams } = new URL(request.url);
  const symbol   = (searchParams.get('symbol') || 'RELIANCE').toUpperCase();
  const interval = searchParams.get('interval') || '10minute';
  const from     = searchParams.get('from') || '';
  const to       = searchParams.get('to')   || '';

  const candles = generateMockCandles(symbol, interval, from, to);

  return NextResponse.json({ candles, symbol, interval });
}
