import { useEffect, useRef } from 'react';
import type { Time } from 'lightweight-charts';
import type { PredictiveSignal } from '../store/useTradeStore';
import type { ChartCandle, VolumeBar, EmaPoint, ChartRefs, Timeframe } from '../utils/chartTypes';
import { TIMEFRAME_MS } from '../utils/chartTypes';

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
    if (chartData.length === 0) return;

    const prevTimeframe = lastPaintedTimeframeRef.current;
    const prevSymbol = lastPaintedSymbolRef.current;
    const prevCount = lastPaintedCandleCountRef.current;

    const timeframeChanged = prevTimeframe !== effectiveTimeframe;
    const symbolChanged = prevSymbol !== activeSymbol;
    const newCandleArrived = chartData.length !== prevCount;

    if (timeframeChanged || symbolChanged || newCandleArrived) {
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
        // Force chart to auto-fit both time and price axes to the new data.
        // Without this, switching symbols/timeframes can leave the chart
        // zoomed to the previous data's price range, causing visual distortion.
        chartRef.current?.timeScale().fitContent();
      }
    } else {
      // ── SMOOTH UPDATE PATH ─────────────────────────────────────────
      const lastCandle = chartData[chartData.length - 1];
      const lastVolume = volumeData[volumeData.length - 1];
      const lastEma9 = ema9Data[ema9Data.length - 1];
      const lastEma21 = ema21Data[ema21Data.length - 1];

      candleSeriesRef.current.update(lastCandle as { time: Time; open: number; high: number; low: number; close: number });
      volumeSeriesRef.current.update(lastVolume as { time: Time; value: number; color: string });
      if (ema9SeriesRef.current && lastEma9) {
        ema9SeriesRef.current.update(lastEma9 as { time: Time; value: number });
      }
      if (ema21SeriesRef.current && lastEma21) {
        ema21SeriesRef.current.update(lastEma21 as { time: Time; value: number });
      }
    }
  }, [chartData, volumeData, ema9Data, ema21Data, effectiveTimeframe, activeSymbol, candleSeriesRef, volumeSeriesRef, ema9SeriesRef, ema21SeriesRef, chartRef]);

  // ── Ghost Line (predictive forward projection) ──────────────────────
  //
  // Two paths:
  //   1. If the backend Predictive Agent has published a signal for this
  //      symbol, project a straight line from the current close to the
  //      predicted close.
  //   2. Fallback: compute a zero-based-index OLS regression over the last
  //      8 EMA-9 values and project the slope forward.
  //
  // CRITICAL: all X-axis values use zero-based indices (0, 1, 2, …), NOT
  // raw Unix timestamps.  Using timestamps causes float overflow in the
  // OLS accumulators, producing NaN/Infinity slopes → ghost line dives
  // off-screen.
  const GHOST_CANDLES = 5;

  useEffect(() => {
    if (!ghostLineRef.current || chartData.length < 8) return;

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

        // Sanity checks:
        //   1. Target timestamp must be reasonably close to the current candle
        //   2. Predicted price must be finite and positive
        //   3. Predicted price must not deviate more than 20% from current
        //      (a >20% move in one projection window is almost certainly bad data)
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

    // ── Path 2: EMA-9 OLS Linear Regression (zero-based index) ──────
    //
    // X values are 0, 1, 2, … (NOT timestamps).
    // This prevents the float overflow that caused the ghost line to
    // compute massive negative slopes and point straight down.
    if (ema9Data.length >= 8) {
      const window = ema9Data.slice(-8);
      const n = window.length;

      // Zero-based index regression using deviation-from-mean form
      // (numerically stable for small n)
      const xMean = (n - 1) / 2;
      const yMean = window.reduce((s, p) => s + p.value, 0) / n;

      let num = 0;
      let den = 0;
      for (let i = 0; i < n; i++) {
        const xDev = i - xMean;
        const yDev = window[i].value - yMean;
        num += xDev * yDev;
        den += xDev * xDev;
      }

      const slope = den !== 0 ? num / den : 0;

      // Guard: slope must be finite and not produce an absurd projection
      if (!Number.isFinite(slope)) {
        ghostLineRef.current.setData([]);
        return;
      }

      // Clamp total move to ±5% of current price to prevent visual noise
      const maxMove = currentPrice * 0.05;
      const projectedEnd = currentPrice + slope * GHOST_CANDLES;
      const clampedEnd = Math.max(
        currentPrice - maxMove,
        Math.min(currentPrice + maxMove, projectedEnd)
      );
      const clampedSlope = (clampedEnd - currentPrice) / GHOST_CANDLES;

      const points = Array.from({ length: GHOST_CANDLES + 1 }, (_, i) => ({
        time: (lastCandle.time + i * intervalSec) as Time,
        value: +(currentPrice + clampedSlope * i).toFixed(2),
      }));

      const totalMove = Math.abs(points[GHOST_CANDLES].value - currentPrice);
      if (totalMove / currentPrice >= 0.00005) {
        ghostLineRef.current.setData(points);
        return;
      }
    }

    ghostLineRef.current.setData([]);
  }, [predictiveSignals, activeSymbol, chartData, ema9Data, effectiveTimeframe, ghostLineRef]);

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
      const { width, height } = chartContainerRef.current.getBoundingClientRect();
      chartRef.current.resize(Math.floor(width), Math.floor(height));
    }
  }, [isExpanded, chartRef, chartContainerRef]);
}
