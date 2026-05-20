import { useEffect, useRef } from 'react';
import type { IChartApi, ISeriesApi } from 'lightweight-charts';
import {
  createChart,
  ColorType,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  CrosshairMode,
  LineStyle,
} from 'lightweight-charts';
import { COLORS } from '../utils/chartTypes';
import type { ChartRefs } from '../utils/chartTypes';

/**
 * useChartInit — Creates the chart instance, all series, and a ResizeObserver.
 * Returns a ChartRefs object consumed by all other chart hooks.
 */
export function useChartInit(
  containerRef: React.RefObject<HTMLDivElement | null>,
): ChartRefs {
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const ghostLineRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ema9SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const ema21SeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const drawingSeriesRef = useRef<ISeriesApi<'Line'>[]>([]);
  const fibOverlayRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
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
        rightOffset: 10,               // Breathing room after the latest candle
        fixLeftEdge: false,             // Allow free scrolling (fixLeftEdge locks to oldest candle)
        fixRightEdge: false,
        shiftVisibleRangeOnNewBar: true, // Auto-scroll right when new live candle arrives
        barSpacing: 8,
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight || 400,
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
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 }, // BUG-9: was 0.05, too coarse for sub-₹100 stocks
    });

    // ── Volume Histogram ───────────────────────────────────────────────
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

    // ── EMA 9 (cyan — fast) ────────────────────────────────────────────
    const ema9Series = chart.addSeries(LineSeries, {
      color: COLORS.ema9, lineWidth: 2,
      crosshairMarkerVisible: false, priceLineVisible: false, lastValueVisible: false,
    });

    // ── EMA 21 (pink — slow) ───────────────────────────────────────────
    const ema21Series = chart.addSeries(LineSeries, {
      color: COLORS.ema21, lineWidth: 2,
      crosshairMarkerVisible: false, priceLineVisible: false, lastValueVisible: false,
    });

    // ── Ghost Line (predictive — amber dashed) ─────────────────────────
    const ghostLine = chart.addSeries(LineSeries, {
      color: COLORS.ghostLine, lineWidth: 3, lineStyle: LineStyle.Dashed,
      crosshairMarkerVisible: true, crosshairMarkerRadius: 5,
      priceLineVisible: false, lastValueVisible: true, title: '▲ Proj',
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    volumeSeriesRef.current = volumeSeries;
    ema9SeriesRef.current = ema9Series;
    ema21SeriesRef.current = ema21Series;
    ghostLineRef.current = ghostLine;

    // ── Responsive resize ──────────────────────────────────────────────
    const resizeObserver = new ResizeObserver(() => {
      if (containerRef.current) {
        const { width, height } = containerRef.current.getBoundingClientRect();
        chart.resize(Math.floor(width), Math.floor(height));
      }
    });
    resizeObserver.observe(containerRef.current);

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

  return {
    chartRef, candleSeriesRef, volumeSeriesRef,
    ghostLineRef, ema9SeriesRef, ema21SeriesRef,
    chartContainerRef: containerRef, drawingSeriesRef, fibOverlayRef,
  };
}
