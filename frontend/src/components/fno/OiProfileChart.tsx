'use client';
/* eslint-disable @typescript-eslint/no-explicit-any */

/**
 * F&O Frontend Section (F4) — OI-Profile / Max-Pain chart (task 7.1).
 *
 * Renders call vs put open interest per strike as two `lightweight-charts`
 * histogram series sharing a strike-indexed axis, visually distinguished by the
 * theme's bull/bear colors (Requirements 3.1, 3.4). The max-pain strike and the
 * OI-wall support/resistance strikes are drawn as labeled price-lines, but ONLY
 * when their source analytic is non-null (Requirements 3.2, 3.3) — a `null`
 * level is never fabricated.
 *
 * Lifecycle (matching `TradingChart.tsx`, Requirement 7.2):
 * - Theme tokens are read from CSS variables off `document.documentElement`.
 * - The chart + both series are created exactly once in a `useEffect`, held in
 *   `useRef`s; a `ResizeObserver` keeps the canvas sized; everything is torn
 *   down on unmount.
 * - On every `model` change the data is pushed through `series.setData(...)`
 *   and the price-lines are recreated via refs, rather than recreating the
 *   chart or re-rendering React (zero-latency updates).
 *
 * Strike fidelity (Requirement 3.5): exactly `OiProfileModel.points` strikes are
 * rendered — the model is already bounded to the snapshot's strikes by
 * `buildOiProfile`, and this component synthesizes none.
 *
 * Consumption only (Requirement 9.1): this component computes no options
 * analytic; it renders the pre-built `OiProfileModel` verbatim.
 */

import React, { useEffect, useRef } from 'react';
import {
  ColorType,
  HistogramSeries,
  IChartApi,
  IPriceLine,
  ISeriesApi,
  LineStyle,
  createChart,
} from 'lightweight-charts';
import { OiProfileModel } from './viewModel';

interface OiProfileChartProps {
  /** The pre-built OI-profile model (output of `buildOiProfile`). */
  model: OiProfileModel;
}

/**
 * Read the bull/bear + chart theme tokens off the document root, mirroring the
 * `TradingChart` convention so the F&O charts share the institutional theme.
 */
function readThemeTokens() {
  const rootStyles = getComputedStyle(document.documentElement);
  const read = (name: string, fallback: string) =>
    rootStyles.getPropertyValue(name).trim() || fallback;

  return {
    chartBackground: read('--chart-bg', '#0a0a0a'),
    chartGrid: read('--chart-grid', '#1a1a1a'),
    chartText: read('--text-secondary', '#d1d5db'),
    borderDefault: read('--border-default', '#1a1a1a'),
    // The theme's bull/bear colors distinguish call OI from put OI (R3.4).
    bull: read('--color-bull', '#10b981'),
    bear: read('--color-bear', '#ef4444'),
    neutral: read('--color-neutral', '#f59e0b'),
    textMuted: read('--text-muted', '#9ca3af'),
  };
}

