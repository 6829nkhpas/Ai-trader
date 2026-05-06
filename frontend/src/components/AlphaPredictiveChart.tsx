'use client';

import React, { useEffect, useRef, useCallback, useMemo } from 'react';
import {
  createChart,
  ColorType,
  Time,
  IChartApi,
  ISeriesApi,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  CrosshairMode,
} from 'lightweight-charts';
import { useTradeStore, TradeProfile } from '../store/useTradeStore';

// ── Exported Types ────────────────────────────────────────────────────────

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

/** Lightweight-charts compatible candle with numeric time. */
interface ChartCandle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

/** Volume histogram bar. */
interface VolumeBar {
  time: number;
  value: number;
  color: string;
}

// ── Constants ─────────────────────────────────────────────────────────────

const TIMEFRAME_MS: Record<Timeframe, number> = {
  '1m': 60_000,
  '5m': 5 * 60_000,
  '15m': 15 * 60_000,
  '1h': 60 * 60_000,
  '1D': 24 * 60 * 60_000,
};

const MAX_RAW_CANDLES = 3000;

const COLORS = {
  up: '#22c55e',
  down: '#ef4444',
  upAlpha: 'rgba(34, 197, 94, 0.25)',
  downAlpha: 'rgba(239, 68, 68, 0.25)',
  grid: 'rgba(30, 41, 59, 0.35)',
  crosshair: '#475569',
  labelBg: '#1e293b',
  border: '#1e293b',
  text: '#94a3b8',
  textMuted: '#64748b',
  ghostLine: '#0ea5e9',
};

// ── Aggregation ───────────────────────────────────────────────────────────

/**
 * Aggregates raw 1-minute candles into the target timeframe.
 * Produces chart-ready candle + volume data, sorted by time.
 * Filters by symbol so only candles for the active instrument are shown.
 */
function aggregateCandles(
  rawCandles: RawCandle[],
  timeframe: Timeframe,
  symbol: string
): { candles: ChartCandle[]; volumes: VolumeBar[] } {
  const intervalMs = TIMEFRAME_MS[timeframe];

  // Filter by symbol (case-insensitive)
  const filtered = symbol
    ? rawCandles.filter(
        (c) => c.symbol.toUpperCase() === symbol.toUpperCase()
      )
    : rawCandles;

  // Sort by timestamp
  const sorted = [...filtered].sort(
    (a, b) => a.start_timestamp_ms - b.start_timestamp_ms
  );

  // Group into time buckets
  const buckets = new Map<
    number,
    { open: number; high: number; low: number; close: number; volume: number }
  >();

  for (const candle of sorted) {
    const bucketKey =
      Math.floor(candle.start_timestamp_ms / intervalMs) * intervalMs;

    const existing = buckets.get(bucketKey);
    if (existing) {
      existing.high = Math.max(existing.high, candle.high);
      existing.low = Math.min(existing.low, candle.low);
      existing.close = candle.close;
      existing.volume += candle.volume;
    } else {
      buckets.set(bucketKey, {
        open: candle.open,
        high: candle.high,
        low: candle.low,
        close: candle.close,
        volume: candle.volume,
      });
    }
  }

  const candles: ChartCandle[] = [];
  const volumes: VolumeBar[] = [];

  // Sort bucket keys to ensure strictly ascending times
  const keys = Array.from(buckets.keys()).sort((a, b) => a - b);

  for (const key of keys) {
    const b = buckets.get(key)!;
    const timeSec = Math.floor(key / 1000);
    const isUp = b.close >= b.open;

    candles.push({
      time: timeSec,
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
    });

    volumes.push({
      time: timeSec,
      value: b.volume,
      color: isUp ? COLORS.upAlpha : COLORS.downAlpha,
    });
  }

  return { candles, volumes };
}

// ── Component ─────────────────────────────────────────────────────────────

