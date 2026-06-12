// Feature: professional-charting-suite
//
// useZoomClamp — wires the pure wheel-zoom clamp (Requirement 10.6) to the
// chart's time scale. It subscribes to visible-logical-range changes (which
// fire on wheel zoom, pinch, and drag) and, whenever the visible candle count
// would fall below 5 or rise above 5,000, re-applies the clamped range so the
// zoom level is constrained while preserving the cursor-centered midpoint.
//
// The pure math lives in `charting/zoom.ts` so it is testable in isolation
// (Property 30). This hook is the thin rendering adapter that connects that
// math to `lightweight-charts`.

import { useEffect, useRef } from 'react';
import type { IChartApi, LogicalRange } from 'lightweight-charts';
import { clampVisibleRange } from '../charting/zoom';

/**
 * Constrain the chart's visible candle count to [5, 5000] (Requirement 10.6).
 *
 * @param chartRef ref to the `lightweight-charts` chart instance created by
 *   {@link useChartInit}. Safe to call before the chart exists; the effect
 *   re-runs are gated on the ref being populated.
 */
export function useZoomClamp(chartRef: React.RefObject<IChartApi | null>): void {
  // Guards against the feedback loop our own setVisibleLogicalRange would
  // otherwise trigger (it re-fires the subscription synchronously).
  const applyingRef = useRef(false);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    const timeScale = chart.timeScale();

    const handler = (range: LogicalRange | null) => {
      if (!range || applyingRef.current) return;

      const clamped = clampVisibleRange(range.from, range.to);
      // No change needed — the span is already within bounds.
      if (clamped.from === range.from && clamped.to === range.to) return;

      applyingRef.current = true;
      try {
        timeScale.setVisibleLogicalRange(clamped);
      } finally {
        // Release on the next frame so the re-entrant change our own
        // setVisibleLogicalRange emits is ignored rather than re-clamped.
        requestAnimationFrame(() => {
          applyingRef.current = false;
        });
      }
    };

    timeScale.subscribeVisibleLogicalRangeChange(handler);
    return () => {
      timeScale.unsubscribeVisibleLogicalRangeChange(handler);
    };
  }, [chartRef]);
}
