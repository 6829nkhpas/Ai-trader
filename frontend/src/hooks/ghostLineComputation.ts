/**
 * ghostLineComputation.ts — Data fetching and projection calculation for the
 * predictive ghost line overlay.
 *
 * Engines (selected by `ghostLineMode`):
 *   · 'linear'   → OLS (unweighted linear regression baseline)
 *   · 'volume'   → VWLR (volume-weighted linear regression — "advanced OLS")
 *   · 'curved'   → VWEPR (volume-weighted polynomial / curvature)
 *   · 'forecast' → volatility-aware, regime-conditioned EWMA-drift forecaster
 *
 * Projected timestamps are aligned to contiguous future NSE trading slots via
 * nextSessionSlots() so the curve continues smoothly off the last candle
 * instead of detaching across the overnight/weekend gap. All projections are
 * anchored to the last candle's current close and volatility-bounded so they
 * stay smooth (no cliff).
 */

import { useChartUIStore } from '../store/useChartUIStore';
import { useTradeStore } from '../store/useTradeStore';
import { TIMEFRAME_MS, KITE_INTERVAL_MAP, type Timeframe } from '../utils/chartTypes';

const isTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

/** How many bars to project forward. Kept short so the ghost is a compact
 *  projection off the last candle, not a long streak across the session. */
const PROJECTION_BARS = 6;

/** Lookback window (bars) for every regression engine. Kept identical to the
 *  Rust engines (`predictive::OLS_MAX_WINDOW` and `vwepr::MAX_WINDOW`, both 50)
 *  so the Tauri path and the pure-JS fallback fit over the same bars and agree.
 */
const REGRESSION_WINDOW = 50;

/** A lookback bar. `high`/`low` carry OHLC so the forecaster can classify the
 *  regime (ADX + choppiness); all data sources populate them. */
type LookbackCandle = {
  time: number;
  close: number;
  volume: number;
  high: number;
  low: number;
};

// ── NSE Session constants ─────────────────────────────────────────────────
// NSE trading hours: 09:15 – 15:30 IST (UTC+5:30)
const IST_OFFSET_SEC = 19800;          // +5:30 = 19800 s
const NSE_OPEN_IST   = 33_300;         // 09:15:00 in seconds from IST midnight
const NSE_CLOSE_IST  = 55_800;         // 15:30:00 in seconds from IST midnight

function isWeekend(utcSec: number): boolean {
  const day = new Date(utcSec * 1000).getUTCDay(); // 0=Sun 6=Sat
  return day === 0 || day === 6;
}

/**
 * Infer the true bar interval (seconds) from the actual spacing of recent
 * lookback bars. This is authoritative — it avoids the "vertical line" bug
 * where a timeframe-string mismatch (e.g. chart resolution "10" vs the map key
 * "10m") collapses the interval to the 60s fallback, cramming the whole
 * projection into one bar's width. Uses the median of the most recent
 * consecutive gaps so a rare overnight/weekend gap can't skew it.
 */
function inferBarIntervalSec(bars: { time: number }[]): number {
  if (bars.length < 3) return 0;
  const diffs: number[] = [];
  for (let i = bars.length - 1; i > 0 && diffs.length < 15; i--) {
    const d = bars[i].time - bars[i - 1].time;
    if (d > 0) diffs.push(d);
  }
  if (diffs.length === 0) return 0;
  diffs.sort((a, b) => a - b);
  return diffs[Math.floor(diffs.length / 2)]; // median
}

/** UNIX-second timestamp of the next weekday's 09:15 IST after `fromUtcSec`. */
function nextTradingOpen(fromUtcSec: number): number {
  let candidate = fromUtcSec;
  for (let tries = 0; tries < 7; tries++) {
    const istMidnightUTC =
      Math.floor((candidate + IST_OFFSET_SEC) / 86400) * 86400 - IST_OFFSET_SEC + 86400;
    const openUTC = istMidnightUTC + NSE_OPEN_IST;
    if (!isWeekend(openUTC)) return openUTC;
    candidate = istMidnightUTC;
  }
  return fromUtcSec + 86400;
}

