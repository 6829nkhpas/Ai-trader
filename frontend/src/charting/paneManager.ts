// Feature: professional-charting-suite
//
// PaneManager — wraps the `lightweight-charts` (v5) multi-pane API so the
// charting suite can host oscillator indicators in dedicated sub-panes that
// share the price pane's time scale.
//
// Responsibilities (Requirement 3.2, 3.3, 3.4, 3.6):
//   - create an Indicator_Pane per oscillator instance, stacked below the
//     price pane in the exact order the instances were added (3.2);
//   - keep every Indicator_Pane on the price pane's time axis — in v5 all panes
//     share a single time scale, so synchronization is intrinsic; the manager
//     exposes an explicit `syncVisibleRange` that applies one visible range to
//     that shared scale (3.3, 3.4);
//   - when an Indicator_Pane becomes empty, remove it and redistribute its
//     vertical space proportionally among the remaining panes so the heights
//     still fill the chart with no unallocated gap (3.6).
//
// The height-redistribution math is isolated in the pure, side-effect-free
// `redistribute` helper at the bottom of this file so it can be property-tested
// (Property 8) without instantiating a real chart. The manager methods are thin
// adapters over `lightweight-charts` that delegate the math to that helper.

import type { IChartApi, IPaneApi, IRange, Time } from 'lightweight-charts';

/**
 * The ordered layout of a single indicator pane.
 *
 * `heightFraction` is the pane's share of the total managed indicator-pane
 * height; across a layout the fractions sum to exactly 1.0. `order` is the
 * pane's top-to-bottom position among the indicator panes (0 = topmost,
 * directly under the price pane).
 */
export interface PaneLayout {
  paneId: string;
  heightFraction: number;
  order: number;
}

/**
 * A visible time range applied across all panes. In `lightweight-charts` v5
 * every pane shares one time scale, so applying this to the chart's time scale
 * synchronizes all panes (Requirement 3.3, 3.4).
 */
export type TimeRange = IRange<Time>;

/**
 * The pane-management surface consumed by the renderer / indicator manager.
 */
export interface PaneManager {
  /**
   * Ensure an Indicator_Pane exists for the given indicator instance and return
   * its stable paneId. The first call for an instance creates a new pane below
   * all existing panes (addition order, Requirement 3.2); subsequent calls for
   * the same instance return the same paneId.
   */
  ensurePane(instanceId: string): string;
  /**
   * Remove the pane if it no longer hosts any series, then redistribute the
   * freed vertical space among the remaining indicator panes (Requirement 3.6).
   * A pane that still has series is left untouched.
   */
  removePaneIfEmpty(paneId: string): void;
  /** The current indicator-pane layout, ordered top→bottom. */
  layout(): PaneLayout[];
  /**
   * Redistribute heights as if `removed` were gone, returning the resulting
   * layout (fractions sum to exactly 1.0). Delegates to the pure helper.
   */
  redistribute(removed: string): PaneLayout[];
  /** Apply one visible time range to the shared time scale (all panes). */
  syncVisibleRange(range: TimeRange): void;
}

/** Internal bookkeeping for a managed indicator pane. */
interface PaneRecord {
  paneId: string;
  /** The live `lightweight-charts` pane handle. */
  api: IPaneApi<Time>;
  /** Addition order among indicator panes (monotonic, 0-based). */
  order: number;
}

/**
 * Create a {@link PaneManager} bound to a `lightweight-charts` chart instance.
 *
 * The price pane (pane index 0) is owned by the chart, not the manager; every
 * pane created here is an Indicator_Pane appended below it. Pane handles are
 * looked up live from the chart so user-driven pane resizing is reflected in
 * {@link PaneManager.layout} and respected during redistribution.
 *
 * @param chart the chart whose panes are managed.
 */
