'use client';

import React, { useEffect, useRef, useCallback } from 'react';
import type { ChartCandle, VolumeBar } from '../../utils/chartTypes';

interface VolumeProfileOverlayProps {
  chartRef: React.RefObject<any>;
  candleSeriesRef: React.RefObject<any>;
  chartData: ChartCandle[];
  volumeData: VolumeBar[];
}

export default function VolumeProfileOverlay({
  chartRef,
  candleSeriesRef,
  chartData,
  volumeData,
}: VolumeProfileOverlayProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rafRef = useRef<number>(0);
  const needsRedrawRef = useRef(true);

  // Keep latest data accessible to the rAF loop via a ref (avoids stale closures)
  const dataRef = useRef({ chartData, volumeData });
  dataRef.current = { chartData, volumeData };

  // ── Core Drawing Function ──────────────────────────────────────────────────
  const draw = useCallback(() => {
    try {
      const canvas = canvasRef.current;
      const chart = chartRef.current;
      const series = candleSeriesRef.current;
      const { chartData: cData, volumeData: vData } = dataRef.current;

      if (!canvas || !chart || !series || cData.length === 0) return;

      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      const parent = canvas.parentElement;
      if (!parent) return;

      const rect = parent.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return;

      // ── 4K Supersampling ─────────────────────────────────────────────────
      const dpr = (window.devicePixelRatio || 1) * 2;
      const bw = Math.floor(rect.width * dpr);
      const bh = Math.floor(rect.height * dpr);

      if (canvas.width !== bw || canvas.height !== bh) {
        canvas.width = bw;
        canvas.height = bh;
      }

      // Always reset transform before drawing to prevent accumulation
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, rect.width, rect.height);

      const w = rect.width;

      // ── Visible Range ────────────────────────────────────────────────────
      let logicalRange: any;
      try {
        logicalRange = chart.timeScale().getVisibleLogicalRange();
      } catch {
        return;
      }
      if (
        !logicalRange ||
        logicalRange.from == null ||
        logicalRange.to == null ||
        isNaN(logicalRange.from) ||
        isNaN(logicalRange.to)
      )
        return;

      const fromIdx = Math.max(0, Math.floor(logicalRange.from));
      const toIdx = Math.min(cData.length - 1, Math.ceil(logicalRange.to));
      if (isNaN(fromIdx) || isNaN(toIdx) || fromIdx > toIdx) return;

      const visible = cData.slice(fromIdx, toIdx + 1);
      if (visible.length === 0) return;

      // ── Price Range ──────────────────────────────────────────────────────
      let minP = Infinity,
        maxP = -Infinity;
      for (const c of visible) {
        if (c.low < minP) minP = c.low;
        if (c.high > maxP) maxP = c.high;
      }
      if (minP >= maxP) return;

      // ── Bin Volume into 50 price levels ──────────────────────────────────
      const BIN_COUNT = 50;
      const binSize = (maxP - minP) / BIN_COUNT;
      const bins = new Float64Array(BIN_COUNT);

      // O(1) volume lookup map (avoid .find() per candle)
      const volMap = new Map<any, number>();
      for (const v of vData) volMap.set(v.time, v.value);

      for (const c of visible) {
        const vol = volMap.get(c.time) || 0;
        const lo = Math.max(0, Math.floor((c.low - minP) / binSize));
        const hi = Math.min(BIN_COUNT - 1, Math.floor((c.high - minP) / binSize));
        if (lo === hi) {
          bins[lo] += vol;
        } else {
          const share = vol / (hi - lo + 1);
          for (let i = lo; i <= hi; i++) bins[i] += share;
        }
      }

      // ── Point of Control (POC) ───────────────────────────────────────────
      let maxVol = 0,
        pocIdx = 0;
      for (let i = 0; i < BIN_COUNT; i++) {
        if (bins[i] > maxVol) {
          maxVol = bins[i];
          pocIdx = i;
        }
      }
      if (maxVol === 0) return;

      // ── Value Area (70% of total volume, expanding from POC) ─────────────
      const totalVol = bins.reduce((s, v) => s + v, 0);
      const vaTarget = totalVol * 0.7;
      const vaSet = new Set<number>();
      vaSet.add(pocIdx);
      let vaVol = bins[pocIdx];
      let vaLo = pocIdx,
        vaHi = pocIdx;
      while (vaVol < vaTarget) {
        const below = vaLo > 0 ? bins[vaLo - 1] : 0;
        const above = vaHi < BIN_COUNT - 1 ? bins[vaHi + 1] : 0;
        if (below === 0 && above === 0) break;
        if (below >= above) {
          vaLo--;
          vaSet.add(vaLo);
          vaVol += below;
        } else {
          vaHi++;
          vaSet.add(vaHi);
          vaVol += above;
        }
      }

      // ── Draw Bars from RIGHT side (professional volume profile layout) ───
      const rightMargin = 65; // lightweight-charts right price scale
      const profileMaxW = w * 0.30;
      const barAnchorX = w - rightMargin;

      for (let i = 0; i < BIN_COUNT; i++) {
        const pLo = minP + i * binSize;
        const pHi = minP + (i + 1) * binSize;

        let yHi: number | null, yLo: number | null;
        try {
          yHi = series.priceToCoordinate(pHi);
          yLo = series.priceToCoordinate(pLo);
        } catch {
          continue;
        }
        if (yHi == null || yLo == null || isNaN(yHi) || isNaN(yLo)) continue;

        const barH = Math.max(1, Math.abs(yLo - yHi));
        const barW = (bins[i] / maxVol) * profileMaxW;
        const yTop = Math.round(Math.min(yHi, yLo));
        const xStart = Math.round(barAnchorX - barW);
        const roundedBarW = Math.round(barW);
        const roundedBarH = Math.round(barH);

        const isVA = vaSet.has(i);

        // VA bars: warm amber/orange. Non-VA: cool slate gray
        ctx.fillStyle = isVA
          ? 'rgba(245, 158, 11, 0.35)'
          : 'rgba(148, 163, 184, 0.12)';
        ctx.fillRect(xStart, yTop, roundedBarW, roundedBarH);

        // Subtle edge border
        ctx.strokeStyle = isVA
          ? 'rgba(245, 158, 11, 0.18)'
          : 'rgba(148, 163, 184, 0.06)';
        ctx.lineWidth = 0.5;
        ctx.strokeRect(xStart + 0.5, yTop + 0.5, roundedBarW, roundedBarH);
      }

      // ── POC Line (pink, dashed, full width) ──────────────────────────────
      const pocPrice = minP + (pocIdx + 0.5) * binSize;
      let yPoc: number | null;
      try {
        yPoc = series.priceToCoordinate(pocPrice);
      } catch {
        return;
      }
      if (yPoc != null && !isNaN(yPoc)) {
        const yP = Math.round(yPoc) + 0.5; // +0.5 → crisp 1px line
        ctx.strokeStyle = '#ec4899'; // pink-500
        ctx.lineWidth = 1.5;
        ctx.setLineDash([6, 3]);
        ctx.beginPath();
        ctx.moveTo(0, yP);
        ctx.lineTo(barAnchorX, yP);
        ctx.stroke();
        ctx.setLineDash([]);

        // POC label
        ctx.fillStyle = '#ec4899';
        ctx.font = 'bold 10px monospace';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(`POC ${pocPrice.toFixed(2)}`, 8, yP - 10);
      }

      // ── VAH / VAL Reference Lines (purple, dashed) ──────────────────────
      const vahPrice = minP + (vaHi + 1) * binSize;
      const valPrice = minP + vaLo * binSize;

      for (const { price, label } of [
        { price: vahPrice, label: 'VAH' },
        { price: valPrice, label: 'VAL' },
      ]) {
        let yLine: number | null;
        try {
          yLine = series.priceToCoordinate(price);
        } catch {
          continue;
        }
        if (yLine == null || isNaN(yLine)) continue;

        const yL = Math.round(yLine) + 0.5;
        ctx.strokeStyle = 'rgba(168, 85, 247, 0.5)'; // purple
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(0, yL);
        ctx.lineTo(barAnchorX, yL);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.fillStyle = 'rgba(168, 85, 247, 0.7)';
        ctx.font = '9px monospace';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(`${label} ${price.toFixed(2)}`, 8, yL - 8);
      }
    } catch {
      // Silently swallow any transient drawing errors
    }
  }, [chartRef, candleSeriesRef]);

  // ── requestAnimationFrame Render Loop ───────────────────────────────────────
  // Only draws when the dirty flag is set, avoiding wasted GPU cycles.
  useEffect(() => {
    let active = true;

    const loop = () => {
      if (!active) return;
      if (needsRedrawRef.current) {
        needsRedrawRef.current = false;
        draw();
      }
      rafRef.current = requestAnimationFrame(loop);
    };

    rafRef.current = requestAnimationFrame(loop);

    return () => {
      active = false;
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [draw]);

  // ── Chart Event Subscriptions (only set dirty flag, never draw directly) ───
  useEffect(() => {
    let active = true;
    let unsubFn: (() => void) | null = null;

    const markDirty = () => {
      needsRedrawRef.current = true;
    };

    // Retry subscription until chart ref is available
    const trySubscribe = () => {
      const chart = chartRef.current;
      if (!chart) {
        if (active) setTimeout(trySubscribe, 100);
        return;
      }

      try {
        chart.timeScale().subscribeVisibleLogicalRangeChange(markDirty);
        unsubFn = () => {
          try {
            chart.timeScale().unsubscribeVisibleLogicalRangeChange(markDirty);
          } catch {}
        };
      } catch {}
    };

    trySubscribe();
    window.addEventListener('resize', markDirty);

    return () => {
      active = false;
      unsubFn?.();
      window.removeEventListener('resize', markDirty);
    };
  }, [chartRef]);

  // ── Mark dirty on data changes ─────────────────────────────────────────────
  useEffect(() => {
    needsRedrawRef.current = true;
  }, [chartData, volumeData]);

  // ── Inline styles guarantee pixel-perfect sizing (no CSS class issues) ─────
  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        width: '100%',
        height: '100%',
        pointerEvents: 'none',
        zIndex: 5,
      }}
    />
  );
}
