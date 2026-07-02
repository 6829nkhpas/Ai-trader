import { useEffect, useRef } from 'react';
import { useTradeStore } from '../store/useTradeStore';
import { useChartUIStore } from '../store/useChartUIStore';
import { TIMEFRAME_MS, KITE_INTERVAL_MAP, type Timeframe } from '../utils/chartTypes';

const isTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

function parseBincodeCandles(buffer: Uint8Array): any[] {
  const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
  const length = Number(view.getBigUint64(0, true));
  let offset = 8;
  const bars = [];
  for (let i = 0; i < length; i++) {
    const tsMicro = Number(view.getBigInt64(offset, true));
    const open = view.getFloat64(offset + 8, true);
    const high = view.getFloat64(offset + 16, true);
    const low = view.getFloat64(offset + 24, true);
    const close = view.getFloat64(offset + 32, true);
    const volume = Number(view.getBigInt64(offset + 40, true));
    bars.push({
      time: Math.floor(tsMicro / 1000000), // convert to seconds
      open, high, low, close, volume,
    });
    offset += 48;
  }
  return bars;
}

async function fetchLookbackCandles(symbol: string, timeframe: string): Promise<any[]> {
  const kiteInterval = KITE_INTERVAL_MAP[timeframe as Timeframe] ?? 'minute';
  
  if (isTauri()) {
    try {
      const tauri = await import('@tauri-apps/api/core');
      const response = await tauri.invoke<number[] | Uint8Array>('get_historical_view', {
        symbol,
        timeframe,
      });
      const buffer = response instanceof Uint8Array ? response : new Uint8Array(response);
      const parsed = parseBincodeCandles(buffer);
      if (parsed.length > 0) return parsed;
    } catch (err) {
      console.warn('[GhostLine] Tauri historical view failed:', err);
    }
  }

  // Fallback to Kite historical endpoint directly
  try {
    const to = new Date();
    const daysBack = timeframe.endsWith('D') || timeframe.endsWith('W') || timeframe.endsWith('M') ? 365 : 10;
    const from = new Date(to.getTime() - daysBack * 24 * 60 * 60 * 1000);
    const fmt = (d: Date) => d.toISOString().slice(0, 10);
    const url = `/kite/historical?symbol=${encodeURIComponent(symbol)}&interval=${kiteInterval}&from=${fmt(from)}&to=${fmt(to)}`;
    const res = await fetch(url);
    if (res.ok) {
      const data = await res.json();
      return (data.candles || []).map((c: any) => ({
        time: c.time,
        close: c.close,
        volume: c.volume || 1.0,
      }));
    }
  } catch (err) {
    console.warn('[GhostLine] Direct Kite fetch failed:', err);
  }
  return [];
}

/**
 * Custom React hook to calculate and render the predictive forward "ghost line"
 * overlay (OLS and VWEPR engines) on the TradingView Advanced Charts instance.
 */
