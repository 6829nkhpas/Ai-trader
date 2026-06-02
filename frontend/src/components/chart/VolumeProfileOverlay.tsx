'use client';

import React, { useEffect, useRef } from 'react';
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

  const drawVolumeProfileRef = useRef<() => void>(undefined);

  const drawVolumeProfile = () => {
    try {
      const canvas = canvasRef.current;
      const currentChart = chartRef.current;
      const currentSeries = candleSeriesRef.current;
      if (!canvas || !currentChart || !currentSeries || chartData.length === 0) return;

      const ctx = canvas.getContext('2d');
      if (!ctx) return;

      const rect = canvas.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;

      const dpr = window.devicePixelRatio || 1;
      const expectedWidth = Math.floor(rect.width * dpr);
      const expectedHeight = Math.floor(rect.height * dpr);

      if (canvas.width !== expectedWidth || canvas.height !== expectedHeight) {
        canvas.width = expectedWidth;
        canvas.height = expectedHeight;
        ctx.scale(dpr, dpr);
      } else {
        ctx.clearRect(0, 0, rect.width, rect.height);
      }

      const logicalRange = currentChart.timeScale().getVisibleLogicalRange();
      if (!logicalRange) {
        ctx.clearRect(0, 0, rect.width, rect.height);
        return;
      }

      if (
        logicalRange.from === null ||
        logicalRange.to === null ||
        isNaN(logicalRange.from) ||
        isNaN(logicalRange.to)
      ) {
        ctx.clearRect(0, 0, rect.width, rect.height);
        return;
      }

      const fromIndex = Math.max(0, Math.floor(logicalRange.from));
      const toIndex = Math.min(chartData.length - 1, Math.ceil(logicalRange.to));

      if (isNaN(fromIndex) || isNaN(toIndex) || fromIndex > toIndex) {
        ctx.clearRect(0, 0, rect.width, rect.height);
        return;
      }

      const visibleCandles = chartData.slice(fromIndex, toIndex + 1);

      if (visibleCandles.length === 0) {
        ctx.clearRect(0, 0, rect.width, rect.height);
        return;
      }

      // Find price range in visible window
      let minPrice = Infinity;
      let maxPrice = -Infinity;
      visibleCandles.forEach((c) => {
        if (c.low < minPrice) minPrice = c.low;
        if (c.high > maxPrice) maxPrice = c.high;
      });

      if (minPrice === Infinity || maxPrice === -Infinity || minPrice === maxPrice) {
        ctx.clearRect(0, 0, rect.width, rect.height);
        return;
      }

      // Group into 40 price bins
      const binCount = 40;
      const binSize = (maxPrice - minPrice) / binCount;
      const bins = Array(binCount).fill(0);

      visibleCandles.forEach((c) => {
        const volBar = volumeData.find((v) => v.time === c.time);
        const volumeVal = volBar ? volBar.value : 0;

        const lowBin = Math.floor((c.low - minPrice) / binSize);
        const highBin = Math.floor((c.high - minPrice) / binSize);
        if (lowBin === highBin) {
          const b = Math.min(binCount - 1, Math.max(0, lowBin));
          bins[b] += volumeVal;
        } else {
          const share = volumeVal / (highBin - lowBin + 1);
          for (let i = lowBin; i <= highBin; i++) {
            if (i >= 0 && i < binCount) bins[i] += share;
          }
        }
      });

      // Find POC (Point of Control)
      let maxVol = 0;
      let pocIndex = -1;
      bins.forEach((v, idx) => {
        if (v > maxVol) {
          maxVol = v;
          pocIndex = idx;
        }
      });

      // Institutional Value Area (VA) Calculation (70% of total volume)
      const totalVolume = bins.reduce((sum, v) => sum + v, 0);
      const targetVolume = totalVolume * 0.70;
      const vaBins = new Set<number>();

      if (pocIndex !== -1 && totalVolume > 0) {
        vaBins.add(pocIndex);
        let vaVolume = bins[pocIndex];
        let lowerIdx = pocIndex;
        let upperIdx = pocIndex;

        while (vaVolume < targetVolume) {
          const volBelow = lowerIdx > 0 ? bins[lowerIdx - 1] : 0;
          const volAbove = upperIdx < binCount - 1 ? bins[upperIdx + 1] : 0;

          if (volBelow === 0 && volAbove === 0) break;

          if (volBelow >= volAbove) {
            lowerIdx--;
            vaBins.add(lowerIdx);
            vaVolume += volBelow;
          } else {
            upperIdx++;
            vaBins.add(upperIdx);
            vaVolume += volAbove;
          }
        }
      }

      const maxWidth = rect.width * 0.35; // Profile takes max 35% of chart width

      // Draw horizontal profile bars on the left Y-axis
      for (let i = 0; i < binCount; i++) {
        const binPriceLow = minPrice + i * binSize;
        const binPriceHigh = minPrice + (i + 1) * binSize;

        const yHigh = currentSeries.priceToCoordinate(binPriceHigh);
        const yLow = currentSeries.priceToCoordinate(binPriceLow);
        if (yHigh === null || yLow === null || isNaN(yHigh) || isNaN(yLow)) continue;

        const barHeight = Math.abs(yLow - yHigh);
        const barWidth = maxVol > 0 ? (bins[i] / maxVol) * maxWidth : 0;

        const isValueArea = vaBins.has(i);

        // Styling: Solid blue/gray for Value Area, translucent for outside VA
        ctx.fillStyle = isValueArea
          ? 'rgba(59, 130, 246, 0.25)' // Solid blue/gray with 25% opacity
          : 'rgba(148, 163, 184, 0.08)'; // Slate with 8% opacity

        ctx.fillRect(0, Math.min(yHigh, yLow), barWidth, barHeight);

        ctx.strokeStyle = isValueArea ? 'rgba(59, 130, 246, 0.1)' : 'rgba(148, 163, 184, 0.04)';
        ctx.lineWidth = 0.5;
        ctx.strokeRect(0, Math.min(yHigh, yLow), barWidth, barHeight);
      }

      // Draw a bright solid red POC line spanning the width of the profile
      if (pocIndex !== -1) {
        const pocPrice = minPrice + (pocIndex + 0.5) * binSize;
        const yPoc = currentSeries.priceToCoordinate(pocPrice);
        if (yPoc !== null && !isNaN(yPoc)) {
          ctx.strokeStyle = '#ef4444'; // Bright red
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(0, yPoc);
          ctx.lineTo(maxWidth, yPoc);
          ctx.stroke();

          // Label
          ctx.fillStyle = '#ef4444';
          ctx.font = 'bold 9px monospace';
          ctx.fillText(`POC ${pocPrice.toFixed(2)}`, maxWidth + 8, yPoc + 3);
        }
      }
    } catch (err) {
      console.warn('[VolumeProfileOverlay] Draw error ignored:', err);
    }
  };

  drawVolumeProfileRef.current = drawVolumeProfile;

  // ── Stable event listener setup ──────────────────────────────────────────
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const onVisibleRangeChange = () => {
      drawVolumeProfileRef.current?.();
    };

    chart.timeScale().subscribeVisibleLogicalRangeChange(onVisibleRangeChange);

    const resizeHandler = () => {
      drawVolumeProfileRef.current?.();
    };
    window.addEventListener('resize', resizeHandler);

    // Initial draw trigger (slight delay to let parent render references finish)
    const timer = setTimeout(() => {
      drawVolumeProfileRef.current?.();
    }, 50);

    return () => {
      clearTimeout(timer);
      try {
        chart.timeScale().unsubscribeVisibleLogicalRangeChange(onVisibleRangeChange);
      } catch (e) {}
      window.removeEventListener('resize', resizeHandler);
    };
  }, [chartRef]);

  // ── Redraw trigger on data changes ───────────────────────────────────────
  useEffect(() => {
    drawVolumeProfileRef.current?.();
  }, [chartData, volumeData]);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 pointer-events-none z-[5]"
    />
  );
}
