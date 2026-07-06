/**
 * ghostLineComputation.ts — Data fetching and projection calculation for
 * the predictive ghost line overlay (OLS / VWEPR engines).
 *
 * All projected timestamps are remapped through remapToTradingHours() so
 * that points falling in overnight / weekend gaps are pushed into the next
 * valid NSE trading session.  TradingView Advanced Charts hides timestamps
 * that fall outside the loaded session — without this remap the ghost line
 * shapes are created but invisible.
 */

import { useChartUIStore } from '../store/useChartUIStore';
import { TIMEFRAME_MS, KITE_INTERVAL_MAP, type Timeframe } from '../utils/chartTypes';

const isTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

// ── NSE Session constants ─────────────────────────────────────────────────
// NSE trading hours: 09:15 – 15:30 IST (UTC+5:30)
const IST_OFFSET_SEC = 19800;          // +5:30 = 19800 s
const NSE_OPEN_IST   = 33_300;         // 09:15:00 in seconds from IST midnight
const NSE_CLOSE_IST  = 55_800;         // 15:30:00 in seconds from IST midnight
const NSE_SESSION_SECS = NSE_CLOSE_IST - NSE_OPEN_IST; // 22 500 s = 6 h 15 m

function isWeekend(utcSec: number): boolean {
  const day = new Date(utcSec * 1000).getUTCDay(); // 0=Sun 6=Sat
  return day === 0 || day === 6;
}

/** Return the UNIX-second timestamp of the next weekday's 09:15 IST. */
function nextTradingOpen(fromUtcSec: number): number {
  let candidate = fromUtcSec;
  for (let tries = 0; tries < 7; tries++) {
    // Advance to the IST midnight of the next calendar day
    const istMidnightUTC =
      Math.floor((candidate + IST_OFFSET_SEC) / 86400) * 86400 - IST_OFFSET_SEC + 86400;
    const openUTC = istMidnightUTC + NSE_OPEN_IST;
    if (!isWeekend(openUTC)) return openUTC; // next weekday found
    candidate = istMidnightUTC;
  }
  return fromUtcSec + 86400; // fallback
}

/**
 * Remap projected timestamps so every point lands within a valid NSE session.
 * Points that fall after 15:30 IST are pushed into the next session's morning,
 * maintaining their relative spacing.
 */
function remapToTradingHours(
  points: { time: number; price: number }[],
  intervalSec: number,
): { time: number; price: number }[] {
  const result: { time: number; price: number }[] = [];
  let carry = 0; // cumulative time shift applied to out-of-hours points

  for (let i = 0; i < points.length; i++) {
    const t = points[i].time + carry;
    const istSec = (t + IST_OFFSET_SEC) % 86400; // seconds into IST day

    if (istSec >= NSE_OPEN_IST && istSec < NSE_CLOSE_IST) {
      result.push({ time: t, price: points[i].price });
    } else {
      // Outside trading hours — map to next session
      const nextOpen = nextTradingOpen(t);
      const overshoot = istSec >= NSE_CLOSE_IST
        ? istSec - NSE_CLOSE_IST          // seconds past 15:30
        : istSec;                          // before 09:15 — treat as 0 offset
      const sessionOffset = Math.min(overshoot, NSE_SESSION_SECS - intervalSec);
      const remapped = nextOpen + sessionOffset;
      carry += remapped - t;
      result.push({ time: remapped, price: points[i].price });
      console.log(`[GhostLine] Session remap pt${i}: ${t} → ${remapped}`);
    }
  }

  // Remove any duplicated timestamps at session boundary
  const seen = new Set<number>();
  return result.filter(p => {
    if (seen.has(p.time)) return false;
    seen.add(p.time);
    return true;
  });
}

// ── Lookback candle fetch ─────────────────────────────────────────────────