export function useGhostLine(
  widget: any,
  activeSymbol: string,
  effectiveTimeframe: string
) {
  const predictiveSignals = useTradeStore((s) => s.predictiveSignals);
  const ghostLineMode = useChartUIStore((s) => s.ghostLineMode);
  const lastEntityIdRef = useRef<any>(null);

  useEffect(() => {
    if (!widget) return;
    
    let active = true;

    const runGhostLine = async () => {
      const lookback = await fetchLookbackCandles(activeSymbol, effectiveTimeframe);
      if (!active || lookback.length < 20) return;

      const lastCandle = lookback[lookback.length - 1];
      const currentPrice = lastCandle.close;
      const intervalSec = Math.floor((TIMEFRAME_MS[effectiveTimeframe as Timeframe] ?? 60000) / 1000);

      // Guard: current price must be a valid positive number
      if (!Number.isFinite(currentPrice) || currentPrice <= 0) return;

      let points: { time: number; price: number }[] = [];

      // ── Path 1: Backend Predictive Signal ────────────────────────────
      if (predictiveSignals.length > 0) {
        const symbolSignals = activeSymbol
          ? predictiveSignals.filter((s) => s.symbol.toUpperCase() === activeSymbol.toUpperCase())
          : predictiveSignals;
        const latest = symbolSignals.length > 0 ? symbolSignals[symbolSignals.length - 1] : null;

        if (latest) {
          const targetTimeSec = Math.floor(latest.target_timestamp_ms / 1000);
          const predictedPrice = latest.predicted_close_price;
          const minValidTime = lastCandle.time - intervalSec * 10;

          const priceDeviation = Math.abs(predictedPrice - currentPrice) / currentPrice;
          const priceIsValid = Number.isFinite(predictedPrice) && predictedPrice > 0 && priceDeviation < 0.20;

          if (targetTimeSec > minValidTime && priceIsValid) {
            const GHOST_CANDLES = 6;
            const endTime = Math.max(targetTimeSec, lastCandle.time + intervalSec * GHOST_CANDLES);
            const slope = (predictedPrice - currentPrice) / GHOST_CANDLES;

            points = Array.from({ length: GHOST_CANDLES + 1 }, (_, i) => ({
              time: lastCandle.time + i * intervalSec,
              price: +(currentPrice + slope * i).toFixed(2),
            }));
            points[points.length - 1] = { time: endTime, price: +(predictedPrice).toFixed(2) };
          }
        }
      }

      // ── Path 2: Dual-Engine via Rust IPC ────────────────────────────
      if (points.length === 0 && isTauri()) {
        try {
          const tauri = await import('@tauri-apps/api/core');
          const lookbackCandles = lookback.slice(-60).map(c => ({
            time: c.time,
            close: c.close,
            volume: c.volume || 1.0,
          }));

          const payload = await tauri.invoke<any>('compute_ghost_curve', {
            candles: lookbackCandles,
            intervalSec,
            projectionLength: 6,
          });

          if (!active) return;

          // Persist acceleration coefficient for AI analysis
          useChartUIStore.getState().setAccelerationCoefficient(payload.acceleration_coefficient);

          const activePoints = ghostLineMode === 'linear'
            ? payload.linear_points
            : payload.curved_points;

          if (activePoints && activePoints.length > 0) {
            points = activePoints.map((p: any) => ({
              time: p.time,
              price: +p.value.toFixed(2),
            }));
          }
        } catch (error) {
          console.error('👻 [GHOST ENGINE ERROR] Failed to compute projection:', error);
        }
      }

      if (!active) return;

      widget.onChartReady(() => {
        try {
          const chart = widget.activeChart();

          // 1. Remove old shape if exists
          if (lastEntityIdRef.current) {
            try {
              chart.removeEntity(lastEntityIdRef.current);
            } catch (e) {
              // Might already be removed
            }
            lastEntityIdRef.current = null;
          }

          if (points.length > 0) {
            // Draw new polyline shape
            const shapePoints = points.map(p => ({
              time: p.time,
              price: p.price,
            }));
            chart.createMultipointShape(shapePoints, {
              shape: 'polyline',
              lock: true,
              disableSelection: true,
              disableSave: true,
              disableUndo: true,
              overrides: {
                'linetoolpolyline.linecolor': '#f59e0b',
                'linetoolpolyline.linewidth': 2,
                'linetoolpolyline.linestyle': 2, // dashed
                'linetoolpolyline.filled': false,
                'linetoolpolyline.fillBackground': false,
              },
            }).then((entityId: any) => {
              lastEntityIdRef.current = entityId;
            });
          }
        } catch (err) {
          console.error('[GhostLine] failed to draw on TV:', err);
        }
      });
    };

    runGhostLine();

    return () => {
      active = false;
      if (lastEntityIdRef.current && widget) {
        try {
          const chart = widget.activeChart();
          chart.removeEntity(lastEntityIdRef.current);
        } catch (e) {
          // Might already be removed
        }
        lastEntityIdRef.current = null;
      }
    };
  }, [widget, predictiveSignals, activeSymbol, effectiveTimeframe, ghostLineMode]);
}
