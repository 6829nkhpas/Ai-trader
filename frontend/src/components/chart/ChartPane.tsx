'use client';

// Feature: terminal-ux-overhaul (Task 5.1)
//
// ChartPane — one independent chart within the Split_Chart_View (Requirement 4).
//
// Each ChartPane mounts its OWN `MainTerminalChart` instance, driven by its own
// `ChartPaneState` ({ id, symbol, timeframe, chartType }) from `useChartUIStore`.
// The panes are fully independent in this phase (AD-4 / R4.8):
//   · no shared refs and no synced crosshair — each pane is a separate React
//     component instance with its own chart instance (the parent container
//     gives each pane a stable React `key`, so React never reuses one pane's
//     chart for the other);
//   · clicking anywhere in the pane designates it the Active_Pane via
//     `setActivePane(id)` (R4.4, R4.5);
//   · the Active_Pane (activePaneId === id) shows the existing emerald ring
//     accent — no new colors are introduced (R8.4);
//   · if the pane fails to initialize, an inline error placeholder is rendered
//     WITHIN this pane only, so the sibling pane and the rest of the terminal
//     keep working (Error Handling: "Pane mount failure").
//
// The pane header strip surfaces the pane's own {symbol · timeframe · chartType}
// so each pane visibly reflects its independent state.

import React from 'react';
import { AlertTriangle } from 'lucide-react';
import MainTerminalChart from '../MainTerminalChart';
import type { Timeframe } from '../../utils/chartTypes';
import {
  useChartUIStore,
  type ChartPaneState,
  type PaneId,
} from '../../store/useChartUIStore';

interface ChartPaneProps {
  /** The independent state for this pane (symbol/timeframe/chartType). */
  pane: ChartPaneState;
}

// ── Per-pane Error Boundary ────────────────────────────────────────────
//
// A render error in one pane's chart must not unmount the sibling pane or the
// terminal. React error boundaries can only be class components, so this small
// boundary wraps each pane's chart and renders an inline placeholder on failure
// (Error Handling: "Pane mount failure"). It is scoped per-pane: it catches only
// the errors thrown by its own subtree.

interface PaneErrorBoundaryProps {
  paneId: PaneId;
  children: React.ReactNode;
}

interface PaneErrorBoundaryState {
  hasError: boolean;
}

class PaneErrorBoundary extends React.Component<
  PaneErrorBoundaryProps,
  PaneErrorBoundaryState
> {
  constructor(props: PaneErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): PaneErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error) {
    // Keep the failure local and observable without crashing the terminal.
    console.error(`[ChartPane ${this.props.paneId}] failed to initialize:`, error);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          role="alert"
          className="flex h-full w-full flex-col items-center justify-center gap-2 bg-surface px-4 text-center"
        >
          <AlertTriangle size={20} className="text-amber-400" />
          <span className="text-xs font-semibold text-text-primary">
            Chart pane {this.props.paneId} failed to load
          </span>
          <span className="text-[10px] text-text-muted">
            The other pane is unaffected. Try switching the instrument or timeframe.
          </span>
        </div>
      );
    }
    return this.props.children;
  }
}

/**
 * One independent chart pane. Renders its own `MainTerminalChart`, reflects its
 * own pane state, designates itself active on click, and shows the emerald ring
 * when it is the Active_Pane.
 */
export default function ChartPane({ pane }: ChartPaneProps) {
  const activePaneId = useChartUIStore((s) => s.activePaneId);
  const setActivePane = useChartUIStore((s) => s.setActivePane);

  const isActive = activePaneId === pane.id;

  return (
    <div
      data-pane-id={pane.id}
      data-active={isActive}
      onClick={() => setActivePane(pane.id)}
      className={`flex h-full w-full flex-col overflow-hidden bg-surface transition-[box-shadow] ${
        isActive ? 'ring-2 ring-inset ring-emerald-500/70' : 'ring-1 ring-inset ring-border-default'
      }`}
    >
      {/* Pane header — surfaces this pane's own independent state (R4.3). */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border-default bg-surface/80 px-2 py-1">
        <span
          className={`text-[9px] font-black uppercase tracking-wider ${
            isActive ? 'text-emerald-400' : 'text-text-muted'
          }`}
        >
          {pane.id}
        </span>
        <span className="text-[11px] font-bold text-text-primary">{pane.symbol}</span>
        <span className="text-[9px] font-semibold uppercase text-text-muted">
          {pane.timeframe}
        </span>
        <span className="text-[9px] uppercase text-text-muted">{pane.chartType}</span>
      </div>

      {/* The independent chart instance for this pane. */}
      <div className="relative min-h-0 flex-1">
        <PaneErrorBoundary paneId={pane.id}>
          <MainTerminalChart timeframe={pane.timeframe as Timeframe} />
        </PaneErrorBoundary>
      </div>
    </div>
  );
}