async function fetchLookbackCandles(
  symbol: string,
  timeframe: string,
): Promise<{ time: number; close: number; volume: number }[]> {
  const kiteInterval = KITE_INTERVAL_MAP[timeframe as Timeframe] ?? 'minute';

  // Path A: Tauri IPC — read cached bars from QuestDB
  if (isTauri()) {
    try {
      const tauri = await import('@tauri-apps/api/core');
      const response = await tauri.invoke<number[] | Uint8Array>('get_historical_view', {
        symbol,
        timeframe,
      });
      const buffer = response instanceof Uint8Array ? response : new Uint8Array(response);
      if (buffer.length > 8) {
        const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
        const length = Number(view.getBigUint64(0, true));
        let offset = 8;
        const bars: { time: number; close: number; volume: number }[] = [];
        for (let i = 0; i < length; i++) {
          const tsMicro = Number(view.getBigInt64(offset, true));
          const close   = view.getFloat64(offset + 32, true);
          const volume  = Number(view.getBigInt64(offset + 40, true));
          bars.push({ time: Math.floor(tsMicro / 1_000_000), close, volume });
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

  // Path B: Next.js rewrite — /kite/historical
  // ALPHA_TEST_MODE → /api/kite/historical (mock)
  // Production     → http://127.0.0.1:8087/api/kite/historical (aggregator)
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
      const candles = (data.candles || []).map((c: any) => ({
        time: typeof c.time === 'number' ? c.time : Math.floor(new Date(c.time).getTime() / 1000),
        close: c.close,
        volume: c.volume || 1.0,
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
  closes: number[],
  lastTime: number,
  intervalSec: number,
  projLen: number,
): { time: number; price: number }[] {
  const n = closes.length;
  if (n < 5) return [];

  let sumX = 0, sumY = 0, sumXY = 0, sumX2 = 0;
  for (let i = 0; i < n; i++) {
    sumX += i; sumY += closes[i];
    sumXY += i * closes[i]; sumX2 += i * i;
  }
  const denom = n * sumX2 - sumX * sumX;
  if (denom === 0) return [];

  const slope     = (n * sumXY - sumX * sumY) / denom;
  const intercept = (sumY - slope * sumX) / n;
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

// ── VWEPR curved projection ────────────────────────────────────────────────

function vweprProjection(
  candles: { close: number; volume: number }[],
  lastTime: number,
  intervalSec: number,
  projLen: number,
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
    pts.push({
      time:  lastTime + i * intervalSec,
      price: +Math.max(0.01, a0 + a1*x + a2*x*x + correction).toFixed(2),
    });
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

  const last        = lookback[lookback.length - 1];
  const intervalSec = Math.floor((TIMEFRAME_MS[effectiveTimeframe as Timeframe] ?? 60_000) / 1000);
  console.log(`[GhostLine] last bar: time=${last.time}, close=${last.close}, interval=${intervalSec}s`);

  if (!Number.isFinite(last.close) || last.close <= 0) return [];

  let points: { time: number; price: number }[] = [];

  // ── Path 1: backend predictive signal ───────────────────────────────
  if (predictiveSignals.length > 0) {
    const sigs = predictiveSignals.filter(s => s.symbol?.toUpperCase() === activeSymbol.toUpperCase());
    const sig  = sigs[sigs.length - 1] ?? null;
    if (sig) {
      const targetSec = Math.floor(sig.target_timestamp_ms / 1000);
      const predicted = sig.predicted_close_price;
      const dev = Math.abs(predicted - last.close) / last.close;
      const ok  = Number.isFinite(predicted) && predicted > 0 && dev < 0.20;
      console.log(`[GhostLine] Path1 signal: predicted=${predicted} ok=${ok}`);
      if (ok && targetSec > last.time - intervalSec * 10) {
        const N   = 12;
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

  // ── Path 2: Tauri Rust dual-engine ──────────────────────────────────
  if (points.length === 0 && isTauri()) {
    try {
      const tauri = await import('@tauri-apps/api/core');
      const candles = lookback.slice(-60).map(c => ({ time: c.time, close: c.close, volume: c.volume || 1.0 }));
      console.log('[GhostLine] Path2: compute_ghost_curve via Tauri');
      const payload = await tauri.invoke<any>('compute_ghost_curve', {
        candles, intervalSec, projectionLength: 12,
      });
      useChartUIStore.getState().setAccelerationCoefficient(payload.acceleration_coefficient);
      const raw = ghostLineMode === 'linear' ? payload.linear_points : payload.curved_points;
      if (raw?.length > 0) {
        points = raw.map((p: any) => ({ time: p.time, price: +p.value.toFixed(2) }));
        console.log('[GhostLine] Path2 Tauri:', points.length, 'points');
      }
    } catch (err) {
      console.error('[GhostLine] Path2 failed:', err);
    }
  }

  // ── Path 3: pure-JS fallback ─────────────────────────────────────────
  if (points.length === 0) {
    console.log('[GhostLine] Path3: pure-JS engine, mode=', ghostLineMode);
    const closes = lookback.slice(-60).map(c => c.close);
    points = ghostLineMode === 'linear'
      ? olsProjection(closes, last.time, intervalSec, 8)
      : vweprProjection(lookback.slice(-60), last.time, intervalSec, 8);
    console.log('[GhostLine] Path3:', points.length, 'points');
  }

  // ── Remap out-of-session timestamps to next trading session ──────────
  // TradingView hides timestamps in overnight/weekend gaps — shapes placed
  // there are created but invisible.  Remap pushes them into the next
  // valid NSE session so the ghost line actually appears on the chart.
  if (points.length > 0) {
    points = remapToTradingHours(points, intervalSec);
    console.log('[GhostLine] After session remap:', points.length, 'points');
    console.log('[GhostLine] Final:', JSON.stringify(points));
  }

  return points;
}
