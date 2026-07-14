import { useEffect, useRef, useState } from 'react';
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
 * Draw the ghost line as connected dashed `trend_line` segments.
 *
 * We deliberately use per-bar segments (NOT `polyline`/`path`): TradingView's
 * `polyline` auto-closes into a triangle and `path` collapses to a stub in this
 * build, whereas connected `trend_line` segments render reliably and extend
 * into the future whitespace. The points are already a smooth, bounded curve,
 * so the joined segments read as one continuous dashed line.
 */
async function drawGhostSegments(
  chart: any,
  points: { time: number; price: number }[],
): Promise<string[]> {
  const entityIds: string[] = [];

  // Keep strictly-increasing, de-duplicated points (guards against a session
  // boundary producing two points at the same timestamp).
  const clean: { time: number; price: number }[] = [];
  for (const p of points) {
    if (clean.length === 0 || p.time > clean[clean.length - 1].time) clean.push(p);
  }

  console.log('[GhostLine] Drawing', clean.length, 'points as connected segments');

  for (let i = 0; i < clean.length - 1; i++) {
    try {
      const entityId = await chart.createMultipointShape(
        [
          { time: clean[i].time,     price: clean[i].price },
          { time: clean[i + 1].time, price: clean[i + 1].price },
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
      if (entityId !== null && entityId !== undefined) entityIds.push(String(entityId));
    } catch (err) {
      console.warn(`[GhostLine] Segment ${i} failed:`, err);
    }
  }

  // Ensure the projection tail is visible — WITHOUT yanking the user's view.
  // TradingView's setVisibleRange / getVisibleRange are in UNIX *seconds*
  // (same unit as the shape point times), NOT milliseconds. The old code
  // multiplied by 1000, scrolling the chart to ~year 55446 so the line
  // vanished — that was the "ghost line not working" bug.
  if (entityIds.length > 0) {
    try {
      const intervalSec  = clean.length > 1 ? Math.abs(clean[1].time - clean[0].time) : 600;
      const projEndSec   = clean[clean.length - 1].time;
      const desiredToSec = projEndSec + intervalSec * 3;

      let current: { from: number; to: number } | null = null;
      try { current = chart.getVisibleRange(); } catch { current = null; }

      if (current && Number.isFinite(current.from) && Number.isFinite(current.to)) {
        if (desiredToSec > current.to) {
          chart.setVisibleRange(
            { from: current.from, to: desiredToSec },
            { applyDefaultRightMargin: false },
          );
        }
      } else {
        const fromSec = clean[0].time - intervalSec * 30;
        chart.setVisibleRange({ from: fromSec, to: desiredToSec });
      }
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
  const ghostLineMode = useChartUIStore((s) => s.ghostLineMode);

  // Redraw triggers (lightweight so we don't thrash the async shape API):
  //   · lastBarTime   advances only when a NEW bar forms for this symbol.
  //   · predictiveKey changes when a fresh backend predictive signal arrives.
  const lastBarTime = useTradeStore((s) => {
    const sym = activeSymbol.toUpperCase();
    let t = 0;
    for (const c of s.ohlcCandles) {
      if (c.symbol?.toUpperCase() === sym && c.start_timestamp_ms > t) t = c.start_timestamp_ms;
    }
    return t;
  });
  const predictiveKey = useTradeStore((s) => {
    const sym = activeSymbol.toUpperCase();
    for (let i = s.predictiveSignals.length - 1; i >= 0; i--) {
      const sig = s.predictiveSignals[i];
      if (sig.symbol?.toUpperCase() === sym) {
        return `${sig.target_timestamp_ms}:${sig.predicted_close_price}`;
      }
    }
    return '';
  });

  const entityIdsRef = useRef<string[]>([]);
  const runIdRef     = useRef<number>(0);

  // ── Realtime pulse ───────────────────────────────────────────────────
  // Re-project intra-bar as the live price ticks (throttled to ≤ 1 / 4s).
  const [pulse, setPulse] = useState(0);
  const lastCloseRef = useRef(0);
  const lastPulseRef = useRef(0);
  useEffect(() => {
    const sym = activeSymbol.toUpperCase();
    const unsub = useTradeStore.subscribe((s) => {
      let close = 0;
      let t = 0;
      for (const c of s.ohlcCandles) {
        if (c.symbol?.toUpperCase() === sym && c.start_timestamp_ms > t) {
          t = c.start_timestamp_ms;
          close = c.close;
        }
      }
      if (close === 0 || close === lastCloseRef.current) return;
      lastCloseRef.current = close;
      const now = Date.now();
      if (now - lastPulseRef.current < 4000) return;
      lastPulseRef.current = now;
      setPulse((p) => p + 1);
    });
    return () => unsub();
  }, [activeSymbol]);

  useEffect(() => {
    if (!widget) {
      console.log('[GhostLine] widget is null — skipping');
      return;
    }

    console.log('[GhostLine] useEffect fired — symbol=', activeSymbol, 'tf=', effectiveTimeframe, 'mode=', ghostLineMode);

    // Per-run cancellation token + run id. Prevents the "double line" race
    // where a stale async run finishes after a newer one and draws a second
    // line (a shared abort boolean would be reset by every re-run).
    let cancelled = false;
    const myRunId = ++runIdRef.current;
    const isStale = () => cancelled || runIdRef.current !== myRunId;

    const run = async () => {
      // Read signals at run-time (not as a render subscription) so the effect
      // isn't re-fired by every predictive tick's new array reference.
      const predictiveSignals = useTradeStore.getState().predictiveSignals;
      const points = await computeGhostPoints(
        activeSymbol,
        effectiveTimeframe,
        ghostLineMode,
        predictiveSignals,
      );

      if (isStale()) return;
      if (points.length < 2) {
        console.warn('[GhostLine] Not enough points:', points.length);
        return;
      }

      widget.onChartReady(() => {
        if (isStale()) return;
        try {
          const chart = widget.activeChart();

          if (entityIdsRef.current.length > 0) {
            removeGhostSegments(chart, entityIdsRef.current);
            entityIdsRef.current = [];
          }

          drawGhostSegments(chart, points)
            .then((ids) => {
              if (isStale()) {
                removeGhostSegments(chart, ids); // superseded — never keep two lines
                return;
              }
              entityIdsRef.current = ids;
              console.log('[GhostLine] Ghost line ready with', ids.length, 'segments');
            })
            .catch((err) => console.error('[GhostLine] drawGhostSegments threw:', err));
        } catch (err) {
          console.error('[GhostLine] chart.activeChart() failed:', err);
        }
      });
    };

    run();

    return () => {
      cancelled = true;
      if (entityIdsRef.current.length > 0) {
        try {
          const chart = widget.activeChart();
          removeGhostSegments(chart, entityIdsRef.current);
        } catch { /* widget may be removed */ }
        entityIdsRef.current = [];
      }
    };
  }, [widget, activeSymbol, effectiveTimeframe, ghostLineMode, lastBarTime, predictiveKey, pulse]);
}
