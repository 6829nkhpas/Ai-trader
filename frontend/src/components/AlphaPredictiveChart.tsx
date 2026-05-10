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
  LineStyle,
} from 'lightweight-charts';
import { useTradeStore, OhlcCandle, TradeProfile, ChartTimeframe } from '../store/useTradeStore';
import { useHistoricalData, HistoricalCandle } from '../hooks/useHistoricalData';
import { Maximize2, Minimize2 } from 'lucide-react';

// ── Exported Types ────────────────────────────────────────────────────────

export type Timeframe = '1m' | '5m' | '10m' | '15m' | '1h' | '1D';

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

/** EMA data point. */
interface EmaPoint {
  time: number;
  value: number;
}

// ── Constants ─────────────────────────────────────────────────────────────

const TIMEFRAME_MS: Record<Timeframe, number> = {
  '1m': 60_000,
  '5m': 5 * 60_000,
  '10m': 10 * 60_000,
  '15m': 15 * 60_000,
  '1h': 60 * 60_000,
  '1D': 24 * 60 * 60_000,
};

// ── Institutional Dark-Mode Palette ──────────────────────────────────────
const COLORS = {
  // Canvas background — deep slate-900
  canvasBg: '#0F172A',
  // Text on price/time axes
  text: '#CBD5E1',
  // Candlestick body and wick
  up: '#22c55e',
  down: '#ef4444',
  // Volume bars — semi-transparent
  volumeUp: 'rgba(34, 197, 94, 0.35)',
  volumeDown: 'rgba(239, 68, 68, 0.30)',
  // Grid lines — subtle slate
  grid: 'rgba(51, 65, 85, 0.4)',
  // Crosshair
  crosshair: 'rgba(148, 163, 184, 0.5)',
  crosshairLabel: '#1E293B',
  // Axis borders
  border: '#334155',
  // Ghost line (predictive)
  ghostLine: '#0ea5e9',
  // EMA ribbons
  ema9: '#38bdf8',   // sky-400 (fast)
  ema21: '#f472b6',  // pink-400 (slow)
};

// ── EMA Calculation Engine ────────────────────────────────────────────────

/**
 * Calculates the Exponential Moving Average for an array of closing prices.
 *
 * @param closes  — Array of `{ time, value }` where `value` is the closing price.
 * @param period  — EMA period (e.g. 9 or 21).
 * @returns         Array of `{ time, value }` EMA points (same length as input;
 *                  the first `period - 1` points use a progressively-built SMA seed).
 */
function calculateEMA(
  closes: { time: number; value: number }[],
  period: number
): EmaPoint[] {
  if (closes.length === 0) return [];

  const result: EmaPoint[] = [];
  const k = 2 / (period + 1); // smoothing factor

  // Seed: SMA of the first `period` data points
  let sum = 0;
  for (let i = 0; i < closes.length; i++) {
    if (i < period) {
      sum += closes[i].value;
      // Progressive SMA until we have enough data
      const sma = sum / (i + 1);
      result.push({ time: closes[i].time, value: sma });
    } else {
      // EMA = close * k + prevEMA * (1 - k)
      const prevEma = result[result.length - 1].value;
      const ema = closes[i].value * k + prevEma * (1 - k);
      result.push({ time: closes[i].time, value: ema });
    }
  }

  return result;
}

// ── Aggregation ───────────────────────────────────────────────────────────

