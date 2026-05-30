import { useEffect, useRef, useCallback } from 'react';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { useChartUIStore, type Point } from '../store/useChartUIStore';
import type { ChartCandle } from '../utils/chartTypes';

const HIT_TOLERANCE_PX = 12;

const getTimeNum = (t: any): number => {
  if (typeof t === 'number') return t;
  if (t instanceof Date) return Math.floor(t.getTime() / 1000);
  if (typeof t === 'string') {
    const parsed = Date.parse(t);
    return isNaN(parsed) ? 0 : Math.floor(parsed / 1000);
  }
  if (t && typeof t === 'object' && 'year' in t) {
    return Math.floor(Date.UTC(t.year, t.month - 1, t.day) / 1000);
  }
  return 0;
};

const BOX_TOOLS = new Set([
  'rectangle', 'gann-box', 'gann-square', 'gann-square-fixed', 
  'price-range', 'measure', 'long-position', 'short-position',
  'fib-retracement', 'fib-extension', 'fib-channel', 'parallel-channel',
  'flat-top-bottom', 'disjoint-channel', 'date-range', 'date-price-range'
]);

const snapToGrid = (time: number, candles: ChartCandle[], intervalSec: number): number => {
  if (!candles || candles.length === 0) return time;
  const firstTime = candles[0].time;
  const lastTime = candles[candles.length - 1].time;

  if (time < firstTime) {
    const diff = firstTime - time;
    const steps = Math.round(diff / intervalSec);
    return firstTime - steps * intervalSec;
  }

  if (time > lastTime) {
    const diff = time - lastTime;
    const steps = Math.round(diff / intervalSec);
    return lastTime + steps * intervalSec;
  }

  let low = 0;
  let high = candles.length - 1;
  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    const midTime = candles[mid].time;
    if (midTime === time) return time;
    if (midTime < time) {
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }

  const timeLow = low < candles.length ? candles[low].time : Infinity;
  const timeHigh = high >= 0 ? candles[high].time : -Infinity;
  if (Math.abs(timeLow - time) < Math.abs(time - timeHigh)) {
    return timeLow;
  }
  return timeHigh;
};

/**
 * useDrawingInteraction — Select, Move, Resize, Delete drawings from all sides/corners
 */
