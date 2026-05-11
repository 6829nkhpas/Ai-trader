import { useEffect, useRef, useCallback } from 'react';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { useChartUIStore, type Point } from '../store/useChartUIStore';

const HIT_TOLERANCE_PX = 12;

/**
 * useDrawingInteraction — Select, Move, Resize, Delete drawings
 *
 * Uses getState() for reading drawings inside event handlers to avoid
 * re-render loops when updateDrawingPoints mutates drawings on mousemove.
 */
export function useDrawingInteraction(
  chartRef: React.RefObject<IChartApi | null>,
  candleSeriesRef: React.RefObject<ISeriesApi<'Candlestick'> | null>,
  containerRef: React.RefObject<HTMLDivElement | null>,
) {
  // Only subscribe to minimal props needed for effect setup/teardown
  const activeDrawingTool = useChartUIStore((s) => s.activeDrawingTool);
  const drawingsLocked = useChartUIStore((s) => s.drawingsLocked);

  // Interaction state refs (never trigger re-renders)
  const dragMode = useRef<'none' | 'move' | 'resize-start' | 'resize-end'>('none');
  const dragStartPixel = useRef<{ x: number; y: number } | null>(null);
  const originalPoints = useRef<Point[] | null>(null);
  const selectedIdRef = useRef<string | null>(null);

  // ── Pixel → Point converter ────────────────────────────────────────
  const pixelToPoint = useCallback(
    (x: number, y: number): Point | null => {
      const chart = chartRef.current;
      const series = candleSeriesRef.current;
      if (!chart || !series) return null;

      const time = chart.timeScale().coordinateToTime(x);
      if (time === null || time === undefined) return null;

      const price = series.coordinateToPrice(y);
      if (price === null || price === undefined) return null;

      return { time: time as number, price: +price.toFixed(2) };
    },
    [chartRef, candleSeriesRef],
  );

  // ── Point → Pixel converter ────────────────────────────────────────
  const pointToPixel = useCallback(
    (point: Point): { x: number; y: number } | null => {
      const chart = chartRef.current;
      const series = candleSeriesRef.current;
      if (!chart || !series) return null;

      const x = chart.timeScale().timeToCoordinate(point.time as Time);
      const y = series.priceToCoordinate(point.price);
      if (x === null || y === null) return null;

      return { x, y };
    },
    [chartRef, candleSeriesRef],
  );

  // ── Find drawing near a pixel position (reads state imperatively) ──
  const findDrawingAt = useCallback(
    (px: number, py: number): { id: string; hitType: 'start' | 'end' | 'body' } | null => {
      const { drawings } = useChartUIStore.getState();
      for (const drawing of drawings) {
        if (drawing.points.length < 2) continue;

        const p1px = pointToPixel(drawing.points[0]);
        const p2px = pointToPixel(drawing.points[1]);
        if (!p1px || !p2px) continue;

        if (Math.hypot(px - p1px.x, py - p1px.y) < HIT_TOLERANCE_PX) {
          return { id: drawing.id, hitType: 'start' };
        }
        if (Math.hypot(px - p2px.x, py - p2px.y) < HIT_TOLERANCE_PX) {
          return { id: drawing.id, hitType: 'end' };
        }

        const dist = pointToSegmentDistance(px, py, p1px.x, p1px.y, p2px.x, p2px.y);
        if (dist < HIT_TOLERANCE_PX) {
          return { id: drawing.id, hitType: 'body' };
        }
      }
      return null;
    },
    [pointToPixel],
  );

  // ── Main mouse event handlers ──────────────────────────────────────
  useEffect(() => {
    const container = containerRef.current;
    const chart = chartRef.current;
    if (!container || !chart || activeDrawingTool || drawingsLocked) return;

    const getLocal = (e: MouseEvent) => {
      const rect = container.getBoundingClientRect();
      return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    };

    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 0) return;
      const { x, y } = getLocal(e);
      const hit = findDrawingAt(x, y);

      const store = useChartUIStore.getState();

      if (hit) {
        e.stopPropagation();
        store.setSelectedDrawing(hit.id);
        selectedIdRef.current = hit.id;

        const drawing = store.drawings.find((d) => d.id === hit.id);
        if (!drawing) return;

        originalPoints.current = [...drawing.points];
        dragStartPixel.current = { x, y };

        if (hit.hitType === 'start') {
          dragMode.current = 'resize-start';
        } else if (hit.hitType === 'end') {
          dragMode.current = 'resize-end';
        } else {
          dragMode.current = 'move';
        }

        chart.applyOptions({ handleScroll: false, handleScale: false });
      } else {
        store.setSelectedDrawing(null);
        selectedIdRef.current = null;
      }
    };

    const onMouseMove = (e: MouseEvent) => {
      if (dragMode.current === 'none' || !originalPoints.current || !dragStartPixel.current) {
        // Update cursor based on hover
        const { x, y } = getLocal(e);
        const hit = findDrawingAt(x, y);
        if (hit) {
          container.style.cursor = hit.hitType === 'body' ? 'grab' : 'nwse-resize';
        } else {
          container.style.cursor = '';
        }
        return;
      }

      const sid = selectedIdRef.current;
      if (!sid) return;

      const { x, y } = getLocal(e);
      const currentPoint = pixelToPoint(x, y);
      if (!currentPoint) return;

      const origPts = originalPoints.current;
      const store = useChartUIStore.getState();

      if (dragMode.current === 'move') {
        const startPoint = pixelToPoint(dragStartPixel.current.x, dragStartPixel.current.y);
        if (!startPoint) return;
        const dTime = currentPoint.time - startPoint.time;
        const dPrice = currentPoint.price - startPoint.price;
        store.updateDrawingPoints(sid, [
          { time: origPts[0].time + dTime, price: +(origPts[0].price + dPrice).toFixed(2) },
          { time: origPts[1].time + dTime, price: +(origPts[1].price + dPrice).toFixed(2) },
        ]);
      } else if (dragMode.current === 'resize-start') {
        store.updateDrawingPoints(sid, [currentPoint, origPts[1]]);
      } else if (dragMode.current === 'resize-end') {
        store.updateDrawingPoints(sid, [origPts[0], currentPoint]);
      }

      container.style.cursor = dragMode.current === 'move' ? 'grabbing' : 'nwse-resize';
    };

    const onMouseUp = () => {
      if (dragMode.current !== 'none') {
        chart.applyOptions({ handleScroll: true, handleScale: true });
      }
      dragMode.current = 'none';
      dragStartPixel.current = null;
      originalPoints.current = null;
      container.style.cursor = '';
    };

    const onKeyDown = (e: KeyboardEvent) => {
      const store = useChartUIStore.getState();
      const sid = store.selectedDrawingId;
      if (!sid) return;
      if (e.key === 'Delete' || e.key === 'Backspace') {
        store.removeDrawing(sid);
        selectedIdRef.current = null;
      } else if (e.key === 'Escape') {
        store.setSelectedDrawing(null);
        selectedIdRef.current = null;
      }
    };

    container.addEventListener('mousedown', onMouseDown, true);
    container.addEventListener('mousemove', onMouseMove);
    container.addEventListener('mouseup', onMouseUp);
    document.addEventListener('keydown', onKeyDown);

    return () => {
      container.removeEventListener('mousedown', onMouseDown, true);
      container.removeEventListener('mousemove', onMouseMove);
      container.removeEventListener('mouseup', onMouseUp);
      document.removeEventListener('keydown', onKeyDown);
      container.style.cursor = '';
      chart.applyOptions({ handleScroll: true, handleScale: true });
    };
  }, [activeDrawingTool, drawingsLocked, chartRef, candleSeriesRef, 
      containerRef, pixelToPoint, findDrawingAt]);
}

// ── Geometry helper: point-to-line-segment distance ──────────────────
function pointToSegmentDistance(
  px: number, py: number,
  x1: number, y1: number,
  x2: number, y2: number,
): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(px - x1, py - y1);

  let t = ((px - x1) * dx + (py - y1) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));

  const projX = x1 + t * dx;
  const projY = y1 + t * dy;
  return Math.hypot(px - projX, py - projY);
}
