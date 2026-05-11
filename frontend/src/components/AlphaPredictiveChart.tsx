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
import { useDrawingEngine } from '../hooks/useDrawingEngine';
import { useDrawingInteraction } from '../hooks/useDrawingInteraction';

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

  // ── Drawing series refs (one LineSeries per trendline on chart) ──────
  const drawingSeriesRef = useRef<ISeriesApi<'Line'>[]>([]);

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
  const drawings = useChartUIStore((s) => s.drawings);
  const drawingsVisible = useChartUIStore((s) => s.drawingsVisible);
  const selectedDrawingId = useChartUIStore((s) => s.selectedDrawingId);

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

  // ── Drawing Engine (drag-to-draw physics bridge v2) ──────────────────
  useDrawingEngine(chartRef, candleSeriesRef, chartContainerRef);
  // ── Drawing Interaction (select, move, resize, delete) ──────────────
  useDrawingInteraction(chartRef, candleSeriesRef, chartContainerRef);

  // ── Drawing Renderer — tool-specific rendering onto the chart canvas ──
  // Each tool type gets distinct visual behavior matching TradingView conventions.
  useEffect(() => {
    const chart = chartRef.current;
    const mainSeries = candleSeriesRef.current;
    if (!chart) return;

    // Remove previous drawing series from chart
    for (const series of drawingSeriesRef.current) {
      try {
        chart.removeSeries(series);
      } catch {
        // series may already be removed if chart was re-created
      }
    }
    drawingSeriesRef.current = [];

    // If drawings are hidden, stop here
    if (!drawingsVisible) return;

    // Color map per drawing tool
    const TOOL_COLORS: Record<string, string> = {
      'trendline': '#2962FF',
      'ray': '#2962FF',
      'info-line': '#00BCD4',
      'extended-line': '#2962FF',
      'trend-angle': '#FF9800',
      'horizontal-line': '#FF6D00',
      'horizontal-ray': '#FF6D00',
      'vertical-line': '#AB47BC',
      'cross-line': '#AB47BC',
      'parallel-channel': '#26A69A',
      'regression-trend': '#EC407A',
      'flat-top-bottom': '#26A69A',
      'disjoint-channel': '#78909C',
      'fib-retracement': '#FFD600',
      'trend-fib': '#FFD600',
      'long-position': '#22c55e',
      'short-position': '#ef4444',
      'price-range': '#00BCD4',
    };

    // Line style map per tool type
    const TOOL_LINE_STYLES: Record<string, number> = {
      'trendline': 0,       // Solid
      'ray': 0,             // Solid
      'info-line': 0,       // Solid
      'extended-line': 0,   // Solid
      'trend-angle': 0,     // Solid
      'horizontal-line': 2, // Dashed
      'horizontal-ray': 2,  // Dashed
      'vertical-line': 2,   // Dashed
      'cross-line': 2,      // Dashed
      'parallel-channel': 0,
      'regression-trend': 2,
      'flat-top-bottom': 2,
      'disjoint-channel': 0,
    };

    // Helper: compute interval from chart data
    const intervalSec = chartData.length >= 2
      ? chartData[1].time - chartData[0].time
      : 600; // fallback 10min

    // Helper: create a standard line series with endpoints
    const createLine = (
      data: { time: Time; value: number }[],
      color: string,
      lineWidth: 1 | 2 | 3 | 4 = 2,
      lineStyle: number = 0,
      title?: string,
    ) => {
      const line = chart.addSeries(LineSeries, {
        color,
        lineWidth,
        lineStyle,
        crosshairMarkerVisible: true,
        crosshairMarkerRadius: 6,
        crosshairMarkerBackgroundColor: '#FFFFFF',
        crosshairMarkerBorderColor: color,
        priceLineVisible: false,
        lastValueVisible: false,
        ...(title ? { title } : {}),
      });
      line.setData(data);
      drawingSeriesRef.current.push(line);
      return line;
    };

    for (const drawing of drawings) {
      if (drawing.points.length < 2) continue;
      const color = TOOL_COLORS[drawing.tool] || '#2962FF';
      const lineStyle = TOOL_LINE_STYLES[drawing.tool] ?? 0;
      const p1 = drawing.points[0];
      const p2 = drawing.points[1];
      const sorted = [p1, p2].sort((a, b) => a.time - b.time);

      switch (drawing.tool) {
        // ── TREND LINE ─────────────────────────────────────────
        case 'trendline':
        default: {
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 2, lineStyle,
          );
          break;
        }

        // ── RAY — extends from p1 through p2 to far right ─────
        case 'ray': {
          const slope = (p2.price - p1.price) / ((p2.time - p1.time) || 1);
          const farTime = sorted[1].time + intervalSec * 200;
          const farPrice = p2.price + slope * (farTime - p2.time);
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
              { time: farTime as Time, value: +farPrice.toFixed(2) },
            ],
            color, 2, 0,
          );
          break;
        }

        // ── INFO LINE — trend line + measurement label ────────
        case 'info-line': {
          const priceDiff = p2.price - p1.price;
          const pctChange = ((priceDiff / p1.price) * 100).toFixed(2);
          const timeDiffSec = Math.abs(p2.time - p1.time);
          const bars = Math.round(timeDiffSec / intervalSec);
          const hours = Math.floor(timeDiffSec / 3600);
          const mins = Math.floor((timeDiffSec % 3600) / 60);
          const duration = hours > 24
            ? `${Math.floor(hours / 24)}d ${hours % 24}h ${mins}m`
            : `${hours}h ${mins}m`;
          const angle = Math.atan2(priceDiff, bars || 1) * (180 / Math.PI);
          const title = `${priceDiff >= 0 ? '+' : ''}${priceDiff.toFixed(2)} (${pctChange}%) · ${bars} bars (${duration}) · ${angle.toFixed(1)}°`;

          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 2, 0, title,
          );
          break;
        }

        // ── EXTENDED LINE — extends in both directions ────────
        case 'extended-line': {
          const exSlope = (p2.price - p1.price) / ((p2.time - p1.time) || 1);
          const leftTime = sorted[0].time - intervalSec * 200;
          const rightTime = sorted[1].time + intervalSec * 200;
          const leftPrice = sorted[0].price + exSlope * (leftTime - sorted[0].time);
          const rightPrice = sorted[1].price + exSlope * (rightTime - sorted[1].time);
          createLine(
            [
              { time: leftTime as Time, value: +leftPrice.toFixed(2) },
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
              { time: rightTime as Time, value: +rightPrice.toFixed(2) },
            ],
            color, 2, 0,
          );
          break;
        }

        // ── TREND ANGLE — like trend line + angle display ─────
        case 'trend-angle': {
          const taBars = Math.round(Math.abs(p2.time - p1.time) / intervalSec);
          const taDiff = p2.price - p1.price;
          const taAngle = Math.atan2(taDiff, taBars || 1) * (180 / Math.PI);
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 2, 0, `∠ ${taAngle.toFixed(1)}°`,
          );
          break;
        }

        // ── HORIZONTAL LINE — full-width dashed line at price ──
        case 'horizontal-line': {
          if (mainSeries) {
            mainSeries.createPriceLine({
              price: p1.price,
              color,
              lineWidth: 1,
              lineStyle: 2,
              axisLabelVisible: true,
            });
          }
          break;
        }

        // ── HORIZONTAL RAY — from point extending right ───────
        case 'horizontal-ray': {
          const hrFarTime = sorted[0].time + intervalSec * 500;
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: hrFarTime as Time, value: sorted[0].price },
            ],
            color, 1, 2,
          );
          break;
        }

        // ── VERTICAL LINE — tall vertical at a time point ─────
        case 'vertical-line': {
          // Approximate vertical with extreme price range
          const vHigh = p1.price * 1.15;
          const vLow = p1.price * 0.85;
          createLine(
            [
              { time: sorted[0].time as Time, value: +vLow.toFixed(2) },
              { time: sorted[0].time as Time, value: +vHigh.toFixed(2) },
            ],
            color, 1, 2,
          );
          break;
        }

        // ── CROSS LINE — horizontal + vertical at a point ─────
        case 'cross-line': {
          // Horizontal
          const clLeftTime = sorted[0].time - intervalSec * 100;
          const clRightTime = sorted[0].time + intervalSec * 100;
          createLine(
            [
              { time: clLeftTime as Time, value: p1.price },
              { time: clRightTime as Time, value: p1.price },
            ],
            color, 1, 2,
          );
          // Vertical
          const clHigh = p1.price * 1.10;
          const clLow = p1.price * 0.90;
          createLine(
            [
              { time: sorted[0].time as Time, value: +clLow.toFixed(2) },
              { time: sorted[0].time as Time, value: +clHigh.toFixed(2) },
            ],
            color, 1, 2,
          );
          break;
        }

        // ── PARALLEL CHANNEL — two parallel lines ─────────────
        case 'parallel-channel':
        case 'flat-top-bottom': {
          // Main line
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 2, 0,
          );
          // Parallel offset line (mirror around midpoint)
          const pcMid = (sorted[0].price + sorted[1].price) / 2;
          const offset = Math.abs(sorted[1].price - sorted[0].price) * 0.5;
          const direction = sorted[1].price > sorted[0].price ? -1 : 1;
          createLine(
            [
              { time: sorted[0].time as Time, value: +(sorted[0].price + offset * direction).toFixed(2) },
              { time: sorted[1].time as Time, value: +(sorted[1].price + offset * direction).toFixed(2) },
            ],
            color, 1, 2,
          );
          break;
        }

        // ── REGRESSION TREND — line with upper/lower bounds ───
        case 'regression-trend': {
          // Main regression line
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 2, 0,
          );
          // Upper/lower deviation bands (±2% of price range)
          const rtRange = Math.abs(sorted[1].price - sorted[0].price) * 0.3;
          createLine(
            [
              { time: sorted[0].time as Time, value: +(sorted[0].price + rtRange).toFixed(2) },
              { time: sorted[1].time as Time, value: +(sorted[1].price + rtRange).toFixed(2) },
            ],
            color, 1, 2,
          );
          createLine(
            [
              { time: sorted[0].time as Time, value: +(sorted[0].price - rtRange).toFixed(2) },
              { time: sorted[1].time as Time, value: +(sorted[1].price - rtRange).toFixed(2) },
            ],
            color, 1, 2,
          );
          break;
        }

        // ── DISJOINT CHANNEL — non-parallel lines ─────────────
        case 'disjoint-channel': {
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 2, 0,
          );
          const dcOffset = Math.abs(sorted[1].price - sorted[0].price) * 0.4;
          createLine(
            [
              { time: sorted[0].time as Time, value: +(sorted[0].price - dcOffset * 0.5).toFixed(2) },
              { time: sorted[1].time as Time, value: +(sorted[1].price - dcOffset * 1.5).toFixed(2) },
            ],
            color, 1, 2,
          );
          break;
        }

        // ═══════════════════════════════════════════════════════
        // ── FIBONACCI TOOLS ───────────────────────────────────
        // ═══════════════════════════════════════════════════════

        // ── FIB RETRACEMENT — horizontal lines at fib levels ──
        case 'fib-retracement':
        case 'trend-fib': {
          const fibLevels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
          const fibRange = sorted[1].price - sorted[0].price;
          const fibAlpha = ['FF', 'CC', 'AA', '99', 'AA', 'CC', 'FF'];
          for (let i = 0; i < fibLevels.length; i++) {
            const level = fibLevels[i];
            const price = sorted[0].price + fibRange * level;
            const levelColor = color + fibAlpha[i];
            const line = createLine(
              [
                { time: sorted[0].time as Time, value: +price.toFixed(2) },
                { time: sorted[1].time as Time, value: +price.toFixed(2) },
              ],
              color, 1, 2,
              `${(level * 100).toFixed(1)}% — ${price.toFixed(2)}`,
            );
          }
          break;
        }

        // ── FIB EXTENSION — retracement + extension levels ────
        case 'fib-extension': {
          const extLevels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.272, 1.618, 2, 2.618];
          const extRange = sorted[1].price - sorted[0].price;
          for (const level of extLevels) {
            const price = sorted[0].price + extRange * level;
            createLine(
              [
                { time: sorted[0].time as Time, value: +price.toFixed(2) },
                { time: (sorted[1].time + intervalSec * 50) as Time, value: +price.toFixed(2) },
              ],
              color, level > 1 ? 1 : 1, level > 1 ? 0 : 2,
              `${(level * 100).toFixed(1)}%`,
            );
          }
          break;
        }

        // ── FIB CHANNEL — parallel lines at fib intervals ─────
        case 'fib-channel': {
          const chFibLevels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
          const chSlope = (sorted[1].price - sorted[0].price) / ((sorted[1].time - sorted[0].time) || 1);
          const chRange = Math.abs(sorted[1].price - sorted[0].price) * 0.5;
          for (const level of chFibLevels) {
            const offset = chRange * level;
            createLine(
              [
                { time: sorted[0].time as Time, value: +(sorted[0].price + offset).toFixed(2) },
                { time: sorted[1].time as Time, value: +(sorted[1].price + offset).toFixed(2) },
              ],
              color, level === 0 || level === 1 ? 2 : 1, level === 0 || level === 1 ? 0 : 2,
              level === 0 ? '' : `${(level * 100).toFixed(1)}%`,
            );
          }
          break;
        }

        // ── FIB TIME ZONE — vertical lines at fib time intervals
        case 'fib-time-zone':
        case 'fib-time-trend': {
          const fibSequence = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55];
          let cumBars = 0;
          const vHigh = Math.max(sorted[0].price, sorted[1].price) * 1.05;
          const vLow = Math.min(sorted[0].price, sorted[1].price) * 0.95;
          for (const n of fibSequence) {
            cumBars += n;
            const t = sorted[0].time + intervalSec * cumBars;
            if (t > sorted[1].time + intervalSec * 300) break;
            createLine(
              [
                { time: t as Time, value: +vLow.toFixed(2) },
                { time: t as Time, value: +vHigh.toFixed(2) },
              ],
              color, 1, 2,
              `${cumBars}`,
            );
          }
          break;
        }

        // ── FIB SPEED RESISTANCE FAN — lines from p1 to fib levels at p2 time
        case 'fib-speed-fan': {
          const fanLevels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
          const fanRange = sorted[1].price - sorted[0].price;
          for (const level of fanLevels) {
            const targetPrice = sorted[0].price + fanRange * level;
            const farTime = sorted[1].time + intervalSec * 100;
            const farSlope = (targetPrice - sorted[0].price) / ((sorted[1].time - sorted[0].time) || 1);
            const farPrice = targetPrice + farSlope * (farTime - sorted[1].time);
            createLine(
              [
                { time: sorted[0].time as Time, value: sorted[0].price },
                { time: sorted[1].time as Time, value: +targetPrice.toFixed(2) },
                { time: farTime as Time, value: +farPrice.toFixed(2) },
              ],
              color, level === 0.5 ? 2 : 1, level === 0.5 ? 0 : 2,
              `${(level * 100).toFixed(1)}%`,
            );
          }
          break;
        }

        // ── FIB CIRCLES — concentric horizontal bands at fib radii
        case 'fib-circles': {
          const circLevels = [0.236, 0.382, 0.5, 0.618, 0.786, 1];
          const circRange = Math.abs(sorted[1].price - sorted[0].price);
          const midPrice = (sorted[0].price + sorted[1].price) / 2;
          const midTime = Math.round((sorted[0].time + sorted[1].time) / 2);
          for (const level of circLevels) {
            const radius = circRange * level;
            const tSpread = Math.round((sorted[1].time - sorted[0].time) * level / 2);
            // Top arc approximation
            createLine(
              [
                { time: (midTime - tSpread) as Time, value: +midPrice.toFixed(2) },
                { time: midTime as Time, value: +(midPrice + radius / 2).toFixed(2) },
                { time: (midTime + tSpread) as Time, value: +midPrice.toFixed(2) },
              ],
              color, 1, 2,
              `${(level * 100).toFixed(1)}%`,
            );
            // Bottom arc approximation
            createLine(
              [
                { time: (midTime - tSpread) as Time, value: +midPrice.toFixed(2) },
                { time: midTime as Time, value: +(midPrice - radius / 2).toFixed(2) },
                { time: (midTime + tSpread) as Time, value: +midPrice.toFixed(2) },
              ],
              color, 1, 2,
            );
          }
          break;
        }

        // ── FIB SPIRAL — expanding fan at golden ratio angles ─
        case 'fib-spiral': {
          const spiralLevels = [1, 1.618, 2.618, 4.236, 6.854];
          const spRange = Math.abs(sorted[1].price - sorted[0].price);
          const spDir = sorted[1].price > sorted[0].price ? 1 : -1;
          for (const mult of spiralLevels) {
            const targetPrice = sorted[0].price + spRange * mult * spDir;
            const targetTime = sorted[0].time + (sorted[1].time - sorted[0].time) * mult;
            createLine(
              [
                { time: sorted[0].time as Time, value: sorted[0].price },
                { time: targetTime as Time, value: +targetPrice.toFixed(2) },
              ],
              color, 1, 2,
              `${mult.toFixed(3)}`,
            );
          }
          break;
        }

        // ── FIB SPEED RESISTANCE ARCS — curved lines at fib levels
        case 'fib-arcs': {
          const arcLevels = [0.236, 0.382, 0.5, 0.618, 0.786];
          const arcRange = Math.abs(sorted[1].price - sorted[0].price);
          const arcTimeDiff = sorted[1].time - sorted[0].time;
          for (const level of arcLevels) {
            const radius = arcRange * level;
            const tR = Math.round(arcTimeDiff * level);
            const pts = [];
            for (let i = 0; i <= 8; i++) {
              const frac = i / 8;
              const t = sorted[1].time - tR + Math.round(tR * 2 * frac);
              const pOffset = radius * Math.sqrt(1 - Math.pow(frac * 2 - 1, 2));
              pts.push({ time: t as Time, value: +(sorted[1].price + pOffset).toFixed(2) });
            }
            createLine(pts, color, 1, 2, `${(level * 100).toFixed(1)}%`);
          }
          break;
        }

        // ── FIB WEDGE — converging lines from endpoints ───────
        case 'fib-wedge': {
          const wLevels = [0.236, 0.382, 0.5, 0.618, 0.786];
          const wRange = sorted[1].price - sorted[0].price;
          const convergenceTime = sorted[1].time + (sorted[1].time - sorted[0].time);
          const convergencePrice = (sorted[0].price + sorted[1].price) / 2;
          for (const level of wLevels) {
            const startPrice = sorted[0].price + wRange * level;
            createLine(
              [
                { time: sorted[0].time as Time, value: +startPrice.toFixed(2) },
                { time: convergenceTime as Time, value: +convergencePrice.toFixed(2) },
              ],
              color, 1, 2,
              `${(level * 100).toFixed(1)}%`,
            );
          }
          break;
        }

        // ── PITCHFAN — radiating lines from origin ────────────
        case 'pitchfan': {
          const pfLevels = [0.25, 0.382, 0.5, 0.618, 0.75, 1];
          const pfRange = sorted[1].price - sorted[0].price;
          const pfTimeDiff = sorted[1].time - sorted[0].time;
          for (const level of pfLevels) {
            const targetPrice = sorted[0].price + pfRange * level;
            const farTime = sorted[1].time + pfTimeDiff;
            const slope = (targetPrice - sorted[0].price) / (pfTimeDiff || 1);
            const farPrice = targetPrice + slope * pfTimeDiff;
            createLine(
              [
                { time: sorted[0].time as Time, value: sorted[0].price },
                { time: sorted[1].time as Time, value: +targetPrice.toFixed(2) },
                { time: farTime as Time, value: +farPrice.toFixed(2) },
              ],
              color, level === 0.5 ? 2 : 1, level === 0.5 ? 0 : 2,
            );
          }
          break;
        }

        // ═══════════════════════════════════════════════════════
        // ── GANN TOOLS ────────────────────────────────────────
        // ═══════════════════════════════════════════════════════

        // ── GANN BOX — grid of horizontal + vertical lines ────
        case 'gann-box':
        case 'gann-square-fixed':
        case 'gann-square': {
          const gLevels = [0, 0.25, 0.5, 0.75, 1];
          const gPriceRange = sorted[1].price - sorted[0].price;
          const gTimeDiff = sorted[1].time - sorted[0].time;
          // Horizontal grid lines
          for (const level of gLevels) {
            const price = sorted[0].price + gPriceRange * level;
            createLine(
              [
                { time: sorted[0].time as Time, value: +price.toFixed(2) },
                { time: sorted[1].time as Time, value: +price.toFixed(2) },
              ],
              color, level === 0 || level === 1 ? 2 : 1,
              level === 0 || level === 1 ? 0 : 2,
              `${(level * 100).toFixed(0)}%`,
            );
          }
          // Vertical grid lines
          const vPriceHigh = Math.max(sorted[0].price, sorted[1].price);
          const vPriceLow = Math.min(sorted[0].price, sorted[1].price);
          for (const level of gLevels) {
            if (level === 0 || level === 1) continue;
            const t = sorted[0].time + Math.round(gTimeDiff * level);
            createLine(
              [
                { time: t as Time, value: +vPriceLow.toFixed(2) },
                { time: t as Time, value: +vPriceHigh.toFixed(2) },
              ],
              color, 1, 2,
            );
          }
          // Diagonal
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 1, 2,
          );
          break;
        }

        // ── GANN FAN — lines at classic Gann angles ───────────
        case 'gann-fan': {
          // Gann angles: 1x8, 1x4, 1x3, 1x2, 1x1, 2x1, 3x1, 4x1, 8x1
          const gannMultipliers = [0.125, 0.25, 0.333, 0.5, 1, 2, 3, 4, 8];
          const gannLabels = ['1×8', '1×4', '1×3', '1×2', '1×1', '2×1', '3×1', '4×1', '8×1'];
          const gfTimeDiff = sorted[1].time - sorted[0].time;
          const gfPricePerBar = (sorted[1].price - sorted[0].price) / (gfTimeDiff / intervalSec || 1);
          for (let i = 0; i < gannMultipliers.length; i++) {
            const mult = gannMultipliers[i];
            const farTime = sorted[0].time + gfTimeDiff * 2;
            const barsToFar = (farTime - sorted[0].time) / intervalSec;
            const farPrice = sorted[0].price + gfPricePerBar * mult * barsToFar;
            createLine(
              [
                { time: sorted[0].time as Time, value: sorted[0].price },
                { time: farTime as Time, value: +farPrice.toFixed(2) },
              ],
              color, mult === 1 ? 2 : 1, mult === 1 ? 0 : 2,
              gannLabels[i],
            );
          }
          break;
        }
      }
    }
  }, [drawings, drawingsVisible, chartData]);

  // ── Fibonacci Zone Overlay — ref-based DOM rendering (no setState) ────
  const fibOverlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const chart = chartRef.current;
    const series = candleSeriesRef.current;
    const overlay = fibOverlayRef.current;
    if (!chart || !series || !overlay) return;

    const FIB_TOOLS = new Set(['fib-retracement', 'trend-fib', 'fib-extension']);
    const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
    const ZONE_COLORS = [
      'rgba(233, 30, 99, 0.10)',
      'rgba(234, 57, 67, 0.12)',
      'rgba(255, 152, 0, 0.12)',
      'rgba(76, 175, 80, 0.14)',
      'rgba(0, 150, 136, 0.12)',
      'rgba(33, 150, 243, 0.10)',
    ];

    const paintZones = () => {
      // Read drawings imperatively (no reactive subscription)
      const { drawings: currentDrawings, drawingsVisible: visible } = useChartUIStore.getState();
      overlay.innerHTML = '';

      if (!visible) return;

      for (const drawing of currentDrawings) {
        if (!FIB_TOOLS.has(drawing.tool) || drawing.points.length < 2) continue;

        const sorted = [...drawing.points].sort((a, b) => a.time - b.time);
        const priceRange = sorted[1].price - sorted[0].price;

        const x1 = chart.timeScale().timeToCoordinate(sorted[0].time as Time);
        const x2 = chart.timeScale().timeToCoordinate(sorted[1].time as Time);
        if (x1 === null || x2 === null) continue;

        const left = Math.min(x1, x2);
        const width = Math.abs(x2 - x1);
        if (width < 2) continue;

        for (let i = 0; i < FIB_LEVELS.length - 1; i++) {
          const priceTop = sorted[0].price + priceRange * FIB_LEVELS[i + 1];
          const priceBot = sorted[0].price + priceRange * FIB_LEVELS[i];

          const yTop = series.priceToCoordinate(priceTop);
          const yBot = series.priceToCoordinate(priceBot);
          if (yTop === null || yBot === null) continue;

          const top = Math.min(yTop, yBot);
          const height = Math.abs(yBot - yTop);
          if (height < 1) continue;

          const band = document.createElement('div');
          band.style.cssText = `position:absolute;top:${top}px;left:${left}px;width:${width}px;height:${height}px;background:${ZONE_COLORS[i]};border-top:1px solid rgba(255,255,255,0.08);pointer-events:none;`;

          const label = document.createElement('span');
          label.style.cssText = 'position:absolute;right:4px;top:1px;font-size:9px;color:rgba(255,255,255,0.45);font-family:monospace;white-space:nowrap;';
          label.textContent = `${(FIB_LEVELS[i] * 100).toFixed(1)}% — ${(FIB_LEVELS[i + 1] * 100).toFixed(1)}%`;
          band.appendChild(label);
          overlay.appendChild(band);
        }
      }
    };

    paintZones();

    // Repaint on scroll/zoom (no state changes)
    chart.timeScale().subscribeVisibleTimeRangeChange(paintZones);

    // Also repaint when drawings change via Zustand subscribe (no React re-render)
    const unsubStore = useChartUIStore.subscribe(paintZones);

    return () => {
      chart.timeScale().unsubscribeVisibleTimeRangeChange(paintZones);
      unsubStore();
      if (overlay) overlay.innerHTML = '';
    };
  }, [chartData]); // Only re-setup when chart data changes (chart/series recreated)

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

  // handleChartInteraction is no longer needed — useDrawingEngine
  // subscribes directly to chart.subscribeClick for drawing logic.

  const ohlcLabel = latestCandle
    ? `O ${latestCandle.open.toFixed(2)}  H ${latestCandle.high.toFixed(2)}  L ${latestCandle.low.toFixed(2)}  C ${latestCandle.close.toFixed(2)}`
    : '';

  return (
    <div
      className={`relative flex h-full w-full flex-col outline-none ${cursorClass}`}
      /* Drawing interactions handled by useDrawingEngine hook */
    >
      {/* ── Chart Canvas ─────────────────────────────────────────── */}
      <div ref={chartContainerRef} className="flex-1 min-h-0 w-full" />

      {/* ── Fibonacci Colored Zone Overlay (ref-based, no re-renders) ─ */}
      <div ref={fibOverlayRef} className="pointer-events-none absolute inset-0" />

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