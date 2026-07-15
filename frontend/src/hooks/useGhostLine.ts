import { useEffect, useRef, useState } from 'react';
import { useTradeStore } from '../store/useTradeStore';
import { useChartUIStore } from '../store/useChartUIStore';
import { computeGhostPoints } from './ghostLineComputation';

// ── Drawing helpers ──────────────────────────────────────────────────────

/**
 * Remove the given ghost entities from the chart.
 *
 * Returns the ids that FAILED to remove so the caller can keep tracking them
 * and retry on the next pass. Previously failures were swallowed silently,
 * which orphaned whole segment-sets on the chart — every subsequent redraw
 * then stacked a fresh projection on top, producing the ladder/fan of dashed
 * lines. Keeping the un-removed ids tracked makes clearing self-healing.
 */
function removeGhostSegments(chart: any, entityIds: string[]): string[] {
  const failed: string[] = [];
  for (const id of entityIds) {
    try {
      chart.removeEntity(id);
    } catch (err) {
      // Not necessarily fatal (the entity may already be gone), but do NOT
      // silently drop it — keep it so the next clear retries.
      console.warn('[GhostLine] removeEntity failed, will retry:', id, err);
      failed.push(id);
    }
  }
  return failed;
}

/**
 * Draw the ghost line as connected dashed `trend_line` segments.
 *
 * We deliberately use per-bar segments (NOT `polyline`/`path`): TradingView's
 * `polyline` auto-closes into a triangle and `path` collapses to a stub in this
 * build, whereas connected `trend_line` segments render reliably and extend
 * into the future whitespace. The points are already a smooth, bounded curve,
 * so the joined segments read as one continuous dashed line.
 *
 * `shouldAbort` is polled between each segment. If a newer draw supersedes this
 * one mid-flight we stop immediately and remove whatever we already drew, so a
 * stale run can never leave half a line behind (a source of the stacked-line
 * artefact).
 */
