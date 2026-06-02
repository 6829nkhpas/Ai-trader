'use client';

import React from 'react';
import { useTradeStore } from '../store/useTradeStore';
import AlphaPredictiveChart from './AlphaPredictiveChart';
import FootprintChart from './chart/FootprintChart';
import type { AlphaPredictiveChartProps } from '../utils/chartTypes';

export default function MainTerminalChart(props: AlphaPredictiveChartProps) {
  const chartMode = useTradeStore((s) => s.chartMode);

  const isFootprint = chartMode === 'FOOTPRINT';
  const showVolumeProfile = chartMode === 'VOLUME_PROFILE';

  return (
    <div className="relative h-full w-full">
      {/* Footprint Chart Container */}
      {isFootprint && (
        <div className="absolute inset-0 h-full w-full z-10">
          <FootprintChart {...props} />
        </div>
      )}

      {/* Standard Chart Container (hidden using display: none when Footprint is active to prevent unmounting/bridge drop) */}
      <div
        className="h-full w-full"
        style={{ display: isFootprint ? 'none' : 'block' }}
      >
        <AlphaPredictiveChart
          {...props}
          showVolumeProfile={showVolumeProfile}
        />
      </div>
    </div>
  );
}
