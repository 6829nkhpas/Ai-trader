'use client';

/**
 * FnoChartPanel — Price chart for the selected F&O contract.
 *
 * Reads `selectedSymbol` from the trade store (set when the user clicks an
 * F&O instrument in the search modal) and renders a ChartSurface for it.
 * Shows a placeholder when no F&O contract is selected.
 */

import React from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import ChartSurface from '../chart/ChartSurface';

/** Detect whether a symbol looks like an F&O tradingsymbol (contains CE/PE/FUT). */
function isFnoSymbol(symbol: string): boolean {
  const upper = symbol.toUpperCase();
  if (upper.endsWith('FUT')) {
    return true;
  }
  if (upper.endsWith('CE') || upper.endsWith('PE')) {
    return /\d/.test(upper);
  }
  return false;
}

export default function FnoChartPanel() {
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
