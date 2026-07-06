'use client';

// Feature: terminal-ux-overhaul (Task 5.2)
//
// SplitChartContainer — the dual-pane chart layout (Requirement 4).
//
// Renders the Split_Chart_View as two fully-independent `ChartPane`s side by
// side, using the terminal's `react-resizable-panels` primitive (the same
// `Group`/`Panel`/`Separator` building blocks and divider styling already used
// by `FnoSection`, so the resize-handle look stays consistent — R5.4, R8.4).
//
// Design decisions honored here:
//   · The two panes come straight from `useChartUIStore.panes` ([A, B]); the
//     container holds no pane state of its own (single source of truth — AD-3,
//     R6.1).
//   · Exactly TWO panes are rendered in this phase — no more (AD-4, R4.2, R7.5).
//     Additional pane counts are deferred to future work.
//   · Each `ChartPane` is given a STABLE React `key` of its pane id, so React
//     never reuses one pane's chart instance for the other when the underlying
//     symbols change — keeping the two chart instances isolated (R4.8).
//   · The container accepts a `mode` prop typed to the split-enabled profiles
//     ('INTRADAY' | 'FNO'); split is only ever mounted in those modes (R4.7,
//     mode-gated at the store boundary and again where this is rendered).

import React from 'react';
import { Group, Panel, Separator } from 'react-resizable-panels';

import ChartPane from './ChartPane';
import { useChartUIStore } from '../../store/useChartUIStore';
import type { TradeProfile } from '../../store/useTradeStore';

/** The workspace profiles in which the Split_Chart_View is available (R4.7). */
export type SplitEnabledProfile = Extract<TradeProfile, 'INTRADAY' | 'FNO'>;

interface SplitChartContainerProps {
  /** The active workspace mode hosting the split view ('INTRADAY' | 'FNO'). */
  mode: SplitEnabledProfile;
}

/**
 * Two-pane split chart. Mounts one `ChartPane` per pane in the store, each with
 * an independent symbol/timeframe/chart type and a stable React key, separated
 * by a draggable divider that reuses the existing resize-handle styling.
 */
export default function SplitChartContainer({ mode }: SplitChartContainerProps) {
  const panes = useChartUIStore((s) => s.panes);

  // Exactly two panes this phase (R4.2, R7.5): index 0 = 'A' (left),
  // index 1 = 'B' (right). Destructure to make the two-pane contract explicit.
  const [paneA, paneB] = panes;

  return (
    <div
      data-split-mode={mode}
      className="flex h-full w-full min-h-0 flex-col bg-background"
    >
      <Group orientation="horizontal" className="h-full w-full min-h-0">
        <Panel defaultSize={50} minSize={20}>
          {/* Stable key = pane id so React keeps each chart instance isolated. */}
          <ChartPane key={paneA.id} pane={paneA} />
        </Panel>
        <Separator className="w-px cursor-col-resize bg-border-default transition-colors hover:bg-emerald-500/40 data-[separator]:w-1" />
        <Panel defaultSize={50} minSize={20}>
          <ChartPane key={paneB.id} pane={paneB} />
        </Panel>
      </Group>
    </div>
  );
}
