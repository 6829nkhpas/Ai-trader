'use client';

import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, Time, IChartApi, ISeriesApi } from 'lightweight-charts';

export default function AlphaPredictiveChart() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const ghostLineRef = useRef<ISeriesApi<'Line'> | null>(null);

  // Current state tracking for the anchor of the ghost line
  const lastCloseRef = useRef<{ time: Time; value: number } | null>(null);

  // ── Chart initialisation & Zero-Latency Data Pipeline ────────────────
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

    const candleSeries = chart.addCandlestickSeries({
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    });

    // Ghost Line — dashed purple projection into the future
    const ghostLine = chart.addLineSeries({
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

    // Initialise Direct WebSockets for Zero-Latency Rendering
    const ws1 = new WebSocket('ws://127.0.0.1:8081'); // Historical & Live OHLC
    const ws2 = new WebSocket('ws://127.0.0.1:8082'); // Predictive Ghost Lines

    ws1.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        const candles = Array.isArray(data) ? data : [data];

        candles.forEach(candle => {
          const time = Math.floor(candle.start_timestamp_ms / 1000) as Time;
          const mappedData = {
            time,
            open: candle.open,
            high: candle.high,
            low: candle.low,
            close: candle.close,
          };

          // Directly mutate the canvas, bypassing React state
          candleSeries.update(mappedData);

          // Update our anchor for the predictive line
          lastCloseRef.current = { time, value: candle.close };
        });
      } catch (error) {
        console.error('Error parsing WS1 OHLC data', error);
      }
    };

    ws2.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        const signals = Array.isArray(data) ? data : [data];

        signals.forEach(signal => {
          if (lastCloseRef.current) {
            const targetTime = Math.floor(signal.target_timestamp_ms / 1000) as Time;

            // Anchor point from the latest close
            ghostLine.update({
              time: lastCloseRef.current.time,
              value: lastCloseRef.current.value
            });

            // Target prediction point
            ghostLine.update({
              time: targetTime,
              value: signal.predicted_close_price
            });
          }
        });
      } catch (error) {
        console.error('Error parsing WS2 Predictive data', error);
      }
    };

    return () => {
      window.removeEventListener('resize', handleResize);
      ws1.close();
      ws2.close();
      chart.remove();
    };
  }, []);

  return (
    <div
      ref={chartContainerRef}
      className="h-[400px] w-full border border-slate-800 rounded-lg overflow-hidden"
    />
  );
}