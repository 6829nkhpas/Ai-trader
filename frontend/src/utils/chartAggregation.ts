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
): { candles: ChartCandle[]; volumes: VolumeBar[]; ema9: EmaPoint[]; ema21: EmaPoint[]; isIndexVolume: boolean } {
  const empty = { candles: [], volumes: [], ema9: [], ema21: [], isIndexVolume: false };
  const intervalMs = TIMEFRAME_MS[timeframe];
  if (!intervalMs) return empty;

  const filtered = symbol
    ? rawCandles.filter((c) => c.symbol.toUpperCase() === symbol.toUpperCase())
    : rawCandles;

  // BUG-3: Guard against zero/negative timestamps and invalid OHLC values.
  // lightweight-charts throws "Data must be in ascending order" if any candle
  // has time <= 0, and NaN prices cause invisible (zero-height) candles.
  const valid = filtered.filter((c) =>
    c.start_timestamp_ms > 0 &&
    Number.isFinite(c.open) && c.open > 0 &&
    Number.isFinite(c.high) && c.high >= c.open &&
    Number.isFinite(c.low)  && c.low  > 0 &&
    Number.isFinite(c.close) && c.close > 0
  );

  const sorted = [...valid].sort((a, b) => a.start_timestamp_ms - b.start_timestamp_ms);

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

  // ── Index Volume Proxy ──────────────────────────────────────────────
  // Indices (NIFTY 50, BANK NIFTY, etc.) have volume=0 from the Kite API
  // because they are calculated values, not directly traded instruments.
  // When all volume values are zero, generate synthetic "activity bars"
  // based on the candle's price range (high - low). This gives visual
  // context about intra-bar volatility — a common technique used by
  // platforms like TradingView for index charts.
  const allZeroVolume = volumes.length > 0 && volumes.every((v) => v.value === 0);
  if (allZeroVolume && candles.length > 0) {
    for (let i = 0; i < candles.length; i++) {
      const c = candles[i];
      // Price spread as a proxy for activity (in absolute INR terms)
      const spread = c.high - c.low;
      volumes[i] = {
        time: c.time,
        value: spread > 0 ? spread : 0.01, // Tiny fallback so bars are visible
        color: c.close >= c.open ? COLORS.volumeUp : COLORS.volumeDown,
      };
    }
  }

  const ema9 = calculateEMA(closes, 9);
  const ema21 = calculateEMA(closes, 21);

  return { candles, volumes, ema9, ema21, isIndexVolume: allZeroVolume };
}
