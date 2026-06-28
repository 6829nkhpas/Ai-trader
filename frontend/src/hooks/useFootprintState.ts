import { useState, useEffect, useRef, useMemo } from 'react';
import { useTradeStore, type OhlcCandle } from '../store/useTradeStore';
import { useHistoricalData } from './useHistoricalData';
import { KITE_INTERVAL_MAP, type Timeframe } from '../utils/chartTypes';
import { aggregateCandles } from '../utils/chartAggregation';
import { buildFootprint, cumulativeDelta, type FootprintCandle } from '../charting/engines';

// ── Adaptive Tick Size ──────────────────────────────────────────────────────
function autoTickSize(avgPrice: number): number {
  if (avgPrice > 5000) return 5.0;
  if (avgPrice > 2000) return 2.0;
  if (avgPrice > 1000) return 1.0;
  if (avgPrice > 500) return 0.5;
  if (avgPrice > 100) return 0.25;
  if (avgPrice > 20) return 0.1;
  return 0.05;
}

export function useFootprintState(timeframe: string) {
  const activeSymbol = useTradeStore((s) => s.selectedSymbol || 'RELIANCE');
  const ohlcCandles = useTradeStore((s) => s.ohlcCandles);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);
  const orderFlowData = useTradeStore((s) => s.orderFlowData);

  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // ── Pan and Zoom State ────────────────────────────────────────────────
  const [zoomX, setZoomX] = useState(120); // column width px
  const [zoomY, setZoomY] = useState(24);  // row height px
  const [scrollX, setScrollX] = useState(0);
  const [scrollY, setScrollY] = useState(0);

  const isDragging = useRef(false);
  const dragStart = useRef({ x: 0, y: 0, scrollX: 0, scrollY: 0 });

  // ── High-DPI ResizeObserver ──────────────────────────────────────────
  const [dimensions, setDimensions] = useState({ width: 600, height: 400 });

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const resizeObserver = new ResizeObserver((entries) => {
      if (!entries || entries.length === 0) return;
      const { width, height } = entries[0].contentRect;
      setDimensions({ width, height });
    });

    resizeObserver.observe(container);
    return () => resizeObserver.disconnect();
  }, []);

  // ── Fetch Historical and Merge Live Candles ───────────────────────────
  const effectiveTimeframe = (activeTimeframe as Timeframe) ?? timeframe;
  
  const rangeDays = useMemo(() => {
    const tfStr = effectiveTimeframe as string;
    if (tfStr === '1d') return 60;
    if (tfStr === '1h' || tfStr === '4h') return 15;
    return 3;
  }, [effectiveTimeframe]);

  const kiteInterval = KITE_INTERVAL_MAP[effectiveTimeframe] ?? '10minute';
  const { candles: historicalCandles, loading: histLoading } = useHistoricalData(
    activeSymbol, rangeDays, kiteInterval, effectiveTimeframe
  );

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

    return Array.from(candleMap.values()).sort((a, b) => a.start_timestamp_ms - b.start_timestamp_ms);
  }, [historicalCandles, ohlcCandles, activeSymbol]);

  const { candles: chartDataRaw } = useMemo(
    () => aggregateCandles(mergedCandles, effectiveTimeframe, activeSymbol),
    [mergedCandles, effectiveTimeframe, activeSymbol]
  );

  const chartData = useMemo(() => {
    const limit = 150;
    if (chartDataRaw.length <= limit) return chartDataRaw;
    return chartDataRaw.slice(chartDataRaw.length - limit);
  }, [chartDataRaw]);


  // ── Auto-center Y-axis on latest close on first load ───────────────────
  const initialCenterSet = useRef<string | null>(null);
  useEffect(() => {
    if (chartData.length > 0 && initialCenterSet.current !== activeSymbol) {
      const latestPrice = chartData[chartData.length - 1].close;
      setScrollY(latestPrice);
      setScrollX(0);
      initialCenterSet.current = activeSymbol;
    }
  }, [chartData, activeSymbol]);

  // ── Compute dynamic tick size from current price ───────────────────────
  const tickSize = useMemo(() => {
    if (chartData.length === 0) return 1.0;
    const avgPrice = chartData[chartData.length - 1].close;
    return autoTickSize(avgPrice);
  }, [chartData]);

  // ── Build Footprint Candles via the pure FootprintEngine ──────────────
  const footprintCandles = useMemo<FootprintCandle[]>(
    () => buildFootprint(chartData, orderFlowData, { tickSize }),
    [chartData, orderFlowData, tickSize]
  );

  const cumDeltas = useMemo(() => cumulativeDelta(footprintCandles), [footprintCandles]);

  const fpByTime = useMemo(() => {
    const m = new Map<number, { fp: FootprintCandle; cumDelta: number }>();
    footprintCandles.forEach((fp, i) => {
      m.set(fp.time, { fp, cumDelta: cumDeltas[i] ?? 0 });
    });
    return m;
  }, [footprintCandles, cumDeltas]);

  // ── Drag and Scroll Handlers ──────────────────────────────────────────
  const handleMouseDown = (e: React.MouseEvent) => {
    isDragging.current = true;
    dragStart.current = { x: e.clientX, y: e.clientY, scrollX, scrollY };
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging.current) return;
    const deltaX = e.clientX - dragStart.current.x;
    const deltaY = e.clientY - dragStart.current.y;

    const rightMargin = 72;
    const chartWidth = dimensions.width - rightMargin;
    const maxScrollX = Math.max(0, chartData.length * zoomX - chartWidth);

    setScrollX(Math.min(maxScrollX, Math.max(0, dragStart.current.scrollX - deltaX)));
    const priceDelta = (deltaY / zoomY) * tickSize;
    setScrollY(dragStart.current.scrollY + priceDelta);
  };

  const handleMouseUp = () => {
    isDragging.current = false;
  };

  // ── Native non-passive Wheel Event Listener ────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const handleNativeWheel = (e: WheelEvent) => {
      e.preventDefault();
      e.stopPropagation();

      const factor = e.deltaY > 0 ? 0.9 : 1.1;
      if (e.shiftKey) {
        setZoomY((prev) => Math.min(80, Math.max(12, Math.round(prev * factor))));
      } else {
        setZoomX((prev) => Math.min(300, Math.max(60, Math.round(prev * factor))));
      }
    };

    canvas.addEventListener('wheel', handleNativeWheel, { passive: false });
    return () => {
      canvas.removeEventListener('wheel', handleNativeWheel);
    };
  }, [setZoomX, setZoomY]);

  return {
    zoomX, setZoomX,
    zoomY, setZoomY,
    scrollX, setScrollX,
    scrollY, setScrollY,
    dimensions, setDimensions,
    chartData,
    tickSize,
    fpByTime,
    histLoading,
    activeSymbol,
    handleMouseDown,
    handleMouseMove,
    handleMouseUp,
    containerRef,
    canvasRef
  };
}

export function fmtVol(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 10_000) return `${(v / 1000).toFixed(0)}k`;
  if (v >= 1_000) return `${(v / 1000).toFixed(1)}k`;
  return v.toString();
}

export function fmtDelta(v: number): string {
  const sign = v > 0 ? '+' : v < 0 ? '-' : '';
  return `${sign}${fmtVol(Math.abs(v))}`;
}
