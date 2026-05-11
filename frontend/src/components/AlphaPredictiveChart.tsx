'use client';

import React, { useMemo } from 'react';
import { useTradeStore, type OhlcCandle } from '../store/useTradeStore';
import { useChartUIStore } from '../store/useChartUIStore';
import { useHistoricalData } from '../hooks/useHistoricalData';

import type { Timeframe, AlphaPredictiveChartProps } from '../utils/chartTypes';
export type { Timeframe };
import { aggregateCandles } from '../utils/chartAggregation';

import { useChartInit } from '../hooks/useChartInit';
import { useChartDataSync } from '../hooks/useChartDataSync';
import { useDrawingEngine } from '../hooks/useDrawingEngine';
import { useDrawingInteraction } from '../hooks/useDrawingInteraction';
import { useDrawingRenderer } from '../hooks/useDrawingRenderer';
import { useFibZoneOverlay } from '../hooks/useFibZoneOverlay';

export default function AlphaPredictiveChart({
  activeProfile = 'INTRADAY',
  timeframe = '1m',
  isExpanded = false,
  onToggleExpand,
}: AlphaPredictiveChartProps) {
  // ── Store Subscriptions ─────────────────────────────────────────────
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

  // ── Historical Data ──────────────────────────────────────────────────
  const { candles: historicalCandles, loading: histLoading } = useHistoricalData(activeSymbol);

  // ── Merge Historical + Live ──────────────────────────────────────────
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

  // ── Aggregation ──────────────────────────────────────────────────────
  const effectiveTimeframe = (activeTimeframe as Timeframe) ?? timeframe;
  const { candles: chartData, volumes: volumeData, ema9: ema9Data, ema21: ema21Data } = useMemo(
    () => aggregateCandles(mergedCandles, effectiveTimeframe, activeSymbol),
    [mergedCandles, effectiveTimeframe, activeSymbol]
  );

  const latestCandle = useMemo(() => {
    if (!activeSymbol || mergedCandles.length === 0) return null;
    const sym = mergedCandles.filter((c) => c.symbol.toUpperCase() === activeSymbol.toUpperCase());
    return sym.length > 0 ? sym[sym.length - 1] : null;
  }, [mergedCandles, activeSymbol]);

  // ── Chart Initialization & DOM Container ─────────────────────────────
  const containerRef = React.useRef<HTMLDivElement>(null);
  const refs = useChartInit(containerRef);

  // ── Sub-systems / Hooks ──────────────────────────────────────────────
  useChartDataSync(refs, chartData, volumeData, ema9Data, ema21Data, effectiveTimeframe, activeSymbol, predictiveSignals, isExpanded);
  useDrawingEngine(refs.chartRef, refs.candleSeriesRef, containerRef);
  useDrawingInteraction(refs.chartRef, refs.candleSeriesRef, containerRef);
  useDrawingRenderer(refs, chartData);
  useFibZoneOverlay(refs, chartData);

  // ── Render Helpers ───────────────────────────────────────────────────
  const cursorClass = useMemo(() => {
    if (activeDrawingTool) return 'cursor-crosshair';
    switch (activeCursor) {
      case 'cross': return 'cursor-crosshair';
      case 'eraser': return 'cursor-not-allowed';
      default: return 'cursor-default';
    }
  }, [activeCursor, activeDrawingTool]);

  const ohlcLabel = latestCandle
    ? `O ${latestCandle.open.toFixed(2)}  H ${latestCandle.high.toFixed(2)}  L ${latestCandle.low.toFixed(2)}  C ${latestCandle.close.toFixed(2)}`
    : '';

  return (
    <div className={`relative flex h-full w-full flex-col outline-none ${cursorClass}`}>
      {/* ── Chart Canvas ─────────────────────────────────────────── */}
      <div ref={containerRef} className="flex-1 min-h-0 w-full" />

      {/* ── Fibonacci Colored Zone Overlay (ref-based, no re-renders) ─ */}
      <div ref={refs.fibOverlayRef} className="pointer-events-none absolute inset-0" />

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