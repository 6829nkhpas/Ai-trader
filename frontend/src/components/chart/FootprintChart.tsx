'use client';

import React, { useEffect, useRef, useState, useMemo } from 'react';
import { useTradeStore, type OhlcCandle, type OrderFlowTick } from '../../store/useTradeStore';
import { useHistoricalData } from '../../hooks/useHistoricalData';
import { RANGE_DAYS, KITE_INTERVAL_MAP, TIMEFRAME_MS, type Timeframe } from '../../utils/chartTypes';
import { aggregateCandles } from '../../utils/chartAggregation';

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
  const [zoomX, setZoomX] = useState(85); // Width of a candle column (pixels)
  const [zoomY, setZoomY] = useState(22); // Height of a price row (pixels)
  const [scrollX, setScrollX] = useState(0); // Offset in pixels from the right edge
  const [scrollY, setScrollY] = useState(0); // Offset in pixels from the center price

  // Keep track of drag state
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

  // Aggregate candles to respect custom timeframes
  const { candles: chartData, volumes: volumeData } = useMemo(
    () => aggregateCandles(mergedCandles, effectiveTimeframe, activeSymbol),
    [mergedCandles, effectiveTimeframe, activeSymbol]
  );

  // Timeframe interval size in ms
  const intervalMs = useMemo(() => {
    return TIMEFRAME_MS[effectiveTimeframe] || 60000;
  }, [effectiveTimeframe]);

  // ── Auto-center Y-axis on latest close on first load ───────────────────
  const initialCenterSet = useRef<string | null>(null);
  useEffect(() => {
    if (chartData.length > 0 && initialCenterSet.current !== activeSymbol) {
      const latestPrice = chartData[chartData.length - 1].close;
      setScrollY(latestPrice);
      setScrollX(0); // reset scroll to show latest candles
      initialCenterSet.current = activeSymbol;
    }
  }, [chartData, activeSymbol]);

  // ── Group Real-Time L2 Order Flow Data by Candle & Price Level ─────────
  const footprintData = useMemo(() => {
    const grouped = new Map<string, { bid: number; ask: number; delta: number }>();
    const tickSize = 0.05;

    // Track which candle timestamps have live data
    const activeLiveCandles = new Set<number>();

    orderFlowData.forEach((tick) => {
      const candleTime = Math.floor(tick.timestamp / intervalMs) * intervalMs;
      activeLiveCandles.add(candleTime);
      
      const key = `${candleTime}_${tick.price_level.toFixed(2)}`;
      const current = grouped.get(key) || { bid: 0, ask: 0, delta: 0 };
      current.bid += tick.bid_volume;
      current.ask += tick.ask_volume;
      current.delta += tick.delta;

      grouped.set(key, current);
    });

    // Populate historical candle footprints with bell-curve volume distribution
    chartData.forEach((candle) => {
      const candleTime = candle.time * 1000;
      if (activeLiveCandles.has(candleTime)) return;

      const candleStartTick = Math.round(candle.low / tickSize) * tickSize;
      const candleEndTick = Math.round(candle.high / tickSize) * tickSize;
      const levelsCount = Math.max(1, Math.round((candleEndTick - candleStartTick) / tickSize) + 1);
      
      const volBar = volumeData.find((v) => v.time === candle.time);
      const totalVol = volBar ? volBar.value : 1000;
      const baseVolPerLevel = totalVol / levelsCount;

      const midPrice = (candle.high + candle.low) / 2;

      for (let pr = candleStartTick; pr <= candleEndTick; pr += tickSize) {
        const key = `${candleTime}_${pr.toFixed(2)}`;
        
        const dist = Math.abs(pr - midPrice) / Math.max(tickSize, candle.high - candle.low);
        const weight = Math.max(0.3, 1.2 * (1.0 - dist));
        const levelVol = baseVolPerLevel * weight;

        const isBullish = candle.close >= candle.open;
        const bidRatio = isBullish ? 0.48 : 0.52;
        const askRatio = 1 - bidRatio;

        grouped.set(key, {
          bid: Math.round(levelVol * bidRatio),
          ask: Math.round(levelVol * askRatio),
          delta: Math.round(levelVol * (askRatio - bidRatio)),
        });
      }
    });

    return grouped;
  }, [orderFlowData, chartData, volumeData, intervalMs]);

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
    return () => {
      resizeObserver.disconnect();
    };
  }, []);

  // ── requestAnimationFrame Drawing Loop using State Refs ────────────────
  const stateRef = useRef({
    scrollX,
    scrollY,
    zoomX,
    zoomY,
    chartData,
    orderFlowData,
    footprintData,
    histLoading,
    activeSymbol,
    dimensions,
  });

  stateRef.current = {
    scrollX,
    scrollY,
    zoomX,
    zoomY,
    chartData,
    orderFlowData,
    footprintData,
    histLoading,
    activeSymbol,
    dimensions,
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const render = () => {
      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      const {
        scrollX: sX,
        scrollY: sY,
        zoomX: zX,
        zoomY: zY,
        chartData: cData,
        orderFlowData: ofData,
        footprintData: fpData,
        histLoading: isLoading,
        activeSymbol: symbol,
        dimensions: dims,
      } = stateRef.current;

      // Supersample by 2x the standard DPR to deliver 4K crystal-clear rendering
      const dpr = (window.devicePixelRatio || 1) * 2;
      const expectedWidth = Math.floor(dims.width * dpr);
      const expectedHeight = Math.floor(dims.height * dpr);

      if (canvas.width !== expectedWidth || canvas.height !== expectedHeight) {
        canvas.width = expectedWidth;
        canvas.height = expectedHeight;
      }

      // Always reset transform to prevent accumulation across frames
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const width = dims.width;
      const height = dims.height;

      // Draw background
      ctx.fillStyle = '#0f172a'; // slate-900 canvas bg
      ctx.fillRect(0, 0, width, height);

      if (cData.length === 0) {
        ctx.fillStyle = 'rgba(255, 255, 255, 0.4)';
        ctx.font = '12px Inter, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(
          isLoading
            ? `Loading historical candles for ${symbol}...`
            : `Waiting for candle data...`,
          width / 2,
          height / 2
        );
        return;
      }

      // Grid margins
      const rightMargin = 65;
      const bottomMargin = 25;

      const chartWidth = width - rightMargin;
      const chartHeight = height - bottomMargin;

      const tickSize = 0.05;

      const priceToY = (price: number) => {
        const priceDiff = sY - price;
        // Round to nearest integer to align perfectly with physical screen pixels
        return Math.round(chartHeight / 2 + (priceDiff / tickSize) * zY);
      };

      const yToPrice = (y: number) => {
        const yDiff = chartHeight / 2 - y;
        const ticks = yDiff / zY;
        return sY + ticks * tickSize;
      };

      // Draw price grid lines & labels
      const minPriceVisible = yToPrice(chartHeight);
      const maxPriceVisible = yToPrice(0);

      const startPrice = Math.floor(minPriceVisible / tickSize) * tickSize;
      const endPrice = Math.ceil(maxPriceVisible / tickSize) * tickSize;

      ctx.strokeStyle = 'rgba(51, 65, 85, 0.15)';
      ctx.lineWidth = 1;
      ctx.font = '9px monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';

      for (let pr = startPrice; pr <= endPrice; pr += tickSize) {
        const y = priceToY(pr);
        if (y >= 0 && y <= chartHeight) {
          ctx.beginPath();
          ctx.moveTo(0, y);
          ctx.lineTo(chartWidth, y);
          ctx.stroke();

          ctx.fillStyle = 'rgba(255, 255, 255, 0.3)';
          ctx.fillText(pr.toFixed(2), chartWidth + 32, y);
        }
      }

      // Draw columns representing time periods from right to left
      let currentX = Math.round(chartWidth - sX);

      for (let i = cData.length - 1; i >= 0; i--) {
        const candle = cData[i];
        const nextX = Math.round(currentX - zX);

        if (currentX < 0) break;
        if (nextX > chartWidth) {
          currentX = nextX;
          continue;
        }

        // Draw vertical divider
        ctx.strokeStyle = 'rgba(51, 65, 85, 0.15)';
        ctx.beginPath();
        ctx.moveTo(currentX, 0);
        ctx.lineTo(currentX, chartHeight);
        ctx.stroke();

        const yOpen = priceToY(candle.open);
        const yClose = priceToY(candle.close);
        const yHigh = priceToY(candle.high);
        const yLow = priceToY(candle.low);
        const isBullish = candle.close >= candle.open;

        // Draw wicks
        ctx.strokeStyle = isBullish ? 'rgba(34, 197, 94, 0.35)' : 'rgba(239, 68, 68, 0.35)';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(nextX + zX / 2, yHigh);
        ctx.lineTo(nextX + zX / 2, yLow);
        ctx.stroke();

        // Draw candle body outline box
        ctx.strokeStyle = isBullish ? 'rgba(34, 197, 94, 0.55)' : 'rgba(239, 68, 68, 0.55)';
        ctx.lineWidth = 2;
        const rectY = Math.min(yOpen, yClose);
        const rectH = Math.max(2, Math.abs(yOpen - yClose));
        ctx.strokeRect(nextX + 2, rectY, zX - 4, rectH);

        // Find internal POC of this specific candle (highest volume price level)
        let maxCandleVol = 0;
        let candlePocPrice = -1;

        const candleStartTick = Math.round(candle.low / tickSize) * tickSize;
        const candleEndTick = Math.round(candle.high / tickSize) * tickSize;

        for (let pr = candleStartTick; pr <= candleEndTick; pr += tickSize) {
          const key = `${candle.time * 1000}_${pr.toFixed(2)}`;
          const tickVal = fpData.get(key);
          if (tickVal) {
            const tot = tickVal.bid + tickVal.ask;
            if (tot > maxCandleVol) {
              maxCandleVol = tot;
              candlePocPrice = pr;
            }
          }
        }

        // Draw footprint cells
        for (let pr = candleStartTick; pr <= candleEndTick; pr += tickSize) {
          const yTop = priceToY(pr + tickSize);
          const yBottom = priceToY(pr);

          if (yBottom < 0 || yTop > chartHeight) continue;

          const cellH = Math.abs(yBottom - yTop);
          const key = `${candle.time * 1000}_${pr.toFixed(2)}`;
          const tickVal = fpData.get(key);

          if (tickVal) {
            const { bid, ask, delta } = tickVal;
            const totalVol = bid + ask;

            // Color gradient scaling
            const maxImbalance = 1000;
            const intensity = Math.min(1.0, Math.abs(delta) / Math.max(1, totalVol, maxImbalance));

            // Draw translucent green/blue gradient or red gradient based on delta imbalance
            const grad = ctx.createLinearGradient(nextX + 1, yTop + 1, nextX + zX - 1, yTop + cellH - 1);
            if (delta > 0) {
              // Aggressive Buyers: emerald green to sky blue
              grad.addColorStop(0, `rgba(16, 185, 129, ${0.12 + intensity * 0.4})`);
              grad.addColorStop(1, `rgba(14, 165, 233, ${0.05 + intensity * 0.3})`);
            } else if (delta < 0) {
              // Aggressive Sellers: rose red to dark red
              grad.addColorStop(0, `rgba(239, 68, 68, ${0.12 + intensity * 0.4})`);
              grad.addColorStop(1, `rgba(185, 28, 28, ${0.05 + intensity * 0.3})`);
            } else {
              grad.addColorStop(0, 'rgba(100, 116, 139, 0.1)');
              grad.addColorStop(1, 'rgba(100, 116, 139, 0.05)');
            }

            ctx.fillStyle = grad;
            ctx.fillRect(nextX + 1, yTop + 1, zX - 2, cellH - 2);

            // Highlight the internal POC of each specific candle with a bold border
            if (pr === candlePocPrice) {
              ctx.strokeStyle = '#f59e0b'; // Bold amber POC border
              ctx.lineWidth = 1.5;
              ctx.strokeRect(nextX + 1.5, yTop + 1.5, zX - 3, cellH - 3);
            }

            // Draw Bid x Ask text
            if (zX > 55 && zY >= 14) {
              ctx.fillStyle = '#ffffff';
              ctx.font = `${Math.min(9, zY - 5)}px monospace`;
              ctx.textAlign = 'center';
              ctx.textBaseline = 'middle';

              const bidStr = bid >= 1000 ? `${(bid / 1000).toFixed(1)}k` : bid.toString();
              const askStr = ask >= 1000 ? `${(ask / 1000).toFixed(1)}k` : ask.toString();
              ctx.fillText(`${bidStr}x${askStr}`, nextX + zX / 2, yTop + cellH / 2);
            }
          }
        }

        // Draw time scale timestamps at the bottom
        const timeDate = new Date(candle.time * 1000);
        const timeStr = timeDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

        ctx.fillStyle = 'rgba(255, 255, 255, 0.25)';
        ctx.font = '8px monospace';
        ctx.textAlign = 'center';
        ctx.fillText(timeStr, nextX + zX / 2, chartHeight + 15);

        currentX = nextX;
      }

      // Draw right margin axis dividers
      ctx.strokeStyle = 'rgba(51, 65, 85, 0.3)';
      ctx.beginPath();
      ctx.moveTo(chartWidth, 0);
      ctx.lineTo(chartWidth, height);
      ctx.moveTo(0, chartHeight);
      ctx.lineTo(width, chartHeight);
      ctx.stroke();

      // Historical data fallback ensures candles render even when L2 stream is starting.
    };

    const loop = () => {
      render();
      rafIdRef.current = requestAnimationFrame(loop);
    };

    rafIdRef.current = requestAnimationFrame(loop);

    return () => {
      if (rafIdRef.current) {
        cancelAnimationFrame(rafIdRef.current);
      }
    };
  }, []);

  // ── Drag to Scroll Event Handlers ─────────────────────────────────────
  const handleMouseDown = (e: React.MouseEvent) => {
    isDragging.current = true;
    dragStart.current = {
      x: e.clientX,
      y: e.clientY,
      scrollX: scrollX,
      scrollY: scrollY,
    };
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging.current) return;

    const deltaX = e.clientX - dragStart.current.x;
    const deltaY = e.clientY - dragStart.current.y;

    setScrollX(Math.max(0, dragStart.current.scrollX - deltaX));

    const tickStep = 0.05;
    const priceDelta = (deltaY / zoomY) * tickStep;
    setScrollY(dragStart.current.scrollY + priceDelta);
  };

  const handleMouseUp = () => {
    isDragging.current = false;
  };

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();

    if (e.shiftKey) {
      const factor = e.deltaY > 0 ? 0.9 : 1.1;
      setZoomY((prev) => Math.min(80, Math.max(10, Math.round(prev * factor))));
    } else {
      const factor = e.deltaY > 0 ? 0.9 : 1.1;
      setZoomX((prev) => Math.min(250, Math.max(40, Math.round(prev * factor))));
    }
  };

  return (
    <div
      ref={containerRef}
      className="relative flex h-full w-full select-none overflow-hidden bg-slate-950"
    >
      <canvas
        ref={canvasRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onWheel={handleWheel}
        style={{ width: dimensions.width, height: dimensions.height }}
        className="cursor-grab active:cursor-grabbing"
      />
    </div>
  );
}
