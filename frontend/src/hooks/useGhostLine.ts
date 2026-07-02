import { useEffect, useRef } from 'react';
import { useTradeStore } from '../store/useTradeStore';
import { useChartUIStore } from '../store/useChartUIStore';
import { computeGhostPoints } from './ghostLineComputation';

// ── Drawing helpers ──────────────────────────────────────────────────────

function removeGhostSegments(chart: any, entityIds: string[]): void {
  for (const id of entityIds) {
    try { chart.removeEntity(id); } catch { /* already removed */ }
  }
}

/**
 * Draw ghost line as connected dashed `trend_line` segments.
 *
 * We intentionally do NOT use Catmull-Rom spline interpolation here.
 * Catmull-Rom interpolates both time AND price between control points.
 * When control points span an NSE session boundary (e.g. 15:25 → next day
 * 09:20), the interpolated sub-point timestamps fall in the overnight gap
 * and TradingView hides them — resulting in flat horizontal dashes at the
 * same X position instead of diagonal segments.
 *
 * The VWEPR Rust engine already outputs curved control points (quadratic
 * regression). Connecting them with direct line segments creates a smooth
 * piecewise-linear approximation that matches the target look.
 */
async function drawGhostSegments(
  chart: any,
  points: { time: number; price: number }[],
): Promise<string[]> {
  console.log('[GhostLine] Drawing', points.length, 'control points as direct segments');

  const entityIds: string[] = [];

  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i];
    const p1 = points[i + 1];

    // Skip segments where both endpoints are at the same time
    // (can happen at session boundaries after remap)
    if (p0.time === p1.time) continue;

    try {
      const entityId = await chart.createMultipointShape(
        [
          { time: p0.time, price: p0.price },
          { time: p1.time, price: p1.price },
        ],
        {
          shape: 'trend_line',
          lock: true,
          disableSelection: true,
          disableSave: true,
          disableUndo: true,
          overrides: {
            linecolor: '#f59e0b',
            linewidth: 2,
            linestyle: 2,          // dashed
            showLabel: false,
            extendLeft: false,
            extendRight: false,
          },
        },
      );
      if (entityId !== null && entityId !== undefined) {
        entityIds.push(String(entityId));
        console.log(`[GhostLine] Segment ${i}: [${p0.time},${p0.price}] → [${p1.time},${p1.price}] id=${entityId}`);
      }
    } catch (err) {
      console.warn(`[GhostLine] Segment ${i} failed:`, err);
    }
  }

  // Scroll chart to show the projection
  if (entityIds.length > 0) {
    try {
      const intervalSec = points.length > 1 ? Math.abs(points[1].time - points[0].time) : 600;
      const fromSec = points[0].time - intervalSec * 30;
      const toSec   = points[points.length - 1].time + intervalSec * 3;
      // TV setVisibleRange expects milliseconds
      chart.setVisibleRange({ from: fromSec * 1000, to: toSec * 1000 });
      console.log('[GhostLine] setVisibleRange:', { fromSec, toSec });
    } catch (err) {
      console.warn('[GhostLine] setVisibleRange failed:', err);
    }
  }

  console.log('[GhostLine] Total segments drawn:', entityIds.length);
  return entityIds;
}

// ── Main Hook ─────────────────────────────────────────────────────────────

export function useGhostLine(
  widget: any,
  activeSymbol: string,
  effectiveTimeframe: string,
) {
  const predictiveSignals = useTradeStore((s) => s.predictiveSignals);
  const ghostLineMode     = useChartUIStore((s) => s.ghostLineMode);
  const entityIdsRef      = useRef<string[]>([]);
  const abortRef          = useRef<boolean>(false);

  useEffect(() => {
    if (!widget) {
      console.log('[GhostLine] widget is null — skipping');
      return;
    }

    console.log('[GhostLine] useEffect fired — symbol=', activeSymbol, 'tf=', effectiveTimeframe, 'mode=', ghostLineMode);
    abortRef.current = false;

    const run = async () => {
      const points = await computeGhostPoints(
        activeSymbol,
        effectiveTimeframe,
        ghostLineMode,
        predictiveSignals,
      );

      if (abortRef.current) return;

      if (points.length < 2) {
        console.warn('[GhostLine] Not enough points:', points.length);
        return;
      }

      widget.onChartReady(() => {
        if (abortRef.current) return;

        try {
          const chart = widget.activeChart();

          // Remove previous ghost line
          if (entityIdsRef.current.length > 0) {
            removeGhostSegments(chart, entityIdsRef.current);
            entityIdsRef.current = [];
          }

          drawGhostSegments(chart, points)
            .then((ids) => {
              if (!abortRef.current) {
                entityIdsRef.current = ids;
                console.log('[GhostLine] Ghost line ready with', ids.length, 'segments');
              } else {
                removeGhostSegments(chart, ids);
              }
            })
            .catch((err) => console.error('[GhostLine] drawGhostSegments threw:', err));
        } catch (err) {
          console.error('[GhostLine] chart.activeChart() failed:', err);
        }
      });
    };

    run();

    return () => {
      abortRef.current = true;
      if (entityIdsRef.current.length > 0) {
        try {
          const chart = widget.activeChart();
          removeGhostSegments(chart, entityIdsRef.current);
        } catch { /* widget may be removed */ }
        entityIdsRef.current = [];
      }
    };
  }, [widget, activeSymbol, effectiveTimeframe, ghostLineMode, predictiveSignals]);
}
