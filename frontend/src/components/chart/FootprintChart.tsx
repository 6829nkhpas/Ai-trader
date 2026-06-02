'use client';

import React, { useEffect, useRef, useState, useMemo } from 'react';
import { useTradeStore, type OhlcCandle, type OrderFlowTick } from '../../store/useTradeStore';
import { useHistoricalData } from '../../hooks/useHistoricalData';
import { RANGE_DAYS, KITE_INTERVAL_MAP, TIMEFRAME_MS, type Timeframe } from '../../utils/chartTypes';
import { aggregateCandles } from '../../utils/chartAggregation';

// ── Adaptive Tick Size ──────────────────────────────────────────────────────
// Returns a tick increment that produces ~8-18 price rows per candle, keeping
// the text readable regardless of the instrument's price level.
function autoTickSize(avgPrice: number): number {
  if (avgPrice > 5000) return 5.0;
  if (avgPrice > 2000) return 2.0;
  if (avgPrice > 1000) return 1.0;
  if (avgPrice > 500) return 0.5;
  if (avgPrice > 100) return 0.25;
  if (avgPrice > 20) return 0.1;
  return 0.05;
}

// ── Volume Formatter ────────────────────────────────────────────────────────
function fmtVol(v: number): string {
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 10_000) return `${(v / 1000).toFixed(0)}k`;
  if (v >= 1_000) return `${(v / 1000).toFixed(1)}k`;
  return v.toString();
}

