// utils/radarData.ts — Native scan helpers for the Quant Radar (FEAT-037).
//
// Desktop-first by design: all candle fetching and pattern/strategy detection
// happen in the Rust backend for near-native speed. The frontend just invokes
// `scan_radar_symbol` (fetch + locate in one native call) and renders the
// located detections. There is NO browser fetch path — the app runs as a
// Tauri desktop application only.

import type { Timeframe } from './chartTypes';

const isTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

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
 * "pool not ready", "no data") instead of a generic message. Returns `null`
 * only when not running inside the Tauri desktop app.
 */
export async function scanRadarSymbol(
  symbol: string,
  timeframe: Timeframe,
  lookback = 60,
): Promise<RadarScan | null> {
  if (!isTauri()) return null;
  const { invoke } = await import('@tauri-apps/api/core');
  return await invoke<RadarScan>('scan_radar_symbol', {
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
  if (!isTauri()) return null;
  if (candles.length === 0) return null;
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    return await invoke<RadarScan>('scan_quant_radar', {
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
