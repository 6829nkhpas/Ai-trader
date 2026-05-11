'use client';

import React, { useEffect, useRef, useMemo, useCallback } from 'react';
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
import { useHistoricalData } from '../hooks/useHistoricalData';
import { useChartUIStore } from '../store/useChartUIStore';

// ── Exported Types ────────────────────────────────────────────────────────

export type Timeframe = '1m' | '5m' | '10m' | '15m' | '1h' | '1H' | '1D';

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
  '1H': 60 * 60_000,
  '1D': 24 * 60 * 60_000,
};

// ── Institutional Dark-Mode Palette ──────────────────────────────────────
const COLORS = {
  canvasBg: '#0F172A',
  text: '#CBD5E1',
  up: '#22c55e',
  down: '#ef4444',
  volumeUp: 'rgba(34, 197, 94, 0.35)',
  volumeDown: 'rgba(239, 68, 68, 0.30)',
  grid: 'rgba(51, 65, 85, 0.4)',
  crosshair: 'rgba(148, 163, 184, 0.5)',
  crosshairLabel: '#1E293B',
  border: '#334155',
  // Amber ghost line — stands out clearly against the dark background
  // at any zoom level. Sky blue was too close to the EMA-9 ribbon color
  // and too subtle when the slope is small relative to the price scale.
  ghostLine: '#f59e0b',
  ema9: '#38bdf8',
  ema21: '#f472b6',
};

// ── EMA Calculation Engine ────────────────────────────────────────────────

