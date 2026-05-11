import { useEffect, useRef, useState, useCallback } from 'react';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { useChartUIStore, type Point } from '../store/useChartUIStore';

// Tools that use a 2-click (start → end) drawing model
const TWO_POINT_TOOLS = new Set([
  'trendline',
  'horizontal-line',
  'horizontal-ray',
  'vertical-line',
  'cross-line',
  'fib-retracement',
  'trend-fib',
  'long-position',
  'short-position',
  'price-range',
]);

// Tools that are single-click or not yet supported
const UNSUPPORTED_TOOLS = new Set([
  'brush',
  'highlighter',
  'rectangle',
  'circle',
  'text',
  'callout',
  'price-label',
]);

/**
 * useDrawingEngine — Drawing Physics Bridge
 *
 * Translates raw screen-pixel clicks on the lightweight-charts canvas into
 * logical Time/Price coordinates, then persists completed drawings into the
 * Zustand store.
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

  // Keep ref in sync so the click handler always reads the latest value
  // without needing to re-subscribe on every state change.
  useEffect(() => {
    currentPointsRef.current = currentPoints;
  }, [currentPoints]);

  // Reset in-progress state whenever the tool changes (e.g. user cancels)
  useEffect(() => {
    setCurrentPoints([]);
  }, [activeDrawingTool]);

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
      // Primary: use param.time (snaps to nearest bar — standard behavior).
      // Fallback: use timeScale.coordinateToTime() for clicks between bars.
      let time: number | null = null;

      if (param.time !== undefined && param.time !== null) {
        time = param.time as number;
      } else {
        // Fallback: convert pixel X → time via the time scale
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

      // ── 2-point drawing tools ─────────────────────────────────────
      if (TWO_POINT_TOOLS.has(activeDrawingTool)) {
        const points = [...currentPointsRef.current, point];

        if (points.length === 1) {
          // First click — save anchor point, wait for second click
          setCurrentPoints(points);
          console.log('[DRAWING ENGINE] Anchor set:', point);
        } else if (points.length >= 2) {
          // Second click — complete the drawing
          const id = crypto.randomUUID();
          addDrawing({ id, tool: activeDrawingTool, points: [points[0], points[1]] });
          console.log(`[DRAWING ENGINE] ${activeDrawingTool} complete:`, id);

          // Reset: deactivate the tool so user exits drawing mode
          setCurrentPoints([]);
          setActiveDrawingTool(null);
        }
      }
    },
    [activeDrawingTool, chartRef, candleSeriesRef, addDrawing, setActiveDrawingTool],
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
