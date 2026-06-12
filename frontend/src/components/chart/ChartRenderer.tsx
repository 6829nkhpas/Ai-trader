'use client';

// Feature: professional-charting-suite
//
// ChartRenderer — the generalized price-pane renderer for the Professional
// Charting Suite. It is `AlphaPredictiveChart` generalized to consume the pure
// charting engines instead of hard-coding a candlestick presentation:
//
//   - the price pane is driven by `ChartTypeEngine` output via
//     `useChartTypeRenderer` (Requirement 1.1; the substrate overlay indicators
//     of 2.2/2.4 align to);
//   - active overlay indicators are drawn on the price pane and oscillator
//     indicators in dedicated panes (with reference levels) via
//     `useIndicatorRenderer` + `PaneManager` (Requirements 2.2, 2.4, 2.8, 3.5);
//   - applied-strategy entry/exit signals are drawn as markers via
//     `useStrategyMarkers` (Requirement 8.4).
//
// The base chart scaffolding (chart instance, candlestick/volume/EMA/ghost
// series, drawing overlays, workspace persistence) is reused unchanged from the
// existing hooks so behavior parity with `AlphaPredictiveChart` is preserved.
// `AlphaPredictiveChart` itself is left intact for existing callers.

import React, { useMemo, useEffect, useRef, useCallback } from 'react';
import { useTradeStore, type OhlcCandle } from '../../store/useTradeStore';
import { useChartUIStore, type ActiveIndicator } from '../../store/useChartUIStore';
import { useHistoricalData } from '../../hooks/useHistoricalData';

import type { Timeframe, AlphaPredictiveChartProps } from '../../utils/chartTypes';
import { RANGE_DAYS, KITE_INTERVAL_MAP } from '../../utils/chartTypes';
import { aggregateCandles } from '../../utils/chartAggregation';

import { useChartInit } from '../../hooks/useChartInit';
import { useZoomClamp } from '../../hooks/useZoomClamp';
import { useChartDataSync } from '../../hooks/useChartDataSync';
import { useChartTypeRenderer } from '../../hooks/useChartTypeRenderer';
import { useIndicatorRenderer } from '../../hooks/useIndicatorRenderer';
import { useStrategyMarkers } from '../../hooks/useStrategyMarkers';
import { useCrosshairController } from '../../hooks/useCrosshairController';
import { NO_VALUE, DEFAULT_PRICE_PRECISION } from '../../charting/crosshair';
import { useDrawingEngine } from '../../hooks/useDrawingEngine';
import { useDrawingInteraction } from '../../hooks/useDrawingInteraction';
import { DrawingOverlays } from './DrawingOverlays';
import DrawingContextToolbar from './DrawingContextToolbar';
import DrawingLayersPanel from './DrawingLayersPanel';
import { useDrawingRenderer } from '../../hooks/useDrawingRenderer';
import { useRadarOverlay } from '../../hooks/useRadarOverlay';
import { useTauriLiveData } from '../../hooks/useTauriLiveData';
import { useConnectionStatus } from '../../hooks/useConnectionStatus';
import VolumeProfileOverlay from './VolumeProfileOverlay';

import type { ChartType, ChartTypeParams } from '../../charting/engines';
import type { StrategyParams } from '../../charting/engines';

/** Stable empty list so the store selector keeps a constant identity. */
const EMPTY_INDICATORS: ActiveIndicator[] = [];

/**
 * Props for {@link ChartRenderer}. A superset of {@link AlphaPredictiveChartProps}
 * that adds the chart-type, indicator, and strategy inputs the generalized
 * renderer consumes. All additions are optional so the component renders a
 * plain candlestick chart (parity with `AlphaPredictiveChart`) by default.
 */
export interface ChartRendererProps extends AlphaPredictiveChartProps {
  /** Show the volume-profile canvas overlay. */
  showVolumeProfile?: boolean;
  /** The selected chart type; defaults to candlestick. */
  chartType?: ChartType;
  /** Parameters for the parametric chart types (Renko box size, etc.). */
  chartTypeParams?: ChartTypeParams;
  /** The applied strategy id, or null/undefined when none is applied. */
  activeStrategyId?: string | null;
  /** Optional per-strategy parameter overrides. */
  strategyParams?: StrategyParams;
}