function calculateEMA(
  closes: { time: number; value: number }[],
  period: number
): EmaPoint[] {
  if (closes.length === 0) return [];
  const result: EmaPoint[] = [];
  const k = 2 / (period + 1);
  let sum = 0;
  for (let i = 0; i < closes.length; i++) {
    if (i < period) {
      sum += closes[i].value;
      result.push({ time: closes[i].time, value: sum / (i + 1) });
    } else {
      const ema = closes[i].value * k + result[result.length - 1].value * (1 - k);
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
  const empty = { candles: [], volumes: [], ema9: [], ema21: [] };
  const intervalMs = TIMEFRAME_MS[timeframe];
  if (!intervalMs) return empty;

  const filtered = symbol
    ? rawCandles.filter((c) => c.symbol.toUpperCase() === symbol.toUpperCase())
    : rawCandles;

  const sorted = [...filtered].sort((a, b) => a.start_timestamp_ms - b.start_timestamp_ms);

  const buckets = new Map<
    number,
    { open: number; high: number; low: number; close: number; volume: number }
  >();

  for (const candle of sorted) {
    const bucketKey = Math.floor(candle.start_timestamp_ms / intervalMs) * intervalMs;
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

  // ── Refs tracking what's already painted on the chart ────────────────
  // These let us use series.update() for the last candle instead of
  // series.setData() (which redraws everything and causes the jitter).
  const lastPaintedCandleCountRef = useRef<number>(0);
  const lastPaintedTimeframeRef = useRef<string>('');
  const lastPaintedSymbolRef = useRef<string>('');
  const historicalLoadedRef = useRef<boolean>(false);

  // ── Store subscriptions ─────────────────────────────────────────────
  const ohlcCandles = useTradeStore((s) => s.ohlcCandles);
  const activeDecision = useTradeStore((s) => s.activeDecision);
  const liveDecisions = useTradeStore((s) => s.liveDecisions);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);
  const predictiveSignals = useTradeStore((s) => s.predictiveSignals);

  const { activeCursor, activeDrawingTool } = useChartUIStore();

  const activeSymbol = useMemo(() => {
    const d = activeDecision ?? liveDecisions[liveDecisions.length - 1];
    return d?.symbol ?? 'RELIANCE';
  }, [activeDecision, liveDecisions]);

  // ── Historical data from QuestDB ──────────────────────────────────────
  const { candles: historicalCandles, loading: histLoading } = useHistoricalData(activeSymbol);

  // ── Merge historical + live candles ──────────────────────────────────
  const mergedCandles = useMemo(() => {
    const histAsOhlc: OhlcCandle[] = historicalCandles.map((h) => ({
      symbol: activeSymbol,
      start_timestamp_ms: h.time * 1000,
      open: h.open,
      high: h.high,
      low: h.low,
      close: h.close,
      volume: h.volume,
    }));

    const candleMap = new Map<number, OhlcCandle>();
    for (const c of histAsOhlc) candleMap.set(c.start_timestamp_ms, c);
    for (const c of ohlcCandles) {
      if (c.symbol.toUpperCase() === activeSymbol.toUpperCase()) {
        candleMap.set(c.start_timestamp_ms, c);
      }
    }
    return Array.from(candleMap.values());
  }, [historicalCandles, ohlcCandles, activeSymbol]);

  // ── Effective timeframe (Zustand wins over prop) ──────────────────────
  const effectiveTimeframe = (activeTimeframe as Timeframe) ?? timeframe;

  // ── Aggregated chart data ─────────────────────────────────────────────
  const { candles: chartData, volumes: volumeData, ema9: ema9Data, ema21: ema21Data } = useMemo(
    () => aggregateCandles(mergedCandles, effectiveTimeframe, activeSymbol),
    [mergedCandles, effectiveTimeframe, activeSymbol]
  );

  // Latest candle for watermark header
  const latestCandle = useMemo(() => {
    if (!activeSymbol || mergedCandles.length === 0) return null;
    const sym = mergedCandles.filter((c) => c.symbol.toUpperCase() === activeSymbol.toUpperCase());
    return sym.length > 0 ? sym[sym.length - 1] : null;
  }, [mergedCandles, activeSymbol]);

  // ── Chart Init ────────────────────────────────────────────────────────
  useEffect(() => {
    if (!chartContainerRef.current) return;

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
        horzLine: { color: COLORS.crosshair, style: LineStyle.Dashed, labelBackgroundColor: COLORS.crosshairLabel },
        vertLine: { color: COLORS.crosshair, style: LineStyle.Dashed, labelBackgroundColor: COLORS.crosshairLabel },
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
        fixRightEdge: false, // ← false so realtime scroll works
        barSpacing: 8,
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight || 400,
    });

    // ── Candlestick Series ─────────────────────────────────────────────
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

    // ── Volume Histogram ───────────────────────────────────────────────
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    // ── EMA 9 (cyan — fast) ────────────────────────────────────────────
    const ema9Series = chart.addSeries(LineSeries, {
      color: COLORS.ema9,
      lineWidth: 2,
      crosshairMarkerVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    // ── EMA 21 (pink — slow) ───────────────────────────────────────────
    const ema21Series = chart.addSeries(LineSeries, {
      color: COLORS.ema21,
      lineWidth: 2,
      crosshairMarkerVisible: false,
      priceLineVisible: false,
      lastValueVisible: false,
    });

    // ── Ghost Line (predictive — amber dashed, 5-candle projection) ──────
    const ghostLine = chart.addSeries(LineSeries, {
      color: COLORS.ghostLine,
      lineWidth: 3,
      lineStyle: LineStyle.Dashed,
      crosshairMarkerVisible: true,
      crosshairMarkerRadius: 5,
      priceLineVisible: false,
      lastValueVisible: true,
      title: '▲ Proj',
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    ema9SeriesRef.current = ema9Series;
    ema21SeriesRef.current = ema21Series;
    ghostLineRef.current = ghostLine;

    // Reset paint tracking on mount
    lastPaintedCandleCountRef.current = 0;
    lastPaintedTimeframeRef.current = '';
    lastPaintedSymbolRef.current = '';
    historicalLoadedRef.current = false;

    // ── Responsive resize ──────────────────────────────────────────────
    const resizeObserver = new ResizeObserver(() => {
      if (chartContainerRef.current) {
        const { width, height } = chartContainerRef.current.getBoundingClientRect();
        chart.resize(Math.floor(width), Math.floor(height));
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

  // ── Smart data sync: setData on full reset, update() for last candle ─
  // This is the KEY fix for the shaking / jitter problem.
  //
  // Strategy:
  //   • When timeframe or symbol changes  → full setData() (unavoidable)
  //   • When a new bucket arrives          → full setData() (avoids ordering issues)
  //   • When the LAST candle updates       → series.update() only (zero re-render)
  //
  // series.update() in lightweight-charts replaces or appends a single bar
  // without touching any other data, producing perfectly smooth animation.
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current) return;
    if (chartData.length === 0) return;

    const prevTimeframe = lastPaintedTimeframeRef.current;
    const prevSymbol = lastPaintedSymbolRef.current;
    const prevCount = lastPaintedCandleCountRef.current;

    const timeframeChanged = prevTimeframe !== effectiveTimeframe;
    const symbolChanged = prevSymbol !== activeSymbol;
    // A candle was added (new bucket started) or this is the first render
    const newCandleArrived = chartData.length !== prevCount;

    if (timeframeChanged || symbolChanged || newCandleArrived) {
      // Full repaint needed — use setData
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

      // Only scroll to real-time on initial load or symbol/timeframe change,
      // not on every live tick (would fight user's manual scroll position).
      if (timeframeChanged || symbolChanged || prevCount === 0) {
        chartRef.current?.timeScale().scrollToRealTime();
      }
    } else {
      // ── SMOOTH UPDATE PATH ─────────────────────────────────────────
      // Same timeframe, same symbol, same candle count means the last
      // candle (current in-progress bucket) was updated with a new tick.
      // Use series.update() which only redraws that single bar — no jitter.
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
  }, [chartData, volumeData, ema9Data, ema21Data, effectiveTimeframe, activeSymbol]);

  // ── Ghost Line (predictive forward projection) ──────────────────────
  // Projects 5 candles forward so the line is clearly visible at normal
  // zoom. A 1-candle projection on a ₹2950 stock with a small EMA slope
  // is only ₹0.10 tall — invisible. 5 candles = ₹0.50+ = clearly visible.
  //
  // Uses real predictive signal when port 8082 is running, otherwise falls
  // back to EMA-9 linear regression slope across the last 8 candles.
  const GHOST_CANDLES = 5; // how many candles ahead to project

  useEffect(() => {
    if (!ghostLineRef.current || chartData.length < 8) return;

    const lastCandle = chartData[chartData.length - 1];
    const intervalSec = Math.floor((TIMEFRAME_MS[effectiveTimeframe] ?? TIMEFRAME_MS['10m']) / 1000);

    // ── Try real predictive signal first (port 8082) ───────────────────
    if (predictiveSignals.length > 0) {
      const symbolSignals = activeSymbol
        ? predictiveSignals.filter((s) => s.symbol.toUpperCase() === activeSymbol.toUpperCase())
        : predictiveSignals;

      const latest = symbolSignals.length > 0 ? symbolSignals[symbolSignals.length - 1] : null;

      if (latest) {
        const targetTimeSec = Math.floor(latest.target_timestamp_ms / 1000);
        // Accept signals that are at most 10 candles old
        const minValidTime = lastCandle.time - intervalSec * 10;
        if (targetTimeSec > minValidTime) {
          const endTime = Math.max(targetTimeSec, lastCandle.time + intervalSec * GHOST_CANDLES);
          const startPrice = lastCandle.close;
          const endPrice = latest.predicted_close_price;
          const slope = (endPrice - startPrice) / GHOST_CANDLES;

          // Build a point per candle for smooth rendering
          const points = Array.from({ length: GHOST_CANDLES + 1 }, (_, i) => ({
            time: (lastCandle.time + i * intervalSec) as Time,
            value: +(startPrice + slope * i).toFixed(2),
          }));
          // Replace last point with actual prediction target
          points[points.length - 1] = { time: endTime as Time, value: +(endPrice).toFixed(2) };

          ghostLineRef.current.setData(points);
          return;
        }
      }
    }

    // ── Fallback: EMA-9 linear regression slope (always visible) ─────────
    // Fit a least-squares line through the last 8 EMA-9 values to extract
    // the slope, then project it 5 candles forward. This is always visible
    // because 5 × slope is large enough to see even at normal zoom.
    if (ema9Data.length >= 8) {
      const window = ema9Data.slice(-8);
      const n = window.length;

      // Least-squares slope: Σ(xi - x̄)(yi - ȳ) / Σ(xi - x̄)²
      const xMean = (n - 1) / 2;
      const yMean = window.reduce((s, p) => s + p.value, 0) / n;
      let num = 0;
      let den = 0;
      for (let i = 0; i < n; i++) {
        num += (i - xMean) * (window[i].value - yMean);
        den += (i - xMean) ** 2;
      }
      const slope = den !== 0 ? num / den : 0;

      // Build projection: 5 candles ahead, one point each
      const points = Array.from({ length: GHOST_CANDLES + 1 }, (_, i) => ({
        time: (lastCandle.time + i * intervalSec) as Time,
        value: +(lastCandle.close + slope * i).toFixed(2),
      }));

      // Only draw if the projection is meaningful (at least 0.005% per candle)
      const totalMove = Math.abs(points[GHOST_CANDLES].value - lastCandle.close);
      if (totalMove / lastCandle.close >= 0.00005) {
        ghostLineRef.current.setData(points);
        return;
      }
    }

    ghostLineRef.current.setData([]);
  }, [predictiveSignals, activeSymbol, chartData, ema9Data, effectiveTimeframe]);

  // ── Update time scale on timeframe change ───────────────────────────
  useEffect(() => {
    chartRef.current?.timeScale().applyOptions({
      secondsVisible: effectiveTimeframe === '1m',
      barSpacing:
        effectiveTimeframe === '1D' ? 14
        : effectiveTimeframe === '1h' || effectiveTimeframe === '1H' ? 10
        : 8,
    });
  }, [effectiveTimeframe]);

  // ── Resize on expand/collapse ────────────────────────────────────────
  useEffect(() => {
    if (chartRef.current && chartContainerRef.current) {
      const { width, height } = chartContainerRef.current.getBoundingClientRect();
      chartRef.current.resize(Math.floor(width), Math.floor(height));
    }
  }, [isExpanded]);

  // ── Cursor class ─────────────────────────────────────────────────────
  const cursorClass = useMemo(() => {
    if (activeDrawingTool) return 'cursor-crosshair';
    switch (activeCursor) {
      case 'cross': return 'cursor-crosshair';
      case 'eraser': return 'cursor-not-allowed';
      default: return 'cursor-default';
    }
  }, [activeCursor, activeDrawingTool]);

  const handleChartInteraction = useCallback(() => {
    if (activeDrawingTool) {
      console.log(`[DRAWING ENGINE] tool: ${activeDrawingTool}`);
    }
  }, [activeDrawingTool]);

  const ohlcLabel = latestCandle
    ? `O ${latestCandle.open.toFixed(2)}  H ${latestCandle.high.toFixed(2)}  L ${latestCandle.low.toFixed(2)}  C ${latestCandle.close.toFixed(2)}`
    : '';

  return (
    <div
      className={`relative flex h-full w-full flex-col outline-none ${cursorClass}`}
      onMouseDown={handleChartInteraction}
    >
      {/* ── Chart Canvas ─────────────────────────────────────────── */}
      <div ref={chartContainerRef} className="flex-1 min-h-0 w-full" />

      {/* ── OHLC watermark (top-left overlay) ───────────────────── */}
      {ohlcLabel && (
        <div className="pointer-events-none absolute left-3 top-2 text-[10px] font-mono text-text-muted/60 select-none">
          {ohlcLabel}
        </div>
      )}

      {/* ── Empty / Loading state ────────────────────────────────── */}
      {chartData.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
          <div className="flex flex-col items-center gap-2 text-text-muted">
            <div className="h-8 w-8 animate-pulse rounded-full border-2 border-text-muted/30" />
            <span className="text-xs">
              {histLoading
                ? `Loading ${activeSymbol} historical data…`
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