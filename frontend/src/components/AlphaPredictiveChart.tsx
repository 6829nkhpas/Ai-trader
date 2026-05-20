'use client';

import React, { useMemo, useEffect, useRef, useCallback } from 'react';
import { useTradeStore, type OhlcCandle } from '../store/useTradeStore';
import { useChartUIStore } from '../store/useChartUIStore';
import { useHistoricalData } from '../hooks/useHistoricalData';

import type { Timeframe, AlphaPredictiveChartProps } from '../utils/chartTypes';
import { RANGE_DAYS, KITE_INTERVAL_MAP } from '../utils/chartTypes';
export type { Timeframe };
import { aggregateCandles } from '../utils/chartAggregation';

import { useChartInit } from '../hooks/useChartInit';
import { useChartDataSync } from '../hooks/useChartDataSync';
import { useDrawingEngine } from '../hooks/useDrawingEngine';
import { useDrawingInteraction } from '../hooks/useDrawingInteraction';
import { DrawingOverlays } from './chart/DrawingOverlays';
import { useDrawingRenderer } from '../hooks/useDrawingRenderer';
import { useFibZoneOverlay } from '../hooks/useFibZoneOverlay';
import { useTauriLiveData } from '../hooks/useTauriLiveData';

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
  const activeRange = useTradeStore((s) => s.activeRange);
  const predictiveSignals = useTradeStore((s) => s.predictiveSignals);
  // selectedSymbol is the watchlist-driven selection; it takes priority over
  // the AI decision symbol so clicking a stock immediately switches the chart.
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);

  const { activeCursor, activeDrawingTool, drawings } = useChartUIStore();

  const activeSymbol = useMemo(() => {
    // 1. Explicit watchlist selection (highest priority)
    if (selectedSymbol) return selectedSymbol.toUpperCase();
    // 2. Fall back to the latest AI decision symbol
    const d = activeDecision ?? liveDecisions[liveDecisions.length - 1];
    return d?.symbol ?? 'RELIANCE';
  }, [selectedSymbol, activeDecision, liveDecisions]);

  // ── Dynamic Live Data Binding ─────────────────────────────────────────
  // Subscribes Tauri IPC event listeners to the current activeSymbol.
  // When the symbol changes, old live buffers are flushed and listeners
  // re-bound so the chart immediately switches to the new instrument.
  useTauriLiveData(activeSymbol);

  // ── Clear live buffer on symbol change (WebSocket path) ───────────────
  const previousSymbolRef = useRef<string>(activeSymbol);
  useEffect(() => {
    if (previousSymbolRef.current !== activeSymbol) {
      useTradeStore.getState().clearLiveBuffer();
      previousSymbolRef.current = activeSymbol;
    }
  }, [activeSymbol]);

  // BUG-7: Removed redundant clearLiveBuffer effect on timeframe change.
  // setActiveTimeframe() atomically sets ohlcCandles:[] in the store, so
  // a separate effect here caused a double-clear extra render cycle.

  // ── Historical Data ──────────────────────────────────────────────────
  const effectiveTimeframe = (activeTimeframe as Timeframe) ?? timeframe;
  const rangeDays = RANGE_DAYS[activeRange] ?? 365;
  const kiteInterval = KITE_INTERVAL_MAP[effectiveTimeframe] ?? '10minute';
  // BUG-1: effectiveTimeframe passed to hook so switching 1m↔2m (same kiteInterval)
  // still fires fetchData, hits cache, returns new array ref → aggregateCandles reruns.
  const { candles: historicalCandles, loading: histLoading } = useHistoricalData(
    activeSymbol, rangeDays, kiteInterval, effectiveTimeframe
  );

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

    const liveForSymbol = ohlcCandles.filter(
      (c) => c.symbol.toUpperCase() === activeSymbol.toUpperCase()
    );

    const candleMap = new Map<number, OhlcCandle>();
    for (const c of histAsOhlc) candleMap.set(c.start_timestamp_ms, c);
    for (const c of liveForSymbol) candleMap.set(c.start_timestamp_ms, c);

    const merged = Array.from(candleMap.values());

    // Debug: trace data sources to diagnose chart distortion
    if (merged.length > 0) {
      const range = (arr: OhlcCandle[]) => {
        if (arr.length === 0) return 'empty';
        const prices = arr.map((c) => c.close);
        return `${Math.min(...prices).toFixed(1)}-${Math.max(...prices).toFixed(1)}`;
      };
      console.log(
        `[Chart] ${activeSymbol} | hist=${histAsOhlc.length} [${range(histAsOhlc)}]` +
        ` | live=${liveForSymbol.length} [${range(liveForSymbol)}]` +
        ` | merged=${merged.length} | tf=${effectiveTimeframe}`
      );
    } else if (histAsOhlc.length === 0) {
      console.warn(`[Chart] ${activeSymbol}: No historical or live data available (interval=${kiteInterval})`);
    }

    return merged;
  // BUG-2: Removed phantom deps effectiveTimeframe and kiteInterval.
  // They were not used inside the memo body — just triggered wasteful
  // extra re-computations. The aggregateCandles memo below correctly
  // depends on effectiveTimeframe for bucket sizing.
  }, [historicalCandles, ohlcCandles, activeSymbol]);

  // ── Aggregation ──────────────────────────────────────────────────────
  const { candles: chartData, volumes: volumeData, ema9: ema9Data, ema21: ema21Data, isIndexVolume } = useMemo(
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

  // ── Workspace Persistence: Auto-Load on Symbol Change ─────────────
  useEffect(() => {
    if (!activeSymbol) return;
    useChartUIStore.getState().loadWorkspaceFromDB(activeSymbol);
  }, [activeSymbol]);

  // ── Workspace Persistence: Debounced Auto-Save on Drawing Change ──
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const drawingsRef = useRef(drawings);
  drawingsRef.current = drawings;

  const debouncedSave = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      if (activeSymbol) {
        useChartUIStore.getState().saveWorkspaceToDB(activeSymbol);
      }
    }, 1000); // 1-second debounce
  }, [activeSymbol]);

  // Track drawing mutations — skip the initial load hydration
  const isFirstRender = useRef(true);
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    debouncedSave();
  }, [drawings, debouncedSave]);

  // Flush on window close to catch any unsaved state
  useEffect(() => {
    const handleBeforeUnload = () => {
      if (activeSymbol && drawingsRef.current.length > 0) {
        useChartUIStore.getState().saveWorkspaceToDB(activeSymbol);
      }
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [activeSymbol]);

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

      {/* ── Index Volume Proxy Label ──────────────────────────────── */}
      {isIndexVolume && chartData.length > 0 && (
        <div className="pointer-events-none absolute left-3 bottom-1 text-[9px] font-mono select-none"
             style={{ color: 'rgba(255,255,255,0.25)' }}>
          Vol: Price Range (Index)
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

      {/* ── HTML Drawing Overlays ──────────────────────────────── */}
      <DrawingOverlays chartRef={refs.chartRef} candleSeriesRef={refs.candleSeriesRef} />
    </div>
  );
}