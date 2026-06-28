'use client';

import React, { useEffect, useRef } from 'react';
import { useFootprintState, fmtVol, fmtDelta } from '../../hooks/useFootprintState';

export default function FootprintChart({
  timeframe = '1m',
}: {
  activeProfile?: string;
  timeframe?: string;
  isExpanded?: boolean;
  onToggleExpand?: () => void;
}) {
  const {
    zoomX, setZoomX,
    zoomY, setZoomY,
    scrollX, setScrollX,
    scrollY, setScrollY,
    dimensions,
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
  } = useFootprintState(timeframe);

  const rafIdRef = useRef<number | null>(null);

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
      ctx.fillStyle = '#000000';
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

        ctx.strokeStyle = 'rgba(26, 26, 26, 0.6)';
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(chartWidth, y);
        ctx.stroke();

        // Price labels on right axis
        ctx.fillStyle = 'rgba(156, 163, 175, 0.6)';
        ctx.font = '10px "JetBrains Mono", monospace';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'middle';
        ctx.fillText(pr.toFixed(2), width - 6, y);
      }

      // ── Candle Columns (right to left) ────────────────────────────────
      const maxScrollX = Math.max(0, cData.length * zX - chartWidth);
      const clampedScrollX = Math.min(maxScrollX, Math.max(0, sX));
      let currentX = Math.round(chartWidth - clampedScrollX);

      for (let i = cData.length - 1; i >= 0; i--) {
        const candle = cData[i];
        const nextX = Math.round(currentX - zX);

        if (currentX < 0) break;
        if (nextX > chartWidth) { currentX = nextX; continue; }

        const colLeft = Math.max(0, nextX);
        const colRight = Math.min(chartWidth, currentX);
        const colW = colRight - colLeft;

        // Column separator
        ctx.strokeStyle = 'rgba(26, 26, 26, 0.5)';
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
        // Bullish emerald-green, Bearish rose-red
        const candleColor = isBullish ? '#10b981' : '#ef4444';
        
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

        const entry = fpData.get(candle.time);
        const fp = entry?.fp;
        const candleCumDelta = entry?.cumDelta ?? 0;

        if (fp && fpW > 10) {
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

            const leftBarW = maxCandleVol > 0 ? (bid / maxCandleVol) * (fpW / 2 - 2) : 0;
            const rightBarW = maxCandleVol > 0 ? (ask / maxCandleVol) * (fpW / 2 - 2) : 0;

            // Left Bar (Bid)
            ctx.fillStyle = isBullish ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)';
            ctx.fillRect(fpMid - leftBarW, yTop + 1, leftBarW, cellH - 2);

            // Right Bar (Ask)
            ctx.fillStyle = isBullish ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)';
            ctx.fillRect(fpMid, yTop + 1, rightBarW, cellH - 2);

            // Center dividing line
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(fpMid, yTop);
            ctx.lineTo(fpMid, yBot);
            ctx.stroke();

            // Imbalance Highlight (amber)
            if (imbalanceSet.has(cell.price)) {
              ctx.fillStyle = 'rgba(245, 158, 11, 0.12)';
              ctx.fillRect(fpLeft, yTop + 1, fpW, cellH - 2);
              ctx.strokeStyle = 'rgba(245, 158, 11, 0.8)';
              ctx.lineWidth = 1;
              ctx.strokeRect(fpLeft + 0.5, yTop + 0.5, fpW - 1, cellH - 1);
            }

            // POC Highlight
            if (fp.poc !== null && cell.price === fp.poc) {
              ctx.strokeStyle = candleColor;
              ctx.lineWidth = 1.5;
              ctx.strokeRect(fpLeft + 0.5, yTop + 0.5, fpW - 1, cellH - 1);
            }

            // Bid / Ask Text
            if (fpW > 40 && cellH >= 12) {
              const fontSize = Math.round(Math.min(10, Math.max(7, cellH - 6)));
              ctx.font = `bold ${fontSize}px "JetBrains Mono", monospace`;
              ctx.textBaseline = 'middle';
              const yMid = Math.round(yTop + cellH / 2);

              ctx.textAlign = 'right';
              ctx.fillStyle = '#f5f5f5';
              ctx.fillText(fmtVol(bid), fpMid - 4, yMid);

              ctx.textAlign = 'left';
              ctx.fillStyle = '#f5f5f5';
              ctx.fillText(fmtVol(ask), fpMid + 4, yMid);
            }
          }

          // Synthetic indicator
          if (fp.synthetic && fpW > 24) {
            ctx.fillStyle = 'rgba(245, 158, 11, 0.85)';
            ctx.font = '8px "JetBrains Mono", monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            ctx.fillText('≈ EST', fpMid, 4);
          }
        }

        // Per-Candle Footer
        if (fp && fpW > 24) {
          const footerTop = chartHeight + 3;
          const deltaPositive = fp.delta >= 0;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'top';

          ctx.font = 'bold 9px "JetBrains Mono", monospace';
          ctx.fillStyle = deltaPositive ? '#10b981' : '#ef4444';
          ctx.fillText(`Δ ${fmtDelta(fp.delta)}`, fpMid, footerTop);

          ctx.font = '8px "JetBrains Mono", monospace';
          ctx.fillStyle = candleCumDelta >= 0 ? 'rgba(16, 185, 129, 0.75)' : 'rgba(239, 68, 68, 0.75)';
          ctx.fillText(`Σ ${fmtDelta(candleCumDelta)}`, fpMid, footerTop + 11);

          ctx.fillStyle = 'rgba(156, 163, 175, 0.7)';
          ctx.fillText(`V ${fmtVol(fp.totalVolume)}`, fpMid, footerTop + 21);
        }

        // Time Label
        const timeDate = new Date(candle.time * 1000);
        const timeStr = timeDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        ctx.fillStyle = 'rgba(156, 163, 175, 0.45)';
        ctx.font = '9px "JetBrains Mono", monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(timeStr, fpMid, chartHeight + footerHeight + 2);

        currentX = nextX;
      }

      // Axis Borders
      ctx.strokeStyle = 'rgba(26, 26, 26, 0.6)';
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

  return (
    <div
      ref={containerRef}
      className="relative flex h-full w-full select-none overflow-hidden"
      style={{ background: '#000000' }}
    >
      <style>{`
        .fp-control-btn {
          background-color: #161616;
          border: 1px solid #1a1a1a;
          color: #d1d5db;
          border-radius: 0px;
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
          background-color: #262626;
          border-color: #3f3f46;
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
        right: '84px',
        top: '12px',
        display: 'flex',
        flexDirection: 'column',
        gap: '6px',
        backgroundColor: 'rgba(10, 10, 10, 0.85)',
        border: '1px solid #1a1a1a',
        borderRadius: '0px',
        padding: '6px',
        backdropFilter: 'blur(8px)',
        zIndex: 10,
        boxShadow: 'none',
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
            color: '#10b981',
          }}
          title="Reset Zoom & Pan to Latest Price"
        >
          Center View
        </button>
      </div>
    </div>
  );
}
