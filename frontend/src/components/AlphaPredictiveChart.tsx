'use client';

import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, Time, IChartApi, ISeriesApi, LineSeries, CandlestickSeries } from 'lightweight-charts';
import { useTradeStore } from '../store/useTradeStore';

export default function AlphaPredictiveChart() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const { ohlcCandles, predictiveSignals } = useTradeStore();
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const ghostLineRef = useRef<ISeriesApi<'Line'> | null>(null);

  // ── Chart initialisation ─────────────────────────────────────────────
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: '#020617' },
        textColor: '#94a3b8',
      },
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight,
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    });

    // Ghost Line — dashed purple projection into the future
    const ghostLine = chart.addSeries(LineSeries, {
      color: '#c084fc',
      lineWidth: 2,
      lineStyle: 2, // Dashed
      crosshairMarkerVisible: true,
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    ghostLineRef.current = ghostLine;

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, []);

  // ── Data synchronisation ─────────────────────────────────────────────
  useEffect(() => {
    if (!candleSeriesRef.current) return;

    // Map OHLCCandles to lightweight-charts format
    const mappedData = (ohlcCandles ?? []).map((candle) => ({
      time: Math.floor(candle.start_timestamp_ms / 1000) as Time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    }));

    // De-duplicate and sort ascending
    const uniqueData = Array.from(new Map(mappedData.map(item => [item.time, item])).values());
    uniqueData.sort((a, b) => (a.time as number) - (b.time as number));

    candleSeriesRef.current.setData(uniqueData);

    // ── Ghost projection ───────────────────────────────────────────────
    if (ghostLineRef.current && uniqueData.length > 0) {
      const latestCandle = ohlcCandles[ohlcCandles.length - 1];

      // Find the latest PredictiveSignal matching the active symbol
      const matchingSignals = (predictiveSignals ?? []).filter(
        (s) => s.symbol === latestCandle.symbol
      );
      const latestSignal = matchingSignals[matchingSignals.length - 1];

      if (latestSignal) {
        const point1 = {
          time: Math.floor(latestCandle.start_timestamp_ms / 1000) as Time,
          value: latestCandle.close,
        };
        const point2 = {
          time: Math.floor(latestSignal.target_timestamp_ms / 1000) as Time,
          value: latestSignal.predicted_close_price,
        };

        ghostLineRef.current.setData([point1, point2]);
      } else {
        ghostLineRef.current.setData([]);
      }
    }
  }, [ohlcCandles, predictiveSignals]);

  return (
    <div
      ref={chartContainerRef}
      className="h-[400px] w-full border border-slate-800 rounded-lg overflow-hidden"
    />
  );
}