/**
 * Generate `count` future timestamps (seconds) that continue on from the last
 * bar, aligned to valid NSE trading slots. Intraday steps by `intervalSec` and
 * jumps to the next session's 09:15 when it would cross 15:30 / a weekend;
 * daily+ steps by whole intervals skipping weekends. Produces a contiguous,
 * strictly-increasing, in-session sequence.
 */
function nextSessionSlots(lastBarSec: number, intervalSec: number, count: number): number[] {
  const slots: number[] = [];
  const intraday = intervalSec < 86_400;
  let t = lastBarSec;
  for (let k = 0; k < count; k++) {
    let cand = t + intervalSec;
    if (intraday) {
      const istSec = (cand + IST_OFFSET_SEC) % 86400;
      if (isWeekend(cand) || istSec < NSE_OPEN_IST || istSec >= NSE_CLOSE_IST) {
        cand = nextTradingOpen(t);
      }
    } else {
      while (isWeekend(cand)) cand += 86_400;
    }
    slots.push(cand);
    t = cand;
  }
  return slots;
}

// ── Store-synced lookback ──────────────────────────────────────────────────

/** Read the bars the TradingView datafeed rendered (historicalCache + live
 *  ohlcCandles). Keeps the ghost anchored to exactly what the chart shows.
 *  Timestamps in UNIX seconds. */
function readStoreCandles(symbol: string, timeframe: string): LookbackCandle[] {
  const store        = useTradeStore.getState();
  const sym          = symbol.toUpperCase();
  const kiteInterval = KITE_INTERVAL_MAP[timeframe as Timeframe] ?? 'minute';
  const cacheKey     = `${sym}::${timeframe}::${kiteInterval}`; // same key the datafeed writes

  const hist = store.historicalCache[cacheKey] ?? [];
  const live = store.ohlcCandles.filter((c) => c.symbol?.toUpperCase() === sym);
  if (hist.length === 0 && live.length === 0) return [];

  const byTime = new Map<number, { close: number; volume: number; high: number; low: number }>();
  const add = (c: { start_timestamp_ms: number; close: number; volume: number; high: number; low: number }) =>
    byTime.set(c.start_timestamp_ms, { close: c.close, volume: c.volume || 1, high: c.high, low: c.low });
  for (const c of hist) add(c);
  for (const c of live) add(c); // live overrides historical for overlapping bars

  return Array.from(byTime.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([ms, v]) => ({
      time: Math.floor(ms / 1000), close: v.close, volume: v.volume, high: v.high, low: v.low,
    }));
}

/** Freshest bar the chart is showing (live wins), for pinning the anchor. */
function latestStoreBar(symbol: string): { time: number; close: number } | null {
  const store = useTradeStore.getState();
  const sym = symbol.toUpperCase();
  let bestMs = -1;
  let close = 0;
  for (const c of store.ohlcCandles) {
    if (c.symbol?.toUpperCase() === sym && c.start_timestamp_ms > bestMs) {
      bestMs = c.start_timestamp_ms;
      close = c.close;
    }
  }
  if (bestMs < 0) {
    for (const key of Object.keys(store.historicalCache)) {
      if (!key.startsWith(`${sym}::`)) continue;
      const arr = store.historicalCache[key];
      const lastC = arr[arr.length - 1];
      if (lastC && lastC.start_timestamp_ms > bestMs) {
        bestMs = lastC.start_timestamp_ms;
        close = lastC.close;
      }
    }
  }
  return bestMs < 0 ? null : { time: Math.floor(bestMs / 1000), close };
}

