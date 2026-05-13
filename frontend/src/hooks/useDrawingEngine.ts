import { useEffect, useRef, useCallback } from 'react';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { LineSeries } from 'lightweight-charts';
import { useChartUIStore, type Point } from '../store/useChartUIStore';

// All 2-click / drag drawing tools
const TWO_POINT_TOOLS = new Set([
  'trendline', 'ray', 'info-line', 'extended-line', 'trend-angle',
  'horizontal-line', 'horizontal-ray', 'vertical-line', 'cross-line',
  'parallel-channel', 'regression-trend', 'flat-top-bottom', 'disjoint-channel',
  'fib-retracement', 'fib-extension', 'fib-channel', 'fib-time-zone',
  'fib-speed-fan', 'fib-time-trend', 'fib-circles', 'fib-spiral',
  'fib-arcs', 'fib-wedge', 'pitchfan',
  'gann-box', 'gann-square-fixed', 'gann-square', 'gann-fan',
  'trend-fib', 'long-position', 'short-position', 'price-range',
  // Patterns
  'xabcd-pattern', 'cypher-pattern', 'head-shoulders',
  'abcd-pattern', 'triangle-pattern', 'three-drives',
  // Elliott Waves
  'elliott-impulse', 'elliott-correction', 'elliott-triangle',
  'elliott-double-combo', 'elliott-triple-combo',
  // Cycles
  'cyclic-lines', 'time-cycles', 'sine-line',
  // Arrows
  'arrow-marker', 'arrow', 'arrow-mark-up', 'arrow-mark-down',
  'arrow-mark-left', 'arrow-mark-right',
  // Shapes
  'rectangle', 'rotated-rectangle', 'path', 'circle', 'ellipse',
  'polyline', 'triangle-shape', 'arc', 'curve', 'double-curve',
  // Projection & Volume & Measurer
  'forecast', 'bars-pattern', 'ghost-feed', 'projection',
  'anchored-vwap', 'fixed-range-volume', 'measure',
  'date-range', 'date-price-range',
  // Text & Notes
  'text', 'anchored-text', 'note', 'anchored-note',
  'callout', 'comment', 'price-label', 'price-note',
  'signpost', 'flag-mark',
]);

const FREEHAND_TOOLS = new Set([
  'brush', 'highlighter',
]);

const UNSUPPORTED_TOOLS = new Set<string>();

/**
 * useDrawingEngine — Drag-to-Draw Physics Bridge (v2)
 *
 * Supports drag-to-draw: mousedown sets anchor, mousemove shows live preview,
 * mouseup finalizes the drawing. Falls back to click-click for accessibility.
 */
