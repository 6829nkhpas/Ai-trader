'use client';

import React, { useEffect, useRef, useCallback } from 'react';
import {
  createChart,
  ColorType,
  Time,
  IChartApi,
  ISeriesApi,
  CandlestickSeries,
  LineSeries,
} from 'lightweight-charts';
import { TradeProfile } from '../store/useTradeStore';

// ── Types ─────────────────────────────────────────────────────────────────

export type Timeframe = '1m' | '5m' | '15m' | '1h' | '1D';

interface AlphaPredictiveChartProps {
  activeProfile?: TradeProfile;
  timeframe?: Timeframe;
}

/** Raw 1-minute candle from the OHLC WebSocket server. */
interface RawCandle {
  symbol: string;
  start_timestamp_ms: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** Lightweight-charts compatible candle. */
interface ChartCandle {
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
}

// ── Timeframe → milliseconds mapping ──────────────────────────────────────

const TIMEFRAME_MS: Record<Timeframe, number> = {
  '1m': 60_000,
  '5m': 5 * 60_000,
  '15m': 15 * 60_000,
  '1h': 60 * 60_000,
  '1D': 24 * 60 * 60_000,
};

// ── Aggregation Logic ─────────────────────────────────────────────────────

/**
 * Aggregates an array of 1-minute base candles into higher-timeframe candles.
 * For example, five 1-minute candles become one 5-minute candle.
 *
 * The bucket key is `floor(timestamp_ms / intervalMs) * intervalMs`, so
 * candles naturally align to clock boundaries (e.g. 5m candles at :00, :05, :10…).
 */
function aggregateCandles(
  rawCandles: RawCandle[],
  timeframe: Timeframe
): ChartCandle[] {
  const intervalMs = TIMEFRAME_MS[timeframe];
  const buckets = new Map<
    number,
    { open: number; high: number; low: number; close: number }
  >();

  // Sort by timestamp to ensure correct open/close assignment.
  const sorted = [...rawCandles].sort(
    (a, b) => a.start_timestamp_ms - b.start_timestamp_ms
  );

  for (const candle of sorted) {
    const bucketKey =
      Math.floor(candle.start_timestamp_ms / intervalMs) * intervalMs;

    const existing = buckets.get(bucketKey);
    if (existing) {
      existing.high = Math.max(existing.high, candle.high);
      existing.low = Math.min(existing.low, candle.low);
      existing.close = candle.close; // last candle's close = bucket close
    } else {
      buckets.set(bucketKey, {
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
      });
    }
  }

  // Convert to chart-ready format, sorted by time.
  const result: ChartCandle[] = [];
  const keys = Array.from(buckets.keys()).sort((a, b) => a - b);

  for (const key of keys) {
    const bucket = buckets.get(key)!;
    result.push({
      time: Math.floor(key / 1000) as Time, // lightweight-charts uses seconds
      open: bucket.open,
      high: bucket.high,
      low: bucket.low,
      close: bucket.close,
    });
  }

  return result;
}

// ── Component ─────────────────────────────────────────────────────────────

export default function AlphaPredictiveChart({
  activeProfile = 'INTRADAY',
  timeframe = '1m',
}: AlphaPredictiveChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const ghostLineRef = useRef<ISeriesApi<'Line'> | null>(null);

  // Store all raw 1-minute candles received from the server.
  // Higher timeframes are aggregated from this buffer.
  const rawCandlesRef = useRef<RawCandle[]>([]);

  // Current state tracking for the anchor of the ghost line
  const lastCloseRef = useRef<{ time: Time; value: number } | null>(null);

  // Track the active timeframe in a ref so the WS callback can read it
  // without re-creating the connection.
  const timeframeRef = useRef<Timeframe>(timeframe);
  timeframeRef.current = timeframe;

  // ── Re-aggregate and redraw when timeframe changes ────────────────────
  const redrawChart = useCallback(() => {
    if (!candleSeriesRef.current || rawCandlesRef.current.length === 0) return;

    const aggregated = aggregateCandles(
      rawCandlesRef.current,
      timeframeRef.current
    );
    candleSeriesRef.current.setData(aggregated);
    chartRef.current?.timeScale().scrollToRealTime();

    if (aggregated.length > 0) {
      const last = aggregated[aggregated.length - 1];
      lastCloseRef.current = { time: last.time, value: last.close };
    }
  }, []);

  // Redraw when timeframe prop changes.
  useEffect(() => {
    redrawChart();
  }, [timeframe, redrawChart]);

  // ── Chart initialisation & data pipeline ──────────────────────────────
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#94a3b8',
        fontSize: 12,
        fontFamily: "'Inter', sans-serif",
      },
      grid: {
        vertLines: { color: 'rgba(30, 41, 59, 0.5)' },
        horzLines: { color: 'rgba(30, 41, 59, 0.5)' },
      },
      crosshair: {
        horzLine: { color: '#475569', labelBackgroundColor: '#1e293b' },
        vertLine: { color: '#475569', labelBackgroundColor: '#1e293b' },
      },
      rightPriceScale: { borderColor: '#1e293b' },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: timeframe === '1m',
        rightOffset: 5,
        fixLeftEdge: true,
        fixRightEdge: true,
        barSpacing: 8,
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
      priceFormat: {
        type: 'price',
        precision: 2,
        minMove: 0.05,
      },
    });

    // Ghost Line — dashed blue projection into the future
    const ghostLine = chart.addSeries(LineSeries, {
      color: '#0ea5e9',
      lineWidth: 2,
      lineStyle: 2,
      crosshairMarkerVisible: true,
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    ghostLineRef.current = ghostLine;

    const resizeObserver = new ResizeObserver(() => {
      if (chartContainerRef.current) {
        const rect = chartContainerRef.current.getBoundingClientRect();
        chart.resize(Math.floor(rect.width), Math.floor(rect.height));
      }
    });
    resizeObserver.observe(chartContainerRef.current);

    // ── Data Pipeline: Tauri IPC (native) or WebSocket (browser) ────────
    let cleanupFn: (() => void) | null = null;

    const isTauri =
      typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

    /** Shared handler for incoming raw candle data from either source. */
    const handleCandleData = (candle: RawCandle) => {
      // Store raw 1-minute candle.
      rawCandlesRef.current.push(candle);

      // Cap buffer at 2000 candles to prevent memory leaks.
      if (rawCandlesRef.current.length > 2000) {
        rawCandlesRef.current = rawCandlesRef.current.slice(-2000);
      }

      // Aggregate into the active timeframe and update the chart.
      const intervalMs = TIMEFRAME_MS[timeframeRef.current];
      const bucketKey =
        Math.floor(candle.start_timestamp_ms / intervalMs) * intervalMs;
      const time = Math.floor(bucketKey / 1000) as Time;

      // Find the current bucket in the aggregation.
      // For real-time updates, we build the current bucket from all raw candles
      // that fall within it, then call series.update() for efficiency.
      const bucketCandles = rawCandlesRef.current.filter((c) => {
        const k = Math.floor(c.start_timestamp_ms / intervalMs) * intervalMs;
        return k === bucketKey;
      });

      if (bucketCandles.length > 0) {
        const open = bucketCandles[0].open;
        let high = -Infinity;
        let low = Infinity;
        let close = bucketCandles[0].close;

        for (const c of bucketCandles) {
          if (c.high > high) high = c.high;
          if (c.low < low) low = c.low;
          close = c.close;
        }

        candleSeries.update({ time, open, high, low, close });
        lastCloseRef.current = { time, value: close };
        chart.timeScale().scrollToRealTime();
      }
    };

    if (isTauri) {
      // ── Tauri IPC Path ──────────────────────────────────────────────────
      let unlistenOhlc: (() => void) | undefined;
      let unlistenPredict: (() => void) | undefined;

      const setupTauriListeners = async () => {
        try {
          const { listen } = await import('@tauri-apps/api/event');

          unlistenOhlc = await listen<RawCandle>('ohlc-tick', (event) => {
            try {
              const data = event.payload;
              const candles = Array.isArray(data) ? data : [data];
              candles.forEach(handleCandleData);
            } catch (error) {
              console.error('Error handling IPC OHLC data', error);
            }
          });

          unlistenPredict = await listen<Record<string, unknown>>(
            'predictive-tick',
            (event) => {
              try {
                const data = event.payload;
                const signals = Array.isArray(data) ? data : [data];

                signals.forEach((signal: Record<string, unknown>) => {
                  if (lastCloseRef.current) {
                    const targetTime = Math.floor(
                      (signal.target_timestamp_ms as number) / 1000
                    ) as Time;

                    ghostLine.update({
                      time: lastCloseRef.current.time,
                      value: lastCloseRef.current.value,
                    });
                    ghostLine.update({
                      time: targetTime,
                      value: signal.predicted_close_price as number,
                    });
                  }
                });
              } catch (error) {
                console.error('Error handling IPC Predictive data', error);
              }
            }
          );
        } catch (err) {
          console.warn('Failed to setup Tauri IPC listeners:', err);
        }
      };

      setupTauriListeners();

      cleanupFn = () => {
        if (unlistenOhlc) unlistenOhlc();
        if (unlistenPredict) unlistenPredict();
      };
    } else {
      // ── WebSocket Fallback Path (browser / dev mode) ─────────────────
      const ohlcWsUrl =
        process.env.NEXT_PUBLIC_OHLC_WS_URL || 'ws://127.0.0.1:8081';

      let ohlcWs: WebSocket | null = null;
      let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

      const connectOhlcWs = () => {
        try {
          ohlcWs = new WebSocket(ohlcWsUrl);

          ohlcWs.onopen = () => {
            console.log(
              '[AlphaChart] OHLC WebSocket connected to',
              ohlcWsUrl
            );
          };

          ohlcWs.onmessage = (event) => {
            try {
              const candle: RawCandle = JSON.parse(event.data);
              handleCandleData(candle);
            } catch (e) {
              console.error('[AlphaChart] Error parsing OHLC WS message:', e);
            }
          };

          ohlcWs.onclose = () => {
            console.log(
              '[AlphaChart] OHLC WebSocket disconnected, reconnecting in 3s...'
            );
            reconnectTimer = setTimeout(connectOhlcWs, 3000);
          };

          ohlcWs.onerror = (err) => {
            console.warn('[AlphaChart] OHLC WebSocket error:', err);
          };
        } catch (err) {
          console.warn('[AlphaChart] Failed to connect OHLC WS:', err);
          reconnectTimer = setTimeout(connectOhlcWs, 3000);
        }
      };

      connectOhlcWs();

      cleanupFn = () => {
        if (reconnectTimer) clearTimeout(reconnectTimer);
        if (ohlcWs) {
          ohlcWs.onclose = null;
          ohlcWs.close();
        }
      };
    }

    return () => {
      resizeObserver.disconnect();
      if (cleanupFn) cleanupFn();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      ghostLineRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={chartContainerRef} className="h-full w-full outline-none" />
  );
}