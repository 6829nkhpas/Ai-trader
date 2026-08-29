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
import { kiteFetch } from '../lib/kiteFetch';
import { debugLog } from '../lib/debugLog';

/** Fallback projection length (bars) when the visible range is unknown. */
const PROJECTION_BARS = 6;

/** Dynamic projection sizing: the ghost extends forward by this fraction of the
 *  number of bars currently visible on screen, so it scales with the user's
 *  zoom (zoom out → longer, zoom in → shorter), clamped to sane bounds. */
const PROJECTION_FRACTION = 0.12;
const MIN_PROJECTION_BARS = 3;
const MAX_PROJECTION_BARS = 20;

/** Compute how many bars to project from how many bars are visible. Counting
 *  ACTUAL bars (not raw seconds) makes this immune to overnight/weekend gaps. */
function dynamicProjectionBars(lookback: { time: number }[], visibleFromSec: number): number {
  if (!(visibleFromSec > 0)) return PROJECTION_BARS;
  let visibleCount = 0;
  for (const c of lookback) if (c.time >= visibleFromSec) visibleCount++;
  if (visibleCount <= 0) return PROJECTION_BARS;
  const raw = Math.round(visibleCount * PROJECTION_FRACTION);
  return Math.max(MIN_PROJECTION_BARS, Math.min(MAX_PROJECTION_BARS, raw));
}

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

/**
 * Resolve the projection step interval (seconds) for a given display timeframe.
 *
 * The display timeframe map is authoritative: it is the grid the user sees on
 * the chart, so the ghost line must step on that grid to land on displayed bar
 * boundaries. The inferred bar gap is only a fallback for timeframes missing
 * from the map, because stored bars are at the Kite *base* interval (e.g. `2m`
 * stores 1-minute bars, `75m` stores 15-minute bars) — see the note in
 * `computeGhostPoints` for why the inferred gap is wrong for aggregated
 * timeframes.
 *
 * Exported (pure) so the resolution precedence is unit-testable without driving
 * stores/IPC.
 */