export default function ChartRenderer({
  timeframe = '1m',
  isExpanded = false,
  showVolumeProfile = false,
  chartType = 'candlestick',
  chartTypeParams,
  activeStrategyId = null,
  strategyParams,
}: ChartRendererProps) {
  // ── Store Subscriptions ─────────────────────────────────────────────
  const ohlcCandles = useTradeStore((s) => s.ohlcCandles);
  const activeDecision = useTradeStore((s) => s.activeDecision);
  const liveDecisions = useTradeStore((s) => s.liveDecisions);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);
  const activeRange = useTradeStore((s) => s.activeRange);
  const predictiveSignals = useTradeStore((s) => s.predictiveSignals);
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);

  const { activeCursor, activeDrawingTool, drawings } = useChartUIStore();
  const showLayersPanel = useChartUIStore((s) => s.showLayersPanel);

  const activeSymbol = useMemo(() => {
    if (selectedSymbol) return selectedSymbol.toUpperCase();
    const d = activeDecision ?? liveDecisions[liveDecisions.length - 1];
    return d?.symbol ?? 'RELIANCE';
  }, [selectedSymbol, activeDecision, liveDecisions]);

  // Active indicators for this symbol (stable empty fallback for unknowns).
  const activeIndicators = useChartUIStore(
    (s) => s.activeIndicators[activeSymbol] ?? EMPTY_INDICATORS,
  );

  // ── Dynamic Live Data Binding ─────────────────────────────────────────
  useTauriLiveData(activeSymbol);

  // Realtime-feed connection state for the disconnected indicator (9.7, 9.8).
  const { isDisconnected } = useConnectionStatus();

  const previousSymbolRef = useRef<string>(activeSymbol);
  useEffect(() => {
    if (previousSymbolRef.current !== activeSymbol) {
      useTradeStore.getState().clearLiveBuffer();
      previousSymbolRef.current = activeSymbol;
    }
  }, [activeSymbol]);

  // ── Historical Data ──────────────────────────────────────────────────
  const effectiveTimeframe = (activeTimeframe as Timeframe) ?? timeframe;
  const rangeDays = RANGE_DAYS[activeRange] ?? 365;
  const kiteInterval = KITE_INTERVAL_MAP[effectiveTimeframe] ?? '10minute';
  const { candles: historicalCandles, loading: histLoading } = useHistoricalData(
    activeSymbol, rangeDays, kiteInterval, effectiveTimeframe,
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
      (c) => c.symbol.toUpperCase() === activeSymbol.toUpperCase(),
    );

    const candleMap = new Map<number, OhlcCandle>();
    for (const c of histAsOhlc) candleMap.set(c.start_timestamp_ms, c);
    for (const c of liveForSymbol) candleMap.set(c.start_timestamp_ms, c);

    return Array.from(candleMap.values());
  }, [historicalCandles, ohlcCandles, activeSymbol]);

  // ── Aggregation ──────────────────────────────────────────────────────
  const { candles: chartData, volumes: volumeData, ema9: ema9Data, ema21: ema21Data, isIndexVolume } = useMemo(
    () => aggregateCandles(mergedCandles, effectiveTimeframe, activeSymbol),
    [mergedCandles, effectiveTimeframe, activeSymbol],
  );

  const latestCandle = useMemo(() => {
    if (!activeSymbol || mergedCandles.length === 0) return null;
    const sym = mergedCandles.filter((c) => c.symbol.toUpperCase() === activeSymbol.toUpperCase());
    return sym.length > 0 ? sym[sym.length - 1] : null;
  }, [mergedCandles, activeSymbol]);

  // ── Chart Initialization & DOM Container ─────────────────────────────
  const containerRef = React.useRef<HTMLDivElement>(null);
  const refs = useChartInit(containerRef);

  // ── Wheel-zoom clamp (Requirement 10.6) ──────────────────────────────
  // Constrains the visible candle count to [5, 5000] on every zoom/pan,
  // preserving the cursor-centered midpoint. The price pane is rendered by
  // `lightweight-charts`, which scales its canvas backing store by
  // `window.devicePixelRatio` natively (Requirement 12.6, ratios 1.0–4.0); the
  // footprint and volume-profile canvas overlays apply their own DPR-aware
  // backing stores. The price-range-proxy label for zero-volume index
  // instruments is rendered below (gated on `isIndexVolume`, Requirement 12.7).
  useZoomClamp(refs.chartRef);

  // ── Base price pipeline (candlestick + volume + EMA + ghost) ──────────
  useChartDataSync(refs, chartData, volumeData, ema9Data, ema21Data, effectiveTimeframe, activeSymbol, predictiveSignals, isExpanded);

  // ── Engine-driven rendering ───────────────────────────────────────────
  // Price pane consumes ChartTypeEngine output; overlay/oscillator indicators
  // consume IndicatorEngine via the PaneManager; strategy markers consume
  // StrategyEngine output.
  useChartTypeRenderer(refs, chartData, chartType, chartTypeParams);
  useIndicatorRenderer(refs, chartData, activeIndicators);
  useStrategyMarkers(refs, chartData, activeStrategyId, strategyParams);

  // ── Drawing sub-systems ───────────────────────────────────────────────
  useDrawingEngine(refs.chartRef, refs.candleSeriesRef, containerRef, chartData);
  useDrawingInteraction(refs.chartRef, refs.candleSeriesRef, containerRef, chartData);
  useDrawingRenderer(refs, chartData);

  // ── Quant Radar on-chart visualization ────────────────────────────────
  // Draws the user-selected radar detection (pattern highlight box or strategy
  // marker/level line) when a scan result is clicked. Subscribes to the radar
  // store imperatively so toggling/clicking a detection redraws instantly.
  useRadarOverlay(refs, chartData);

  // ── Synchronized Crosshair Readouts (Requirements 10.1–10.4, 10.8) ────
  // The CrosshairController reads the hovered candle's OHLC plus every active
  // indicator's value at that time, formats them to the instrument's configured
  // precision, and yields a no-value placeholder for warm-up / out-of-range
  // positions. The vertical crosshair is synchronized across panes by the
  // shared v5 time scale (CrosshairMode.Normal configured in useChartInit).
  const { readout } = useCrosshairController(
    refs,
    chartData,
    activeIndicators,
    DEFAULT_PRICE_PRECISION,
  );

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
    }, 1000);
  }, [activeSymbol]);

  const isFirstRender = useRef(true);
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    debouncedSave();
  }, [drawings, debouncedSave]);

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

  const activeOhlcCandle = latestCandle;
  // Structured OHLC parts (hover-aware) so each value can be color-coded in the
  // on-chart legend. Prefers the synchronized crosshair readout (already
  // formatted to instrument precision); falls back to the latest candle when
  // the crosshair is off the chart.
  const ohlcParts = readout.hasCandle
    ? {
        o: readout.ohlc.open,
        h: readout.ohlc.high,
        l: readout.ohlc.low,
        c: readout.ohlc.close,
      }
    : activeOhlcCandle
      ? {
          o: activeOhlcCandle.open.toFixed(DEFAULT_PRICE_PRECISION),
          h: activeOhlcCandle.high.toFixed(DEFAULT_PRICE_PRECISION),
          l: activeOhlcCandle.low.toFixed(DEFAULT_PRICE_PRECISION),
          c: activeOhlcCandle.close.toFixed(DEFAULT_PRICE_PRECISION),
        }
      : null;

  // Indicator readouts for the hovered time (placeholders during warm-up).
  const indicatorLabel = readout.hasCandle
    ? readout.indicators
        .map((ind) => {
          const vals = ind.lines.map((l) => l.value).join(' / ');
          return `${ind.label}: ${vals || NO_VALUE}`;
        })
        .join('   ')
    : '';

  return (
    <div className={`relative flex h-full w-full flex-col outline-none ${cursorClass}`}>
      {/* ── Chart Canvas ─────────────────────────────────────────── */}
      <div ref={containerRef} className="flex-1 min-h-0 w-full overflow-hidden" />

      {/* ── Fibonacci Colored Zone Overlay ─────────────────────────── */}
      <div ref={refs.fibOverlayRef} className="pointer-events-none absolute inset-0" />

      {/* ── Disconnected Realtime-Feed Indicator (Req 9.7, 9.8) ─────────
          Persistent while the feed link is down. The last received dataset
          stays rendered unchanged; only this badge is added. It is removed
          automatically when the feed reconnects. */}
      {isDisconnected && (
        <div
          role="status"
          aria-live="polite"
          className="pointer-events-none absolute right-3 top-2 z-10 flex items-center gap-1.5 rounded-full border border-status-error/40 bg-status-error/15 px-2.5 py-1 text-[10px] font-mono font-semibold uppercase tracking-wider text-status-error select-none"
        >
          <span className="h-2 w-2 animate-pulse rounded-full bg-status-error" />
          Live feed disconnected
        </div>
      )}

      {/* ── Volume Profile Canvas Overlay ─────────────────────────── */}
      {showVolumeProfile && (
        <VolumeProfileOverlay
          chartRef={refs.chartRef}
          candleSeriesRef={refs.candleSeriesRef}
          chartData={chartData}
          volumeData={volumeData}
        />
      )}

      {/* ── On-chart legend: symbol · timeframe + OHLC (hover-aware) ─────
          The OHLC tracks the hovered candle and falls back to the latest
          candle when the crosshair is off the chart. Lives on the graph so the
          header bar stays uncluttered. */}
      <div className="pointer-events-none absolute left-3 top-2 z-10 flex flex-col gap-0.5 select-none">
        <div className="flex items-center gap-2 font-mono text-[11px]">
          <span className="font-semibold text-text-secondary">{activeSymbol}</span>
          <span className="text-text-muted/70">· {effectiveTimeframe}</span>
        </div>
        {ohlcParts && (
          <div className="flex items-center gap-2 font-mono text-[10px]">
            <span className="text-text-muted/60">O <span className="text-sky-300">{ohlcParts.o}</span></span>
            <span className="text-text-muted/60">H <span className="text-emerald-400">{ohlcParts.h}</span></span>
            <span className="text-text-muted/60">L <span className="text-rose-400">{ohlcParts.l}</span></span>
            <span className="text-text-muted/60">C <span className="text-amber-300">{ohlcParts.c}</span></span>
          </div>
        )}
        {indicatorLabel && (
          <div className="font-mono text-[10px] text-text-muted/70">{indicatorLabel}</div>
        )}
      </div>

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

      {/* ── Floating per-drawing context toolbar (on selection) ─── */}
      <DrawingContextToolbar chartRef={refs.chartRef} candleSeriesRef={refs.candleSeriesRef} />

      {/* ── Drawing Layers panel (toggled from the drawing toolbar) ─ */}
      {showLayersPanel && (
        <div className="absolute right-3 top-3 z-40">
          <DrawingLayersPanel />
        </div>
      )}
    </div>
  );
}
