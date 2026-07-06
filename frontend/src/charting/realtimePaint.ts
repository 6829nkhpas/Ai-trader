// Feature: professional-charting-suite
//
// Pure realtime-paint classification for the price renderer (Requirements 9.3,
// 9.4, 9.5, 9.6).
//
// `useChartDataSync` paints each new canonical snapshot relative to the one it
// last painted. The *decision* of HOW to paint — update the last candle in
// place, append-and-follow, or repaint from the canonical series — is pure and
// lives here so it can be unit-tested without React, lightweight-charts, or the
// Tauri runtime (mirroring the `charting/zoom` helper pattern):
//
//   - 'update'  — only the most-recent candle changed in place → `series.update()`
//                 (Requirement 9.3: no re-render of the earlier candles);
//   - 'append'  — exactly one newer candle was added → `series.update()` plus a
//                 right-edge follow when the view was already at the latest bar
//                 (Requirement 9.5);
//   - 'repaint' — anything else (an out-of-order/earlier candle changed, a
//                 reorder, or a shrink) → full `setData()` from the canonical
//                 series, which can never raise (Requirement 9.6).

import { applyLatestCandleUpdate } from './engines/canonicalCandles';
import type { ChartCandle } from './types';

export type RealtimePaintKind = 'update' | 'append' | 'repaint';

/** Structural candle equality on the fields the chart renders. */
export function sameCandle(a: ChartCandle, b: ChartCandle): boolean {
  return (
    a.time === b.time &&
    a.open === b.open &&
    a.high === b.high &&
    a.low === b.low &&
    a.close === b.close
  );
}

/**
 * Classify the transition from the previously painted series to the next one.
 *
 * The leading candles must be unchanged for an in-place `update`/`append`;
 * otherwise an out-of-order or historical change occurred and we repaint from
 * the canonical series. The tail decision (update vs append vs repaint) is
 * delegated to the canonical `applyLatestCandleUpdate` helper.
 */
export function classifyRealtimePaint(
  prev: ChartCandle[],
  next: ChartCandle[],
): RealtimePaintKind {
  if (prev.length === 0 || next.length === 0) return 'repaint';

  const delta = next.length - prev.length;

  // Only a same-length (in-place) change or a single append can be incremental;
  // any other size change is a structural repaint.
  if (delta !== 0 && delta !== 1) return 'repaint';

  // Verify the shared prefix is byte-for-byte unchanged. A change anywhere but
  // the tail means an earlier candle was rewritten (out-of-order tick) and must
  // trigger a repaint (Requirement 9.6).
  const prefixLen = delta === 0 ? prev.length - 1 : prev.length;
  for (let i = 0; i < prefixLen; i++) {
    if (!sameCandle(prev[i], next[i])) return 'repaint';
  }

  // The prefix is intact — let the canonical helper classify the tail candle.
  const { kind } = applyLatestCandleUpdate(prev, next[next.length - 1]);
  if (kind === 'update' && delta !== 0) return 'repaint';
  if (kind === 'append' && delta !== 1) return 'repaint';
  return kind;
}

/**
 * Decide whether the viewport was parked at the right edge before a mutation.
 *
 * `rangeTo` is the (logical) index of the right-most visible bar and
 * `lastIndex` is the index of the latest candle currently painted. We treat
 * "within one bar of the end" as the right edge so the `rightOffset` breathing
 * room kept by the time scale still counts as following the latest bar
 * (Requirement 9.5).
 */
export function isViewAtRightEdge(rangeTo: number, lastIndex: number): boolean {
  return rangeTo >= lastIndex;
}

/**
 * Decide whether the renderer should scroll to keep a freshly appended candle
 * visible. We only follow on an `append` and only when the view was already
 * pinned to the latest bar before the append (Requirement 9.5). In-place
 * updates and repaints never move the viewport.
 */
export function shouldFollowRightEdge(
  kind: RealtimePaintKind,
  wasAtRightEdge: boolean,
): boolean {
  return kind === 'append' && wasAtRightEdge;
}
