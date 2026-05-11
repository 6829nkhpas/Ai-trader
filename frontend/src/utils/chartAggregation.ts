import type { OhlcCandle } from '../store/useTradeStore';
import type { Timeframe, ChartCandle, VolumeBar, EmaPoint } from './chartTypes';
import { TIMEFRAME_MS, COLORS } from './chartTypes';

// ── EMA Calculation Engine ────────────────────────────────────────────────

export function calculateEMA(
  closes: { time: number; value: number }[],
  period: number
): EmaPoint[] {
  if (closes.length === 0) return [];
  const result: EmaPoint[] = [];
  const k = 2 / (period + 1);
  let sum = 0;
  for (let i = 0; i < closes.length; i++) {
    if (i < period) {
      sum += closes[i].value;
      result.push({ time: closes[i].time, value: sum / (i + 1) });
    } else {
      const ema = closes[i].value * k + result[result.length - 1].value * (1 - k);
      result.push({ time: closes[i].time, value: ema });
    }
  }
  return result;
}

// ── Candle Aggregation ───────────────────────────────────────────────────

export function aggregateCandles(
  rawCandles: OhlcCandle[],
  timeframe: Timeframe,
  symbol: string
): { candles: ChartCandle[]; volumes: VolumeBar[]; ema9: EmaPoint[]; ema21: EmaPoint[] } {
  const empty = { candles: [], volumes: [], ema9: [], ema21: [] };
  const intervalMs = TIMEFRAME_MS[timeframe];
  if (!intervalMs) return empty;

  const filtered = symbol
    ? rawCandles.filter((c) => c.symbol.toUpperCase() === symbol.toUpperCase())
    : rawCandles;

  const sorted = [...filtered].sort((a, b) => a.start_timestamp_ms - b.start_timestamp_ms);

  const buckets = new Map<
    number,
    { open: number; high: number; low: number; close: number; volume: number }
  >();

  for (const candle of sorted) {
    const bucketKey = Math.floor(candle.start_timestamp_ms / intervalMs) * intervalMs;
    const existing = buckets.get(bucketKey);
    if (existing) {
      existing.high = Math.max(existing.high, candle.high);
      existing.low = Math.min(existing.low, candle.low);
      existing.close = candle.close;
      existing.volume += candle.volume;
    } else {
      buckets.set(bucketKey, {
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: candle.volume,
      });
    }
  }

  const candles: ChartCandle[] = [];
  const volumes: VolumeBar[] = [];
  const closes: { time: number; value: number }[] = [];
  const keys = Array.from(buckets.keys()).sort((a, b) => a - b);

  for (const key of keys) {
    const b = buckets.get(key)!;
    const timeSec = Math.floor(key / 1000);
    const isUp = b.close >= b.open;
    candles.push({ time: timeSec, open: b.open, high: b.high, low: b.low, close: b.close });
    volumes.push({ time: timeSec, value: b.volume, color: isUp ? COLORS.volumeUp : COLORS.volumeDown });
    closes.push({ time: timeSec, value: b.close });
  }

  const ema9 = calculateEMA(closes, 9);
  const ema21 = calculateEMA(closes, 21);

  return { candles, volumes, ema9, ema21 };
}