export function createPaneManager(chart: IChartApi): PaneManager {
  // instanceId → paneId, so repeated ensurePane calls are idempotent.
  const instanceToPane = new Map<string, string>();
  // paneId → record, in insertion (addition) order thanks to Map semantics.
  const panes = new Map<string, PaneRecord>();
  let paneCounter = 0;
  let orderCounter = 0;

  /** Records sorted by their addition order (top→bottom). */
  function orderedRecords(): PaneRecord[] {
    return Array.from(panes.values()).sort((a, b) => a.order - b.order);
  }

  /**
   * Apply a layout's height fractions to the live panes via stretch factors.
   * Stretch factors are relative, so the fractions (which sum to 1.0) can be
   * used directly: `lightweight-charts` normalizes them into pixel heights.
   */
  function applyLayout(next: PaneLayout[]): void {
    for (const l of next) {
      const rec = panes.get(l.paneId);
      if (rec) {
        try {
          rec.api.setStretchFactor(l.heightFraction);
        } catch {
          // The pane handle may be detached during teardown; ignore.
        }
      }
    }
  }

  function ensurePane(instanceId: string): string {
    const existing = instanceToPane.get(instanceId);
    if (existing && panes.has(existing)) {
      return existing;
    }

    // Append a new pane below all existing panes (addition order → Req 3.2).
    const api = chart.addPane();
    const paneId = `indicator-pane-${paneCounter++}`;
    panes.set(paneId, { paneId, api, order: orderCounter++ });
    instanceToPane.set(instanceId, paneId);

    // Re-balance the indicator panes to equal heights on add; subsequent user
    // resizing and proportional redistribution-on-remove build on top of this.
    rebalanceEqually();
    return paneId;
  }

  /** Give every managed indicator pane an equal share of the height. */
  function rebalanceEqually(): void {
    const records = orderedRecords();
    const n = records.length;
    if (n === 0) return;
    const equal = 1 / n;
    applyLayout(
      records.map((r, i) => ({ paneId: r.paneId, heightFraction: equal, order: i })),
    );
  }

  function layout(): PaneLayout[] {
    const records = orderedRecords();
    if (records.length === 0) return [];

    // Read live stretch factors so user-driven resizing is reflected.
    const factors = records.map((r) => {
      try {
        const f = r.api.getStretchFactor();
        return Number.isFinite(f) && f > 0 ? f : 0;
      } catch {
        return 0;
      }
    });
    const total = factors.reduce((s, f) => s + f, 0);

    // Fall back to an equal split if the chart reports no usable factors.
    if (!(total > 0)) {
      const equal = 1 / records.length;
      return records.map((r, i) => ({
        paneId: r.paneId,
        heightFraction: equal,
        order: i,
      }));
    }

    return records.map((r, i) => ({
      paneId: r.paneId,
      heightFraction: factors[i] / total,
      order: i,
    }));
  }

  function removePaneIfEmpty(paneId: string): void {
    const rec = panes.get(paneId);
    if (!rec) return;

    // Only remove a pane that no longer hosts any series (Requirement 3.6).
    let seriesCount: number;
    try {
      seriesCount = rec.api.getSeries().length;
    } catch {
      seriesCount = 0;
    }
    if (seriesCount > 0) return;

    // Compute the post-removal layout BEFORE detaching the pane so we can read
    // the current proportions of every pane.
    const next = redistribute(layout(), paneId);

    // Detach the pane from the chart.
    try {
      chart.removePane(rec.api.paneIndex());
    } catch {
      // Already detached; continue with bookkeeping cleanup.
    }

    // Drop bookkeeping for the removed pane (and any instance pointing at it).
    panes.delete(paneId);
    for (const [instanceId, mappedPaneId] of instanceToPane) {
      if (mappedPaneId === paneId) instanceToPane.delete(instanceId);
    }

    // Renumber the remaining records so `order` stays contiguous, then apply
    // the redistributed heights so the panes fill the chart with no gap.
    orderedRecords().forEach((r, i) => {
      r.order = i;
    });
    orderCounter = panes.size;
    applyLayout(next);
  }

  function syncVisibleRange(range: TimeRange): void {
    // All panes share one time scale in v5, so setting the visible range on the
    // chart's time scale applies identically to every pane (Req 3.3, 3.4).
    try {
      chart.timeScale().setVisibleRange(range);
    } catch {
      // An out-of-bounds range can throw; the caller retains the prior view.
    }
  }

  return {
    ensurePane,
    removePaneIfEmpty,
    layout,
    redistribute: (removed: string) => redistribute(layout(), removed),
    syncVisibleRange,
  };
}

/**
 * Pure height-redistribution helper (Property 8 target).
 *
 * Given the current indicator-pane layout and the id of a pane being removed,
 * returns the layout for the remaining panes with the removed pane's vertical
 * space distributed **proportionally** among them — each surviving pane keeps
 * its relative size, and the freed share is split in proportion to those sizes.
 *
 * Guarantees:
 *   - the returned fractions sum to exactly 1.0 with no unallocated gap: the
 *     last pane in order absorbs any floating-point residual (Requirement 3.6);
 *   - `order` is renumbered to a contiguous 0..n-1 range, preserving the
 *     surviving panes' top-to-bottom order;
 *   - removing a pane not present in `layouts` simply renormalizes the existing
 *     panes to sum to 1.0;
 *   - an empty result is returned when no panes remain.
 *
 * The function is pure: it neither mutates `layouts` nor touches any chart.
 *
 * @param layouts the current pane layout (fractions need not sum to 1.0).
 * @param removed the paneId being removed.
 */
export function redistribute(layouts: PaneLayout[], removed: string): PaneLayout[] {
  const remaining = layouts
    .filter((l) => l.paneId !== removed)
    .sort((a, b) => a.order - b.order);

  const n = remaining.length;
  if (n === 0) return [];

  const total = remaining.reduce((sum, l) => sum + l.heightFraction, 0);
  // When the surviving fractions carry no usable weight, split evenly.
  const useEqual = !(total > 0);

  const result: PaneLayout[] = [];
  let allocated = 0;
  for (let i = 0; i < n; i++) {
    const l = remaining[i];
    let heightFraction: number;
    if (i === n - 1) {
      // The last pane absorbs the residual so the fractions sum to exactly 1.0.
      heightFraction = 1 - allocated;
    } else {
      heightFraction = useEqual ? 1 / n : l.heightFraction / total;
      allocated += heightFraction;
    }
    result.push({ paneId: l.paneId, heightFraction, order: i });
  }
  return result;
}
