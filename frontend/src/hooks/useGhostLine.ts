import { useEffect, useRef, useState } from 'react';
import { useTradeStore } from '../store/useTradeStore';
import { useChartUIStore } from '../store/useChartUIStore';
import { whenChartReady } from '../charting/widgetReady';
import { computeGhostPoints } from './ghostLineComputation';
import { debugLog } from '../lib/debugLog';

// ── Drawing helpers ──────────────────────────────────────────────────────

/**
 * Remove the given ghost entities from the chart.
 *
 * Returns the ids that FAILED to remove so the caller can keep tracking them
 * and retry on the next pass. Previously failures were swallowed silently,
 * which orphaned whole segment-sets on the chart — every subsequent redraw
 * then stacked a fresh projection on top, producing the ladder/fan of dashed
 * lines. Keeping the un-removed ids tracked makes clearing self-healing.
 *
 * Note: a returned "failed" id is NOT retried forever. The caller bounds
 * retries via `pruneFailedIds` (see below) so that ids which are permanently
 * invalid — e.g. ids that belonged to a previous widget that was torn down
 * and recreated — are dropped after a few consecutive failures instead of
 * accumulating in the ref and spamming warnings on every redraw.
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
 * Pure helper: decide which failed-to-remove ids are still worth retrying and
 * update the per-id attempt counter accordingly.
 *
 * Background: `removeEntity` can throw transiently (e.g. a shape still being
 * committed). Retrying on the next redraw self-heals those. But after a widget
 * teardown/recreate, ids from the dead widget are permanently invalid on the
 * new chart, so `removeEntity` throws on EVERY pass. Without bounding, those
 * dead ids live in `entityIdsRef.current` forever, warn on every redraw, and
 * can transiently let a new run clear a prior run's still-valid segments via
 * the shared ref. We therefore drop an id after `maxAttempts` consecutive
 * failures.
 *
 * Contract:
 *   - `failed`    — ids returned by `removeGhostSegments` this pass (each is
 *                   one more consecutive failure for that id).
 *   - `attempts`  — running per-id consecutive-failure counts. MUTATED in
 *                   place: incremented for every `failed` id, and entries are
 *                   deleted for ids that are dropped or that are not failed
 *                   this pass (a successful remove resets the counter).
 *   - `allTracked`— the full set of ids currently tracked in the ref, so we
 *                   can reset the attempt counter for ids that were NOT in
 *                   `failed` (i.e. they removed successfully this pass). May be
 *                   omitted when the caller handles resets itself.
 *   - returns     — the subset of `failed` still worth retrying (attempt count
 *                   < maxAttempts). An id that has failed `maxAttempts`
 *                   consecutive times is dropped from `attempts` and NOT
 *                   returned.
 */
export function pruneFailedIds(
  failed: string[],
  attempts: Map<string, number>,
  maxAttempts = 2,
  allTracked?: string[],
): string[] {
  // A successful remove resets (clears) that id's consecutive-failure counter.
  // Any tracked id not present in `failed` this pass removed cleanly.
  const failedSet = new Set(failed);
  if (allTracked) {
    for (const id of allTracked) {
      if (!failedSet.has(id)) attempts.delete(id);
    }
  } else {
    // Without the full tracked set we still drop counters for ids we know are
    // no longer failing (keeps the map from growing across healthy runs).
    for (const id of [...attempts.keys()]) {
      if (!failedSet.has(id)) attempts.delete(id);
    }
  }

  const retry: string[] = [];
  for (const id of failed) {
    const next = (attempts.get(id) ?? 0) + 1;
    if (next >= maxAttempts) {
      // Permanently invalid (e.g. belongs to a torn-down widget). The id has
      // now failed `maxAttempts` consecutive times — stop tracking it so the
      // ref can't accumulate dead ids forever. (An id that fails once is
      // retained for one retry; an id that fails maxAttempts times is dropped.)
      attempts.delete(id);
      console.warn(
        `[GhostLine] dropping id after ${maxAttempts} consecutive remove failures:`,
        id,
      );
      continue;
    }
    attempts.set(id, next);
    retry.push(id);
  }
  return retry;
}

