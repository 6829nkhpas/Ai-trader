'use client';

// Feature: terminal-ux-overhaul (Task 5.1 + per-pane controls)
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
//   · the pane is CHROME-FREE: it renders nothing but its chart. Each pane still
//     has its own independent symbol / timeframe / chartType (R4.3) — they live in
//     `useChartUIStore.panes[]` and are driven by the command bar and the left
//     panel, which route to whichever pane is active. An earlier revision put a
//     per-pane header with its own timeframe and chart-type dropdowns inside the
//     pane; it was removed deliberately to give the chart the full pane height, so
//     do not reintroduce one here.
//   · if the pane fails to initialize, an inline error placeholder is rendered
//     WITHIN this pane only, so the sibling pane and the rest of the terminal
//     keep working (Error Handling: "Pane mount failure").

import React from 'react';
import { AlertTriangle } from 'lucide-react';
import MainTerminalChart from '../MainTerminalChart';
import { type Timeframe } from '../../utils/chartTypes';
import { useTradeStore } from '../../store/useTradeStore';
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
 * own pane state, designates itself active on click, shows the emerald ring when
 * it is the Active_Pane, and exposes its own timeframe + chart-type controls.
 */
export default function ChartPane({ pane }: ChartPaneProps) {
  const activePaneId = useChartUIStore((s) => s.activePaneId);
  const setActivePane = useChartUIStore((s) => s.setActivePane);
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);

  const isActive = activePaneId === pane.id;
  const paneSymbol = pane.symbol || selectedSymbol || '';

  // Use onMouseDownCapture instead of onClick so the pane becomes active BEFORE
  // the TradingView iframe swallows the interaction. `onClick` on the wrapper
  // fires only for clicks that bubble out of the iframe's document — clicks on
  // candles / price scale / time scale are handled inside the iframe and never
  // reach this handler, so the active pane never updates when the user actually
  // interacts with the chart. Mouse-down capture fires on the wrapper for every
  // pointer press inside the pane (including over the iframe) because the
  // capture phase runs before the iframe's content receives the event.
  const handleActivate = () => setActivePane(pane.id);

  return (
    <div
      data-pane-id={pane.id}
      data-active={isActive}
      onMouseDownCapture={handleActivate}
      onClick={handleActivate}
      className={`flex h-full w-full flex-col overflow-hidden bg-surface transition-shadow ${
        isActive ? 'ring-2 ring-inset ring-emerald-500/70' : 'ring-1 ring-inset ring-border-default'
      }`}
    >
      {/* The independent chart instance for this pane. */}
      <div className="relative min-h-0 flex-1">
        <PaneErrorBoundary paneId={pane.id}>
          <MainTerminalChart
            symbolOverride={paneSymbol || undefined}
            timeframeOverride={pane.timeframe as Timeframe}
            chartTypeOverride={pane.chartType}
          />
        </PaneErrorBoundary>
      </div>
    </div>
  );
}
