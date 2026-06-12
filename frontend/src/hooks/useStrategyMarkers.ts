// Feature: professional-charting-suite
//
// useStrategyMarkers — renders a StrategyEngine's entry/exit signals as markers
// anchored to the price-pane candle series (Requirement 8.4).
//
// The hook is a thin rendering adapter over the pure `StrategyEngine`: it
// evaluates the selected strategy over the canonical candle series and maps the
// resulting `Signal[]` onto `lightweight-charts` series markers. Removing the
// strategy (passing a null id) clears every marker the strategy contributed
// (Requirement 8.8), and an insufficient-data result yields an empty marker set
// without touching the price series (Requirement 8.3).

import { useEffect, useRef } from 'react';
import {
  createSeriesMarkers,
  type Time,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
} from 'lightweight-charts';

import type { ChartRefs, ChartCandle } from '../utils/chartTypes';
import {
  getStrategy,
  type Signal,
  type StrategyParams,
} from '../charting/engines';

/** Map a single strategy signal to a lightweight-charts marker. */
function toMarker(sig: Signal): SeriesMarker<Time> {
  switch (sig.kind) {
    case 'entry-long':
      return {
        time: sig.time as Time,
        position: 'belowBar',
        color: '#22c55e',
        shape: 'arrowUp',
        text: 'LE',
      };
    case 'exit-long':
      return {
        time: sig.time as Time,
        position: 'aboveBar',
        color: '#ef4444',
        shape: 'arrowDown',
        text: 'LX',
      };
    case 'entry-short':
      return {
        time: sig.time as Time,
        position: 'aboveBar',
        color: '#ef4444',
        shape: 'arrowDown',
        text: 'SE',
      };
    case 'exit-short':
      return {
        time: sig.time as Time,
        position: 'belowBar',
        color: '#22c55e',
        shape: 'arrowUp',
        text: 'SX',
      };
    default:
      return {
        time: sig.time as Time,
        position: 'inBar',
        color: '#94a3b8',
        shape: 'circle',
      };
  }
}

/**
 * Render strategy signal markers on the price pane.
 *
 * @param refs           the shared chart refs (uses `candleSeriesRef`).
 * @param candles        the canonical candle series fed to the engine.
 * @param strategyId     the applied strategy id, or null when none is applied.
 * @param strategyParams optional per-strategy parameter overrides.
 */
export function useStrategyMarkers(
  refs: ChartRefs,
  candles: ChartCandle[],
  strategyId: string | null | undefined,
  strategyParams?: StrategyParams,
): void {
  const { candleSeriesRef } = refs;
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  // Serialize params so the effect re-runs on a value change, not identity.
  const paramsKey = strategyParams ? JSON.stringify(strategyParams) : '';

  useEffect(() => {
    const series = candleSeriesRef.current;
    if (!series) return;

    // Lazily create a dedicated markers plugin instance bound to the price
    // series. Reused across renders so we never clobber unrelated markers.
    if (!markersRef.current) {
      try {
        markersRef.current = createSeriesMarkers(series, []);
      } catch {
        return;
      }
    }
    const plugin = markersRef.current;

    // No strategy applied → clear all of this plugin's markers (Req 8.8).
    if (!strategyId) {
      try {
        plugin.setMarkers([]);
      } catch {
        /* series detached */
      }
      return;
    }

    const def = getStrategy(strategyId);
    if (!def) {
      try {
        plugin.setMarkers([]);
      } catch {
        /* detached */
      }
      return;
    }

    const params = { ...def.defaults, ...(strategyParams ?? {}) };

    // Insufficient data → evaluate returns [] and we render no markers (Req 8.3).
    const signals = def.evaluate(candles, params);
    const markers = signals
      .map(toMarker)
      .sort((a, b) => (a.time as number) - (b.time as number));

    try {
      plugin.setMarkers(markers);
    } catch {
      /* series detached during teardown */
    }
  }, [candles, strategyId, paramsKey, candleSeriesRef, strategyParams]);

  // Detach the markers plugin on unmount so it does not leak onto a reused
  // series instance.
  useEffect(() => {
    return () => {
      try {
        markersRef.current?.setMarkers([]);
      } catch {
        /* detached */
      }
      try {
        markersRef.current?.detach();
      } catch {
        /* already detached */
      }
      markersRef.current = null;
    };
  }, []);
}
