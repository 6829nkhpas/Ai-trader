'use client';

// Feature: professional-charting-suite (Task 15.1)
//
// MainTerminalChart is the single mount point for the charting suite inside the
// three terminal layouts (Swing / Investor / Intraday). It now hosts the
// engine-driven {@link ChartSurface} shell, which wires the full suite together
// end-to-end:
//   - the pure engines (chart-type, indicator, footprint, volume-profile,
//     strategy) feed the renderer through the canonical candle selector
//     consumed by ChartRenderer (Requirement 9.1 — live data via the canonical
//     selector);
//   - the persistent control bar exposes chart type, indicators, drawing tools,
//     the chart-mode toggle, timeframe, strategy, and fullscreen
//     (Requirement 12.1);
//   - workspace persistence is wired inside ChartRenderer (Requirement 11.1);
//   - the crosshair controller and pane manager are composed by ChartRenderer.
//
// ChartSurface owns chart mode (Standard / Volume Profile / Footprint) and the
// active timeframe (read from useTradeStore.activeTimeframe), so it also renders
// the footprint surface itself when chartMode === 'FOOTPRINT'. MainTerminalChart
// therefore does NOT mount FootprintChart separately — that would double-mount
// the footprint surface.
//
// The {@link AlphaPredictiveChartProps} signature is preserved so the existing
// layout call sites (SwingLayout, InvestorLayout, IntradayLayout) keep working
// unchanged. ChartSurface manages chart mode and timeframe internally, so the
// passed-through `timeframe` is handled by ChartRenderer's fallback to the
// store's activeTimeframe.

import React from 'react';
import ChartSurface from './chart/ChartSurface';
import type { AlphaPredictiveChartProps, Timeframe } from '../utils/chartTypes';
import type { ChartType } from '../charting/engines';

interface MainTerminalChartProps extends AlphaPredictiveChartProps {
  /** Per-pane symbol (split view) — charts this instrument independently. */
  symbolOverride?: string;
  /** Per-pane timeframe (split view). */
  timeframeOverride?: Timeframe;
  /** Per-pane chart type (split view). */
  chartTypeOverride?: ChartType;
}

export default function MainTerminalChart({
  symbolOverride,
  timeframeOverride,
  chartTypeOverride,
}: MainTerminalChartProps) {
  return (
    <ChartSurface
      className="h-full w-full"
      symbolOverride={symbolOverride}
      timeframeOverride={timeframeOverride}
      chartTypeOverride={chartTypeOverride}
    />
  );
}