export default function OiProfileChart({ model }: OiProfileChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const callSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const putSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  // Price-lines (max-pain / support / resistance) are owned on the call series
  // and recreated on each model change; tracked here so they can be removed.
  const priceLinesRef = useRef<IPriceLine[]>([]);

  // --- Create the chart + series ONCE (Requirement 7.2) ---------------------
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const theme = readThemeTokens();
    chartContainerRef.current.style.backgroundColor = theme.chartBackground;

    const chart = createChart(chartContainerRef.current, {
      rightPriceScale: {
        borderColor: theme.borderDefault,
      },
      timeScale: {
        borderColor: theme.borderDefault,
        // The axis is strike-indexed: each point's `time` is its strike, so the
        // tick/crosshair labels render the strike value rather than a clock time.
        tickMarkFormatter: (time: any) => `${time}`,
      },
      localization: {
        timeFormatter: (time: any) => `Strike ${time}`,
        priceFormatter: (price: number) => `${Math.round(price).toLocaleString()}`,
      },
      layout: {
        background: { type: ColorType.Solid, color: theme.chartBackground },
        textColor: theme.chartText,
      },
      grid: {
        vertLines: { color: theme.chartGrid },
        horzLines: { color: theme.chartGrid },
      },
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight || 360,
    });

    // Call OI -> bull color; Put OI -> bear color (R3.4).
    const callSeries = chart.addSeries(HistogramSeries, {
      color: theme.bull,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: 'volume' },
    });
    const putSeries = chart.addSeries(HistogramSeries, {
      color: theme.bear,
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: 'volume' },
    });

    chartRef.current = chart;
    callSeriesRef.current = callSeries;
    putSeriesRef.current = putSeries;

    const resizeObserver = new ResizeObserver(() => {
      if (!chartContainerRef.current || !chartRef.current) return;
      try {
        const rect = chartContainerRef.current.getBoundingClientRect();
        chartRef.current.resize(Math.floor(rect.width), Math.floor(rect.height));
      } catch (e) {
        console.warn('[OiProfileChart] Resize failed:', e);
      }
    });
    resizeObserver.observe(chartContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      priceLinesRef.current = [];
      chart.remove();
      chartRef.current = null;
      callSeriesRef.current = null;
      putSeriesRef.current = null;
    };
  }, []);

  // --- Push the model through refs on every model change (Requirement 7.2) --
  useEffect(() => {
    const chart = chartRef.current;
    const callSeries = callSeriesRef.current;
    const putSeries = putSeriesRef.current;
    if (!chart || !callSeries || !putSeries) return;

    const theme = readThemeTokens();

    // Exactly the model's strikes are rendered (R3.5); `buildOiProfile` already
    // sorted them ascending and bounded them to the snapshot. Null OI is
    // dropped rather than plotted as a fabricated `0` (R8.2): a missing bar is
    // honest, a zero-height bar would imply real zero interest.
    const callData = model.points
      .filter((point) => point.callOi !== null)
      .map((point) => ({ time: point.strike as any, value: point.callOi as number }));
    const putData = model.points
      .filter((point) => point.putOi !== null)
      .map((point) => ({ time: point.strike as any, value: point.putOi as number }));

    callSeries.setData(callData);
    putSeries.setData(putData);
    chart.timeScale().fitContent();

    // Recreate the analytic marker price-lines. Each level is drawn ONLY when
    // its source analytic is non-null (R3.2, R3.3) — a null level is omitted,
    // never fabricated.
    for (const line of priceLinesRef.current) {
      try {
        callSeries.removePriceLine(line);
      } catch {
        /* series may already be torn down */
      }
    }
    priceLinesRef.current = [];

    const markerLevels: Array<{ value: number | null; title: string; color: string }> = [
      { value: model.maxPain, title: 'Max Pain', color: theme.neutral },
      { value: model.support, title: 'OI Support', color: theme.bull },
      { value: model.resistance, title: 'OI Resistance', color: theme.bear },
    ];

    for (const level of markerLevels) {
      if (level.value === null) continue;
      const priceLine = callSeries.createPriceLine({
        price: level.value,
        color: level.color,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: level.title,
      });
      priceLinesRef.current.push(priceLine);
    }
  }, [model]);

  return (
    <div className="relative flex h-full min-h-0 w-full flex-col bg-transparent">
      <div className="flex items-center justify-between gap-4 border-b border-border-default bg-surface px-4 py-2.5">
        <div className="text-sm font-semibold text-text-primary">OI Profile / Max Pain</div>
        <div className="flex items-center gap-3 text-xs text-text-secondary">
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-none bg-bull" /> Call OI
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-none bg-bear" /> Put OI
          </span>
        </div>
      </div>

      <div
        ref={chartContainerRef}
        className="h-full w-full flex-1 overflow-hidden rounded-none bg-chart-bg"
        style={{ minHeight: '300px' }}
      />
    </div>
  );
}
