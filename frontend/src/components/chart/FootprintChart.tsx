'use client';

import React, { useEffect, useRef, useState, useMemo } from 'react';
import { useTradeStore, type OhlcCandle } from '../../store/useTradeStore';
import { useHistoricalData } from '../../hooks/useHistoricalData';
import { KITE_INTERVAL_MAP, type Timeframe } from '../../utils/chartTypes';
import { aggregateCandles } from '../../utils/chartAggregation';
import { buildFootprint, cumulativeDelta, type FootprintCandle } from '../../charting/engines';

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

// Signed delta formatter for the per-candle footer (Requirement 6.8).
function fmtDelta(v: number): string {
  const sign = v > 0 ? '+' : v < 0 ? '-' : '';
  return `${sign}${fmtVol(Math.abs(v))}`;
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
  
  // For footprint chart, we only need a few days of history to cover the last 150 candles.
  // Requesting 365 days is extremely heavy and makes the chart slow/uncontrollable.
  const rangeDays = useMemo(() => {
    const tfStr = effectiveTimeframe as string;
    if (tfStr === '1d') return 60;
    if (tfStr === '1h' || tfStr === '4h') return 15;
    return 3; // 3 days for intraday footprint
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

  // Limit footprint chart to display only the most recent 150 candles
  // This drastically improves performance, keeps panning responsive,
  // and prevents loading/rendering excessive historical candles.
  const chartData = useMemo(() => {
    const limit = 150;
    if (chartDataRaw.length <= limit) return chartDataRaw;
    return chartDataRaw.slice(chartDataRaw.length - limit);
  }, [chartDataRaw]);

  // Clamp scrollX within valid bounds (0 to maxScrollX) whenever zoomX, chartData, or dimensions change
  useEffect(() => {
    const rightMargin = 72;
    const chartWidth = dimensions.width - rightMargin;
    const maxScrollX = Math.max(0, chartData.length * zoomX - chartWidth);
    if (scrollX > maxScrollX) {
      setScrollX(maxScrollX);
    }
  }, [zoomX, chartData, dimensions.width, scrollX]);

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
  // All aggregation (tick-size clustering, delta, POC, imbalance detection,
  // synthetic fallback) lives in the engine. The component only consumes its
  // output and draws it. Recomputes when the candle series, order-flow ticks,
  // or tick size change — satisfying regroup-on-tick-size-change (Req 6.9).
  const footprintCandles = useMemo<FootprintCandle[]>(
    () => buildFootprint(chartData, orderFlowData, { tickSize }),
    [chartData, orderFlowData, tickSize]
  );

  // Running Cumulative_Delta aligned to the footprint candle order (Req 6.5).
  const cumDeltas = useMemo(() => cumulativeDelta(footprintCandles), [footprintCandles]);

  // Index footprint candles by candle time (seconds) for O(1) render lookup.
  const fpByTime = useMemo(() => {
    const m = new Map<number, { fp: FootprintCandle; cumDelta: number }>();
    footprintCandles.forEach((fp, i) => {
      m.set(fp.time, { fp, cumDelta: cumDeltas[i] ?? 0 });
    });
    return m;
  }, [footprintCandles, cumDeltas]);



  // ── requestAnimationFrame Drawing Loop ────────────────────────────────
  const stateRef = useRef({
    scrollX, scrollY, zoomX, zoomY,
    chartData, fpByTime,
    histLoading, activeSymbol, dimensions, tickSize,
  });

  stateRef.current = {
    scrollX, scrollY, zoomX, zoomY,
    chartData, fpByTime,
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
        chartData: cData, fpByTime: fpData,
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
      // Footer hosts two stacked lines per candle: delta + total volume
      // (Requirement 6.8), then the time label.
      const footerHeight = 30;
      const bottomMargin = 28 + footerHeight;
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

        // ── Candle Color Scheme ──────────────────────────────────────────
        // Bullish green, Bearish purple/magenta
        const candleColor = isBullish ? '#22c55e' : '#a855f7';
        
        // ── Narrow Candlestick Body & Wicks on the Left (approx 10px wide) ─
        const candleW = 4;
        const candleLeft = colLeft + 4;
        const xWick = candleLeft + candleW / 2;

        // Draw Wick
        ctx.strokeStyle = candleColor;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(xWick, yHigh);
        ctx.lineTo(xWick, yLow);
        ctx.stroke();

        // Draw Body
        ctx.fillStyle = candleColor;
        const bodyTop = Math.min(yOpen, yClose);
        const bodyH = Math.max(2, Math.abs(yOpen - yClose));
        ctx.fillRect(candleLeft, bodyTop, candleW, bodyH);

        // ── Footprint Area starts to the right of the candle body ─────────
        const fpLeft = colLeft + 12;
        const fpW = colW - 14;
        const fpMid = fpLeft + fpW / 2;

        // Engine output for this candle (clusters, delta, POC, imbalances).
        const entry = fpData.get(candle.time);
        const fp = entry?.fp;
        const candleCumDelta = entry?.cumDelta ?? 0;

        if (fp && fpW > 10) {
          // Greatest-volume level scales the bid/ask bar widths.
          let maxCandleVol = 0;
          for (const cell of fp.cells) {
            const tot = cell.bid + cell.ask;
            if (tot > maxCandleVol) maxCandleVol = tot;
          }

          const imbalanceSet = new Set(fp.imbalances);

          // ── Draw Footprint Cells from engine cells ──────────────────────
          for (const cell of fp.cells) {
            if (cell.bid === 0 && cell.ask === 0) continue;

            const yTop = priceToY(cell.price + ts);
            const yBot = priceToY(cell.price);
            if (yBot < -zY || yTop > chartHeight + zY) continue;

            const cellH = Math.max(1, Math.abs(yBot - yTop));
            const { bid, ask } = cell;

            // ── Split Bid/Ask Horizontal Volume Profiles ───────────────────
            // Scale bar widths outward from the middle relative to maxCandleVol
            const leftBarW = maxCandleVol > 0 ? (bid / maxCandleVol) * (fpW / 2 - 2) : 0;
            const rightBarW = maxCandleVol > 0 ? (ask / maxCandleVol) * (fpW / 2 - 2) : 0;

            // Left Bar (Bid)
            ctx.fillStyle = isBullish ? 'rgba(34, 197, 94, 0.35)' : 'rgba(168, 85, 247, 0.35)';
            ctx.fillRect(fpMid - leftBarW, yTop + 1, leftBarW, cellH - 2);

            // Right Bar (Ask)
            ctx.fillStyle = isBullish ? 'rgba(34, 197, 94, 0.35)' : 'rgba(168, 85, 247, 0.35)';
            ctx.fillRect(fpMid, yTop + 1, rightBarW, cellH - 2);

            // Center dividing line for this cell
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.1)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(fpMid, yTop);
            ctx.lineTo(fpMid, yBot);
            ctx.stroke();

            // ── Imbalance Highlight (amber, distinct from POC) ─────────────
            if (imbalanceSet.has(cell.price)) {
              ctx.fillStyle = 'rgba(251, 191, 36, 0.18)';
              ctx.fillRect(fpLeft, yTop + 1, fpW, cellH - 2);
              ctx.strokeStyle = 'rgba(251, 191, 36, 0.9)';
              ctx.lineWidth = 1;
              ctx.strokeRect(fpLeft + 0.5, yTop + 0.5, fpW - 1, cellH - 1);
            }

            // ── POC Highlight ─────────────────────────────────────────────
            if (fp.poc !== null && cell.price === fp.poc) {
              ctx.strokeStyle = candleColor;
              ctx.lineWidth = 1.5;
              ctx.strokeRect(fpLeft + 0.5, yTop + 0.5, fpW - 1, cellH - 1);
            }

            // ── Bid / Ask Text ────────────────────────────────────────────
            if (fpW > 40 && cellH >= 12) {
              const fontSize = Math.round(Math.min(10, Math.max(7, cellH - 6)));
              ctx.font = `bold ${fontSize}px "JetBrains Mono", monospace`;
              ctx.textBaseline = 'middle';
              const yMid = Math.round(yTop + cellH / 2);

              // Left aligned to center line for Ask, right aligned for Bid
              ctx.textAlign = 'right';
              ctx.fillStyle = '#ffffff';
              ctx.fillText(fmtVol(bid), fpMid - 4, yMid);

              ctx.textAlign = 'left';
              ctx.fillStyle = '#ffffff';
              ctx.fillText(fmtVol(ask), fpMid + 4, yMid);
            }
          }

          // ── Synthetic-distribution indication (Requirement 6.3) ─────────
          if (fp.synthetic && fpW > 24) {
            ctx.fillStyle = 'rgba(251, 191, 36, 0.85)';
            ctx.font = '8px "JetBrains Mono", monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            ctx.fillText('≈ EST', fpMid, 4);
          }
        }

        // ── Per-Candle Footer: Delta / Cumulative Delta / Total Volume ──
        // (Requirements 6.4, 6.5, 6.8)
        if (fp && fpW > 24) {
          const footerTop = chartHeight + 3;
          const deltaPositive = fp.delta >= 0;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'top';

          ctx.font = 'bold 9px "JetBrains Mono", monospace';
          ctx.fillStyle = deltaPositive ? '#22c55e' : '#f87171';
          ctx.fillText(`Δ ${fmtDelta(fp.delta)}`, fpMid, footerTop);

          ctx.font = '8px "JetBrains Mono", monospace';
          ctx.fillStyle = candleCumDelta >= 0 ? 'rgba(34,197,94,0.7)' : 'rgba(248,113,113,0.7)';
          ctx.fillText(`Σ ${fmtDelta(candleCumDelta)}`, fpMid, footerTop + 11);

          ctx.fillStyle = 'rgba(148, 163, 184, 0.7)';
          ctx.fillText(`V ${fmtVol(fp.totalVolume)}`, fpMid, footerTop + 21);
        }

        // ── Time Label (aligned to footprints center) ──────────────────
        const timeDate = new Date(candle.time * 1000);
        const timeStr = timeDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        ctx.fillStyle = 'rgba(148, 163, 184, 0.45)';
        ctx.font = '9px "JetBrains Mono", "Fira Code", monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(timeStr, fpMid, chartHeight + footerHeight + 2);

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

    const rightMargin = 72;
    const chartWidth = dimensions.width - rightMargin;
    const maxScrollX = Math.max(0, chartData.length * zoomX - chartWidth);

    setScrollX(Math.min(maxScrollX, Math.max(0, dragStart.current.scrollX - deltaX)));
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
      <style>{`
        .fp-control-btn {
          background-color: rgba(30, 41, 59, 0.85);
          border: 1px solid rgba(148, 163, 184, 0.2);
          color: #cbd5e1;
          border-radius: 4px;
          padding: 4px 8px;
          font-size: 11px;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          min-width: 32px;
          height: 24px;
          transition: all 0.15s ease-in-out;
          font-family: "Inter", sans-serif;
          user-select: none;
        }
        .fp-control-btn:hover {
          background-color: rgba(51, 65, 85, 0.95);
          border-color: rgba(148, 163, 184, 0.4);
          color: #ffffff;
        }
        .fp-control-btn:active {
          transform: scale(0.95);
        }
      `}</style>

      <canvas
        ref={canvasRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        style={{ width: dimensions.width, height: dimensions.height }}
        className="cursor-grab active:cursor-grabbing"
      />

      {/* Floating Control Panel */}
      <div style={{
        position: 'absolute',
        right: '84px', // aligned left of the right price axis labels
        top: '12px',
        display: 'flex',
        flexDirection: 'column',
        gap: '6px',
        backgroundColor: 'rgba(10, 15, 30, 0.85)',
        border: '1px solid rgba(148, 163, 184, 0.2)',
        borderRadius: '6px',
        padding: '6px',
        backdropFilter: 'blur(8px)',
        zIndex: 10,
        boxShadow: '0 4px 12px rgba(0, 0, 0, 0.5)',
      }}>
        <div style={{ display: 'flex', gap: '4px' }}>
          <button
            onClick={() => setZoomX(prev => Math.min(300, prev + 15))}
            className="fp-control-btn"
            title="Zoom In Width (Col width)"
          >
            ↔ +
          </button>
          <button
            onClick={() => setZoomX(prev => Math.max(60, prev - 15))}
            className="fp-control-btn"
            title="Zoom Out Width (Col width)"
          >
            ↔ -
          </button>
        </div>
        <div style={{ display: 'flex', gap: '4px' }}>
          <button
            onClick={() => setZoomY(prev => Math.min(80, prev + 2))}
            className="fp-control-btn"
            title="Zoom In Height (Row height)"
          >
            ↕ +
          </button>
          <button
            onClick={() => setZoomY(prev => Math.max(12, prev - 2))}
            className="fp-control-btn"
            title="Zoom Out Height (Row height)"
          >
            ↕ -
          </button>
        </div>
        <button
          onClick={() => {
            if (chartData.length > 0) {
              const latestPrice = chartData[chartData.length - 1].close;
              setScrollY(latestPrice);
              setScrollX(0);
              setZoomX(120);
              setZoomY(24);
            }
          }}
          className="fp-control-btn"
          style={{
            width: '100%',
            fontWeight: '600',
            color: '#38bdf8', // light blue theme color
          }}
          title="Reset Zoom & Pan to Latest Price"
        >
          Center View
        </button>
      </div>
    </div>
  );
}
