'use client';

/**
 * FnoChartPanel — Price chart for the selected F&O contract.
 *
 * Reads `selectedSymbol` from the trade store (set when the user clicks an
 * F&O instrument in the search modal) and renders a ChartSurface for it.
 * Shows a placeholder when no F&O contract is selected.
 *
 * Uses `useFnoAutoContract` so that when the user enters F&O mode with a
 * non-contract symbol selected (e.g. `RELIANCE` equity, `NIFTY 50` index), the
 * panel auto-resolves the nearest CE/PE contract for that underlying and
 * replaces `selectedSymbol` with it — so the chart loads a tradable contract
 * instead of the empty placeholder below.
 */

import React from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { isFnoSymbol } from '../../charting/symbolUtils';
import { useFnoAutoContract } from './useFnoAutoContract';
import ChartSurface from '../chart/ChartSurface';

export default function FnoChartPanel() {
  useFnoAutoContract();
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const showChart = selectedSymbol && isFnoSymbol(selectedSymbol);

  if (!showChart) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-surface border-b border-border-default/30">
        <div className="text-center">
          <span className="text-[11px] font-bold uppercase tracking-widest text-text-muted block">
            F&amp;O Contract Chart
          </span>
          <span className="text-[10px] text-text-muted/60 mt-1 block">
            Search and select an F&amp;O contract to view its price chart
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full w-full flex-col bg-surface">
      <div className="min-h-0 flex-1">
        <ChartSurface
          className="h-full w-full"
          symbolOverride={selectedSymbol}
        />
      </div>
    </div>
  );
}