export function resolveIntervalSec(
  effectiveTimeframe: string,
  lookback: { time: number }[],
): number {
  const mapInterval = Math.floor((TIMEFRAME_MS[effectiveTimeframe as Timeframe] ?? 0) / 1000);
  if (mapInterval > 0) return mapInterval;
  const barInterval = inferBarIntervalSec(lookback);
  if (barInterval > 0) {
    console.warn(
      `[GhostLine] resolveIntervalSec — timeframe "${effectiveTimeframe}" missing from TIMEFRAME_MS map; falling back to inferred bar interval ${barInterval}s`,
    );
    return barInterval;
  }
  console.warn(
    `[GhostLine] resolveIntervalSec — timeframe "${effectiveTimeframe}" missing from map and bar interval could not be inferred; using 60s default`,
  );
  return 60;
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
  const allLive = store.ohlcCandles.filter((c) => c.symbol?.toUpperCase() === sym);

  // Only merge live bars that sit on the DISPLAY timeframe's grid.
  //
  // `ohlcCandles` carries the WS feed's own base interval, which is NOT the
  // display interval for aggregated timeframes: a `2m` chart stores 1-minute
  // bars, `75m` stores 15-minute bars, `2h` stores 1-hour bars. Merging those
  // raw bars into the aggregated series mixed two resolutions in one array, so
  // the regression was fitted over bars with inconsistent spacing and closes
  // that never appear on the chart — the reported "ghost line shows wrong
  // values". Filtering to grid-aligned timestamps keeps the live *update* of the
  // forming bar (the reason live is merged at all) and discards intra-bar
  // samples that the chart does not display as separate candles.
  const intervalMs = TIMEFRAME_MS[timeframe as Timeframe] ?? 0;
  const histTimes = new Set(hist.map((c) => c.start_timestamp_ms));
  const live =
    intervalMs > 0
      ? allLive.filter(
          (c) =>
            histTimes.has(c.start_timestamp_ms) ||
            c.start_timestamp_ms % intervalMs === 0,
        )
      : allLive;

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

/**
 * The freshest traded price for `symbol`, used to pin the projection's anchor
 * PRICE to the live market.
 *
 * Price only — deliberately no timestamp. The previous version returned a
 * `{ time, close }` pair taken from a scan over `ohlcCandles` (base interval)
 * with a fallback that scanned EVERY `historicalCache` key beginning with
 * `SYMBOL::`, i.e. any timeframe's bars. The caller then shifted the whole
 * projection by `anchor.time - points[0].time`, so an anchor from a different
 * resolution slid the entire line off the displayed candles. The anchor's TIME
 * must come from the same series the projection was fitted on (the caller
 * already has it as `last.time`); only the price needs to be live.
 *
 * `intervalMs` restricts the scan to bars on the display grid, matching
 * `readStoreCandles`, so an intra-bar sample of an aggregated timeframe cannot
 * win.
 */
function latestStorePrice(symbol: string, intervalMs: number): number | null {
  const store = useTradeStore.getState();
  const sym = symbol.toUpperCase();
  let bestMs = -1;
  let close = 0;
  for (const c of store.ohlcCandles) {
    if (c.symbol?.toUpperCase() !== sym) continue;
    if (intervalMs > 0 && c.start_timestamp_ms % intervalMs !== 0) continue;
    if (c.start_timestamp_ms > bestMs) {
      bestMs = c.start_timestamp_ms;
      close = c.close;
    }
  }
  if (bestMs < 0) return null;
  return Number.isFinite(close) && close > 0 ? close : null;
}

async function fetchLookbackCandles(symbol: string, timeframe: string): Promise<LookbackCandle[]> {
  const kiteInterval = KITE_INTERVAL_MAP[timeframe as Timeframe] ?? 'minute';

  // Path 0: in-memory store — authoritative, matches the chart.
  const storeBars = readStoreCandles(symbol, timeframe);
  if (storeBars.length >= 20) {
    debugLog(`[GhostLine] Store bars (chart-synced): ${storeBars.length} for ${symbol}`);
    return storeBars;
  }

  // Kite REST via the gateway, through `kiteFetch` so the `/kite` prefix (and the
  // same-origin route handler that holds the gateway credential) is applied in one
  // place. Path is the part AFTER `/kite`.
  try {
    const to   = new Date();
    const days = timeframe.endsWith('D') || timeframe.endsWith('W') || timeframe.endsWith('M') ? 365 : 10;
    const from = new Date(to.getTime() - days * 86_400_000);
    const fmt  = (d: Date) => d.toISOString().slice(0, 10);
    const url  = `/historical?symbol=${encodeURIComponent(symbol)}&interval=${kiteInterval}&from=${fmt(from)}&to=${fmt(to)}`;
    debugLog('[GhostLine] Fetching candles from /kite:', url);
    const res = await kiteFetch(url);
    if (res.ok) {
      const data = await res.json();
      const candles: LookbackCandle[] = (data.candles || []).map((c: any) => ({
        time: typeof c.time === 'number' ? c.time : Math.floor(new Date(c.time).getTime() / 1000),
        close: c.close,
        volume: c.volume || 1.0,
        high: typeof c.high === 'number' ? c.high : c.close,
        low:  typeof c.low  === 'number' ? c.low  : c.close,
      }));
      debugLog(`[GhostLine] API candles: ${candles.length} bars`);
      return candles;
    }
    console.warn('[GhostLine] API non-OK:', res.status);
  } catch (err) {
    console.warn('[GhostLine] kite/historical failed:', err);
  }
  return [];
}

// ── OLS linear regression ──────────────────────────────────────────────────

/** Exported for unit tests: the deterministic OLS slope fitted over the
 *  given closes (anchored at the last close). Pure, no store/IPC. */
export function olsSlope(closes: number[]): number {
  const n = closes.length;
  if (n < 5) return 0;
  let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;
  for (let i = 0; i < n; i++) { sumX += i; sumY += closes[i]; sumXY += i * closes[i]; sumX2 += i * i; }
  const denom = n * sumX2 - sumX * sumX;
  if (denom === 0) return 0;
  return (n * sumXY - sumX * sumY) / denom;
}

function olsProjection(
  closes: number[], lastTime: number, intervalSec: number, projLen: number,
): { time: number; price: number }[] {
  const n = closes.length;
  if (n < 5) return [];

  const slope      = olsSlope(closes);
  if (!Number.isFinite(slope)) return [];
  const sumX = (n * (n - 1)) / 2;
  const sumY = closes.reduce((a, b) => a + b, 0);
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

export function vweprProjection(
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

export function forecastProjection(
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
  debugLog(`[GhostLine] Forecast regime=${trend} weight=${weight}`);

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
  visibleFromSec: number = 0,
): Promise<{ time: number; price: number }[]> {
  debugLog(`[GhostLine] computeGhostPoints — symbol=${activeSymbol} tf=${effectiveTimeframe} mode=${ghostLineMode}`);

  const lookback = await fetchLookbackCandles(activeSymbol, effectiveTimeframe);
  if (lookback.length < 20) {
    console.warn(`[GhostLine] Not enough candles (${lookback.length})`);
    return [];
  }

  const last = lookback[lookback.length - 1];
  // Resolution of the forward projection step.
  //
  // The display timeframe (e.g. `2m`, `75m`, `2h`) is the grid the user sees on
  // the chart, and the ghost line must step on that same grid so it lands on
  // displayed bar boundaries. The TIMEFRAME_MS map gives us exactly that.
  //
  // `inferBarIntervalSec` measures the median gap of the *stored* bars, but the
  // stored bars are at the Kite *base* interval, NOT the display interval: a
  // `2m` chart stores 1-minute bars, `75m` stores 15-minute bars, `2h` stores
  // 1-hour bars, etc. For aggregated timeframes (`2m`/`4m`/`75m`/`125m`/`2h`/
  // `3h`/`4h`/`1W`/`1M`) the inferred gap is the base interval, so the
  // projection steps at the base interval → 2–5× too many points, off the
  // displayed bar grid → visible jitter/"unstable" ghost line.
  //
  // Therefore the display-timeframe map wins; `barInterval` is kept only as a
  // fallback when the map has no entry for this timeframe.
  const intervalSec = resolveIntervalSec(effectiveTimeframe, lookback);
  debugLog(`[GhostLine] intervalSec=${intervalSec}`);
  if (!Number.isFinite(last.close) || last.close <= 0) return [];

  // Length scales with the current zoom (fraction of visible bars).
  const projBars = dynamicProjectionBars(lookback, visibleFromSec);
  debugLog(`[GhostLine] projBars=${projBars} (visibleFromSec=${visibleFromSec})`);

  // Single 50-bar window shared by every engine (OLS, VWLR, VWEPR) and by both
  // the Rust and JS paths, so all four agree on exactly which bars are fit.
  const window = lookback.slice(-REGRESSION_WINDOW);
  let points: { time: number; price: number }[] = [];

  // ── Path 1: backend predictive signal (forecast-mode ML close) ──────
  // The predictive signal is the ML/forward-looking engine, so it only drives
  // the projection when the user selected `forecast`. The other modes
  // (`linear` OLS / `volume` VWLR / `curved` VWEPR) must win via Path 2 (Rust)
  // or Path 3 (pure-JS) so toggling the engine in GhostLineToggle actually
  // changes which projection is drawn while a signal is live.
  if (ghostLineMode === 'forecast' && predictiveSignals.length > 0) {
    const sigs = predictiveSignals.filter(s => s.symbol?.toUpperCase() === activeSymbol.toUpperCase());
    const sig  = sigs[sigs.length - 1] ?? null;
    if (sig) {
      const targetSec = Math.floor(sig.target_timestamp_ms / 1000);
      const predicted = sig.predicted_close_price;
      const dev = Math.abs(predicted - last.close) / last.close;
      const ok  = Number.isFinite(predicted) && predicted > 0 && dev < 0.20;
      if (ok && targetSec > last.time - intervalSec * 10) {
        const N   = projBars;
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

  // ── Projection engines ──────────────────────────────────────────────
  if (points.length === 0) {
    debugLog('[GhostLine] engine mode=', ghostLineMode);
    // Window sizes stay pinned to the retired Rust engines' constants
    // (predictive::OLS_MAX_WINDOW / vwepr::MAX_WINDOW) because `quant-core` still
    // fits over the same bars server-side — the agent's read of a projection and
    // the user's must not disagree.
    const closes = window.map(c => c.close);
    points =
      ghostLineMode === 'linear'   ? olsProjection(closes, last.time, intervalSec, projBars) :
      ghostLineMode === 'volume'   ? vwlrProjection(window, last.time, intervalSec, projBars) :
      ghostLineMode === 'forecast' ? forecastProjection(window, last.time, intervalSec, projBars) :
      vweprProjection(window, last.time, intervalSec, projBars);
    debugLog('[GhostLine] Path3:', points.length, 'points');
  }

  // ── Pin the anchor onto the last candle at its CURRENT price ─────────
  //
  // PRICE-ONLY shift. Every engine already anchors `points[0].time` to
  // `last.time` — the last bar of the very series it was fitted on, which is by
  // construction on the display grid. Re-deriving the anchor's TIME from a
  // separate store scan was what let a bar from another resolution slide the
  // whole line off the displayed candles. The only thing worth refreshing here
  // is the price, so the ghost starts at the live close rather than the close of
  // the last completed bar.
  if (points.length > 0) {
    const livePrice = latestStorePrice(activeSymbol, TIMEFRAME_MS[effectiveTimeframe as Timeframe] ?? 0);
    if (livePrice !== null) {
      const dPrice = livePrice - points[0].price;
      if (Math.abs(dPrice) > 1e-9) {
        points = points.map((p) => ({ time: p.time, price: +(p.price + dPrice).toFixed(2) }));
      }
    }
  }

  // ── Bound the curve so it stays a smooth continuation (no cliff) ─────
  // Only the CURVED engines ('curved' VWEPR / 'forecast') can produce a runaway
  // cliff that needs clamping. The straight-line engines ('linear' OLS and
  // 'volume' VWLR) must stay perfectly straight — a per-step clamp would bend
  // them into a curve, violating the "rigid straight vector" definition — so we
  // skip the clamp entirely for them (isStraightLine).
  //
  // Tuning (loosened so genuine curvature survives):
  //   · maxStep = avgStep * 12   — per-step cap kept ONLY as a guard against
  //     truly pathological single-step spikes (a bad tick / NaN blow-up). It is
  //     deliberately generous: a VWEPR parabola or a Forecast
  //     anchor·exp(drift·i) on a volatile instrument legitimately produces steps
  //     far larger than the trailing average as it accelerates.
  //   · maxTotal = maxStep * (points.length - 1) * 2 — the total deviation
  //     budget SCALES WITH PROJECTION LENGTH instead of a flat 5×. The old
  //     `maxStep * (N-1) * 5` (= avgStep * 40 * (N-1)) bit for accelerating
  //     curves, flattening the projection to anchorPrice ± maxTotal so the line
  //     looked like it "gave up" / pointed the wrong way. Scaling at 2× the
  //     per-step budget over the projection length lets a real curve reach its
  //     natural apex while still rejecting a flat-out vertical blow-up.
  const isStraightLine = ghostLineMode === 'linear' || ghostLineMode === 'volume';
  if (points.length > 1 && !isStraightLine) {
    const recent = window.slice(-20).map((c) => c.close);
    let sumAbs = 0;
    for (let i = 1; i < recent.length; i++) sumAbs += Math.abs(recent[i] - recent[i - 1]);
    const avgStep  = (recent.length > 1 ? sumAbs / (recent.length - 1) : 0) || Math.max(0.01, last.close * 0.0005);
    const maxStep  = avgStep * 12.0;
    const maxTotal = maxStep * (points.length - 1) * 2.0;
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

  debugLog(
    '[GhostLine] FINAL times=', points.map((p) => p.time).join(','),
    'prices=', points.map((p) => p.price).join(','),
  );
  return points;
}