/**
 * Pure, unit-testable decision: should a `onVisibleRangeChanged` event re-fire
 * the zoom pulse?
 *
 * TradingView auto-scrolls the right edge forward as live bars arrive, which
 * ALSO fires `onVisibleRangeChanged`. A new bar already bumps `lastBarTime`
 * (a redraw dep), so if we re-project on every range change we get 2–3
 * concurrent redraws per new bar — the source of the "ghost line thrash".
 *
 * We therefore pulse ONLY on genuine user zoom, NOT on programmatic
 * auto-scroll. TradingView's auto-scroll keeps the visible window's WIDTH
 * constant and just slides both edges forward by one bar as new bars arrive,
 * so the signature of "no user zoom" is: the range WIDTH (`to - from`) is
 * unchanged:
 *   · WIDTH (`to - from`) changed → user ZOOMED → pulse.
 *   · width unchanged → auto-scroll / no-op → do NOT pulse (`lastBarTime`
 *     already covers the new bar; a width-preserving move doesn't change the
 *     projection length anyway).
 * The first event (no `prev`) always pulses so we bootstrap on first range.
 *
 * Kept pure (no React, no widget) so it can be unit-tested directly.
 */
export function shouldPulseOnRangeChange(
  prev: { from: number; to: number } | null,
  next: { from: number; to: number },
): boolean {
  // First event — establish a baseline; pulse so the projection length tracks
  // the initial visible range.
  if (prev === null) return true;
  // User zoomed (range width changed) → re-project. A constant-width move is
  // either programmatic auto-scroll (new bar) or a width-preserving pan —
  // neither changes the projection length, so we skip both.
  return next.to - next.from !== prev.to - prev.from;
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

  debugLog(
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

  debugLog('[GhostLine] Total segments drawn:', entityIds.length);
  return entityIds;
}

// ── Draw-commit helper ──────────────────────────────────────────────────

/**
 * Decide the next `entityIdsRef` value and which ids to remove now, given the
 * outcome of a double-buffered draw.
 *
 * Drawing is async and slow (one IPC round-trip into the TradingView iframe
 * per segment). If we cleared the OLD line before drawing the NEW one, the
 * chart would be empty for the whole draw window — the line visibly vanishes
 * then reappears segment-by-segment = the "flicker / appears then disappears"
 * artefact. Instead we draw the new line FIRST, then swap, then remove the old
 * one, so there is never a frame where zero lines are on the chart.
 *
 * This helper is pure (no chart access) so it can be unit-tested without a
 * TradingView widget mock. It is called from the production draw path so the
 * unit tests guard the real id-lifecycle invariants, not a shadow copy.
 *
 * @param prevIds The ids that were on the chart BEFORE this draw started.
 * @param newIds The ids this draw just created (may be empty if it aborted).
 * @param stale  True if a newer run superseded this one while/after drawing.
 * @returns
 *   - `next`: the new value for `entityIdsRef.current` (who owns the chart now).
 *   - `removeNow`: the ids the caller should remove from the chart immediately.
 *     On success these are the prev ids; on a stale-after-draw these are the
 *     newly-drawn ids (the stale run hands ownership back to the prior run).
 *   The caller is responsible for folding any removeNow ids that FAIL to
 *   remove back into `next` (the self-healing retry), since this helper has no
 *   chart access.
 */
export function commitDraw(
  prevIds: string[],
  newIds: string[],
  stale: boolean,
): { next: string[]; removeNow: string[] } {
  if (stale) {
    // A newer run owns the chart. Throw away our just-drawn ids (the caller
    // removes them) and leave the prior ids in place — those still represent
    // the last good line and the newer run is responsible for replacing them.
    return { next: prevIds, removeNow: newIds };
  }
  // Success: the new line is the source of truth. Remove the old line now that
  // the new one is already on the chart (zero empty frames).
  return { next: newIds, removeNow: prevIds };
}

// ── Main Hook ─────────────────────────────────────────────────────────────

export function useGhostLine(
  widget: any,
  activeSymbol: string,
  effectiveTimeframe: string,
) {
  const ghostLineMode = useChartUIStore((s) => s.ghostLineMode);

  // Redraw triggers (lightweight so we don't thrash the async shape API):
  //   · lastBarTime advances only when a NEW bar forms for this symbol.
  //
  // NOTE: predictive signals are intentionally NOT a reactive redraw trigger.
  // They are read via `useTradeStore.getState().predictiveSignals` inside the
  // main effect (a non-reactive read), so a streaming signal does NOT re-fire
  // the effect. Previously a `predictiveKey` selector made every predictive
  // tick re-fire the effect immediately, bypassing the 4s `pulse` throttle and
  // causing a redraw storm. Signals are now consumed only on the throttled
  // cadence (lastBarTime / pulse / zoomPulse / mode·symbol·timeframe changes).
  const lastBarTime = useTradeStore((s) => {
    const sym = activeSymbol.toUpperCase();
    let t = 0;
    for (const c of s.ohlcCandles) {
      if (c.symbol?.toUpperCase() === sym && c.start_timestamp_ms > t) t = c.start_timestamp_ms;
    }
    return t;
  });

  // The single source of truth for what is currently on the chart. Each draw
  // is double-buffered: the NEW line is drawn first, then ownership swaps to
  // it, then the OLD line is removed — so at most ONE ghost line ever exists
  // and there is never an empty frame between draws.
  const entityIdsRef = useRef<string[]>([]);
  // Per-id consecutive-remove-failure counts. Lets us bound retries: an id
  // that fails to remove a few passes in a row is dropped from the ref (see
  // `pruneFailedIds`) so dead ids — e.g. from a torn-down widget — can't pile
  // up forever and warn on every redraw.
  const failedAttemptsRef = useRef<Map<string, number>>(new Map());
  // Monotonic draw generation. Any run whose generation is no longer the latest
  // is "stale": it won't start a draw, and aborts (removing its own segments)
  // if it's already mid-draw.
  const genRef = useRef<number>(0);

  // ── Zoom pulse ───────────────────────────────────────────────────────
  // Re-project when the user zooms so the line length tracks the visible
  // range. Throttled to 900ms so a drag doesn't thrash the async shape API.
  //
  // We IGNORE programmatic auto-scroll: TradingView slides the right edge
  // forward as live bars arrive, which fires `onVisibleRangeChanged` too. A
  // new bar already bumps `lastBarTime` (a redraw dep), so pulsing here on top
  // of that caused 2–3 concurrent redraws per new bar — the "ghost line
  // thrash". `shouldPulseOnRangeChange` pulses only when the visible range
  // WIDTH changes (a real user zoom); constant-width slides (auto-scroll on
  // a new bar, or a width-preserving pan that doesn't change the projection
  // length) are skipped.
  const [zoomPulse, setZoomPulse] = useState(0);
  useEffect(() => {
    if (!widget) return;
    const token = {};            // unique owner for unsubscribeAll
    let lastZoom = 0;
    let prevRange: { from: number; to: number } | null = null;
    // `disposed` guards the race where this effect cleans up BEFORE
    // `onChartReady` fires: if so, we never subscribe, and we never call
    // `setZoomPulse` on an unmounted effect.
    let disposed = false;
    // The unsubscribe handler from `subscribe`. Kept outside the ready
    // callback so cleanup can unsubscribe even if the chart became ready AFTER
    // cleanup began (otherwise the subscription leaks until the widget dies).
    let unsub: (() => void) | null = null;
    let subscribed = false;
    // `whenChartReady` guards the INVOCATION too, not just the callback body.
    // The bare `widget.onChartReady(...)` this replaces threw a TypeError when
    // the widget had already been removed, because the guards all lived inside
    // the callback. It also uses the non-deprecated `chartReady()` promise.
    whenChartReady(widget, () => {
      // Cleanup already ran — do NOT subscribe (would leak + setState on dead
      // effect).
      if (disposed) return;
      try {
        const stream = widget.activeChart().onVisibleRangeChanged();
        stream.subscribe(token, () => {
          let vr: { from: number; to: number } | null = null;
          try {
            const r = widget.activeChart().getVisibleRange();
            if (r && Number.isFinite(r.from) && Number.isFinite(r.to)) {
              vr = { from: r.from, to: r.to };
            }
          } catch { /* range not ready yet */ }
          if (vr === null) return;
          if (!shouldPulseOnRangeChange(prevRange, vr)) {
            // Still remember the range so the next genuine change is detected
            // against the latest position, not the stale baseline.
            prevRange = vr;
            return;
          }
          prevRange = vr;
          const now = Date.now();
          if (now - lastZoom < 900) return;   // throttle to ≤ ~1.1 / s
          lastZoom = now;
          setZoomPulse((p) => p + 1);
        });
        subscribed = true;
        unsub = () => {
          try { stream.unsubscribeAll(token); } catch { /* torn down */ }
        };
      } catch { /* ignore */ }
    });
    return () => {
      disposed = true;
      // If `onChartReady` already subscribed, drop the subscription via the
      // captured handler (the very same stream instance we subscribed to).
      // If `onChartReady` hasn't fired yet, `disposed` stops it from
      // subscribing later. `subscribed` is only ever set together with `unsub`
      // (consecutive lines in the same try block), so the bare-`subscribed`
      // branch is a defensive fallback in case a future edit splits them.
      if (subscribed && unsub) {
        unsub();
      } else if (subscribed) {
        try { widget.activeChart().onVisibleRangeChanged().unsubscribeAll(token); } catch { /* torn down */ }
      }
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
      debugLog('[GhostLine] widget is null — skipping');
      return;
    }

    debugLog('[GhostLine] useEffect fired — symbol=', activeSymbol, 'tf=', effectiveTimeframe, 'mode=', ghostLineMode);

    // This run owns generation `myGen`. It becomes stale the moment a newer run
    // bumps genRef, or when this effect is cleaned up (`cancelled`).
    let cancelled = false;
    const myGen = ++genRef.current;
    const isStale = () => cancelled || genRef.current !== myGen;

    whenChartReady(widget, async () => {
      // Only the latest generation is allowed to draw. A superseded run bails
      // here before touching the chart, so two runs never both render.
      if (isStale()) return;

      // The widget can be torn down between the time the ready callback was
      // scheduled and now. Guard against a nulled-out `widget._tradingViewApi`
      // (and other internal tear-down state) before touching the chart.
      if (!widget || !(widget as any).activeChart) return;
      let chart: any;
      try {
        chart = widget.activeChart();
      } catch (err) {
        console.warn('[GhostLine] activeChart() threw — widget torn down:', err);
        return;
      }
      if (!chart) return;

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
        // ── Double-buffered draw (no empty-frame flicker) ────────────────
        // TradingView's shape API is an async IPC into the iframe and is slow
        // (one round-trip per segment). If we CLEARED the old line BEFORE
        // drawing the new one, the chart would be empty for the whole draw
        // window → the line visibly vanishes then reappears segment-by-segment
        // (the "flicker / appears then disappears" artefact). Instead we draw
        // the NEW line first, then swap ownership, then remove the OLD line, so
        // there is never a frame where zero lines are on the chart.
        //
        // `drawGhostSegments` polls `shouldAbort` (= isStale) between each
        // segment and removes its own segments if aborted, so a stale run
        // cleans up after itself and never leaves half a line behind.

        if (points.length < 2) {
          console.warn('[GhostLine] Not enough points:', points.length);
          // Nothing new to draw — just clear the old line. (This is the one
          // case where an empty frame is unavoidable and correct.)
          const failedRemove = removeGhostSegments(chart, entityIdsRef.current);
          entityIdsRef.current = pruneFailedIds(
            failedRemove,
            failedAttemptsRef.current,
            2,
            entityIdsRef.current,
          );
          return;
        }

        // Snapshot the previously-displayed ids BEFORE drawing, so we can
        // remove them after the new line is on the chart.
        const prevIds = entityIdsRef.current;

        // Draw the new line first. If a newer run supersedes us mid-draw,
        // drawGhostSegments aborts and removes whatever it already drew,
        // returning [].
        const newIds = await drawGhostSegments(chart, points, isStraight, isStale);

        // Decide ownership via the pure `commitDraw` helper. Re-check isStale
        // here — a newer run may have bumped genRef while we were awaiting the
        // draw. `stale` is captured BEFORE we remove anything, so we don't
        // race between the check and the removal/assignment below.
        const stale = isStale();
        const { next, removeNow } = commitDraw(prevIds, newIds, stale);

        // Remove whichever ids the helper selected (the OLD line on success,
        // or the just-drawn NEW line on a stale-after-draw). Any ids that FAIL
        // to remove are folded back into `next` so they stay tracked and get
        // retried next pass — this is the self-healing that keeps the chart
        // from accumulating orphaned segments. `pruneFailedIds` bounds those
        // retries so a permanently-invalid id (e.g. from a torn-down widget)
        // is eventually dropped instead of warning forever.
        const failed = removeGhostSegments(chart, removeNow);
        const retry = pruneFailedIds(
          failed,
          failedAttemptsRef.current,
          2,
          next,
        );
        entityIdsRef.current = [...next, ...retry];
        if (!stale) {
          debugLog('[GhostLine] Ghost line ready with', newIds.length, 'segments');
        }
      } catch (err) {
        console.error('[GhostLine] draw failed:', err);
      }
    }, isStale, 'GhostLine');

    return () => {
      // Mark stale so any in-flight draw aborts and no queued run draws.
      cancelled = true;

      // Clear the segments we own — but only FORGET the ids we actually managed
      // to remove.
      //
      // This effect re-runs on every symbol / timeframe / mode change, and (now
      // that the widget is no longer rebuilt per symbol) the chart is usually
      // still ALIVE across those re-runs. The previous version cleared the ref
      // unconditionally: any `removeEntity` that failed left its segment on the
      // chart with nothing tracking it, and the next draw stacked a fresh line
      // on top — the reported "two ghost lines". So:
      //
      //  · chart reachable → keep the ids that failed to remove so the next
      //    pass retries them (`pruneFailedIds` still bounds those retries, so a
      //    permanently-invalid id is dropped after a couple of attempts rather
      //    than warning forever).
      //  · chart gone (widget removed) → the ids belong to a dead chart and can
      //    never be removed, so drop everything. Retaining them would make
      //    `removeEntity` throw on every redraw of the next widget.
      // Narrowed to what the cleanup actually needs, so this isn't another `any`.
      let chart: { removeEntity: (id: string) => void } | null = null;
      try {
        chart = widget.activeChart();
      } catch { /* widget already removed */ }

      if (chart) {
        const failed = removeGhostSegments(chart, entityIdsRef.current);
        entityIdsRef.current = pruneFailedIds(
          failed,
          failedAttemptsRef.current,
          2,
          entityIdsRef.current,
        );
      } else {
        entityIdsRef.current = [];
        failedAttemptsRef.current.clear();
      }
    };
  }, [widget, activeSymbol, effectiveTimeframe, ghostLineMode, lastBarTime, pulse, zoomPulse]);
}