async function fetchLookbackCandles(symbol: string, timeframe: string): Promise<LookbackCandle[]> {
  const kiteInterval = KITE_INTERVAL_MAP[timeframe as Timeframe] ?? 'minute';

  // Path 0: in-memory store — authoritative, matches the chart.
  const storeBars = readStoreCandles(symbol, timeframe);
  if (storeBars.length >= 20) {
    console.log(`[GhostLine] Store bars (chart-synced): ${storeBars.length} for ${symbol}`);
    return storeBars;
  }

  // Path A: Tauri IPC — cached bars from QuestDB (bincode).
  if (isTauri()) {
    try {
      const tauri = await import('@tauri-apps/api/core');
      const response = await tauri.invoke<number[] | Uint8Array>('get_historical_view', { symbol, timeframe });
      const buffer = response instanceof Uint8Array ? response : new Uint8Array(response);
      if (buffer.length > 8) {
        const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
        const length = Number(view.getBigUint64(0, true));
        let offset = 8;
        const bars: LookbackCandle[] = [];
        for (let i = 0; i < length; i++) {
          const tsMicro = Number(view.getBigInt64(offset, true));
          // BinaryCandle layout: ts@0, open@8, high@16, low@24, close@32, vol@40.
          const high   = view.getFloat64(offset + 16, true);
          const low    = view.getFloat64(offset + 24, true);
          const close  = view.getFloat64(offset + 32, true);
          const volume = Number(view.getBigInt64(offset + 40, true));
          bars.push({ time: Math.floor(tsMicro / 1_000_000), close, volume, high, low });
          offset += 48;
        }
        if (bars.length >= 20) {
          console.log(`[GhostLine] Tauri historical: ${bars.length} bars for ${symbol}`);
          return bars;
        }
      }
    } catch (err) {
      console.warn('[GhostLine] Tauri get_historical_view failed, falling back to API:', err);
    }
  }

  // Path B: Next.js rewrite — /kite/historical.
  try {
    const to   = new Date();
    const days = timeframe.endsWith('D') || timeframe.endsWith('W') || timeframe.endsWith('M') ? 365 : 10;
    const from = new Date(to.getTime() - days * 86_400_000);
    const fmt  = (d: Date) => d.toISOString().slice(0, 10);
    const url  = `/kite/historical?symbol=${encodeURIComponent(symbol)}&interval=${kiteInterval}&from=${fmt(from)}&to=${fmt(to)}`;
    console.log('[GhostLine] Fetching candles from:', url);
    const res = await fetch(url);
    if (res.ok) {
      const data = await res.json();
      const candles: LookbackCandle[] = (data.candles || []).map((c: any) => ({
        time: typeof c.time === 'number' ? c.time : Math.floor(new Date(c.time).getTime() / 1000),
        close: c.close,
        volume: c.volume || 1.0,
        high: typeof c.high === 'number' ? c.high : c.close,
        low:  typeof c.low  === 'number' ? c.low  : c.close,
      }));
      console.log(`[GhostLine] API candles: ${candles.length} bars`);
      return candles;
    }
    console.warn('[GhostLine] API non-OK:', res.status);
  } catch (err) {
    console.warn('[GhostLine] kite/historical failed:', err);
  }
  return [];
}

// ── OLS linear regression ──────────────────────────────────────────────────

function olsProjection(
  closes: number[], lastTime: number, intervalSec: number, projLen: number,
): { time: number; price: number }[] {
  const n = closes.length;
  if (n < 5) return [];

  let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;
  for (let i = 0; i < n; i++) { sumX += i; sumY += closes[i]; sumXY += i * closes[i]; sumX2 += i * i; }
  const denom = n * sumX2 - sumX * sumX;
  if (denom === 0) return [];

  const slope      = (n * sumXY - sumX * sumY) / denom;
  const intercept  = (sumY - slope * sumX) / n;
  const correction = closes[n - 1] - (intercept + slope * (n - 1)); // anchor to last close

  const pts: { time: number; price: number }[] = [];
  for (let i = 0; i <= projLen; i++) {
    pts.push({
      time:  lastTime + i * intervalSec,
      price: +Math.max(0.01, intercept + slope * (n - 1 + i) + correction).toFixed(2),
    });
  }
  return pts;
}

// ── VWLR: volume-weighted linear regression ("advanced OLS") ─────────────────

function vwlrProjection(
  candles: { close: number; volume: number }[], lastTime: number, intervalSec: number, projLen: number,
): { time: number; price: number }[] {
  const n = candles.length;
  if (n < 5) return [];

  let sw = 0, swx = 0, swy = 0, swxx = 0, swxy = 0;
  for (let i = 0; i < n; i++) {
    const x = i, y = candles[i].close, w = Math.max(candles[i].volume, 1);
    sw += w; swx += w * x; swy += w * y; swxx += w * x * x; swxy += w * x * y;
  }
  const denom = sw * swxx - swx * swx;
  if (Math.abs(denom) < 1e-12) return [];
  const slope = (sw * swxy - swx * swy) / denom;
  if (!Number.isFinite(slope)) return [];

  const anchor = candles[n - 1].close;
  const pts: { time: number; price: number }[] = [];
  for (let i = 0; i <= projLen; i++) {
    pts.push({ time: lastTime + i * intervalSec, price: +Math.max(0.01, anchor + slope * i).toFixed(2) });
  }
  return pts;
}

