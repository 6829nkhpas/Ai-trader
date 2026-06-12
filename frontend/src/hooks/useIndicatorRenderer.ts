// Feature: professional-charting-suite
//
// useIndicatorRenderer — renders the active indicator set onto the chart by
// consuming `IndicatorEngine` output through the `PaneManager`.
//
// Responsibilities:
//   - Overlay indicators (kind 'overlay', paneId null) are drawn on the price
//     pane (pane index 0), rendering every constituent line and band edge the
//     indicator defines (Requirement 2.2, 2.4, 2.8).
//   - Oscillator indicators (kind 'oscillator') are drawn in a dedicated
//     Indicator_Pane created and ordered by the `PaneManager`, with their
//     reference levels drawn as horizontal price lines in that pane
//     (Requirement 3.5). Removing the last oscillator in a pane removes the
//     pane and redistributes the freed height (Requirement 3.6).
//
// The hook splits work across two effects so the expensive pane structure is
// only rebuilt when the active-indicator *set* changes, while per-frame data /
// style / visibility updates run cheaply on existing series.

import { useEffect, useRef } from 'react';
import {
  LineSeries,
  LineStyle,
  type ISeriesApi,
  type IPriceLine,
  type Time,
} from 'lightweight-charts';

import type { ChartRefs, ChartCandle } from '../utils/chartTypes';
import type { ActiveIndicator } from '../store/useChartUIStore';
import { createPaneManager, type PaneManager } from '../charting/paneManager';
import { getIndicator, type IndicatorPlot } from '../charting/engines';
import type { LinePoint, LineStyleSpec } from '../charting/types';

/** Per-instance render bookkeeping. */
interface InstanceRender {
  /** lineId → the lightweight-charts line series rendering it. */
  lines: Map<string, ISeriesApi<'Line'>>;
  /** Reference-level horizontal price lines (oscillators). */
  priceLines: IPriceLine[];
  /** The Indicator_Pane id for oscillators; null for price-pane overlays. */
  paneId: string | null;
  /** Whether this instance is an oscillator (dedicated pane) or an overlay. */
  oscillator: boolean;
}

/** Translate the engine's line-style token to the lightweight-charts enum. */
function toLineStyle(style: LineStyleSpec['lineStyle']): LineStyle {
  switch (style) {
    case 'dashed':
      return LineStyle.Dashed;
    case 'dotted':
      return LineStyle.Dotted;
    default:
      return LineStyle.Solid;
  }
}

/** Map a charting `LinePoint[]` to lightweight-charts line data. */
function toLineData(points: LinePoint[]): Array<{ time: Time; value: number }> {
  return points.map((p) => ({ time: p.time as Time, value: p.value }));
}

/**
 * A stable key describing the active-indicator *structure* (which instances
 * exist and which indicator each is). Changing this rebuilds panes/series.
 */
function structureKey(list: ActiveIndicator[]): string {
  return list.map((i) => `${i.instanceId}:${i.indicatorId}`).join('|');
}

/**
 * A key describing the per-instance *configuration* (params, style, visibility)
 * so the data effect re-runs when any of them changes without churning panes.
 */
function configKey(list: ActiveIndicator[]): string {
  return list
    .map(
      (i) =>
        `${i.instanceId}:${JSON.stringify(i.params)}:${i.style.color}:${i.style.lineWidth}:${i.style.lineStyle}:${i.visible ? 1 : 0}`,
    )
    .join('|');
}

/**
 * Render the active indicators for a symbol onto the chart.
 *
 * @param refs    the shared chart refs (uses `chartRef`).
 * @param candles the canonical candle series fed to every indicator.
 * @param active  the active-indicator list for the current symbol.
 */
