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
        chartRef.current?.timeScale().scrollToRealTime();
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
  const GHOST_CANDLES = 5;

  useEffect(() => {
    if (!ghostLineRef.current || chartData.length < 8) return;

    const lastCandle = chartData[chartData.length - 1];
    const intervalSec = Math.floor((TIMEFRAME_MS[effectiveTimeframe] ?? TIMEFRAME_MS['10m']) / 1000);

    if (predictiveSignals.length > 0) {
      const symbolSignals = activeSymbol
        ? predictiveSignals.filter((s) => s.symbol.toUpperCase() === activeSymbol.toUpperCase())
        : predictiveSignals;

      const latest = symbolSignals.length > 0 ? symbolSignals[symbolSignals.length - 1] : null;

      if (latest) {
        const targetTimeSec = Math.floor(latest.target_timestamp_ms / 1000);
        const minValidTime = lastCandle.time - intervalSec * 10;
        if (targetTimeSec > minValidTime) {
          const endTime = Math.max(targetTimeSec, lastCandle.time + intervalSec * GHOST_CANDLES);
          const startPrice = lastCandle.close;
          const endPrice = latest.predicted_close_price;
          const slope = (endPrice - startPrice) / GHOST_CANDLES;

          const points = Array.from({ length: GHOST_CANDLES + 1 }, (_, i) => ({
            time: (lastCandle.time + i * intervalSec) as Time,
            value: +(startPrice + slope * i).toFixed(2),
          }));
          points[points.length - 1] = { time: endTime as Time, value: +(endPrice).toFixed(2) };

          ghostLineRef.current.setData(points);
          return;
        }
      }
    }

    // ── Fallback: EMA-9 linear regression slope ─────────
    if (ema9Data.length >= 8) {
      const window = ema9Data.slice(-8);
      const n = window.length;

      const xMean = (n - 1) / 2;
      const yMean = window.reduce((s, p) => s + p.value, 0) / n;
      let num = 0;
      let den = 0;
      for (let i = 0; i < n; i++) {
        num += (i - xMean) * (window[i].value - yMean);
        den += (i - xMean) ** 2;
      }
      const slope = den !== 0 ? num / den : 0;

      const points = Array.from({ length: GHOST_CANDLES + 1 }, (_, i) => ({
        time: (lastCandle.time + i * intervalSec) as Time,
        value: +(lastCandle.close + slope * i).toFixed(2),
      }));

      const totalMove = Math.abs(points[GHOST_CANDLES].value - lastCandle.close);
      if (totalMove / lastCandle.close >= 0.00005) {
        ghostLineRef.current.setData(points);
        return;
      }
    }

    ghostLineRef.current.setData([]);
  }, [predictiveSignals, activeSymbol, chartData, ema9Data, effectiveTimeframe, ghostLineRef]);

  // ── Update time scale on timeframe change ───────────────────────────
  useEffect(() => {
    chartRef.current?.timeScale().applyOptions({
      secondsVisible: effectiveTimeframe === '1m',
      barSpacing:
        effectiveTimeframe === '1D' ? 14
        : effectiveTimeframe === '1h' || effectiveTimeframe === '1H' ? 10
        : 8,
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