export default function AlphaPredictiveChart({
  activeProfile = 'INTRADAY',
  timeframe = '1m',
}: AlphaPredictiveChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const ghostLineRef = useRef<ISeriesApi<'Line'> | null>(null);

  // Raw 1-minute candle buffer, keyed by symbol for efficient filtering.
  const rawCandlesRef = useRef<RawCandle[]>([]);

  // Track the current active timeframe and symbol via refs (avoids re-creating WS).
  const timeframeRef = useRef<Timeframe>(timeframe);
  timeframeRef.current = timeframe;

  // Ghost line anchor
  const lastCloseRef = useRef<{ time: number; value: number } | null>(null);

  // Get active symbol from trade store decisions
  const activeDecision = useTradeStore((s) => s.activeDecision);
  const liveDecisions = useTradeStore((s) => s.liveDecisions);

  const activeSymbol = useMemo(() => {
    const d = activeDecision ?? liveDecisions[liveDecisions.length - 1];
    return d?.symbol ?? '';
  }, [activeDecision, liveDecisions]);

  const activeSymbolRef = useRef(activeSymbol);
  activeSymbolRef.current = activeSymbol;

  // ── Full redraw (timeframe change, symbol change) ───────────────────────
  const redrawChart = useCallback(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current) return;

    const { candles, volumes } = aggregateCandles(
      rawCandlesRef.current,
      timeframeRef.current,
      activeSymbolRef.current
    );

    // setData() expects strictly ascending times — our aggregation guarantees this.
    candleSeriesRef.current.setData(candles as Array<{ time: Time; open: number; high: number; low: number; close: number }>);
    volumeSeriesRef.current.setData(volumes as Array<{ time: Time; value: number; color: string }>);

    if (candles.length > 0) {
      chartRef.current?.timeScale().scrollToRealTime();
      const last = candles[candles.length - 1];
      lastCloseRef.current = { time: last.time, value: last.close };
    }
  }, []);

  // Redraw when timeframe or symbol changes.
  useEffect(() => {
    redrawChart();
  }, [timeframe, activeSymbol, redrawChart]);

  // ── Chart initialisation & data pipeline ──────────────────────────────
  useEffect(() => {
    if (!chartContainerRef.current) return;

    // ── Create chart ────────────────────────────────────────────────────
    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: COLORS.text,
        fontSize: 11,
        fontFamily: "'Inter', 'SF Mono', monospace",
      },
      grid: {
        vertLines: { color: COLORS.grid },
        horzLines: { color: COLORS.grid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        horzLine: { color: COLORS.crosshair, labelBackgroundColor: COLORS.labelBg },
        vertLine: { color: COLORS.crosshair, labelBackgroundColor: COLORS.labelBg },
      },
      rightPriceScale: {
        borderColor: COLORS.border,
        scaleMargins: { top: 0.05, bottom: 0.25 }, // Leave space for volume at bottom
      },
      timeScale: {
        borderColor: COLORS.border,
        timeVisible: true,
        secondsVisible: timeframe === '1m',
        rightOffset: 5,
        fixLeftEdge: true,
        fixRightEdge: true,
        barSpacing: 8,
      },
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight || 400,
    });

    // ── Candlestick series ──────────────────────────────────────────────
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: COLORS.up,
      downColor: COLORS.down,
      borderVisible: false,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
      priceLineVisible: true,
      lastValueVisible: true,
      priceFormat: { type: 'price', precision: 2, minMove: 0.05 },
    });

    // ── Volume histogram (overlaid at bottom) ───────────────────────────
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '',  // Overlay on main pane
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    // ── Ghost prediction line ───────────────────────────────────────────
    const ghostLine = chart.addSeries(LineSeries, {
      color: COLORS.ghostLine,
      lineWidth: 2,
      lineStyle: 2,
      crosshairMarkerVisible: true,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    ghostLineRef.current = ghostLine;

    // ── Responsive resize ───────────────────────────────────────────────
    const resizeObserver = new ResizeObserver(() => {
      if (chartContainerRef.current) {
        const rect = chartContainerRef.current.getBoundingClientRect();
        chart.resize(Math.floor(rect.width), Math.floor(rect.height));
      }
    });
    resizeObserver.observe(chartContainerRef.current);

    // ── Data Pipeline ───────────────────────────────────────────────────
    let cleanupFn: (() => void) | null = null;

    const isTauri =
      typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

    /**
     * Shared handler: receives a raw 1-minute candle from any data source,
     * stores it, and updates the chart using setData() for correctness.
     *
     * We use setData() (full redraw of current symbol/timeframe) instead of
     * update() to avoid "Cannot update oldest data" errors caused by
     * interleaved multi-symbol candles arriving out of time order.
     */
    const handleCandleData = (candle: RawCandle) => {
      // Validate the candle has required fields
      if (
        !candle.symbol ||
        !Number.isFinite(candle.start_timestamp_ms) ||
        candle.start_timestamp_ms <= 0 ||
        !Number.isFinite(candle.open)
      ) {
        return;
      }

      // Store in the raw buffer
      rawCandlesRef.current.push(candle);

      // Cap buffer size
      if (rawCandlesRef.current.length > MAX_RAW_CANDLES) {
        rawCandlesRef.current = rawCandlesRef.current.slice(-MAX_RAW_CANDLES);
      }

      // Only update chart if this candle's symbol matches the active symbol
      const sym = activeSymbolRef.current;
      if (sym && candle.symbol.toUpperCase() !== sym.toUpperCase()) {
        return;
      }

      // Re-aggregate and set full data (safe, no time ordering issues)
      const { candles, volumes } = aggregateCandles(
        rawCandlesRef.current,
        timeframeRef.current,
        sym
      );

      if (candles.length > 0 && candleSeriesRef.current && volumeSeriesRef.current) {
        candleSeriesRef.current.setData(candles as Array<{ time: Time; open: number; high: number; low: number; close: number }>);
        volumeSeriesRef.current.setData(volumes as Array<{ time: Time; value: number; color: string }>);
        chart.timeScale().scrollToRealTime();

        const last = candles[candles.length - 1];
        lastCloseRef.current = { time: last.time, value: last.close };
      }
    };

    if (isTauri) {
      // ── Tauri IPC Path ──────────────────────────────────────────────
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
              console.error('[Chart] IPC OHLC error:', error);
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
                    );
                    ghostLine.setData([
                      { time: lastCloseRef.current.time as Time, value: lastCloseRef.current.value },
                      { time: targetTime as Time, value: signal.predicted_close_price as number },
                    ]);
                  }
                });
              } catch (error) {
                console.error('[Chart] IPC Predictive error:', error);
              }
            }
          );
        } catch (err) {
          console.warn('[Chart] Tauri IPC setup failed:', err);
        }
      };

      setupTauriListeners();
      cleanupFn = () => {
        if (unlistenOhlc) unlistenOhlc();
        if (unlistenPredict) unlistenPredict();
      };
    } else {
      // ── WebSocket Fallback (browser / Next.js dev) ──────────────────
      const ohlcWsUrl =
        process.env.NEXT_PUBLIC_OHLC_WS_URL || 'ws://127.0.0.1:8081';

      let ohlcWs: WebSocket | null = null;
      let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
      let destroyed = false;

      const connectOhlcWs = () => {
        if (destroyed) return;
        try {
          ohlcWs = new WebSocket(ohlcWsUrl);

          ohlcWs.onopen = () => {
            console.log('[Chart] OHLC WS connected →', ohlcWsUrl);
          };

          ohlcWs.onmessage = (event) => {
            try {
              const candle: RawCandle = JSON.parse(event.data);
              handleCandleData(candle);
            } catch (e) {
              console.error('[Chart] OHLC WS parse error:', e);
            }
          };

          ohlcWs.onclose = () => {
            if (!destroyed) {
              reconnectTimer = setTimeout(connectOhlcWs, 3000);
            }
          };

          ohlcWs.onerror = () => {
            // onclose will fire after this, triggering reconnect
          };
        } catch {
          if (!destroyed) {
            reconnectTimer = setTimeout(connectOhlcWs, 3000);
          }
        }
      };

      connectOhlcWs();

      cleanupFn = () => {
        destroyed = true;
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
      volumeSeriesRef.current = null;
      ghostLineRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Update time scale settings when timeframe changes (show seconds only for 1m)
  useEffect(() => {
    chartRef.current?.timeScale().applyOptions({
      secondsVisible: timeframe === '1m',
      barSpacing: timeframe === '1D' ? 14 : timeframe === '1h' ? 10 : 8,
    });
  }, [timeframe]);

  return (
    <div ref={chartContainerRef} className="h-full w-full outline-none" />
  );
}