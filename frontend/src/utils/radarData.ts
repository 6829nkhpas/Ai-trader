// utils/radarData.ts — Scan helpers for the Quant Radar (FEAT-037).
//
// All candle fetching and pattern/strategy detection happen in Rust for
// near-native speed: `scan_radar_symbol` (fetch + locate in one call) and
// `scan_quant_radar` (CPU-only, over caller-supplied candles). The frontend
// just invokes and renders the located detections — the detection math is never
// reimplemented in TS.
//
// On desktop those run in-process. In a browser they route through the bridge
// to the equivalent `tool-server` routes, which reuse the same `quant-core`
// crate, so both paths run identical logic.

import type { Timeframe } from './chartTypes';
import { bridgeInvoke } from '../lib/bridge';

// ── Located detection contracts (mirror the Rust scanner structs) ─────────

export interface LocatedPattern {
  name: string;
  bias: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  candle_index: number;
  time: number; // UNIX seconds
  open: number;
  high: number;
  low: number;
  close: number;
  start_time?: number;
}

export interface LocatedStrategy {
  name: string;
  bias: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  candle_index: number;
  time: number; // UNIX seconds
  price: number;
  level: number | null;
}

export interface RadarScan {
  symbol: string;
  timeframe: string;
  candle_count: number;
  last_close: number;
  last_time: number;
  trend_score: number;
  momentum_state: string;
  volatility_state: string;
  volume_flow_state: string;
  patterns: LocatedPattern[];
  strategies: LocatedStrategy[];
}

/** Timed OHLCV candle — the input contract for the in-memory `scan_quant_radar`. */
export interface TimedCandle {
  time: number; // UNIX seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/**
 * Fetch candles AND scan them in a single native Rust call.
 *
 * This is the primary radar entrypoint. The backend loads candles from
 * QuestDB via the in-process Postgres pool (proactively backfilling from
 * Kite when credentials exist) and runs the located scanner — no network
 * round-trips from the frontend.
 *
 * Throws on backend failure so callers can surface the real reason (e.g.
 * "pool not ready", "no data") instead of a generic message. In a browser,
 * until the `tool-server` route lands, that failure is a
 * `BridgeUnsupportedError` naming the missing surface.
 */
export async function scanRadarSymbol(
  symbol: string,
  timeframe: Timeframe,
  lookback = 60,
): Promise<RadarScan | null> {
  return await bridgeInvoke<RadarScan>('scan_radar_symbol', {
    symbol: symbol.toUpperCase(),
    timeframe,
    lookback,
  });
}

/**
 * Scan a candle series the caller already has in memory (e.g. the active
 * chart's candles) via the native CPU-only scanner. Zero I/O — used for
 * instant rescans without re-fetching from the DB.
 */
export async function scanInMemory(
  symbol: string,
  timeframe: Timeframe,
  candles: TimedCandle[],
  lookback = 60,
): Promise<RadarScan | null> {
  if (candles.length === 0) return null;
  try {
    return await bridgeInvoke<RadarScan>('scan_quant_radar', {
      symbol: symbol.toUpperCase(),
      timeframe,
      candles,
      lookback,
    });
  } catch (err) {
    console.warn(`[Radar] scan_quant_radar failed for ${symbol}:`, err);
    return null;
  }
}

// ── Bias → colour mapping for UI + chart markers ──────────────────────────

export const BIAS_COLORS: Record<string, string> = {
  BULLISH: '#22c55e',
  BEARISH: '#ef4444',
  NEUTRAL: '#eab308',
};
