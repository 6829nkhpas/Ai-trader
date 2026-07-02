'use client';

// Feature: tradingview-advanced-charts
//
// ChartSurface — the host for the TradingView Advanced Charts widget.
//
// Now renders the TV widget for ALL chart modes including Volume Footprint,
// TPO (Market Profile), Session Volume Profile, and Volume Candle — all
// natively supported by TradingView Advanced Charts v31.
//
// The user can switch between chart types via TV's built-in chart type
// dropdown in the header bar.

import React from 'react';

import TradingViewWidget from './TradingViewWidget';

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
 * ALL chart views. Volume Footprint, TPO, SVP, and Volume Candle are now
 * available natively through TV's chart type selector.
 *
 * All chart UI (drawing tools, indicators, chart types, timeframe selection) is
 * delegated to the TradingView widget's native interface.
 */
export default function ChartSurface({
  className = '',
  symbolOverride,
  timeframeOverride,
}: ChartSurfaceProps) {
  return (
    <div className={`relative h-full w-full ${className}`}>
      <TradingViewWidget
        symbolOverride={symbolOverride}
        timeframeOverride={timeframeOverride}
      />
    </div>
  );
}
