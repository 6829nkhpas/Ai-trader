'use client';

// Feature: professional-charting-suite
//
// ChartSurface — the thin host for the single engine-driven price renderer.
//
// Single source of truth: all chart-control state (chart type + params, applied
// strategy + params, indicator-manager visibility) lives in `useChartUIStore`
// and is driven by the ONE control surface in the terminal page header
// (ChartTypeSelector, StrategySelector, the Indicators toggle, the chart-mode
// toggle, the timeframe selector and fullscreen). ChartSurface deliberately
// renders NO controls of its own — it just reads the store and renders:
//   · the engine-driven `ChartRenderer` (or `FootprintChart` in footprint mode),
//   · the indicator-manager panel as an overlay when the store flag is set.
// This keeps a single tools section and a single renderer with no duplication.

import React from 'react';

import ChartRenderer from './ChartRenderer';
import IndicatorManagerPanel from './IndicatorManagerPanel';
import FootprintChart from './FootprintChart';

import { useTradeStore } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import type { Timeframe } from '../../utils/chartTypes';

export interface ChartSurfaceProps {
  className?: string;
}

/**
 * The chart surface shell. Reads the chart-control state from `useChartUIStore`
 * (owned by the page header) and renders the single renderer plus the optional
 * indicator-manager overlay. Chart mode, timeframe and fullscreen are owned by
 * the page header.
 */
export default function ChartSurface({ className = '' }: ChartSurfaceProps) {
  // Chart mode + timeframe are owned by the page header (read-only here).
  const chartMode = useTradeStore((s) => s.chartMode);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);

  // Chart-control selections (single source of truth — set by the page header).
  const chartType = useChartUIStore((s) => s.chartType);
  const chartTypeParams = useChartUIStore((s) => s.chartTypeParams);
  const activeStrategyId = useChartUIStore((s) => s.activeStrategyId);
  const strategyParams = useChartUIStore((s) => s.strategyParams);
  const showIndicatorManager = useChartUIStore((s) => s.showIndicatorManager);
  const setShowIndicatorManager = useChartUIStore((s) => s.setShowIndicatorManager);

  const showVolumeProfile = chartMode === 'VOLUME_PROFILE';
  const isFootprint = chartMode === 'FOOTPRINT';
  const effectiveTimeframe = (activeTimeframe as Timeframe) ?? '1m';

  return (
    <div className={`relative h-full w-full ${className}`}>
      {isFootprint ? (
        <FootprintChart timeframe={effectiveTimeframe} />
      ) : (
        <ChartRenderer
          timeframe={effectiveTimeframe}
          showVolumeProfile={showVolumeProfile}
          chartType={chartType}
          chartTypeParams={chartTypeParams}
          activeStrategyId={activeStrategyId}
          strategyParams={strategyParams}
        />
      )}

      {/* Indicator manager overlay (keeps the chart visible — Req 12.2).
          Toggled from the page header's Indicators button via the store. */}
      {showIndicatorManager && (
        <div className="absolute right-3 top-3 z-50">
          <IndicatorManagerPanel onClose={() => setShowIndicatorManager(false)} />
        </div>
      )}
    </div>
  );
}