function aggregateCandles(
  rawCandles: OhlcCandle[],
  timeframe: Timeframe,
  symbol: string
): { candles: ChartCandle[]; volumes: VolumeBar[]; ema9: EmaPoint[]; ema21: EmaPoint[] } {
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
  const closes: { time: number; value: number }[] = [];
  const keys = Array.from(buckets.keys()).sort((a, b) => a - b);

  for (const key of keys) {
    const b = buckets.get(key)!;
    const timeSec = Math.floor(key / 1000);
    const isUp = b.close >= b.open;

    candles.push({ time: timeSec, open: b.open, high: b.high, low: b.low, close: b.close });
    volumes.push({ time: timeSec, value: b.volume, color: isUp ? COLORS.volumeUp : COLORS.volumeDown });
    closes.push({ time: timeSec, value: b.close });
  }

  // Calculate EMA overlays
  const ema9 = calculateEMA(closes, 9);
  const ema21 = calculateEMA(closes, 21);

  return { candles, volumes, ema9, ema21 };
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
  const ema9SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ema21SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  // ── Read OHLC data from the PERSISTED Zustand store ─────────────────
  // The store's connectAlphaWebSocket() (called once in page.tsx) handles the
  // WebSocket connection and stores candles in ohlcCandles[]. This data
  // persists across component remounts, profile switches, etc.
  const ohlcCandles = useTradeStore((s) => s.ohlcCandles);

  // Active symbol from trade decisions
  const activeDecision = useTradeStore((s) => s.activeDecision);
  const liveDecisions = useTradeStore((s) => s.liveDecisions);

  // ── Read the global activeTimeframe from Zustand ─────────────────
  // This is the single source of truth for timeframe selection.
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);

  const activeSymbol = useMemo(() => {
    const d = activeDecision ?? liveDecisions[liveDecisions.length - 1];
    return d?.symbol ?? '';
  }, [activeDecision, liveDecisions]);

  // ── Historical data from QuestDB (5-year daily candles) ─────────────
  const { candles: historicalCandles, loading: histLoading } = useHistoricalData(activeSymbol);

  // ── Merge historical + live candles ─────────────────────────────────
  // The backend emits 10-minute candles via WebSocket port 8081.
  // The aggregateCandles() engine re-buckets these into any selected
  // timeframe (15m, 1H, 1D = upward aggregation; 1m, 5m = displayed
  // as raw 10m bars until sub-10m backend streams are implemented).
  // Live candles always flow through — only the Ghost Line is gated to 10m.
  // TODO: Route additional Kafka timeframes for 1m/5m sub-candle resolution.
  const mergedCandles = useMemo(() => {
    // Convert historical candles to OhlcCandle format
    const histAsOhlc: OhlcCandle[] = historicalCandles.map((h) => ({
      symbol: activeSymbol,
      start_timestamp_ms: h.time * 1000, // seconds → ms
      open: h.open,
      high: h.high,
      low: h.low,
      close: h.close,
      volume: h.volume,
    }));

    // Merge: historical first, then live (deduplicated by timestamp)
    const all = [...histAsOhlc, ...ohlcCandles];

    // Dedup by raw timestamp
    const seen = new Set<number>();
    const deduped: OhlcCandle[] = [];
    for (const c of all) {
      if (!seen.has(c.start_timestamp_ms)) {
        seen.add(c.start_timestamp_ms);
        deduped.push(c);
      }
    }

    return deduped;
  }, [historicalCandles, ohlcCandles, activeSymbol]);

  // OHLC info for the header watermark
  const latestCandle = useMemo(() => {
    if (!activeSymbol || mergedCandles.length === 0) return null;
    const symbolCandles = mergedCandles.filter(
      (c) => c.symbol.toUpperCase() === activeSymbol.toUpperCase()
    );
    return symbolCandles.length > 0 ? symbolCandles[symbolCandles.length - 1] : null;
  }, [mergedCandles, activeSymbol]);

  // ── Aggregated data (memoized) ──────────────────────────────────────
  const { candles: chartData, volumes: volumeData, ema9: ema9Data, ema21: ema21Data } = useMemo(
    () => aggregateCandles(mergedCandles, timeframe, activeSymbol),
    [mergedCandles, timeframe, activeSymbol]
  );

  // ── Chart init ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!chartContainerRef.current) return;

    // ── Institutional Dark-Mode Chart Configuration ───────────────────
    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: COLORS.canvasBg },
        textColor: COLORS.text,
        fontSize: 11,
        fontFamily: "'Inter', 'SF Mono', 'Menlo', monospace",
      },
      grid: {
        vertLines: { color: COLORS.grid, style: LineStyle.Solid },
        horzLines: { color: COLORS.grid, style: LineStyle.Solid },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        horzLine: {
          color: COLORS.crosshair,
          style: LineStyle.Dashed,
          labelBackgroundColor: COLORS.crosshairLabel,
        },
        vertLine: {
          color: COLORS.crosshair,
          style: LineStyle.Dashed,
          labelBackgroundColor: COLORS.crosshairLabel,
        },
      },
      rightPriceScale: {
        borderColor: COLORS.border,
        scaleMargins: { top: 0.05, bottom: 0.22 },
      },
      timeScale: {
        borderColor: COLORS.border,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 5,
        fixLeftEdge: true,
        fixRightEdge: true,
        barSpacing: 8,
      },
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight || 400,
    });

    // ── Candlestick Series ────────────────────────────────────────────
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

    // ── Volume Histogram — pinned to bottom 20% of chart ─────────────
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    // ── EMA 9 Ribbon (fast — cyan) ───────────────────────────────────
    const ema9Series = chart.addSeries(LineSeries, {
      color: COLORS.ema9,
      lineWidth: 2,
      crosshairMarkerVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    // ── EMA 21 Ribbon (slow — pink) ──────────────────────────────────
    const ema21Series = chart.addSeries(LineSeries, {
      color: COLORS.ema21,
      lineWidth: 2,
      crosshairMarkerVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    // ── Ghost Line (predictive forward projection) ───────────────────
    const ghostLine = chart.addSeries(LineSeries, {
      color: COLORS.ghostLine,
      lineWidth: 2,
      lineStyle: LineStyle.SparseDotted,
      crosshairMarkerVisible: true,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    // ── Store refs ───────────────────────────────────────────────────
    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    ema9SeriesRef.current = ema9Series;
    ema21SeriesRef.current = ema21Series;
    ghostLineRef.current = ghostLine;

    // ── Responsive resize ───────────────────────────────────────────
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
      ema9SeriesRef.current = null;
      ema21SeriesRef.current = null;
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

    // Update EMA overlays
    if (ema9SeriesRef.current) {
      ema9SeriesRef.current.setData(
        ema9Data as Array<{ time: Time; value: number }>
      );
    }
    if (ema21SeriesRef.current) {
      ema21SeriesRef.current.setData(
        ema21Data as Array<{ time: Time; value: number }>
      );
    }

    chartRef.current?.timeScale().scrollToRealTime();
  }, [chartData, volumeData, ema9Data, ema21Data]);

  // ── Ghost Line: render latest predictive forward projection ─────────
  // CRITICAL CONSTRAINT: The Ghost Line (ML forward projection) MUST ONLY
  // be visible when activeTimeframe === '10m'. The predictive math uses a
  // 14-period rolling window of 10-minute closing prices for Least-Squares
  // Linear Regression. Rendering it on any other timeframe would display
  // mathematically invalid projections.
  const predictiveSignals = useTradeStore((s) => s.predictiveSignals);

  useEffect(() => {
    if (!ghostLineRef.current) return;

    // ── Timeframe Guard ──────────────────────────────────────────
    // Ghost Line is ONLY valid on the 10m chart. On any other timeframe,
    // clear the line entirely to prevent misleading projections.
    if (activeTimeframe !== '10m') {
      ghostLineRef.current.setData([]);
      return;
    }

    // Only use the LATEST prediction for the active symbol
    const symbolSignals = activeSymbol
      ? predictiveSignals.filter(
          (s) => s.symbol.toUpperCase() === activeSymbol.toUpperCase()
        )
      : predictiveSignals;

    const latest = symbolSignals.length > 0
      ? symbolSignals[symbolSignals.length - 1]
      : null;

    // Need both a candle anchor point AND a prediction to draw the ghost line
    if (!latest || chartData.length === 0) {
      ghostLineRef.current.setData([]);
      return;
    }

    const lastCandle = chartData[chartData.length - 1];
    const targetTimeSec = Math.floor(latest.target_timestamp_ms / 1000);

    // Only project forward — skip if prediction is in the past
    if (targetTimeSec <= lastCandle.time) {
      ghostLineRef.current.setData([]);
      return;
    }

    // 2-point ghost line: current close → predicted close
    ghostLineRef.current.setData([
      { time: lastCandle.time as Time, value: lastCandle.close },
      { time: targetTimeSec as Time, value: latest.predicted_close_price },
    ]);
  }, [predictiveSignals, activeSymbol, chartData, activeTimeframe]);

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

  // Latest EMA values for the header badge
  const latestEma9 = ema9Data.length > 0 ? ema9Data[ema9Data.length - 1].value : null;
  const latestEma21 = ema21Data.length > 0 ? ema21Data[ema21Data.length - 1].value : null;

  return (
    <div className="relative flex h-full w-full flex-col outline-none">
      {/* ── Chart Control Bar ─────────────────────────────────────── */}
      <div className="flex shrink-0 items-center justify-end px-3 py-1.5 border-b border-white/5">
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

      {/* ── Empty / Loading state ────────────────────────────────────── */}
      {chartData.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="flex flex-col items-center gap-2 text-text-muted">
            <div className="h-8 w-8 animate-pulse rounded-full border-2 border-text-muted/30" />
            <span className="text-xs">
              {histLoading
                ? `Loading ${activeSymbol || ''} historical data from QuestDB…`
                : activeSymbol
                  ? `Waiting for ${activeSymbol} candle data…`
                  : 'Waiting for market data…'}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}