export function useDrawingInteraction(
  chartRef: React.RefObject<IChartApi | null>,
  candleSeriesRef: React.RefObject<ISeriesApi<'Candlestick'> | null>,
  containerRef: React.RefObject<HTMLDivElement | null>,
  chartData: ChartCandle[] = [],
) {
  const activeDrawingTool = useChartUIStore((s) => s.activeDrawingTool);
  const drawingsLocked = useChartUIStore((s) => s.drawingsLocked);

  // Maintain refs to chartData to keep pixelToPoint hook reference stable
  const chartDataRef = useRef(chartData);
  chartDataRef.current = chartData;

  const intervalSec = chartData.length >= 2
    ? chartData[1].time - chartData[0].time
    : 600;
  const intervalSecRef = useRef(intervalSec);
  if (chartData.length >= 2) {
    intervalSecRef.current = chartData[1].time - chartData[0].time;
  }

  // Interaction state refs (never trigger re-renders)
  const dragMode = useRef<'none' | 'move' | 'resize-start' | 'resize-end' | string>('none');
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

      let timeNum: number;
      if (typeof time === 'number') {
        timeNum = time;
      } else if (typeof time === 'string') {
        timeNum = Math.floor(Date.parse(time) / 1000);
      } else if (time && typeof time === 'object' && 'year' in time) {
        timeNum = Math.floor(Date.UTC(time.year, time.month - 1, time.day) / 1000);
      } else {
        return null;
      }

      if (isNaN(timeNum)) return null;

      // Snap the timestamp to the timeframe grid to prevent LWC timescale corruption
      const snappedTime = snapToGrid(timeNum, chartDataRef.current, intervalSecRef.current);

      const price = series.coordinateToPrice(y);
      if (price === null || price === undefined) return null;

      return { time: snappedTime, price: +price.toFixed(2) };
    },
    [chartRef, candleSeriesRef],
  );

  // ── Point → Pixel converter ────────────────────────────────────────
  const pointToPixel = useCallback(
    (point: Point): { x: number; y: number } | null => {
      const chart = chartRef.current;
      const series = candleSeriesRef.current;
      if (!chart || !series) return null;

      const timeVal = getTimeNum(point.time);
      const x = chart.timeScale().timeToCoordinate(timeVal as Time);
      const y = series.priceToCoordinate(point.price);
      if (x === null || y === null) return null;

      return { x, y };
    },
    [chartRef, candleSeriesRef],
  );

  // ── Find drawing near a pixel position (reads state imperatively) ──
  const findDrawingAt = useCallback(
    (px: number, py: number): { id: string; hitType: string } | null => {
      const { drawings } = useChartUIStore.getState();
      for (const drawing of drawings) {
        if (drawing.points.length < 2) continue;

        const p1px = pointToPixel(drawing.points[0]);
        const p2px = pointToPixel(drawing.points[1]);
        if (!p1px || !p2px) continue;

        if (BOX_TOOLS.has(drawing.tool)) {
          const xMin = Math.min(p1px.x, p2px.x);
          const xMax = Math.max(p1px.x, p2px.x);
          const yMin = Math.min(p1px.y, p2px.y);
          const yMax = Math.max(p1px.y, p2px.y);

          // 1. Check the 4 corners
          if (Math.hypot(px - xMin, py - yMin) < HIT_TOLERANCE_PX) {
            return { id: drawing.id, hitType: 'corner-tl' };
          }
          if (Math.hypot(px - xMax, py - yMin) < HIT_TOLERANCE_PX) {
            return { id: drawing.id, hitType: 'corner-tr' };
          }
          if (Math.hypot(px - xMin, py - yMax) < HIT_TOLERANCE_PX) {
            return { id: drawing.id, hitType: 'corner-bl' };
          }
          if (Math.hypot(px - xMax, py - yMax) < HIT_TOLERANCE_PX) {
            return { id: drawing.id, hitType: 'corner-br' };
          }

          // 2. Check the 4 edges
          if (Math.abs(py - yMin) < HIT_TOLERANCE_PX && px >= xMin - HIT_TOLERANCE_PX && px <= xMax + HIT_TOLERANCE_PX) {
            return { id: drawing.id, hitType: 'edge-top' };
          }
          if (Math.abs(py - yMax) < HIT_TOLERANCE_PX && px >= xMin - HIT_TOLERANCE_PX && px <= xMax + HIT_TOLERANCE_PX) {
            return { id: drawing.id, hitType: 'edge-bottom' };
          }
          if (Math.abs(px - xMin) < HIT_TOLERANCE_PX && py >= yMin - HIT_TOLERANCE_PX && py <= yMax + HIT_TOLERANCE_PX) {
            return { id: drawing.id, hitType: 'edge-left' };
          }
          if (Math.abs(px - xMax) < HIT_TOLERANCE_PX && py >= yMin - HIT_TOLERANCE_PX && py <= yMax + HIT_TOLERANCE_PX) {
            return { id: drawing.id, hitType: 'edge-right' };
          }

          // 3. Check inside body
          if (px >= xMin && px <= xMax && py >= yMin && py <= yMax) {
            return { id: drawing.id, hitType: 'body' };
          }
        } else {
          // Standard line-based tool
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
        } else if (hit.hitType === 'body') {
          dragMode.current = 'move';
        } else {
          dragMode.current = hit.hitType;
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
          if (hit.hitType === 'body') {
            container.style.cursor = 'grab';
          } else if (hit.hitType === 'edge-top' || hit.hitType === 'edge-bottom') {
            container.style.cursor = 'ns-resize';
          } else if (hit.hitType === 'edge-left' || hit.hitType === 'edge-right') {
            container.style.cursor = 'ew-resize';
          } else if (hit.hitType === 'corner-tl' || hit.hitType === 'corner-br') {
            container.style.cursor = 'nwse-resize';
          } else if (hit.hitType === 'corner-tr' || hit.hitType === 'corner-bl') {
            container.style.cursor = 'nesw-resize';
          } else {
            container.style.cursor = 'nwse-resize';
          }
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
        const t0 = getTimeNum(origPts[0].time);
        const t1 = getTimeNum(origPts[1].time);
        store.updateDrawingPoints(sid, [
          { time: t0 + dTime, price: +(origPts[0].price + dPrice).toFixed(2) },
          { time: t1 + dTime, price: +(origPts[1].price + dPrice).toFixed(2) },
        ]);
      } else if (dragMode.current === 'resize-start') {
        const t1 = getTimeNum(origPts[1].time);
        store.updateDrawingPoints(sid, [currentPoint, { ...origPts[1], time: t1 }]);
      } else if (dragMode.current === 'resize-end') {
        const t0 = getTimeNum(origPts[0].time);
        store.updateDrawingPoints(sid, [{ ...origPts[0], time: t0 }, currentPoint]);
      } else {
        // Multi-directional resizing for box shapes
        const t0 = getTimeNum(origPts[0].time);
        const t1 = getTimeNum(origPts[1].time);
        const timeMinIdx = t0 < t1 ? 0 : 1;
        const timeMaxIdx = t0 < t1 ? 1 : 0;
        const priceMinIdx = origPts[0].price < origPts[1].price ? 0 : 1;
        const priceMaxIdx = origPts[0].price < origPts[1].price ? 1 : 0;

        const nextPoints = [
          { ...origPts[0], time: t0 },
          { ...origPts[1], time: t1 }
        ];

        switch (dragMode.current) {
          case 'corner-tl':
            nextPoints[timeMinIdx].time = currentPoint.time;
            nextPoints[priceMaxIdx].price = currentPoint.price;
            break;
          case 'corner-tr':
            nextPoints[timeMaxIdx].time = currentPoint.time;
            nextPoints[priceMaxIdx].price = currentPoint.price;
            break;
          case 'corner-bl':
            nextPoints[timeMinIdx].time = currentPoint.time;
            nextPoints[priceMinIdx].price = currentPoint.price;
            break;
          case 'corner-br':
            nextPoints[timeMaxIdx].time = currentPoint.time;
            nextPoints[priceMinIdx].price = currentPoint.price;
            break;
          case 'edge-top':
            nextPoints[priceMaxIdx].price = currentPoint.price;
            break;
          case 'edge-bottom':
            nextPoints[priceMinIdx].price = currentPoint.price;
            break;
          case 'edge-left':
            nextPoints[timeMinIdx].time = currentPoint.time;
            break;
          case 'edge-right':
            nextPoints[timeMaxIdx].time = currentPoint.time;
            break;
        }

        store.updateDrawingPoints(sid, nextPoints);
      }

      // Maintain proper cursor style during active dragging
      if (dragMode.current === 'move') {
        container.style.cursor = 'grabbing';
      } else if (dragMode.current === 'edge-top' || dragMode.current === 'edge-bottom') {
        container.style.cursor = 'ns-resize';
      } else if (dragMode.current === 'edge-left' || dragMode.current === 'edge-right') {
        container.style.cursor = 'ew-resize';
      } else if (dragMode.current === 'corner-tl' || dragMode.current === 'corner-br') {
        container.style.cursor = 'nwse-resize';
      } else if (dragMode.current === 'corner-tr' || dragMode.current === 'corner-bl') {
        container.style.cursor = 'nesw-resize';
      } else {
        container.style.cursor = 'nwse-resize';
      }
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
