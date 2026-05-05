'use client';

import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, Time, IChartApi, ISeriesApi, CandlestickSeries, LineSeries } from 'lightweight-charts';
import { listen, UnlistenFn } from '@tauri-apps/api/event';
import { TradeProfile } from '../store/useTradeStore';

interface AlphaPredictiveChartProps {
  activeProfile?: TradeProfile;
}

export default function AlphaPredictiveChart({ activeProfile = 'INTRADAY' }: AlphaPredictiveChartProps) {
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
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#94a3b8',
        fontSize: 12,
        fontFamily: "'Inter', sans-serif",
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

    // Ghost Line — dashed blue projection into the future
    const ghostLine = chart.addSeries(LineSeries, {
      color: '#0ea5e9', // sky-500
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

    // Initialise Native IPC Listeners for Zero-Latency Rendering
    let unlistenOhlc: UnlistenFn | undefined;
    let unlistenPredict: UnlistenFn | undefined;

    const setupListeners = async () => {
      unlistenOhlc = await listen<any>('ohlc-tick', (event) => {
        try {
          const data = event.payload;
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

            candleSeries.update(mappedData);
            lastCloseRef.current = { time, value: candle.close };
          });
        } catch (error) {
          console.error('Error handling IPC OHLC data', error);
        }
      });

      unlistenPredict = await listen<any>('predictive-tick', (event) => {
        try {
          const data = event.payload;
          const signals = Array.isArray(data) ? data : [data];

          signals.forEach(signal => {
            if (lastCloseRef.current) {
              const targetTime = Math.floor(signal.target_timestamp_ms / 1000) as Time;

              ghostLine.update({
                time: lastCloseRef.current.time,
                value: lastCloseRef.current.value
              });

              ghostLine.update({
                time: targetTime,
                value: signal.predicted_close_price
              });
            }
          });
        } catch (error) {
          console.error('Error handling IPC Predictive data', error);
        }
      });
    };

    setupListeners();

    return () => {
      window.removeEventListener('resize', handleResize);
      if (unlistenOhlc) unlistenOhlc();
      if (unlistenPredict) unlistenPredict();
      chart.remove();
    };
  }, []);

  return (
    <div
      ref={chartContainerRef}
      className="h-full w-full outline-none"
    />
  );
}