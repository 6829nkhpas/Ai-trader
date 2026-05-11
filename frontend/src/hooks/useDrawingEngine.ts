import { useEffect, useRef, useState, useCallback } from 'react';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { LineSeries } from 'lightweight-charts';
import { useChartUIStore, type Point } from '../store/useChartUIStore';

// All 2-click (start → end) drawing tools
const TWO_POINT_TOOLS = new Set([
  'trendline',
  'ray',
  'info-line',
  'extended-line',
  'trend-angle',
  'horizontal-line',
  'horizontal-ray',
  'vertical-line',
  'cross-line',
  'parallel-channel',
  'regression-trend',
  'flat-top-bottom',
  'disjoint-channel',
  'fib-retracement',
  'fib-extension',
  'fib-channel',
  'fib-time-zone',
  'fib-speed-fan',
  'fib-time-trend',
  'fib-circles',
  'fib-spiral',
  'fib-arcs',
  'fib-wedge',
  'pitchfan',
  'gann-box',
  'gann-square-fixed',
  'gann-square',
  'gann-fan',
  'trend-fib',
  'long-position',
  'short-position',
  'price-range',
]);

// Tools that are not yet supported (single-click or complex)
const UNSUPPORTED_TOOLS = new Set([
  'brush',
  'highlighter',
  'rectangle',
  'circle',
  'text',
  'callout',
  'price-label',
]);

// Color per tool for the anchor marker
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
  'fib-extension': '#FFD600',
  'fib-channel': '#F48FB1',
  'fib-time-zone': '#CE93D8',
  'fib-speed-fan': '#80CBC4',
  'fib-time-trend': '#CE93D8',
  'fib-circles': '#FFAB91',
  'fib-spiral': '#A5D6A7',
  'fib-arcs': '#80DEEA',
  'fib-wedge': '#EF9A9A',
  'pitchfan': '#B39DDB',
  'gann-box': '#FFF176',
  'gann-square-fixed': '#FFF176',
  'gann-square': '#FFF176',
  'gann-fan': '#FFE082',
  'trend-fib': '#FFD600',
  'long-position': '#22c55e',
  'short-position': '#ef4444',
  'price-range': '#00BCD4',
};

/**
 * useDrawingEngine — Drawing Physics Bridge
 *
 * Translates raw screen-pixel clicks on the lightweight-charts canvas into
 * logical Time/Price coordinates, then persists completed drawings into the
 * Zustand store.
 *
 * Shows a visible anchor marker (pulsing dot) at the first click point
 * so the user knows the start point was registered.
 *
 * @param chartRef       – React ref holding the IChartApi instance.
 * @param candleSeriesRef – React ref holding the primary candlestick series.
 */
