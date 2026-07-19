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
//   · the pane header carries its OWN timeframe and chart-type controls so each
//     pane can chart a different instrument at a different timeframe/type at the
//     same time (R4.3) — these drive the per-pane store setters directly and do
//     NOT touch the global command-bar selections;
//   · if the pane fails to initialize, an inline error placeholder is rendered
//     WITHIN this pane only, so the sibling pane and the rest of the terminal
//     keep working (Error Handling: "Pane mount failure").

import React, { useState } from 'react';
import { AlertTriangle, Clock, CandlestickChart, ChevronDown } from 'lucide-react';
import MainTerminalChart from '../MainTerminalChart';
import { CHART_TYPE_LABELS } from './ChartTypeSelector';
import { CHART_TYPES, type ChartType } from '../../charting/engines';
import { TIMEFRAME_GROUPS, type Timeframe } from '../../utils/chartTypes';
import { useOutsideClose } from '../../hooks/useOutsideClose';
import { type ChartTimeframe, useTradeStore } from '../../store/useTradeStore';
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

// ── Per-pane timeframe selector (compact) ──────────────────────────────
// Drives `setPaneTimeframe(paneId, tf)` so this pane's timeframe is fully
// independent of the other pane and of the global command-bar timeframe.
function PaneTimeframeSelect({ paneId, value }: { paneId: PaneId; value: ChartTimeframe }) {
  const [open, setOpen] = useState(false);
  const ref = useOutsideClose<HTMLDivElement>(() => setOpen(false));
  const setPaneTimeframe = useChartUIStore((s) => s.setPaneTimeframe);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-label="Pane timeframe"
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1 rounded-none border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider transition-colors ${
          open
            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
            : 'border-border-default bg-surface text-text-secondary hover:bg-elevated hover:text-text-primary'
        }`}
      >
        <Clock size={9} className="text-text-muted" />
        <span>{value}</span>
        <ChevronDown size={9} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-px w-52 rounded-none border border-border-default bg-surface/95 p-2 shadow-2xl backdrop-blur-xl">
          {TIMEFRAME_GROUPS.map((group) => (
            <div key={group.label} className="mb-2 last:mb-0">
              <div className="px-1 pb-1 text-[9px] font-bold uppercase tracking-wider text-text-muted/80">
                {group.label}
              </div>
              <div className="grid grid-cols-3 gap-1">
                {group.items.map((item) => {
                  const isActive = value === item.tf;
                  return (
                    <button
                      key={item.tf}
                      type="button"
                      onClick={() => {
                        setPaneTimeframe(paneId, item.tf as ChartTimeframe);
                        setOpen(false);
                      }}
                      className={`rounded-none border px-1.5 py-1 text-[10px] transition-colors ${
                        isActive
                          ? 'border-emerald-500/30 bg-emerald-500/10 font-bold text-emerald-600 dark:text-emerald-400'
                          : 'border-transparent bg-card/40 text-text-secondary hover:bg-elevated hover:text-text-primary'
                      }`}
                    >
                      {item.tf}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Per-pane chart-type selector (compact) ─────────────────────────────
// Drives `setPaneChartType(paneId, type)` independently of the other pane.
function PaneChartTypeSelect({ paneId, value }: { paneId: PaneId; value: ChartType }) {
  const [open, setOpen] = useState(false);
  const ref = useOutsideClose<HTMLDivElement>(() => setOpen(false));
  const setPaneChartType = useChartUIStore((s) => s.setPaneChartType);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        aria-label="Pane chart type"
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1 rounded-none border px-1.5 py-0.5 text-[9px] font-semibold transition-colors ${
          open
            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
            : 'border-border-default bg-surface text-text-secondary hover:bg-elevated hover:text-text-primary'
        }`}
      >
        <CandlestickChart size={9} className="text-text-muted" />
        <span>{CHART_TYPE_LABELS[value]}</span>
        <ChevronDown size={9} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-px w-40 rounded-none border border-border-default bg-surface/95 p-1 shadow-2xl backdrop-blur-xl">
          {CHART_TYPES.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => {
                setPaneChartType(paneId, t);
                setOpen(false);
              }}
              className={`flex w-full items-center justify-between rounded-none px-2 py-1 text-left text-[10px] transition-colors ${
                t === value
                  ? 'bg-primary/10 font-semibold text-primary'
                  : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
              }`}
            >
              <span>{CHART_TYPE_LABELS[t]}</span>
              {t === value && <span className="h-1.5 w-1.5 rounded-none bg-primary" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
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
