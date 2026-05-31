import { useEffect, useRef } from 'react';
import type { Time } from 'lightweight-charts';
import { invoke } from '@tauri-apps/api/core';
import type { PredictiveSignal } from '../store/useTradeStore';
import type { ChartCandle, VolumeBar, EmaPoint, ChartRefs, Timeframe } from '../utils/chartTypes';
import { TIMEFRAME_MS } from '../utils/chartTypes';
import { useChartUIStore } from '../store/useChartUIStore';

// ── Dual-Engine IPC Types ────────────────────────────────────────────────────

/** Minimal candle payload sent to the Rust predictive engines via Tauri IPC. */
interface MinimalCandle {
  time: number;   // UNIX timestamp in seconds
  close: number;
  volume: number;
}

/** A single projected point returned by a Rust regression engine. */
interface ProjectedPoint {
  time: number;   // UNIX timestamp in seconds
  value: number;  // Projected price
}

/** Combined output of both predictive engines (OLS + VWEPR). */
interface ProjectionPayload {
  linear_points: ProjectedPoint[];  // OLS baseline
  curved_points: ProjectedPoint[];  // VWEPR polynomial
  acceleration_coefficient: number; // Quadratic 'a' from VWEPR
}

export function useChartDataSync(
  refs: ChartRefs,
  chartData: ChartCandle[],
  volumeData: VolumeBar[],
  ema9Data: EmaPoint[],
  ema21Data: EmaPoint[],
  effectiveTimeframe: Timeframe,
  activeSymbol: string,
  predictiveSignals: PredictiveSignal[],
  isExpanded: boolean = false
) {
  const { chartRef, candleSeriesRef, volumeSeriesRef, ema9SeriesRef, ema21SeriesRef, ghostLineRef, chartContainerRef } = refs;

  const lastPaintedCandleCountRef = useRef<number>(0);
  const lastPaintedTimeframeRef = useRef<string>('');
  const lastPaintedSymbolRef = useRef<string>('');

  // ── Clear chart immediately when symbol changes (before new data arrives) ─
  // Without this, the old symbol's candles stay visible during the async
  // historical fetch, making it look like the chart didn't respond to the click.
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current) return;
    const prevSymbol = lastPaintedSymbolRef.current;
    if (prevSymbol !== '' && prevSymbol !== activeSymbol) {
      candleSeriesRef.current.setData([]);
      volumeSeriesRef.current.setData([]);
      if (ema9SeriesRef.current) ema9SeriesRef.current.setData([]);
      if (ema21SeriesRef.current) ema21SeriesRef.current.setData([]);
      if (ghostLineRef.current) ghostLineRef.current.setData([]);
      // Reset the painted-count so the next data arrival triggers a full setData
      lastPaintedCandleCountRef.current = 0;
      lastPaintedSymbolRef.current = activeSymbol;
    }
  }, [activeSymbol, candleSeriesRef, volumeSeriesRef, ema9SeriesRef, ema21SeriesRef, ghostLineRef]);

  // ── Smart data sync: setData on full reset, update() for last candle ─
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current) return;

    const prevTimeframe = lastPaintedTimeframeRef.current;
    const prevSymbol = lastPaintedSymbolRef.current;
    const prevCount = lastPaintedCandleCountRef.current;

    const timeframeChanged = prevTimeframe !== effectiveTimeframe;
    const symbolChanged = prevSymbol !== activeSymbol;

    // ── Empty data: chart is loading or had a failed fetch ──────────────
    // Always clear the canvas and update refs so the next data arrival
    // triggers a full setData() correctly (fixes stuck lastPaintedTimeframeRef).
    if (chartData.length === 0) {
      if (timeframeChanged || symbolChanged) {
        candleSeriesRef.current.setData([]);
        volumeSeriesRef.current.setData([]);
        if (ema9SeriesRef.current) ema9SeriesRef.current.setData([]);
        if (ema21SeriesRef.current) ema21SeriesRef.current.setData([]);
        if (ghostLineRef.current) ghostLineRef.current.setData([]);
        lastPaintedTimeframeRef.current = effectiveTimeframe;
        lastPaintedSymbolRef.current = activeSymbol;
        lastPaintedCandleCountRef.current = 0;
      }
      return;
    }

    const newCandleArrived = chartData.length !== prevCount;

    if (timeframeChanged || symbolChanged || newCandleArrived) {
      // ── DIAGNOSTIC TRACER — Final Mile (chartData → setData boundary) ──
      // This is the very last gate before lightweight-charts. If Rust and
      // React Parse logs both look healthy but THIS shows an integrity
      // failure or zero items, the breakage is in the aggregation /
      // merge layer (mergedCandles → aggregateCandles → chartData).
      console.log(
        `🎨 [CHART RENDER] Calling setData with ${chartData.length} items ` +
        `(symbol=${activeSymbol}, tf=${effectiveTimeframe}).`
      );
      if (chartData.length > 0) {
        const isValid = chartData.every(
          (c) =>
            c.time !== undefined &&
            c.time !== null &&
            !Number.isNaN(c.open) &&
            !Number.isNaN(c.high) &&
            !Number.isNaN(c.low) &&
            !Number.isNaN(c.close)
        );
        console.log(`🎨 [CHART RENDER] Data Integrity Check Passed? : ${isValid}`);
        console.log("🎨 [CHART RENDER] Sample First:", JSON.stringify(chartData[0]));
        console.log(
          "🎨 [CHART RENDER] Sample Last :",
          JSON.stringify(chartData[chartData.length - 1])
        );
        if (!isValid) {
          const bad = chartData.find(
            (c) =>
              c.time === undefined ||
              c.time === null ||
              Number.isNaN(c.open) ||
              Number.isNaN(c.high) ||
              Number.isNaN(c.low) ||
              Number.isNaN(c.close)
          );
          console.error("🎨 [CHART RENDER ERROR] Malformed candle detected!", bad);
        }
      }

      candleSeriesRef.current.setData(
        chartData as Array<{ time: Time; open: number; high: number; low: number; close: number }>
      );
      volumeSeriesRef.current.setData(
        volumeData as Array<{ time: Time; value: number; color: string }>
      );
      if (ema9SeriesRef.current) {
        ema9SeriesRef.current.setData(ema9Data as Array<{ time: Time; value: number }>);
      }
      if (ema21SeriesRef.current) {
        ema21SeriesRef.current.setData(ema21Data as Array<{ time: Time; value: number }>);
      }

      lastPaintedTimeframeRef.current = effectiveTimeframe;
      lastPaintedSymbolRef.current = activeSymbol;
      lastPaintedCandleCountRef.current = chartData.length;

      if (timeframeChanged || symbolChanged || prevCount === 0) {
        // Traditional chart viewport: show the latest ~100 candles with the
        // newest candle on the right edge + rightOffset breathing room.
        // This matches TradingView / Zerodha Kite / any professional chart.
        //
        // scrollToRealTime() alone doesn't work on first data load (no prior
        // visible range). Setting an explicit visible logical range ensures
        // the chart always opens at the latest data.
        const ts = chartRef.current?.timeScale();
        if (ts && chartData.length > 0) {
          const visibleBars = Math.min(chartData.length, 100);
          const fromIndex = chartData.length - visibleBars;
          ts.setVisibleLogicalRange({
            from: fromIndex,
            to: chartData.length + 10, // +10 = rightOffset breathing room
          });
        }
      }
    } else {
      // ── SMOOTH UPDATE PATH ─────────────────────────────────────────
      // BUG-6: Wrapped in try-catch. series.update() throws when the new
      // candle's timestamp is earlier than the last painted one — this happens
      // when a live WS tick is superseded by a historical candle at a slightly
      // different ms boundary. Full setData() is always safe as a fallback.
      const lastCandle = chartData[chartData.length - 1];
      const lastVolume = volumeData[volumeData.length - 1];
      const lastEma9 = ema9Data[ema9Data.length - 1];
      const lastEma21 = ema21Data[ema21Data.length - 1];

      try {
        candleSeriesRef.current.update(lastCandle as { time: Time; open: number; high: number; low: number; close: number });
        volumeSeriesRef.current.update(lastVolume as { time: Time; value: number; color: string });
        if (ema9SeriesRef.current && lastEma9) ema9SeriesRef.current.update(lastEma9 as { time: Time; value: number });
        if (ema21SeriesRef.current && lastEma21) ema21SeriesRef.current.update(lastEma21 as { time: Time; value: number });
      } catch (_err) {
        // Fallback: full repaint is safe and produces no visual artifacts.
        candleSeriesRef.current.setData(chartData as Array<{ time: Time; open: number; high: number; low: number; close: number }>);
        volumeSeriesRef.current.setData(volumeData as Array<{ time: Time; value: number; color: string }>);
        if (ema9SeriesRef.current) ema9SeriesRef.current.setData(ema9Data as Array<{ time: Time; value: number }>);
        if (ema21SeriesRef.current) ema21SeriesRef.current.setData(ema21Data as Array<{ time: Time; value: number }>);
        lastPaintedCandleCountRef.current = chartData.length;
      }
    }
  }, [chartData, volumeData, ema9Data, ema21Data, effectiveTimeframe, activeSymbol, candleSeriesRef, volumeSeriesRef, ema9SeriesRef, ema21SeriesRef, chartRef]);

  // ── Ghost Line (predictive forward projection) ──────────────────────
  //
  // Two paths:
  //   1. If the backend Predictive Agent has published a signal for this
  //      symbol, project a straight line from the current close to the
  //      predicted close.
  //   2. Fallback: Volume-Weighted Exponential Polynomial Regression
  //      (VWEPR) — delegates heavy matrix math to the Rust backend via
  //      Tauri IPC. Zero JS-thread blocking.
  //
  // Reactivity:
  //   The effect's dep array includes `chartData`, so every new live tick
  //   that mutates `mergedCandles → aggregateCandles → chartData` triggers
  //   a fresh VWEPR computation.
  const GHOST_CANDLES = 6;

  useEffect(() => {
    if (!ghostLineRef.current) return;

    let active = true;

    // Need enough bars for the VWEPR engine (Rust enforces min 20).
    if (chartData.length < 20) {
      ghostLineRef.current.setData([]);
      return;
    }

    const lastCandle = chartData[chartData.length - 1];
    const intervalSec = Math.floor((TIMEFRAME_MS[effectiveTimeframe] ?? TIMEFRAME_MS['10m']) / 1000);
    const currentPrice = lastCandle.close;

    // Guard: current price must be a valid positive number
    if (!Number.isFinite(currentPrice) || currentPrice <= 0) {
      ghostLineRef.current.setData([]);
      return;
    }

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
          const endTime = Math.max(targetTimeSec, lastCandle.time + intervalSec * GHOST_CANDLES);
          const slope = (predictedPrice - currentPrice) / GHOST_CANDLES;

          const points = Array.from({ length: GHOST_CANDLES + 1 }, (_, i) => ({
            time: (lastCandle.time + i * intervalSec) as Time,
            value: +(currentPrice + slope * i).toFixed(2),
          }));
          points[points.length - 1] = { time: endTime as Time, value: +(predictedPrice).toFixed(2) };

          ghostLineRef.current.setData(points);
          return;
        }
      }
    }

    // ── Path 2: Dual-Engine via Rust IPC ────────────────────────────
    //
    // Slice the trailing 60 candles, map to the lightweight MinimalCandle
    // shape, and delegate both regression engines to the Rust backend.
    // The async call runs off the JS main thread — zero blocking.
    //
    // The returned ProjectionPayload contains:
    //   - curved_points  → VWEPR polynomial projection
    //   - linear_points  → OLS baseline projection
    //   - acceleration_coefficient → stored globally for DeepSeek injection
    //
    // The active dataset is chosen by `ghostLineMode` from the UI store.
    const ghostSeries = ghostLineRef.current; // capture ref for async closure

    const lookbackCandles: MinimalCandle[] = chartData.slice(-60).map(c => ({
      time: c.time as number,
      close: c.close,
      volume: (c as unknown as { volume?: number }).volume || 1.0,
    }));

    (async () => {
      try {
        const payload = await invoke<ProjectionPayload>('compute_ghost_curve', {
          candles: lookbackCandles,
          intervalSec,
          projectionLength: GHOST_CANDLES,
        });

        if (!active) return;

        // ── Persist acceleration coefficient for AI analysis ───────────
        useChartUIStore.getState().setAccelerationCoefficient(
          payload.acceleration_coefficient
        );

        // ── Route dataset based on ghost line mode ───────────────────
        const mode = useChartUIStore.getState().ghostLineMode;
        const activePoints = mode === 'linear'
          ? payload.linear_points
          : payload.curved_points;

        if (activePoints && activePoints.length > 0) {
          const chartPayload = activePoints.map(p => ({
            time: (p.time as number) as Time,
            value: +p.value.toFixed(2),
          }));

          ghostSeries.setData(chartPayload);
        } else {
          ghostSeries.setData([]);
        }
      } catch (error) {
        console.error('👻 [GHOST ENGINE ERROR] Failed to compute projection:', error);
        if (active) {
          ghostSeries.setData([]);
        }
      }
    })();

    return () => {
      active = false;
    };
  }, [predictiveSignals, activeSymbol, chartData, effectiveTimeframe, ghostLineRef]);

  // ── Update time scale on timeframe change ───────────────────────────
  useEffect(() => {
    const tf = effectiveTimeframe;
    const barSpacing =
      tf === '1M' ? 20
      : tf === '1W' ? 16
      : tf === '1D' ? 14
      : tf === '4h' || tf === '3h' || tf === '2h' ? 12
      : tf === '1h' || tf === '1H' ? 10
      : tf === '125m' || tf === '75m' || tf === '30m' ? 9
      : 8;

    chartRef.current?.timeScale().applyOptions({
      secondsVisible: false,
      barSpacing,
    });
  }, [effectiveTimeframe, chartRef]);

  // ── Resize on expand/collapse ────────────────────────────────────────
  useEffect(() => {
    if (chartRef.current && chartContainerRef.current) {
      try {
        const { width, height } = chartContainerRef.current.getBoundingClientRect();
        chartRef.current.resize(Math.floor(width), Math.floor(height));
      } catch (e) {
        console.warn('[useChartDataSync] Resize failed:', e);
      }
    }
  }, [isExpanded, chartRef, chartContainerRef]);
}