export function useDrawingEngine(
  chartRef: React.RefObject<IChartApi | null>,
  candleSeriesRef: React.RefObject<ISeriesApi<'Candlestick'> | null>,
) {
  const activeDrawingTool = useChartUIStore((s) => s.activeDrawingTool);
  const addDrawing = useChartUIStore((s) => s.addDrawing);
  const setActiveDrawingTool = useChartUIStore((s) => s.setActiveDrawingTool);

  // Local state for in-progress drawing
  const [currentPoints, setCurrentPoints] = useState<Point[]>([]);
  const currentPointsRef = useRef<Point[]>([]);

  // Temporary anchor marker series (shows the first-click dot on chart)
  const anchorSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  // Keep ref in sync so the click handler always reads the latest value
  useEffect(() => {
    currentPointsRef.current = currentPoints;
  }, [currentPoints]);

  // ── Cleanup anchor marker helper ────────────────────────────────────
  const removeAnchorMarker = useCallback(() => {
    const chart = chartRef.current;
    if (anchorSeriesRef.current && chart) {
      try {
        chart.removeSeries(anchorSeriesRef.current);
      } catch {
        // already removed
      }
      anchorSeriesRef.current = null;
    }
  }, [chartRef]);

  // ── Show anchor marker at first-click position ──────────────────────
  const showAnchorMarker = useCallback(
    (point: Point, color: string) => {
      const chart = chartRef.current;
      if (!chart) return;

      // Remove any existing anchor marker first
      removeAnchorMarker();

      // Create a visible anchor series — lastValueVisible shows a permanent
      // colored dot + label at the price level on the y-axis.
      // crosshairMarkerVisible shows a dot when the crosshair is near.
      const anchorSeries = chart.addSeries(LineSeries, {
        color: 'transparent',
        lineWidth: 1,
        crosshairMarkerVisible: true,
        crosshairMarkerRadius: 6,
        crosshairMarkerBackgroundColor: color,
        priceLineVisible: false,
        lastValueVisible: true,
        title: '⊙',
      });

      // Single data point — the line won't render (need 2+ pts)
      // but lastValueVisible still shows the price label dot
      anchorSeries.setData([
        { time: point.time as Time, value: point.price },
      ]);

      // Add a subtle horizontal price line as permanent visual indicator
      anchorSeries.createPriceLine({
        price: point.price,
        color,
        lineWidth: 1,
        lineStyle: 2, // Dashed
        axisLabelVisible: true,
      });

      anchorSeriesRef.current = anchorSeries;
    },
    [chartRef, removeAnchorMarker],
  );

  // Reset in-progress state + remove anchor whenever the tool changes
  useEffect(() => {
    setCurrentPoints([]);
    removeAnchorMarker();
  }, [activeDrawingTool, removeAnchorMarker]);

  // ── Click handler — pixel → logical coordinate translation ──────────
  const handleClick = useCallback(
    (param: any) => {
      const chart = chartRef.current;
      const candleSeries = candleSeriesRef.current;

      if (!activeDrawingTool) return;
      if (!chart || !candleSeries) {
        console.warn('[DRAWING ENGINE] Chart or series not ready');
        return;
      }

      // Check if the tool is unsupported
      if (UNSUPPORTED_TOOLS.has(activeDrawingTool)) {
        console.log(`[DRAWING ENGINE] "${activeDrawingTool}" is not yet implemented`);
        setActiveDrawingTool(null);
        return;
      }

      // Must have pixel coordinates
      if (!param.point) return;

      // ── Extract logical time ──────────────────────────────────────
      let time: number | null = null;

      if (param.time !== undefined && param.time !== null) {
        time = param.time as number;
      } else {
        const converted = chart.timeScale().coordinateToTime(param.point.x);
        if (converted !== null && converted !== undefined) {
          time = converted as number;
        }
      }

      if (time === null) {
        console.warn('[DRAWING ENGINE] Could not resolve time coordinate');
        return;
      }

      // ── Extract logical price ─────────────────────────────────────
      const price = candleSeries.coordinateToPrice(param.point.y);
      if (price === null || price === undefined) {
        console.warn('[DRAWING ENGINE] Could not resolve price coordinate');
        return;
      }

      const point: Point = { time, price: +price.toFixed(2) };
      const toolColor = TOOL_COLORS[activeDrawingTool] || '#2962FF';

      // ── 2-point drawing tools ─────────────────────────────────────
      if (TWO_POINT_TOOLS.has(activeDrawingTool)) {
        const points = [...currentPointsRef.current, point];

        if (points.length === 1) {
          // First click — show anchor marker and wait for second click
          setCurrentPoints(points);
          showAnchorMarker(point, toolColor);
          console.log('[DRAWING ENGINE] Anchor set:', point);
        } else if (points.length >= 2) {
          // Second click — remove anchor, complete the drawing
          removeAnchorMarker();
          const id = crypto.randomUUID();
          addDrawing({ id, tool: activeDrawingTool, points: [points[0], points[1]] });
          console.log(`[DRAWING ENGINE] ${activeDrawingTool} complete:`, id);

          // Reset: deactivate the tool so user exits drawing mode
          setCurrentPoints([]);
          setActiveDrawingTool(null);
        }
      }
    },
    [activeDrawingTool, chartRef, candleSeriesRef, addDrawing, setActiveDrawingTool, showAnchorMarker, removeAnchorMarker],
  );

  // ── Subscribe / unsubscribe to chart click events ───────────────────
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !activeDrawingTool) return;

    chart.subscribeClick(handleClick);
    return () => {
      chart.unsubscribeClick(handleClick);
    };
  }, [chartRef, activeDrawingTool, handleClick]);

  return { currentPoints };
}
