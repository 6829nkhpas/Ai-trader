'use client';

import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, Time } from 'lightweight-charts';
import { useTradeStore } from '../store/useTradeStore';

export default function AlphaPredictiveChart() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const { ohlcCandles } = useTradeStore();
  const seriesRef = useRef<any>(null);

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

    const series = chart.addCandlestickSeries({
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    });

    seriesRef.current = series;

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

  useEffect(() => {
    if (seriesRef.current && ohlcCandles) {
      const mappedData = ohlcCandles.map((candle) => ({
        time: Math.floor(candle.start_timestamp_ms / 1000) as Time,
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      }));

      // lightweight-charts requires strictly ascending time order and unique times
      const uniqueData = Array.from(new Map(mappedData.map(item => [item.time, item])).values());
      uniqueData.sort((a, b) => (a.time as number) - (b.time as number));

      seriesRef.current.setData(uniqueData);
    }
  }, [ohlcCandles]);

  return (
    <div
      ref={chartContainerRef}
      className="h-[400px] w-full border border-slate-800 rounded-lg overflow-hidden"
    />
  );
}
