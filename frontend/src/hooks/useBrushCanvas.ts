import { useEffect, useRef, useCallback } from 'react';
import type { IChartApi, ISeriesApi, Time } from 'lightweight-charts';
import { useChartUIStore, type Drawing, type Point } from '../store/useChartUIStore';

const BRUSH_TOOLS = new Set(['brush', 'highlighter']);

/**
 * useBrushCanvas — Renders brush/highlighter strokes on an HTML Canvas overlay.
 *
 * LWC LineSeries requires strictly ascending timestamps, which makes it
 * impossible to render freehand strokes with U-turns. This hook bypasses
 * LWC entirely and draws brush strokes on a 2D canvas that sits on top
 * of the chart container.
 */
export function useBrushCanvas(
  chartRef: React.RefObject<IChartApi | null>,
  candleSeriesRef: React.RefObject<ISeriesApi<'Candlestick'> | null>,
  containerRef: React.RefObject<HTMLDivElement | null>,
) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef<number>(0);

  // ── Create / resize the canvas overlay ──────────────────────────────
  const ensureCanvas = useCallback(() => {
    const container = containerRef.current;
    if (!container) return null;

    if (!canvasRef.current) {
      const canvas = document.createElement('canvas');
      canvas.style.position = 'absolute';
      canvas.style.top = '0';
      canvas.style.left = '0';
      canvas.style.width = '100%';
      canvas.style.height = '100%';
      canvas.style.pointerEvents = 'none';
      canvas.style.zIndex = '15'; // Above chart, below DrawingOverlays (z-20)
      container.style.position = 'relative'; // Ensure positioning context
      container.appendChild(canvas);
      canvasRef.current = canvas;
    }

    const canvas = canvasRef.current;
    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(rect.width);
    const h = Math.round(rect.height);

    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;
    }

    return canvas;
  }, [containerRef]);

  // ── Convert chart {time, price} → pixel {x, y} ─────────────────────
  const pointToPixel = useCallback(
    (pt: Point): { x: number; y: number } | null => {
      const chart = chartRef.current;
      const series = candleSeriesRef.current;
      if (!chart || !series) return null;

      try {
        const x = chart.timeScale().timeToCoordinate(pt.time as unknown as Time);
        const y = series.priceToCoordinate(pt.price);
        if (x === null || y === null) return null;
        return { x, y };
      } catch {
        return null;
      }
    },
    [chartRef, candleSeriesRef],
  );

  // ── Paint all brush/highlighter strokes ─────────────────────────────
  const paint = useCallback(() => {
    const canvas = ensureCanvas();
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);

    const { drawings, drawingsVisible } = useChartUIStore.getState();
    if (!drawingsVisible) return;

    for (const drawing of drawings) {
      if (!BRUSH_TOOLS.has(drawing.tool)) continue;
      if (drawing.points.length < 2) continue;

      const color = drawing.color || (drawing.tool === 'highlighter' ? '#FFEB3B' : '#FF5722');
      const isHighlighter = drawing.tool === 'highlighter';

      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = isHighlighter ? 8 : 3;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.globalAlpha = isHighlighter ? 0.4 : 0.9;

      ctx.beginPath();
      let started = false;

      for (const pt of drawing.points) {
        const px = pointToPixel(pt);
        if (!px) continue;

        if (!started) {
          ctx.moveTo(px.x, px.y);
          started = true;
        } else {
          ctx.lineTo(px.x, px.y);
        }
      }

      if (started) {
        ctx.stroke();
      }
      ctx.restore();
    }
  }, [ensureCanvas, pointToPixel]);

  // ── Schedule a repaint (coalesced via rAF) ──────────────────────────
  const schedulePaint = useCallback(() => {
    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(paint);
  }, [paint]);

  // ── Subscribe to chart movement + drawing changes ───────────────────
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    // Paint whenever the chart pans, zooms, or resizes
    chart.timeScale().subscribeVisibleTimeRangeChange(schedulePaint);
    chart.timeScale().subscribeVisibleLogicalRangeChange(schedulePaint);
    chart.timeScale().subscribeSizeChange(schedulePaint);

    // Initial paint
    schedulePaint();

    return () => {
      chart.timeScale().unsubscribeVisibleTimeRangeChange(schedulePaint);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(schedulePaint);
      chart.timeScale().unsubscribeSizeChange(schedulePaint);
      cancelAnimationFrame(rafRef.current);
    };
  }, [chartRef, schedulePaint]);

  // ── Repaint when drawings change ────────────────────────────────────
  const drawings = useChartUIStore((s) => s.drawings);
  const drawingsVisible = useChartUIStore((s) => s.drawingsVisible);

  useEffect(() => {
    schedulePaint();
  }, [drawings, drawingsVisible, schedulePaint]);

  // ── Cleanup canvas on unmount ───────────────────────────────────────
  useEffect(() => {
    return () => {
      if (canvasRef.current) {
        canvasRef.current.remove();
        canvasRef.current = null;
      }
    };
  }, []);

  // Expose the paint function and canvas ref for the preview system
  return { canvasRef, schedulePaint, pointToPixel };
}
