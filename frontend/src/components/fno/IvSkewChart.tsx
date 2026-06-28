'use client';

/**
 * F&O Frontend Section (F4) — IV-Skew chart (task 7.2).
 *
 * Renders the implied-volatility skew across strikes for the selected expiry
 * using a `lightweight-charts` line series, following the terminal's zero-latency
 * chart lifecycle (chart created once, data pushed through refs via `setData`,
 * theme read from CSS variables, resize/cleanup handled) exactly as
 * `TradingChart.tsx` does.
 *
 * Behavior (Requirements 4.1, 4.2, 4.3, 4.4, 7.2):
 * - Plots one line point per strike in `IvSkewModel.points` (already null-filtered
 *   by `buildIvSkew`, so no fabricated IV reaches the chart — R4.1, R4.2).
 * - Marks the at-the-money strike with a price-line when `atmStrike` is non-null,
 *   anchored to the IV at (or nearest to) that strike for orientation (R4.3).
 * - Renders an explicit Unavailable_State overlay when `points` is empty rather
 *   than an empty axis (R4.4).
 *
 * The component is consumption-only: it renders exactly what the pure
 * `buildIvSkew` selector produced and computes no analytics.
 *
 * NOTE: task 7.3 introduces a shared `FnoUnavailableState` component; until it
 * exists this chart renders a self-contained inline overlay so it stays
 * independently usable.
 */

import React, { useEffect, useRef } from 'react';
import {
  createChart,
  ColorType,
  CrosshairMode,
  LineSeries,
  IChartApi,
  ISeriesApi,
  IPriceLine,
  Time,
} from 'lightweight-charts';
import type { IvSkewModel } from './viewModel';

interface IvSkewChartProps {
  model: IvSkewModel;
}

export default function IvSkewChart({ model }: IvSkewChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const atmLineRef = useRef<IPriceLine | null>(null);

  const points = model?.points ?? [];
  const isEmpty = points.length === 0;

  // Create the chart once. Theme tokens are read from CSS variables on the
  // document root, matching TradingChart's institutional dark theme.
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const rootStyles = getComputedStyle(document.documentElement);
    const chartBackground = rootStyles.getPropertyValue('--chart-bg').trim() || '#0b1220';
    const chartGrid = rootStyles.getPropertyValue('--chart-grid').trim() || '#1e293b';
    const chartText = rootStyles.getPropertyValue('--text-secondary').trim() || '#9ca3af';
    const borderDefault = rootStyles.getPropertyValue('--border-default').trim() || '#374151';
    const lineColor = rootStyles.getPropertyValue('--accent-primary').trim() || '#38bdf8';

    chartContainerRef.current.style.backgroundColor = chartBackground;

    const chart = createChart(chartContainerRef.current, {
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: borderDefault },
      timeScale: {
        borderColor: borderDefault,
        // The x-axis is strike-indexed (strikes are passed as the `time` value),
        // so format tick marks as the strike rather than a date/time.
        tickMarkFormatter: (time: Time) => String(time),
      },
      localization: {
        // Crosshair / tooltip label for the x-axis shows the raw strike.
        timeFormatter: (time: Time) => String(time),
      },
      layout: {
        background: { type: ColorType.Solid, color: chartBackground },
        textColor: chartText,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: chartGrid },
        horzLines: { color: chartGrid },
      },
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight || 320,
    });

    const lineSeries = chart.addSeries(LineSeries, {
      color: lineColor,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      priceFormat: {
        type: 'price',
        precision: 4,
        minMove: 0.0001,
      },
    });

    chartRef.current = chart;
    seriesRef.current = lineSeries;

    const resizeObserver = new ResizeObserver(() => {
      if (!chartContainerRef.current || !chartRef.current) return;
      try {
        const rect = chartContainerRef.current.getBoundingClientRect();
        chartRef.current.resize(Math.floor(rect.width), Math.floor(rect.height));
      } catch (e) {
        console.warn('[IvSkewChart] Resize failed:', e);
      }
    });

    resizeObserver.observe(chartContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      atmLineRef.current = null;
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  // Push the IV-by-strike series and (re)draw the ATM price-line through refs
  // whenever the model changes — zero-latency update, no chart recreation (R7.2).
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;

    // Strikes are used as the (numeric) x-axis value; buildIvSkew already sorts
    // ascending and drops null/non-finite IV, so the series is render-ready.
    const data = points.map((point) => ({
      time: point.strike as unknown as Time,
      value: point.iv,
    }));

    series.setData(data);

    // Remove any stale ATM price-line before drawing the current one.
    if (atmLineRef.current) {
      series.removePriceLine(atmLineRef.current);
      atmLineRef.current = null;
    }

    // Mark the ATM strike with a price-line for orientation, only when non-null
    // (R4.3). Anchor it to the IV at the ATM strike, or the nearest plotted
    // strike's IV when the exact ATM strike has no finite IV.
    const atmStrike = model?.atmStrike ?? null;
    if (atmStrike !== null && data.length > 0) {
      let nearest = points[0];
      let bestDistance = Math.abs(nearest.strike - atmStrike);
      for (const point of points) {
        const distance = Math.abs(point.strike - atmStrike);
        if (distance < bestDistance) {
          nearest = point;
          bestDistance = distance;
        }
      }

      const rootStyles = getComputedStyle(document.documentElement);
      const atmColor = rootStyles.getPropertyValue('--text-secondary').trim() || '#9ca3af';

      atmLineRef.current = series.createPriceLine({
        price: nearest.iv,
        color: atmColor,
        lineWidth: 1,
        lineStyle: 2, // dashed
        axisLabelVisible: true,
        title: `ATM ${atmStrike}`,
      });
    }

    if (data.length > 0) {
      chart.timeScale().fitContent();
    }
  }, [points, model?.atmStrike]);

  return (
    <div className="relative flex h-full min-h-0 w-full flex-col bg-transparent">
      <div
        ref={chartContainerRef}
        className="h-full w-full flex-1 overflow-hidden rounded-none bg-chart-bg"
        style={{ minHeight: '280px' }}
      />

      {isEmpty && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 rounded-none border border-dashed border-border-default bg-card text-center">
          <span className="text-sm font-semibold text-text-secondary">IV skew unavailable</span>
          <span className="px-6 text-xs text-text-muted">
            No strike has a computable implied volatility for the selected expiry.
          </span>
        </div>
      )}
    </div>
  );
}