// ── VWEPR curved projection ─────────────────────────────────────────────────

function vweprProjection(
  candles: { close: number; volume: number }[], lastTime: number, intervalSec: number, projLen: number,
): { time: number; price: number }[] {
  const n = candles.length;
  if (n < 5) return [];

  let sw=0, swx=0, swx2=0, swx3=0, swx4=0, swy=0, swxy=0, swx2y=0;
  for (let i = 0; i < n; i++) {
    const x = i, y = candles[i].close, w = Math.max(candles[i].volume, 1);
    sw += w; swx += w*x; swx2 += w*x*x; swx3 += w*x*x*x; swx4 += w*x*x*x*x;
    swy += w*y; swxy += w*x*y; swx2y += w*x*x*y;
  }
  const A = [[sw,swx,swx2],[swx,swx2,swx3],[swx2,swx3,swx4]];
  const b = [swy, swxy, swx2y];
  const coeffs = solve3x3(A, b);
  if (!coeffs) return olsProjection(candles.map(c => c.close), lastTime, intervalSec, projLen);

  const [a0, a1, a2] = coeffs;
  const correction = candles[n-1].close - (a0 + a1*(n-1) + a2*(n-1)*(n-1));

  const pts: { time: number; price: number }[] = [];
  for (let i = 0; i <= projLen; i++) {
    const x = n - 1 + i;
    pts.push({ time: lastTime + i * intervalSec, price: +Math.max(0.01, a0 + a1*x + a2*x*x + correction).toFixed(2) });
  }
  return pts;
}

function solve3x3(A: number[][], b: number[]): [number,number,number] | null {
  const M = A.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < 3; col++) {
    let max = col;
    for (let r = col+1; r < 3; r++) if (Math.abs(M[r][col]) > Math.abs(M[max][col])) max = r;
    [M[col], M[max]] = [M[max], M[col]];
    if (Math.abs(M[col][col]) < 1e-12) return null;
    for (let r = col+1; r < 3; r++) {
      const f = M[r][col] / M[col][col];
      for (let k = col; k <= 3; k++) M[r][k] -= f * M[col][k];
    }
  }
  const x = [0,0,0];
  for (let i = 2; i >= 0; i--) {
    x[i] = M[i][3];
    for (let j = i+1; j < 3; j++) x[i] -= M[i][j] * x[j];
    x[i] /= M[i][i];
  }
  return [x[0], x[1], x[2]];
}

// ── Regime classifier (faithful port of regime.py: ADX + choppiness) ────────
type OhlcRow = { high: number; low: number; close: number };
const REGIME_ADX_PERIOD = 14;
const REGIME_CHOP_PERIOD = 14;
const REGIME_ADX_TREND_CUTOFF = 25.0;
const REGIME_CHOP_RANGING_CUTOFF = 61.8;

function trueRanges(rows: OhlcRow[]): number[] {
  const trs: number[] = [];
  for (let i = 1; i < rows.length; i++) {
    const h = rows[i].high, l = rows[i].low, pc = rows[i - 1].close;
    if (![h, l, pc].every((v) => Number.isFinite(v))) return [];
    trs.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
  }
  return trs;
}

function wilderSmooth(vals: number[], p: number): number[] {
  if (vals.length < p) return [];
  const out: number[] = [];
  let run = 0;
  for (let i = 0; i < p; i++) run += vals[i];
  out.push(run);
  for (let i = p; i < vals.length; i++) { run = run - run / p + vals[i]; out.push(run); }
  return out;
}