export function useDrawingEngine(
  chartRef: React.RefObject<IChartApi | null>,
  candleSeriesRef: React.RefObject<ISeriesApi<'Candlestick'> | null>,
  containerRef: React.RefObject<HTMLDivElement | null>,
) {
  const activeDrawingTool = useChartUIStore((s) => s.activeDrawingTool);
  const addDrawing = useChartUIStore((s) => s.addDrawing);
  const setActiveDrawingTool = useChartUIStore((s) => s.setActiveDrawingTool);
  const drawingColor = useChartUIStore((s) => s.drawingColor);

  // Drag state refs (avoid re-renders during drag)
  const isDragging = useRef(false);
  const anchorPoint = useRef<Point | null>(null);
  const freehandPoints = useRef<Point[]>([]);
  const previewSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  // ── Pixel → Logical coordinate conversion ──────────────────────────
  const pixelToPoint = useCallback(
    (x: number, y: number): Point | null => {
      const chart = chartRef.current;
      const series = candleSeriesRef.current;
      if (!chart || !series) return null;

      // Time from pixel X
      let time: number | null = null;
      const converted = chart.timeScale().coordinateToTime(x);
      if (converted !== null && converted !== undefined) {
        time = converted as number;
      }
      if (time === null) return null;

      // Price from pixel Y
      const price = series.coordinateToPrice(y);
      if (price === null || price === undefined) return null;

      return { time, price: +price.toFixed(2) };
    },
    [chartRef, candleSeriesRef],
  );

  // ── Show/update live preview line during drag ──────────────────────
  const updatePreview = useCallback(
    (p1: Point, p2: Point, color: string) => {
      const chart = chartRef.current;
      if (!chart) return;

      // Remove old preview
      if (previewSeriesRef.current) {
        try { chart.removeSeries(previewSeriesRef.current); } catch {}
        previewSeriesRef.current = null;
      }

      const sorted = [p1, p2].sort((a, b) => a.time - b.time);
      const preview = chart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        lineStyle: 2, // Dashed for preview
        crosshairMarkerVisible: true,
        crosshairMarkerRadius: 6,
        crosshairMarkerBackgroundColor: '#FFFFFF',
        crosshairMarkerBorderColor: color,
        priceLineVisible: false,
        lastValueVisible: false,
      });

      // LWC requires strictly ascending time — offset if equal
      const t0 = sorted[0].time;
      const t1 = sorted[1].time <= t0 ? t0 + 1 : sorted[1].time;

      preview.setData([
        { time: t0 as Time, value: sorted[0].price },
        { time: t1 as Time, value: sorted[1].price },
      ]);

      previewSeriesRef.current = preview;
    },
    [chartRef],
  );

  // ── Remove preview series ──────────────────────────────────────────
  const clearPreview = useCallback(() => {
    const chart = chartRef.current;
    if (previewSeriesRef.current && chart) {
      try { chart.removeSeries(previewSeriesRef.current); } catch {}
      previewSeriesRef.current = null;
    }
  }, [chartRef]);

  // ── Tool color helper ──────────────────────────────────────────────
  const getToolColor = useCallback((tool: string): string => {
    const colors: Record<string, string> = {
      'trendline': '#2962FF', 'ray': '#2962FF', 'info-line': '#00BCD4',
      'extended-line': '#2962FF', 'trend-angle': '#FF9800',
      'horizontal-line': '#FF6D00', 'horizontal-ray': '#FF6D00',
      'vertical-line': '#AB47BC', 'cross-line': '#AB47BC',
      'parallel-channel': '#26A69A', 'regression-trend': '#EC407A',
      'flat-top-bottom': '#26A69A', 'disjoint-channel': '#78909C',
      'fib-retracement': '#FFD600', 'fib-extension': '#FFD600',
      'fib-channel': '#F48FB1', 'fib-time-zone': '#CE93D8',
      'fib-speed-fan': '#80CBC4', 'fib-time-trend': '#CE93D8',
      'fib-circles': '#FFAB91', 'fib-spiral': '#A5D6A7',
      'fib-arcs': '#80DEEA', 'fib-wedge': '#EF9A9A',
      'pitchfan': '#B39DDB',
      'gann-box': '#FFF176', 'gann-square-fixed': '#FFF176',
      'gann-square': '#FFF176', 'gann-fan': '#FFE082',
      'trend-fib': '#FFD600',
      'long-position': '#22c55e', 'short-position': '#ef4444',
      'price-range': '#00BCD4',
      // Patterns
      'xabcd-pattern': '#2196F3', 'cypher-pattern': '#00BCD4',
      'head-shoulders': '#9C27B0', 'abcd-pattern': '#3F51B5',
      'triangle-pattern': '#009688', 'three-drives': '#FF5722',
      // Elliott Waves
      'elliott-impulse': '#4CAF50', 'elliott-correction': '#FF9800',
      'elliott-triangle': '#E91E63', 'elliott-double-combo': '#673AB7',
      'elliott-triple-combo': '#795548',
      // Cycles
      'cyclic-lines': '#00BCD4', 'time-cycles': '#3F51B5',
      'sine-line': '#E91E63',
      // Arrows
      'arrow-marker': '#FF5722', 'arrow': '#FF5722',
      'arrow-mark-up': '#4CAF50', 'arrow-mark-down': '#ef4444',
      'arrow-mark-left': '#FF9800', 'arrow-mark-right': '#2196F3',
      // Shapes
      'rectangle': '#2962FF', 'rotated-rectangle': '#2962FF',
      'path': '#9C27B0', 'circle': '#00BCD4', 'ellipse': '#00BCD4',
      'polyline': '#FF9800', 'triangle-shape': '#009688',
      'arc': '#E91E63', 'curve': '#673AB7', 'double-curve': '#795548',
      // Projection & Volume & Measurer
      'forecast': '#26A69A', 'bars-pattern': '#FF9800',
      'ghost-feed': '#AB47BC', 'projection': '#2196F3',
      'anchored-vwap': '#FF6D00', 'fixed-range-volume': '#00BCD4',
      'date-range': '#78909C', 'date-price-range': '#607D8B', 'measure': '#2962FF',
      // Brushes
      'brush': '#FF5722', 'highlighter': '#FFEB3B',
      // Text & Notes
      'text': '#E0E0E0', 'anchored-text': '#BDBDBD', 'note': '#FFC107',
      'anchored-note': '#FFB300', 'callout': '#4CAF50', 'comment': '#8BC34A',
      'price-label': '#03A9F4', 'price-note': '#00BCD4',
      'signpost': '#795548', 'flag-mark': '#F44336',
    };
    return colors[tool] || '#2962FF';
  }, []);

  // ── Finalize drawing ───────────────────────────────────────────────
  const finalizeDraw = useCallback(
    (p1: Point, p2: Point) => {
      if (!activeDrawingTool) return;
      clearPreview();
      const id = crypto.randomUUID();
      addDrawing({ id, tool: activeDrawingTool, points: [p1, p2], color: drawingColor });
      console.log(`[DRAW ENGINE] ${activeDrawingTool} complete:`, id);
      isDragging.current = false;
      anchorPoint.current = null;
      setActiveDrawingTool(null);
    },
    [activeDrawingTool, addDrawing, setActiveDrawingTool, clearPreview, drawingColor],
  );

  // ── Mouse event handlers ───────────────────────────────────────────
  useEffect(() => {
    const container = containerRef.current;
    const chart = chartRef.current;
    if (!container || !chart || !activeDrawingTool) return;

    if (UNSUPPORTED_TOOLS.has(activeDrawingTool)) {
      console.log(`[DRAW ENGINE] "${activeDrawingTool}" not yet implemented`);
      setActiveDrawingTool(null);
      return;
    }

    const isFreehand = FREEHAND_TOOLS.has(activeDrawingTool);
    if (!isFreehand && !TWO_POINT_TOOLS.has(activeDrawingTool)) return;

    const color = getToolColor(activeDrawingTool);

    // Get chart canvas offset (the chart container's position on screen)
    const getLocalCoords = (e: MouseEvent) => {
      const rect = container.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };

    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 0) return; // left click only
      const { x, y } = getLocalCoords(e);
      const point = pixelToPoint(x, y);
      if (!point) return;

      isDragging.current = true;
      anchorPoint.current = point;
      freehandPoints.current = isFreehand ? [point] : [];

      // Prevent chart from panning while drawing
      chart.applyOptions({ handleScroll: false, handleScale: false });
    };

    const onMouseMove = (e: MouseEvent) => {
      if (!isDragging.current || !anchorPoint.current) return;
      const { x, y } = getLocalCoords(e);
      const point = pixelToPoint(x, y);
      if (!point) return;

      if (isFreehand) {
        // Collect every point for freehand brush
        freehandPoints.current.push(point);
        // Show live preview of the freehand path
        if (freehandPoints.current.length >= 2) {
          const pts = freehandPoints.current;
          updatePreview(pts[pts.length - 2], pts[pts.length - 1], drawingColor);
        }
      } else {
        // Show live dashed preview line
        updatePreview(anchorPoint.current, point, color);
      }
    };

    const onMouseUp = (e: MouseEvent) => {
      // Re-enable chart interaction
      chart.applyOptions({ handleScroll: true, handleScale: true });

      if (!isDragging.current || !anchorPoint.current) return;

      if (isFreehand && freehandPoints.current.length >= 2) {
        // Finalize freehand drawing with all collected points
        clearPreview();
        const id = crypto.randomUUID();
        addDrawing({ id, tool: activeDrawingTool, points: freehandPoints.current, color: drawingColor });
        console.log(`[DRAW ENGINE] ${activeDrawingTool} freehand complete:`, id, `(${freehandPoints.current.length} pts)`);
        isDragging.current = false;
        anchorPoint.current = null;
        freehandPoints.current = [];
        setActiveDrawingTool(null);
        return;
      }

      const { x, y } = getLocalCoords(e);
      const point = pixelToPoint(x, y);

      if (point) {
        // Only finalize if dragged a meaningful distance (> 5px)
        const dx = Math.abs(e.clientX - (container.getBoundingClientRect().left + 
          (chart.timeScale().timeToCoordinate(anchorPoint.current.time as Time) ?? 0)));
        if (dx > 5 || Math.abs(anchorPoint.current.price - point.price) > 0.01) {
          finalizeDraw(anchorPoint.current, point);
        } else {
          // Too small — treat as a click, wait for second click
          // (keep anchor, don't finalize)
          isDragging.current = false;
        }
      } else {
        isDragging.current = false;
        anchorPoint.current = null;
        clearPreview();
      }
    };

    // Also support click-click as fallback
    const onClick = (e: MouseEvent) => {
      if (isDragging.current) return; // drag handled by mouseup
      if (!anchorPoint.current) return; // no pending anchor from a tiny drag

      const { x, y } = getLocalCoords(e);
      const point = pixelToPoint(x, y);
      if (!point) return;

      // Second click — complete the drawing
      finalizeDraw(anchorPoint.current, point);
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        isDragging.current = false;
        anchorPoint.current = null;
        clearPreview();
        chart.applyOptions({ handleScroll: true, handleScale: true });
        setActiveDrawingTool(null);
      }
    };

    container.addEventListener('mousedown', onMouseDown);
    container.addEventListener('mousemove', onMouseMove);
    container.addEventListener('mouseup', onMouseUp);
    container.addEventListener('click', onClick);
    document.addEventListener('keydown', onKeyDown);

    return () => {
      container.removeEventListener('mousedown', onMouseDown);
      container.removeEventListener('mousemove', onMouseMove);
      container.removeEventListener('mouseup', onMouseUp);
      container.removeEventListener('click', onClick);
      document.removeEventListener('keydown', onKeyDown);
      chart.applyOptions({ handleScroll: true, handleScale: true });
      clearPreview();
      isDragging.current = false;
      anchorPoint.current = null;
      freehandPoints.current = [];
    };
  }, [activeDrawingTool, chartRef, candleSeriesRef, containerRef, 
      pixelToPoint, updatePreview, clearPreview, finalizeDraw, 
      getToolColor, setActiveDrawingTool]);
}
