// Feature: professional-charting-suite, Property 7
//
// Property-based test for Property 7: "Oscillator panes stack in addition
// order" (Validates Requirement 3.2).
//
// For any sequence of `ensurePane(instanceId)` calls, the resulting
// `layout()` orders the indicator panes below the price pane in the exact
// order their instances were first added, top to bottom. Re-adding an existing
// instance is idempotent and must not change the established order.
//
// `createPaneManager` needs a `lightweight-charts` `IChartApi`. Since the pane
// ordering contract is independent of real rendering, we exercise the manager
// headlessly against a minimal in-memory fake chart that implements only the
// methods the manager calls: `addPane`, `removePane`, `timeScale()` and the
// `IPaneApi` surface (`getSeries`, `paneIndex`, `getStretchFactor`,
// `setStretchFactor`).

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import type { IChartApi } from 'lightweight-charts';

import { createPaneManager } from '@/charting/paneManager';

const RUNS = 100;

/**
 * A minimal in-memory pane that mirrors the `IPaneApi` methods PaneManager
 * touches. Panes are owned by {@link FakeChart}; `paneIndex()` is resolved
 * live from the chart's pane array so ordering reflects insertion order.
 */
class FakePane {
  private stretch = 1;
  private series: unknown[] = [];

  constructor(private readonly chart: FakeChart) {}

  paneIndex(): number {
    return this.chart.indexOf(this);
  }

  getStretchFactor(): number {
    return this.stretch;
  }

  setStretchFactor(value: number): void {
    this.stretch = value;
  }

  getSeries(): unknown[] {
    return this.series;
  }

  /** Test helper: attach a fake series so the pane is considered non-empty. */
  addFakeSeries(): void {
    this.series.push({});
  }
}

/**
 * A minimal in-memory chart implementing just the surface PaneManager uses.
 * New panes are appended to the end of the pane array, mirroring
 * `lightweight-charts`' "append below" semantics that anchor addition order.
 */
class FakeChart {
  // Index 0 stands in for the price pane the manager never owns.
  private readonly panes: FakePane[] = [new FakePane(this)];

  addPane(): FakePane {
    const pane = new FakePane(this);
    this.panes.push(pane);
    return pane;
  }

  removePane(index: number): void {
    this.panes.splice(index, 1);
  }

  indexOf(pane: FakePane): number {
    return this.panes.indexOf(pane);
  }

  timeScale() {
    return { setVisibleRange: () => {} };
  }
}

/** Build a manager bound to a fresh fake chart. */
function makeManager() {
  const chart = new FakeChart();
  return createPaneManager(chart as unknown as IChartApi);
}

/**
 * A sequence of `ensurePane` calls drawn from a small instance-id alphabet so
 * that duplicate (re-add) calls occur frequently, exercising idempotency.
 */
const instanceId = () => fc.constantFrom('rsi', 'macd', 'stoch', 'adx', 'atr', 'obv', 'cci');
const callSequence = () => fc.array(instanceId(), { minLength: 0, maxLength: 30 });

/** The order in which distinct instance ids first appear in a sequence. */
function firstAppearanceOrder(calls: string[]): string[] {
  const seen = new Set<string>();
  const order: string[] = [];
  for (const id of calls) {
    if (!seen.has(id)) {
      seen.add(id);
      order.push(id);
    }
  }
  return order;
}

describe('Property 7: oscillator panes stack in addition order', () => {
  it('layout order matches the order instances were first added', () => {
    fc.assert(
      fc.property(callSequence(), (calls) => {
        const mgr = makeManager();

        // Record the paneId returned for each instance as it is ensured.
        const instanceToPane = new Map<string, string>();
        for (const id of calls) {
          const paneId = mgr.ensurePane(id);
          if (!instanceToPane.has(id)) {
            instanceToPane.set(id, paneId);
          } else {
            // Re-adding an existing instance must return the same pane.
            expect(paneId).toBe(instanceToPane.get(id));
          }
        }

        const expectedPaneOrder = firstAppearanceOrder(calls).map(
          (id) => instanceToPane.get(id)!,
        );

        const layout = mgr.layout();

        // One pane per distinct instance — no duplicates created on re-add.
        expect(layout.length).toBe(expectedPaneOrder.length);

        // `order` field is a contiguous 0..n-1 top→bottom sequence.
        expect(layout.map((l) => l.order)).toEqual(
          expectedPaneOrder.map((_, i) => i),
        );

        // The pane ids, read top→bottom, are exactly the addition order.
        const actualPaneOrder = [...layout]
          .sort((a, b) => a.order - b.order)
          .map((l) => l.paneId);
        expect(actualPaneOrder).toEqual(expectedPaneOrder);
      }),
      { numRuns: RUNS },
    );
  });

  it('addition order is stable regardless of how many times instances are re-ensured', () => {
    fc.assert(
      fc.property(
        fc.array(instanceId(), { minLength: 1, maxLength: 7 }),
        fc.array(instanceId(), { minLength: 0, maxLength: 30 }),
        (initial, extraReAdds) => {
          const mgr = makeManager();

          // Establish a baseline order from the unique initial ids.
          const baseOrder = firstAppearanceOrder(initial);
          for (const id of initial) mgr.ensurePane(id);

          const baselinePaneIds = [...mgr.layout()]
            .sort((a, b) => a.order - b.order)
            .map((l) => l.paneId);

          // Re-ensuring only previously-added instances must not reorder panes
          // nor create new ones.
          for (const id of extraReAdds) {
            if (baseOrder.includes(id)) mgr.ensurePane(id);
          }

          const afterPaneIds = [...mgr.layout()]
            .sort((a, b) => a.order - b.order)
            .map((l) => l.paneId);

          expect(afterPaneIds).toEqual(baselinePaneIds);
        },
      ),
      { numRuns: RUNS },
    );
  });
});