export function useIndicatorRenderer(
  refs: ChartRefs,
  candles: ChartCandle[],
  active: ActiveIndicator[],
): void {
  const { chartRef } = refs;
  const paneManagerRef = useRef<PaneManager | null>(null);
  const registryRef = useRef<Map<string, InstanceRender>>(new Map());
  // The latest candles, read by the structure effect without depending on them.
  const candlesRef = useRef<ChartCandle[]>(candles);
  candlesRef.current = candles;

  const struct = structureKey(active);
  const config = configKey(active);

  // ── Structure effect: create/remove panes and instance registry entries ──
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;

    if (!paneManagerRef.current) {
      paneManagerRef.current = createPaneManager(chart);
    }
    const paneManager = paneManagerRef.current;
    const registry = registryRef.current;

    const desired = new Set(active.map((i) => i.instanceId));

    // Remove instances that are no longer active.
    for (const [instanceId, render] of Array.from(registry.entries())) {
      if (desired.has(instanceId)) continue;
      for (const series of render.lines.values()) {
        try {
          chart.removeSeries(series);
        } catch {
          /* already detached */
        }
      }
      render.lines.clear();
      render.priceLines = [];
      // Drop the pane once its series are gone (redistributes height, Req 3.6).
      if (render.oscillator && render.paneId) {
        try {
          paneManager.removePaneIfEmpty(render.paneId);
        } catch {
          /* pane already removed */
        }
      }
      registry.delete(instanceId);
    }

    // Add newly-active instances: reserve a pane for oscillators up front so
    // pane ordering follows addition order (Requirement 3.2).
    for (const ind of active) {
      if (registry.has(ind.instanceId)) continue;
      const def = getIndicator(ind.indicatorId);
      const oscillator = def?.kind === 'oscillator';
      let paneId: string | null = null;
      if (oscillator) {
        try {
          paneId = paneManager.ensurePane(ind.instanceId);
        } catch {
          paneId = null;
        }
      }
      registry.set(ind.instanceId, {
        lines: new Map(),
        priceLines: [],
        paneId,
        oscillator,
      });
    }
    // The data effect (below) populates series for any instance after this runs.
  }, [struct, chartRef, active]);

  // ── Data effect: compute plots and update series, styles, levels ─────────
  useEffect(() => {
    const chart = chartRef.current;
    const paneManager = paneManagerRef.current;
    if (!chart || !paneManager) return;

    const registry = registryRef.current;
    const layout = paneManager.layout();
    const paneIndexById = new Map<string, number>();
    // Price pane is index 0; indicator panes follow in `order` (0-based) → +1.
    for (const l of layout) paneIndexById.set(l.paneId, l.order + 1);

    for (const ind of active) {
      const render = registry.get(ind.instanceId);
      const def = getIndicator(ind.indicatorId);
      if (!render || !def) continue;

      const plot: IndicatorPlot = def.compute(candles, {
        ...def.defaults,
        ...ind.params,
      });

      const paneIndex = render.oscillator && render.paneId
        ? paneIndexById.get(render.paneId) ?? 0
        : 0;

      const baseStyle = {
        color: ind.style.color,
        lineWidth: ind.style.lineWidth as 1 | 2 | 3 | 4,
        lineStyle: toLineStyle(ind.style.lineStyle),
        visible: ind.visible,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      };

      // Render every constituent line the indicator defines (Requirement 2.8).
      const seenLineIds = new Set<string>();
      for (const ln of plot.lines) {
        seenLineIds.add(ln.id);
        let series = render.lines.get(ln.id);
        if (!series) {
          try {
            series = chart.addSeries(LineSeries, { ...baseStyle }, paneIndex);
          } catch {
            continue;
          }
          render.lines.set(ln.id, series);
        }
        try {
          series.applyOptions(baseStyle);
          series.setData(toLineData(ln.points));
        } catch {
          /* series detached */
        }
      }

      // Clear data for any previously-rendered line no longer in the plot
      // (e.g. the indicator dropped to insufficient data) without churning the
      // series instance, so a later recompute can repopulate it.
      for (const [lineId, series] of render.lines.entries()) {
        if (seenLineIds.has(lineId)) continue;
        try {
          series.setData([]);
        } catch {
          /* detached */
        }
      }

      // Reference levels (e.g. RSI 30/70) drawn as horizontal price lines in
      // the indicator's pane (Requirement 3.5). Re-created each run so they
      // track style/visibility changes.
      const anchor =
        render.lines.size > 0 ? render.lines.values().next().value : undefined;
      for (const pl of render.priceLines) {
        try {
          anchor?.removePriceLine(pl);
        } catch {
          /* detached */
        }
      }
      render.priceLines = [];
      if (anchor && ind.visible && plot.referenceLevels) {
        for (const level of plot.referenceLevels) {
          try {
            const pl = anchor.createPriceLine({
              price: level,
              color: 'rgba(148,163,184,0.5)',
              lineWidth: 1,
              lineStyle: LineStyle.Dashed,
              axisLabelVisible: true,
              title: String(level),
            });
            render.priceLines.push(pl);
          } catch {
            /* detached */
          }
        }
      }
    }
  }, [config, candles, chartRef, active]);

  // ── Teardown: remove every managed series/pane on unmount ────────────────
  useEffect(() => {
    return () => {
      const chart = chartRef.current;
      const registry = registryRef.current;
      const paneManager = paneManagerRef.current;
      for (const [, render] of registry) {
        for (const series of render.lines.values()) {
          try {
            chart?.removeSeries(series);
          } catch {
            /* detached */
          }
        }
        if (render.oscillator && render.paneId) {
          try {
            paneManager?.removePaneIfEmpty(render.paneId);
          } catch {
            /* removed */
          }
        }
      }
      registry.clear();
      paneManagerRef.current = null;
    };
  }, [chartRef]);
}
