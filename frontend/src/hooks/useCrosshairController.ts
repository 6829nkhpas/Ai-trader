// Feature: professional-charting-suite
//
// useCrosshairController — the rendering adapter for the Crosshair_Controller.
//
// It subscribes to the chart's crosshair-move events and, for the hovered time,
// produces a fully-formatted readout of the candle's OHLC plus every active
// indicator's value at that time. All value/placeholder decisions are delegated
// to the PURE helpers in `charting/crosshair` so they stay property-testable:
//
//   - OHLC + indicator values are formatted to the instrument's configured
//     decimal precision (Requirements 10.1, 10.2);
//   - warm-up bars (no indicator point emitted yet) and out-of-range crosshair
//     positions (no candle at the time) yield a no-value placeholder
//     (Requirements 10.3, 10.8).
//
// The synchronized vertical crosshair across panes (Requirement 10.4) is
// provided intrinsically by lightweight-charts v5: every pane shares one time
// scale, so a crosshair at a given time renders a vertical line at the same
// time position in every pane. The chart is created with `CrosshairMode.Normal`
// in `useChartInit`, which enables that shared vertical crosshair.

import { useEffect, useMemo, useRef, useState } from 'react';
import type { MouseEventParams } from 'lightweight-charts';

import type { ChartRefs, ChartCandle } from '../utils/chartTypes';
import type { ActiveIndicator } from '../store/useChartUIStore';
import { useChartUIStore } from '../store/useChartUIStore';
import { getIndicator, type IndicatorPlot } from '../charting/engines';
import {
  buildCrosshairReadout,
  findCandleAt,
  EMPTY_CROSSHAIR_READOUT,
  DEFAULT_PRICE_PRECISION,
  type CrosshairReadout,
  type IndicatorReadoutInput,
} from '../charting/crosshair';

/** A precomputed indicator plot paired with its identity for a frame. */
interface ComputedIndicator {
  instanceId: string;
  indicatorId: string;
  label: string;
  plot: IndicatorPlot;
}

/**
 * Drive the crosshair readout for the price pane and its synchronized sub-panes.
 *
 * @param refs             shared chart refs (uses `chartRef`).
 * @param candles          the canonical candle series feeding the readout.
 * @param activeIndicators the active-indicator list for the current symbol.
 * @param precision        the instrument's configured decimal precision.
 * @returns the latest {@link CrosshairReadout}; placeholders fill warm-up and
 *          out-of-range positions.
 */
export function useCrosshairController(
  refs: ChartRefs,
  candles: ChartCandle[],
  activeIndicators: ActiveIndicator[],
  precision: number = DEFAULT_PRICE_PRECISION,
): { readout: CrosshairReadout } {
  const { chartRef } = refs;
  const [readout, setReadout] = useState<CrosshairReadout>(EMPTY_CROSSHAIR_READOUT);

  // Compute each active indicator's plot once per candles/indicator change.
  // The crosshair handler then only does an O(points) lookup per move, keeping
  // the per-move cost well inside the 100 ms readout budget (Req 10.1).
  const computed: ComputedIndicator[] = useMemo(() => {
    const out: ComputedIndicator[] = [];
    for (const ind of activeIndicators) {
      const def = getIndicator(ind.indicatorId);
      if (!def) continue;
      let plot: IndicatorPlot;
      try {
        plot = def.compute(candles, { ...def.defaults, ...ind.params });
      } catch {
        continue;
      }
      out.push({
        instanceId: ind.instanceId,
        indicatorId: ind.indicatorId,
        label: def.name ?? ind.indicatorId,
        plot,
      });
    }
    return out;
    // `activeIndicators` identity changes when params/structure change.
  }, [candles, activeIndicators]);

  // Keep the latest inputs in refs so the (stable) crosshair handler reads
  // current data without resubscribing on every candle tick.
  const candlesRef = useRef<ChartCandle[]>(candles);
  candlesRef.current = candles;
  const computedRef = useRef<ComputedIndicator[]>(computed);
  computedRef.current = computed;
  const precisionRef = useRef<number>(precision);
  precisionRef.current = precision;

  useEffect(() => {
    let activeChart: import('lightweight-charts').IChartApi | null = null;
    let handler: ((param: MouseEventParams) => void) | null = null;

    // The chart instance is created asynchronously by useChartInit; poll until
    // it exists, then subscribe exactly once.
    const interval = setInterval(() => {
      const chart = chartRef.current;
      if (!chart) return;
      clearInterval(interval);
      activeChart = chart;

      handler = (param: MouseEventParams) => {
        const time =
          param && param.time !== undefined && param.time !== null
            ? (param.time as unknown as number)
            : null;

        const indicators: IndicatorReadoutInput[] = computedRef.current.map((c) => ({
          instanceId: c.instanceId,
          indicatorId: c.indicatorId,
          label: c.label,
          plot: c.plot,
        }));

        const next = buildCrosshairReadout({
          time,
          candles: candlesRef.current,
          indicators,
          precision: precisionRef.current,
        });

        // Publish the hovered candle's raw OHLC so the page header readout
        // tracks the crosshair live (Requirement 10.1). Cleared to null when the
        // crosshair is over empty space / off the chart.
        const hovered = findCandleAt(candlesRef.current, time);
        useChartUIStore.getState().setHoverOhlc(
          hovered
            ? {
                open: hovered.open,
                high: hovered.high,
                low: hovered.low,
                close: hovered.close,
                time: hovered.time,
              }
            : null,
        );

        // Avoid redundant state churn when the resolved time is unchanged and
        // there is still no candle (crosshair hovering empty space).
        setReadout((prev) =>
          prev.time === next.time && !prev.hasCandle && !next.hasCandle
            ? prev
            : next,
        );
      };

      chart.subscribeCrosshairMove(handler);
    }, 100);

    return () => {
      clearInterval(interval);
      // Clear any lingering hover readout when the chart unmounts.
      useChartUIStore.getState().setHoverOhlc(null);
      if (activeChart && handler) {
        try {
          activeChart.unsubscribeCrosshairMove(handler);
        } catch {
          /* unmount race — chart already disposed */
        }
      }
    };
  }, [chartRef]);

  return { readout };
}