export default function FootprintChart({
  timeframe = '1m',
}: {
  activeProfile?: string;
  timeframe?: string;
  isExpanded?: boolean;
  onToggleExpand?: () => void;
}) {
  const activeSymbol = useTradeStore((s) => s.selectedSymbol || 'RELIANCE');
  const ohlcCandles = useTradeStore((s) => s.ohlcCandles);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);
  const activeRange = useTradeStore((s) => s.activeRange);
  const orderFlowData = useTradeStore((s) => s.orderFlowData);

  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafIdRef = useRef<number | null>(null);

  // ── Pan and Zoom State ────────────────────────────────────────────────
  const [zoomX, setZoomX] = useState(120); // column width px
  const [zoomY, setZoomY] = useState(24);  // row height px
  const [scrollX, setScrollX] = useState(0);
  const [scrollY, setScrollY] = useState(0);

  const isDragging = useRef(false);
  const dragStart = useRef({ x: 0, y: 0, scrollX: 0, scrollY: 0 });

  // ── Fetch Historical and Merge Live Candles ───────────────────────────
  const effectiveTimeframe = (activeTimeframe as Timeframe) ?? timeframe;
  const rangeDays = RANGE_DAYS[activeRange] ?? 365;
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

  const { candles: chartData, volumes: volumeData } = useMemo(
    () => aggregateCandles(mergedCandles, effectiveTimeframe, activeSymbol),
    [mergedCandles, effectiveTimeframe, activeSymbol]
  );

  const intervalMs = useMemo(() => TIMEFRAME_MS[effectiveTimeframe] || 60000, [effectiveTimeframe]);

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

  // ── Build Volume Map for O(1) Lookup ──────────────────────────────────
  const volMap = useMemo(() => {
    const m = new Map<number, number>();
    for (const v of volumeData) m.set(v.time, v.value);
    return m;
  }, [volumeData]);

  // ── Group Real-Time L2 + Historical Volume by Candle & Price Level ─────
  const footprintData = useMemo(() => {
    const grouped = new Map<string, { bid: number; ask: number; delta: number }>();

    // Track candle timestamps that have real L2 data
    const activeLiveCandles = new Set<number>();

    orderFlowData.forEach((tick) => {
      const candleTime = Math.floor(tick.timestamp / intervalMs) * intervalMs;
      activeLiveCandles.add(candleTime);

      const roundedPrice = Math.round(tick.price_level / tickSize) * tickSize;
      const key = `${candleTime}_${roundedPrice.toFixed(2)}`;
      const current = grouped.get(key) || { bid: 0, ask: 0, delta: 0 };
      current.bid += tick.bid_volume;
      current.ask += tick.ask_volume;
      current.delta += tick.delta;
      grouped.set(key, current);
    });

    // Historical fallback: distribute candle volume with a bell-curve weight
    chartData.forEach((candle, idx) => {
      const candleTime = candle.time * 1000;
      if (activeLiveCandles.has(candleTime)) return;

      const startTick = Math.round(candle.low / tickSize) * tickSize;
      const endTick = Math.round(candle.high / tickSize) * tickSize;
      const levelsCount = Math.max(1, Math.round((endTick - startTick) / tickSize) + 1);

      // Use Map for O(1) volume lookup, with a generous fallback
      const totalVol = volMap.get(candle.time) || 1000;
      const baseVolPerLevel = totalVol / levelsCount;
      const midPrice = (candle.high + candle.low) / 2;
      const priceRange = Math.max(tickSize, candle.high - candle.low);
      const isBullish = candle.close >= candle.open;

      for (let pr = startTick; pr <= endTick + tickSize * 0.01; pr += tickSize) {
        const rounded = Math.round(pr / tickSize) * tickSize;
        const key = `${candleTime}_${rounded.toFixed(2)}`;

        const dist = Math.abs(rounded - midPrice) / priceRange;
        const weight = Math.max(0.25, 1.3 * (1.0 - dist));
        const levelVol = baseVolPerLevel * weight;

        const bidRatio = isBullish ? 0.45 : 0.55;
        const askRatio = 1 - bidRatio;

        grouped.set(key, {
          bid: Math.round(levelVol * bidRatio),
          ask: Math.round(levelVol * askRatio),
          delta: Math.round(levelVol * (askRatio - bidRatio)),
        });
      }
    });

    return grouped;
  }, [orderFlowData, chartData, volMap, intervalMs, tickSize]);

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

  // ── requestAnimationFrame Drawing Loop ────────────────────────────────
  const stateRef = useRef({
    scrollX, scrollY, zoomX, zoomY,
    chartData, orderFlowData, footprintData,
    histLoading, activeSymbol, dimensions, tickSize,
  });

  stateRef.current = {
    scrollX, scrollY, zoomX, zoomY,
    chartData, orderFlowData, footprintData,
    histLoading, activeSymbol, dimensions, tickSize,
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const render = () => {
      const ctx = canvas.getContext('2d', { alpha: false });
      if (!ctx) return;

      const {
        scrollX: sX, scrollY: sY, zoomX: zX, zoomY: zY,
        chartData: cData, footprintData: fpData,
        histLoading: isLoading, activeSymbol: symbol,
        dimensions: dims, tickSize: ts,
      } = stateRef.current;

      // ── 4K Supersampled Resolution ────────────────────────────────────
      // Floor of 2x ensures 1080p CSS → 2160p buffer (true 4K rendering).
      // On HiDPI screens (e.g. 2x Retina) it stays native; on 1x screens
      // it supersamples to 2x for razor-sharp text and lines.
      const dpr = Math.max(window.devicePixelRatio || 1, 2);
      const bw = Math.round(dims.width * dpr);
      const bh = Math.round(dims.height * dpr);

      if (canvas.width !== bw || canvas.height !== bh) {
        canvas.width = bw;
        canvas.height = bh;
      }

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';

      const width = dims.width;
      const height = dims.height;

      // ── Background ────────────────────────────────────────────────────
      ctx.fillStyle = '#0a0f1e';
      ctx.fillRect(0, 0, width, height);

      if (cData.length === 0) {
        ctx.fillStyle = 'rgba(255, 255, 255, 0.4)';
        ctx.font = '500 13px "Inter", sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(
          isLoading ? `Loading ${symbol} candles…` : 'Waiting for candle data…',
          width / 2, height / 2
        );
        return;
      }

      // ── Layout ────────────────────────────────────────────────────────
      const rightMargin = 72;
      const bottomMargin = 28;
      const chartWidth = width - rightMargin;
      const chartHeight = height - bottomMargin;

      // ── Coordinate Mapping ────────────────────────────────────────────
      const priceToY = (price: number) =>
        Math.round(chartHeight / 2 + ((sY - price) / ts) * zY);

      const yToPrice = (y: number) =>
        sY + ((chartHeight / 2 - y) / zY) * ts;

      // ── Price Grid ────────────────────────────────────────────────────
      const minPriceVisible = yToPrice(chartHeight);
      const maxPriceVisible = yToPrice(0);
      const gridStart = Math.floor(minPriceVisible / ts) * ts;
      const gridEnd = Math.ceil(maxPriceVisible / ts) * ts;

      ctx.lineWidth = 1;
      for (let pr = gridStart; pr <= gridEnd; pr += ts) {
        const y = priceToY(pr) + 0.5;
        if (y < 0 || y > chartHeight) continue;

        ctx.strokeStyle = 'rgba(30, 41, 59, 0.5)';
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(chartWidth, y);
        ctx.stroke();

        // Price labels on right axis
        ctx.fillStyle = 'rgba(148, 163, 184, 0.6)';
        ctx.font = '10px "JetBrains Mono", "Fira Code", monospace';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillText(pr.toFixed(2), width - 6, y);
      }

      // ── Candle Columns (right to left) ────────────────────────────────
      let currentX = Math.round(chartWidth - sX);

      for (let i = cData.length - 1; i >= 0; i--) {
        const candle = cData[i];
        const nextX = Math.round(currentX - zX);

        if (currentX < 0) break;
        if (nextX > chartWidth) { currentX = nextX; continue; }

        const colLeft = Math.max(0, nextX);
        const colRight = Math.min(chartWidth, currentX);
        const colW = colRight - colLeft;

        // Column separator
        ctx.strokeStyle = 'rgba(30, 41, 59, 0.4)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(currentX + 0.5, 0);
        ctx.lineTo(currentX + 0.5, chartHeight);
        ctx.stroke();

        const yOpen = priceToY(candle.open);
        const yClose = priceToY(candle.close);
        const yHigh = priceToY(candle.high);
        const yLow = priceToY(candle.low);
        const isBullish = candle.close >= candle.open;
        const bullColor = '#22c55e'; // green-500
        const bearColor = '#ef4444'; // red-500

        // Wick line
        ctx.strokeStyle = isBullish
          ? 'rgba(34, 197, 94, 0.3)'
          : 'rgba(239, 68, 68, 0.3)';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(colLeft + colW / 2 + 0.5, yHigh);
        ctx.lineTo(colLeft + colW / 2 + 0.5, yLow);
        ctx.stroke();

        // ── Find Per-Candle POC ─────────────────────────────────────────
        let maxCandleVol = 0;
        let candlePocPrice = -1;
        const startTick = Math.round(candle.low / ts) * ts;
        const endTick = Math.round(candle.high / ts) * ts;
        const candleTimeMs = candle.time * 1000;

        for (let pr = startTick; pr <= endTick + ts * 0.01; pr += ts) {
          const rounded = Math.round(pr / ts) * ts;
          const key = `${candleTimeMs}_${rounded.toFixed(2)}`;
          const tv = fpData.get(key);
          if (tv) {
            const tot = tv.bid + tv.ask;
            if (tot > maxCandleVol) { maxCandleVol = tot; candlePocPrice = rounded; }
          }
        }

        // ── Draw Footprint Cells ────────────────────────────────────────
        for (let pr = startTick; pr <= endTick + ts * 0.01; pr += ts) {
          const rounded = Math.round(pr / ts) * ts;
          const yTop = priceToY(rounded + ts);
          const yBot = priceToY(rounded);
          if (yBot < -zY || yTop > chartHeight + zY) continue;

          const cellH = Math.max(1, Math.abs(yBot - yTop));
          const key = `${candleTimeMs}_${rounded.toFixed(2)}`;
          const tv = fpData.get(key);

          if (!tv || (tv.bid === 0 && tv.ask === 0)) continue;

          const { bid, ask, delta } = tv;
          const totalVol = bid + ask;

          // ── Cell Background ───────────────────────────────────────────
          const intensity = Math.min(1.0, Math.abs(delta) / Math.max(100, totalVol));

          if (delta > 0) {
            ctx.fillStyle = `rgba(16, 185, 129, ${0.06 + intensity * 0.35})`;
          } else if (delta < 0) {
            ctx.fillStyle = `rgba(239, 68, 68, ${0.06 + intensity * 0.35})`;
          } else {
            ctx.fillStyle = 'rgba(51, 65, 85, 0.08)';
          }
          ctx.fillRect(colLeft + 1, yTop + 1, colW - 2, cellH - 2);

          // ── POC Highlight ─────────────────────────────────────────────
          if (rounded === candlePocPrice) {
            ctx.strokeStyle = '#f59e0b';
            ctx.lineWidth = 2;
            ctx.strokeRect(colLeft + 1.5, yTop + 1.5, colW - 3, cellH - 3);
          }

          // ── Bid x Ask Text ────────────────────────────────────────────
          if (colW > 50 && cellH >= 12) {
            const fontSize = Math.round(Math.min(11, Math.max(8, cellH - 6)));
            ctx.font = `600 ${fontSize}px "JetBrains Mono", "Fira Code", monospace`;
            ctx.textBaseline = 'middle';
            const yMid = Math.round(yTop + cellH / 2);

            const bidStr = fmtVol(bid);
            const askStr = fmtVol(ask);
            const xMid = Math.round(colLeft + colW / 2);

            // Bid on left side (green tint), Ask on right (red tint)
            ctx.textAlign = 'right';
            ctx.fillStyle = delta >= 0
              ? 'rgba(74, 222, 128, 0.9)'   // green-400
              : 'rgba(148, 163, 184, 0.65)'; // muted
            ctx.fillText(bidStr, xMid - 4, yMid);

            ctx.textAlign = 'center';
            ctx.fillStyle = 'rgba(100, 116, 139, 0.4)';
            ctx.fillText('×', xMid, yMid);

            ctx.textAlign = 'left';
            ctx.fillStyle = delta <= 0
              ? 'rgba(248, 113, 113, 0.9)'   // red-400
              : 'rgba(148, 163, 184, 0.65)'; // muted
            ctx.fillText(askStr, xMid + 4, yMid);
          }
        }

        // ── Candle Body Outline ─────────────────────────────────────────
        ctx.strokeStyle = isBullish
          ? 'rgba(34, 197, 94, 0.6)'
          : 'rgba(239, 68, 68, 0.6)';
        ctx.lineWidth = 2;
        const bodyTop = Math.min(yOpen, yClose);
        const bodyH = Math.max(2, Math.abs(yOpen - yClose));
        ctx.strokeRect(colLeft + 3, bodyTop, colW - 6, bodyH);

        // ── Time Label ──────────────────────────────────────────────────
        const timeDate = new Date(candle.time * 1000);
        const timeStr = timeDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        ctx.fillStyle = 'rgba(148, 163, 184, 0.45)';
        ctx.font = '9px "JetBrains Mono", "Fira Code", monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(timeStr, colLeft + colW / 2, chartHeight + 6);

        currentX = nextX;
      }

      // ── Axis Borders ──────────────────────────────────────────────────
      ctx.strokeStyle = 'rgba(51, 65, 85, 0.4)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(chartWidth + 0.5, 0);
      ctx.lineTo(chartWidth + 0.5, height);
      ctx.moveTo(0, chartHeight + 0.5);
      ctx.lineTo(width, chartHeight + 0.5);
      ctx.stroke();
    };

    const loop = () => {
      render();
      rafIdRef.current = requestAnimationFrame(loop);
    };

    rafIdRef.current = requestAnimationFrame(loop);

    return () => {
      if (rafIdRef.current) cancelAnimationFrame(rafIdRef.current);
    };
  }, []);

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

  // ── Drag to Scroll ────────────────────────────────────────────────────
  const handleMouseDown = (e: React.MouseEvent) => {
    isDragging.current = true;
    dragStart.current = { x: e.clientX, y: e.clientY, scrollX, scrollY };
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging.current) return;
    const deltaX = e.clientX - dragStart.current.x;
    const deltaY = e.clientY - dragStart.current.y;
    setScrollX(Math.max(0, dragStart.current.scrollX - deltaX));
    const priceDelta = (deltaY / zoomY) * stateRef.current.tickSize;
    setScrollY(dragStart.current.scrollY + priceDelta);
  };

  const handleMouseUp = () => { isDragging.current = false; };

  return (
    <div
      ref={containerRef}
      className="relative flex h-full w-full select-none overflow-hidden"
      style={{ background: '#0a0f1e' }}
    >
      <canvas
        ref={canvasRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        style={{ width: dimensions.width, height: dimensions.height }}
        className="cursor-grab active:cursor-grabbing"
      />
    </div>
  );
}
