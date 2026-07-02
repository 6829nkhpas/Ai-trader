'use client';

// Feature: tradingview-advanced-charts
//
// ChartSurface — the host for the TradingView Advanced Charts widget.
//
// Replaces the custom lightweight-charts-based ChartRenderer with the full
// TradingView Advanced Charts widget, which provides native drawing tools,
// indicators, chart types, and timeframe controls.
//
// The FootprintChart mode is retained as a custom component (TV does not
// provide a native footprint view). When chartMode === 'FOOTPRINT', the
// legacy FootprintChart is rendered instead of the TV widget.

import React from 'react';

import TradingViewWidget from './TradingViewWidget';
import FootprintChart from './FootprintChart';

import { useTradeStore } from '../../store/useTradeStore';
import type { Timeframe } from '../../utils/chartTypes';
import type { ChartType } from '../../charting/engines';

export interface ChartSurfaceProps {
  className?: string;
  /** Per-pane symbol (split view). Charts this instrument instead of the global
   *  `selectedSymbol` so two panes can show different stocks at once (R4.3). */
  symbolOverride?: string;
  /** Per-pane timeframe (split view); falls back to the store. */
  timeframeOverride?: Timeframe;
  /** Per-pane chart type (split view); falls back to the store. */
  chartTypeOverride?: ChartType;
}

/**
 * The chart surface shell. Renders the TradingView Advanced Charts widget for
 * the standard chart view, and the custom FootprintChart for the footprint mode.
 *
 * All chart UI (drawing tools, indicators, chart types, timeframe selection) is
 * now delegated to the TradingView widget's native interface.
 */
export default function ChartSurface({
  className = '',
  symbolOverride,
  timeframeOverride,
}: ChartSurfaceProps) {
  // Chart mode is owned by the page header (read-only here).
  const chartMode = useTradeStore((s) => s.chartMode);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);

  // Footprint is our custom concept — TV doesn't have a native footprint view.
  const isolated = !!symbolOverride;
  const isFootprint = !isolated && chartMode === 'FOOTPRINT';
  const effectiveTimeframe =
    timeframeOverride ?? (activeTimeframe as Timeframe) ?? '15m';

  return (
    <div className={`relative h-full w-full ${className}`}>
      {isFootprint ? (
        <FootprintChart timeframe={effectiveTimeframe} />
      ) : (
        <TradingViewWidget
          symbolOverride={symbolOverride}
          timeframeOverride={timeframeOverride}
        />
      )}
    </div>
  );
}
