'use client';

import React, { useEffect, useRef, useMemo, useState, useCallback } from 'react';
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
import { useTradeStore, OhlcCandle, TradeProfile } from '../store/useTradeStore';
import { Maximize2, Minimize2 } from 'lucide-react';

// ── Exported Types ────────────────────────────────────────────────────────

export type Timeframe = '1m' | '5m' | '15m' | '1h' | '1D';

interface AlphaPredictiveChartProps {
  activeProfile?: TradeProfile;
  timeframe?: Timeframe;
  /** External fullscreen toggle (controlled by parent layout). */
  isExpanded?: boolean;
  onToggleExpand?: () => void;
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

const COLORS = {
  up: '#22c55e',
  down: '#ef4444',
  upAlpha: 'rgba(34, 197, 94, 0.2)',
  downAlpha: 'rgba(239, 68, 68, 0.2)',
  grid: 'rgba(30, 41, 59, 0.3)',
  crosshair: '#475569',
  labelBg: '#1e293b',
  border: '#1e293b',
  text: '#94a3b8',
  ghostLine: '#0ea5e9',
};

// ── Aggregation ───────────────────────────────────────────────────────────

function aggregateCandles(
  rawCandles: OhlcCandle[],
  timeframe: Timeframe,
  symbol: string
): { candles: ChartCandle[]; volumes: VolumeBar[] } {
  const intervalMs = TIMEFRAME_MS[timeframe];

  const filtered = symbol
    ? rawCandles.filter(
        (c) => c.symbol.toUpperCase() === symbol.toUpperCase()
      )
    : rawCandles;

  const sorted = [...filtered].sort(
    (a, b) => a.start_timestamp_ms - b.start_timestamp_ms
  );

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
  const keys = Array.from(buckets.keys()).sort((a, b) => a - b);

  for (const key of keys) {
    const b = buckets.get(key)!;
    const timeSec = Math.floor(key / 1000);
    const isUp = b.close >= b.open;

    candles.push({ time: timeSec, open: b.open, high: b.high, low: b.low, close: b.close });
    volumes.push({ time: timeSec, value: b.volume, color: isUp ? COLORS.upAlpha : COLORS.downAlpha });
  }

  return { candles, volumes };
}

// ── Component ─────────────────────────────────────────────────────────────

export default function AlphaPredictiveChart({
  activeProfile = 'INTRADAY',
  timeframe = '1m',
  isExpanded = false,
  onToggleExpand,
}: AlphaPredictiveChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const ghostLineRef = useRef<ISeriesApi<'Line'> | null>(null);

  // ── Read OHLC data from the PERSISTED Zustand store ─────────────────
  // The store's connectAlphaWebSocket() (called once in page.tsx) handles the
  // WebSocket connection and stores candles in ohlcCandles[]. This data
  // persists across component remounts, profile switches, etc.
  const ohlcCandles = useTradeStore((s) => s.ohlcCandles);

  // Active symbol from trade decisions
  const activeDecision = useTradeStore((s) => s.activeDecision);
  const liveDecisions = useTradeStore((s) => s.liveDecisions);

  const activeSymbol = useMemo(() => {
    const d = activeDecision ?? liveDecisions[liveDecisions.length - 1];
    return d?.symbol ?? '';
  }, [activeDecision, liveDecisions]);

  // OHLC info for the header watermark
  const latestCandle = useMemo(() => {
    if (!activeSymbol || ohlcCandles.length === 0) return null;
    const symbolCandles = ohlcCandles.filter(
      (c) => c.symbol.toUpperCase() === activeSymbol.toUpperCase()
    );
    return symbolCandles.length > 0 ? symbolCandles[symbolCandles.length - 1] : null;
  }, [ohlcCandles, activeSymbol]);

  // ── Aggregated data (memoized) ──────────────────────────────────────
  const { candles: chartData, volumes: volumeData } = useMemo(
    () => aggregateCandles(ohlcCandles, timeframe, activeSymbol),
    [ohlcCandles, timeframe, activeSymbol]
  );

  // ── Chart init ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!chartContainerRef.current) return;

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
        scaleMargins: { top: 0.05, bottom: 0.22 },
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

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });

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

    const resizeObserver = new ResizeObserver(() => {
      if (chartContainerRef.current) {
        const rect = chartContainerRef.current.getBoundingClientRect();
        chart.resize(Math.floor(rect.width), Math.floor(rect.height));
      }
    });
    resizeObserver.observe(chartContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      candleSeriesRef.current = null;
      volumeSeriesRef.current = null;
      ghostLineRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Sync chart data when store data / timeframe / symbol changes ────
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current) return;

    candleSeriesRef.current.setData(
      chartData as Array<{ time: Time; open: number; high: number; low: number; close: number }>
    );
    volumeSeriesRef.current.setData(
      volumeData as Array<{ time: Time; value: number; color: string }>
    );

    chartRef.current?.timeScale().scrollToRealTime();
  }, [chartData, volumeData]);

  // ── Update time scale on timeframe change ──────────────────────────
  useEffect(() => {
    chartRef.current?.timeScale().applyOptions({
      secondsVisible: timeframe === '1m',
      barSpacing: timeframe === '1D' ? 14 : timeframe === '1h' ? 10 : 8,
    });
  }, [timeframe]);

  // ── Resize chart when expand/collapse toggle fires ─────────────────
  useEffect(() => {
    if (chartRef.current && chartContainerRef.current) {
      const rect = chartContainerRef.current.getBoundingClientRect();
      chartRef.current.resize(Math.floor(rect.width), Math.floor(rect.height));
    }
  }, [isExpanded]);

  // ── Chart info watermark ───────────────────────────────────────────
  const ohlcLabel = latestCandle
    ? `O ${latestCandle.open.toFixed(2)}  H ${latestCandle.high.toFixed(2)}  L ${latestCandle.low.toFixed(2)}  C ${latestCandle.close.toFixed(2)}`
    : '';

  const candleCount = chartData.length;

  return (
    <div className="relative flex h-full w-full flex-col outline-none">
      {/* ── Chart Control Bar ─────────────────────────────────────── */}
      <div className="flex shrink-0 items-center justify-between px-3 py-1.5 border-b border-white/5">
        {/* OHLC watermark */}
        <div className="flex items-center gap-3 text-[11px] tabular-nums">
          {activeSymbol && (
            <span className="font-semibold text-text-primary">{activeSymbol}</span>
          )}
          {ohlcLabel && (
            <span className="text-text-muted">{ohlcLabel}</span>
          )}
          {candleCount > 0 && (
            <span className="text-text-muted opacity-60">
              {candleCount} candles
            </span>
          )}
        </div>

        {/* Expand/Collapse toggle */}
        <div className="flex items-center gap-1">
          {onToggleExpand && (
            <button
              type="button"
              onClick={onToggleExpand}
              className="rounded p-1 text-text-muted transition-colors hover:bg-elevated hover:text-text-primary"
              title={isExpanded ? 'Collapse chart' : 'Expand chart'}
            >
              {isExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
            </button>
          )}
        </div>
      </div>

      {/* ── Chart Canvas ──────────────────────────────────────────── */}
      <div
        ref={chartContainerRef}
        className="flex-1 min-h-0 w-full"
      />

      {/* ── Empty state ───────────────────────────────────────────── */}
      {chartData.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="flex flex-col items-center gap-2 text-text-muted">
            <div className="h-8 w-8 animate-pulse rounded-full border-2 border-text-muted/30" />
            <span className="text-xs">
              {activeSymbol
                ? `Waiting for ${activeSymbol} candle data…`
                : 'Waiting for market data…'}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}