function computeADX(rows: OhlcRow[], period: number): number | null {
  if (rows.length < period + 1) return null;
  const trs = trueRanges(rows);
  if (trs.length < period) return null;
  const plusDM: number[] = [], minusDM: number[] = [];
  for (let i = 1; i < rows.length; i++) {
    const up = rows[i].high - rows[i - 1].high;
    const down = rows[i - 1].low - rows[i].low;
    plusDM.push(up > down && up > 0 ? up : 0);
    minusDM.push(down > up && down > 0 ? down : 0);
  }
  const smTr = wilderSmooth(trs, period), smP = wilderSmooth(plusDM, period), smM = wilderSmooth(minusDM, period);
  const len = Math.min(smTr.length, smP.length, smM.length);
  const dxs: number[] = [];
  for (let i = 0; i < len; i++) {
    if (smTr[i] === 0) continue;
    const pDI = 100 * smP[i] / smTr[i], mDI = 100 * smM[i] / smTr[i];
    const s = pDI + mDI;
    if (s === 0) continue;
    dxs.push(100 * Math.abs(pDI - mDI) / s);
  }
  if (dxs.length === 0) return null;
  const w = dxs.slice(-period);
  return Math.max(0, Math.min(100, w.reduce((a, b) => a + b, 0) / w.length));
}

function computeChoppiness(rows: OhlcRow[], period: number): number | null {
  if (period < 2 || rows.length < period + 1) return null;
  const sub = rows.slice(-(period + 1));
  const trs = trueRanges(sub);
  if (trs.length === 0) return null;
  const highs = sub.slice(1).map((r) => r.high);
  const lows  = sub.slice(1).map((r) => r.low);
  const sumTr = trs.reduce((a, b) => a + b, 0);
  const range = Math.max(...highs) - Math.min(...lows);
  if (range <= 0 || sumTr <= 0) return null;
  return Math.max(0, Math.min(100, 100 * Math.log10(sumTr / range) / Math.log10(period)));
}

function classifyTrendState(rows: OhlcRow[]): 'trending' | 'ranging' | 'transitional' {
  const adx  = computeADX(rows, REGIME_ADX_PERIOD);
  const chop = computeChoppiness(rows, REGIME_CHOP_PERIOD);
  if (adx === null || chop === null) return 'transitional';
  const strong = adx >= REGIME_ADX_TREND_CUTOFF;
  const choppy = chop >= REGIME_CHOP_RANGING_CUTOFF;
  if (strong && !choppy) return 'trending';
  if (!strong && choppy) return 'ranging';
  return 'transitional';
}

// ── Volatility-Aware Forecaster (regime-conditioned EWMA drift) ──────────────
const TREND_CONTINUATION_WEIGHT = 1.5;
const RANGE_REVERSION_WEIGHT = 0.5;

/** Exponentially-weighted mean (span alpha = 2/(n+1); recent weighted more). */
function ewmaMean(values: number[]): number {
  const n = values.length;
  if (n === 0) return 0;
  const oneMinus = 1 - 2 / (n + 1);
  let wsum = 0, vsum = 0;
  for (let i = 0; i < n; i++) {
    const w = Math.pow(oneMinus, n - 1 - i);
    wsum += w; vsum += w * values[i];
  }
  return wsum === 0 ? values.reduce((a, b) => a + b, 0) / n : vsum / wsum;
}

function forecastProjection(
  candles: LookbackCandle[], lastTime: number, intervalSec: number, projLen: number, driftLookback = 30,
): { time: number; price: number }[] {
  const valid = candles.filter((c) => Number.isFinite(c.close) && c.close > 0);
  const closes = valid.map((c) => c.close);
  const n = closes.length;
  if (n < 6) return [];

  const window = closes.slice(-(driftLookback + 1));
  if (window.length < 2) return [];

  const rets: number[] = [];
  for (let i = 1; i < window.length; i++) {
    const ratio = window[i] / window[i - 1];
    if (!Number.isFinite(ratio) || ratio <= 0) return [];
    rets.push(Math.log(ratio));
  }

  let drift = ewmaMean(rets);
  if (!Number.isFinite(drift)) return [];

  // Regime conditioning: amplify momentum in trends, dampen in ranges.
  const trend = classifyTrendState(valid);
  const weight = trend === 'trending' ? TREND_CONTINUATION_WEIGHT
               : trend === 'ranging'  ? RANGE_REVERSION_WEIGHT
               : 1.0;
  drift *= weight;
  console.log(`[GhostLine] Forecast regime=${trend} weight=${weight}`);

  const anchor = closes[n - 1];
  const pts: { time: number; price: number }[] = [];
  for (let i = 0; i <= projLen; i++) {
    pts.push({ time: lastTime + i * intervalSec, price: +Math.max(0.01, anchor * Math.exp(drift * i)).toFixed(2) });
  }
  return pts;
}