async function drawGhostSegments(
  chart: any,
  points: { time: number; price: number }[],
  singleSegment: boolean,
  shouldAbort: () => boolean,
): Promise<string[]> {
  const entityIds: string[] = [];

  // Keep strictly-increasing, de-duplicated points (guards against a session
  // boundary producing two points at the same timestamp).
  const clean: { time: number; price: number }[] = [];
  for (const p of points) {
    if (clean.length === 0 || p.time > clean[clean.length - 1].time) clean.push(p);
  }

  console.log(
    '[GhostLine] DRAW', clean.length, 'pts times=',
    clean.map((p) => p.time).join(','),
    'prices=', clean.map((p) => p.price).join(','),
  );
  if (clean.length < 2) return entityIds;

  // A straight line is ONE segment (anchor → end): a single entity that can't
  // fragment or ladder. A curved line is the consecutive point pairs.
  const pairs: [{ time: number; price: number }, { time: number; price: number }][] =
    singleSegment
      ? [[clean[0], clean[clean.length - 1]]]
      : clean.slice(0, -1).map((p, i) => [p, clean[i + 1]]);

  for (let i = 0; i < pairs.length; i++) {
    // Superseded mid-draw → undo what we've drawn and bail. This is what stops
    // a stale, slower run from leaving orphaned segments on the chart.
    if (shouldAbort()) {
      removeGhostSegments(chart, entityIds);
      return [];
    }
    try {
      const entityId = await chart.createMultipointShape(
        [
          { time: pairs[i][0].time, price: pairs[i][0].price },
          { time: pairs[i][1].time, price: pairs[i][1].price },
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

  // NOTE: we intentionally do NOT call setVisibleRange here. The user owns the
  // zoom; the projection length itself scales with the visible range (computed
  // upstream), so the line stays proportional without us fighting their pan/
  // zoom. Forcing a range here also caused a feedback loop with the zoom
  // subscription that drives redraws.

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

  // The single source of truth for what is currently on the chart. Every draw
  // clears this list first, so at most ONE ghost line ever exists.
  const entityIdsRef = useRef<string[]>([]);
  // Monotonic draw generation. Any run whose generation is no longer the latest
  // is "stale": it won't start a draw, and aborts (removing its own segments)
  // if it's already mid-draw.
  const genRef = useRef<number>(0);

  // ── Zoom pulse ───────────────────────────────────────────────────────
  // Re-project when the user zooms/pans so the line length tracks the visible
  // range (throttled so a drag doesn't thrash the async shape API).
  const [zoomPulse, setZoomPulse] = useState(0);
  useEffect(() => {
    if (!widget) return;
    const token = {};            // unique owner for unsubscribeAll
    let lastZoom = 0;
    let subscribed = false;
    widget.onChartReady(() => {
      try {
        widget.activeChart().onVisibleRangeChanged().subscribe(token, () => {
          const now = Date.now();
          if (now - lastZoom < 400) return;
          lastZoom = now;
          setZoomPulse((p) => p + 1);
        });
        subscribed = true;
      } catch { /* ignore */ }
    });
    return () => {
      if (!subscribed) return;
      try { widget.activeChart().onVisibleRangeChanged().unsubscribeAll(token); } catch { /* torn down */ }
    };
  }, [widget]);

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

    // This run owns generation `myGen`. It becomes stale the moment a newer run
    // bumps genRef, or when this effect is cleaned up (`cancelled`).
    let cancelled = false;
    const myGen = ++genRef.current;
    const isStale = () => cancelled || genRef.current !== myGen;

    widget.onChartReady(async () => {
      // Only the latest generation is allowed to draw. A superseded run bails
      // here before touching the chart, so two runs never both render.
      if (isStale()) return;

      const chart = widget.activeChart();

      // Read the CURRENT zoom window so the projection length can scale to it.
      // `from` is a UNIX-second timestamp of the left edge of the view.
      let visibleFromSec = 0;
      try {
        const vr = chart.getVisibleRange();
        if (vr && Number.isFinite(vr.from) && Number.isFinite(vr.to) && vr.to > vr.from) {
          visibleFromSec = vr.from;
        }
      } catch { /* chart not ready to report a range yet */ }

      // Read signals at run-time (not as a render subscription) so the effect
      // isn't re-fired by every predictive tick's new array reference.
      const predictiveSignals = useTradeStore.getState().predictiveSignals;
      const points = await computeGhostPoints(
        activeSymbol,
        effectiveTimeframe,
        ghostLineMode,
        predictiveSignals,
        visibleFromSec,
      );
      if (isStale()) return;

      // Straight engines (OLS 'linear' / VWLR 'volume') render as a SINGLE
      // trend_line entity (anchor → end) that can never fragment or ladder.
      // Curved engines ('curved' / 'forecast') draw the raw projection points
      // as connected segments — a handful of dashes, not a block.
      const isStraight = ghostLineMode === 'linear' || ghostLineMode === 'volume';

      try {
        // ALWAYS clear the currently-displayed ghost before drawing a new one.
        // Any ids that fail to remove stay tracked and are retried next time,
        // so nothing can silently accumulate.
        entityIdsRef.current = removeGhostSegments(chart, entityIdsRef.current);

        if (points.length < 2) {
          console.warn('[GhostLine] Not enough points:', points.length);
          return;
        }

        // Each draw tracks its own segment ids locally. If a newer run
        // supersedes this one mid-draw, drawGhostSegments aborts and removes
        // whatever it drew, so a stale run cleans up after itself.
        const ids = await drawGhostSegments(chart, points, isStraight, isStale);

        if (isStale()) {
          const failed = removeGhostSegments(chart, ids);
          entityIdsRef.current = [...entityIdsRef.current, ...failed];
          return;
        }

        entityIdsRef.current = [...entityIdsRef.current, ...ids];
        console.log('[GhostLine] Ghost line ready with', ids.length, 'segments');
      } catch (err) {
        console.error('[GhostLine] draw failed:', err);
      }
    });

    return () => {
      // Mark stale so any in-flight draw aborts and no queued run draws.
      cancelled = true;
      // Best-effort clear (on unmount the widget itself is usually torn down).
      try {
        const chart = widget.activeChart();
        entityIdsRef.current = removeGhostSegments(chart, entityIdsRef.current);
      } catch { /* widget may be removed */ }
    };
  }, [widget, activeSymbol, effectiveTimeframe, ghostLineMode, lastBarTime, predictiveKey, pulse, zoomPulse]);
}