// ── Main export ───────────────────────────────────────────────────────────

export async function computeGhostPoints(
  activeSymbol: string,
  effectiveTimeframe: string,
  ghostLineMode: string,
  predictiveSignals: any[],
): Promise<{ time: number; price: number }[]> {
  console.log(`[GhostLine] computeGhostPoints — symbol=${activeSymbol} tf=${effectiveTimeframe} mode=${ghostLineMode}`);

  const lookback = await fetchLookbackCandles(activeSymbol, effectiveTimeframe);
  if (lookback.length < 20) {
    console.warn(`[GhostLine] Not enough candles (${lookback.length})`);
    return [];
  }

  const last = lookback[lookback.length - 1];
  // Prefer the interval measured from the real bars; fall back to the
  // timeframe map only when the spacing can't be inferred.
  const mapInterval = Math.floor((TIMEFRAME_MS[effectiveTimeframe as Timeframe] ?? 60_000) / 1000);
  const barInterval = inferBarIntervalSec(lookback);
  const intervalSec = barInterval > 0 ? barInterval : mapInterval;
  console.log(`[GhostLine] intervalSec=${intervalSec} (map=${mapInterval}, inferred=${barInterval})`);
  if (!Number.isFinite(last.close) || last.close <= 0) return [];

  // Single 50-bar window shared by every engine (OLS, VWLR, VWEPR) and by both
  // the Rust and JS paths, so all four agree on exactly which bars are fit.
  const window = lookback.slice(-REGRESSION_WINDOW);
  let points: { time: number; price: number }[] = [];

  // ── Path 1: backend predictive signal (mode-agnostic ML close) ───────
  if (predictiveSignals.length > 0) {
    const sigs = predictiveSignals.filter(s => s.symbol?.toUpperCase() === activeSymbol.toUpperCase());
    const sig  = sigs[sigs.length - 1] ?? null;
    if (sig) {
      const targetSec = Math.floor(sig.target_timestamp_ms / 1000);
      const predicted = sig.predicted_close_price;
      const dev = Math.abs(predicted - last.close) / last.close;
      const ok  = Number.isFinite(predicted) && predicted > 0 && dev < 0.20;
      if (ok && targetSec > last.time - intervalSec * 10) {
        const N   = PROJECTION_BARS;
        const end = Math.max(targetSec, last.time + intervalSec * N);
        const m   = (predicted - last.close) / N;
        points = Array.from({ length: N + 1 }, (_, i) => ({
          time:  last.time + i * intervalSec,
          price: +(last.close + m * i).toFixed(2),
        }));
        points[points.length - 1] = { time: end, price: +predicted.toFixed(2) };
      }
    }
  }

  // ── Path 2: Tauri Rust engine ───────────────────────────────────────
  if (points.length === 0 && isTauri()) {
    try {
      const tauri = await import('@tauri-apps/api/core');
      const candles = window.map(c => ({ time: c.time, close: c.close, volume: c.volume || 1.0 }));
      const payload = await tauri.invoke<any>('compute_ghost_curve', {
        candles, intervalSec, projectionLength: PROJECTION_BARS,
      });
      useChartUIStore.getState().setAccelerationCoefficient(payload.acceleration_coefficient);
      // OLS, VWLR and VWEPR all come from the single Rust engine so there is
      // exactly ONE implementation of each. Only 'forecast' (which Rust has no
      // equivalent for) falls through to the JS Path 3.
      const raw =
        ghostLineMode === 'linear' ? payload.linear_points :
        ghostLineMode === 'volume' ? payload.volume_points :
        ghostLineMode === 'curved' ? payload.curved_points :
        undefined; // 'forecast' → JS Path 3.
      if (raw?.length > 0) {
        points = raw.map((p: any) => ({ time: p.time, price: +p.value.toFixed(2) }));
        console.log('[GhostLine] Path2 Tauri:', points.length, 'points');
      }
    } catch (err) {
      console.error('[GhostLine] Path2 failed:', err);
    }
  }

  // ── Path 3: pure-JS engines ─────────────────────────────────────────
  if (points.length === 0) {
    console.log('[GhostLine] Path3: pure-JS engine, mode=', ghostLineMode);
    // Fallback (browser preview / Rust unavailable). Every engine fits the
    // same 50-bar `window` as the Rust path so the two agree bar-for-bar.
    const closes = window.map(c => c.close);
    points =
      ghostLineMode === 'linear'   ? olsProjection(closes, last.time, intervalSec, PROJECTION_BARS) :
      ghostLineMode === 'volume'   ? vwlrProjection(window, last.time, intervalSec, PROJECTION_BARS) :
      ghostLineMode === 'forecast' ? forecastProjection(window, last.time, intervalSec, PROJECTION_BARS) :
      vweprProjection(window, last.time, intervalSec, PROJECTION_BARS);
    console.log('[GhostLine] Path3:', points.length, 'points');
  }

  // ── Pin the anchor onto the last candle at its CURRENT price ─────────
  if (points.length > 0) {
    const anchor = latestStoreBar(activeSymbol);
    if (anchor) {
      const dTime  = anchor.time  - points[0].time;
      const dPrice = anchor.close - points[0].price;
      if (dTime !== 0 || Math.abs(dPrice) > 1e-9) {
        points = points.map((p) => ({ time: p.time + dTime, price: +(p.price + dPrice).toFixed(2) }));
      }
    }
  }

  // ── Bound the curve so it stays a smooth continuation (no cliff) ─────
  // Only the CURVED engines ('curved'/'forecast') can produce a runaway cliff
  // that needs clamping. The straight-line engines ('linear' OLS and 'volume'
  // VWLR) must stay perfectly straight — a per-step clamp would bend them into
  // a curve, violating the "rigid straight vector" definition — so skip it.
  const isStraightLine = ghostLineMode === 'linear' || ghostLineMode === 'volume';
  if (points.length > 1 && !isStraightLine) {
    const recent = window.slice(-20).map((c) => c.close);
    let sumAbs = 0;
    for (let i = 1; i < recent.length; i++) sumAbs += Math.abs(recent[i] - recent[i - 1]);
    const avgStep  = (recent.length > 1 ? sumAbs / (recent.length - 1) : 0) || Math.max(0.01, last.close * 0.0005);
    const maxStep  = avgStep * 8.0;
    const maxTotal = maxStep * (points.length - 1) * 5.0;
    const anchorPrice = points[0].price;
    let prev = anchorPrice;
    for (let i = 1; i < points.length; i++) {
      let d = points[i].price - prev;
      if (d >  maxStep) d =  maxStep;
      if (d < -maxStep) d = -maxStep;
      let np = prev + d;
      const dev = np - anchorPrice;
      if (dev >  maxTotal) np = anchorPrice + maxTotal;
      if (dev < -maxTotal) np = anchorPrice - maxTotal;
      points[i] = { time: points[i].time, price: +np.toFixed(2) };
      prev = points[i].price;
    }
  }

  // ── Align projected points to contiguous future NSE session slots ────
  if (points.length > 1) {
    const slots = nextSessionSlots(points[0].time, intervalSec, points.length - 1);
    points = points.map((p, i) => ({ time: i === 0 ? p.time : slots[i - 1], price: p.price }));
  }

  // ── Safety net: times must be strictly forward ──────────────────────
  // Guarantees the line can NEVER render vertically. If any upstream step
  // collapsed the timestamps (span ≤ 0 or non-increasing), rebuild a clean
  // forward ramp from the anchor using the resolved interval.
  if (points.length > 1) {
    const span = points[points.length - 1].time - points[0].time;
    let strictlyIncreasing = true;
    for (let i = 1; i < points.length; i++) {
      if (points[i].time <= points[i - 1].time) { strictlyIncreasing = false; break; }
    }
    if (!(span > 0) || !strictlyIncreasing) {
      const base = points[0].time;
      points = points.map((p, i) => ({ time: base + i * intervalSec, price: p.price }));
      console.warn('[GhostLine] collapsed/non-increasing times — forced forward ramp');
    }
  }

  console.log(
    '[GhostLine] FINAL times=', points.map((p) => p.time).join(','),
    'prices=', points.map((p) => p.price).join(','),
  );
  return points;
}
