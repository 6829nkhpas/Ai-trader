'use client';

import React from 'react';
import { Square, Columns2 } from 'lucide-react';
import { useTradeStore } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';

/**
 * Single / Split segmented toggle for the chart command bar (Requirement 4).
 *
 * Switches the chart area between a single chart and the dual-pane
 * Split_Chart_View by driving `useChartUIStore.setSplitView`, and reads
 * `splitView` to highlight the active segment.
 *
 * The control is mode-gated (R4.7, R5.3): it renders only when the active
 * workspace profile is INTRADAY or FNO. In Swing / Investor it returns null
 * (hidden). The store's `setSplitView` independently enforces the same gating,
 * so this control can never enable split in an unsupported mode.
 *
 * Styling mirrors the neighboring `ChartModeToggle` segmented control: same
 * `bg-surface` / `border-border-default` tokens and emerald accent — no new
 * colors (R5.4 / R8.4).
 */
export default function SplitViewToggle() {
  const activeProfile = useTradeStore((s) => s.activeProfile);
  const splitView = useChartUIStore((s) => s.splitView);
  const setSplitView = useChartUIStore((s) => s.setSplitView);

  // Mode-gated: only Intraday and F&O support the split view (R4.7, R5.3).
  if (activeProfile !== 'INTRADAY' && activeProfile !== 'FNO') {
    return null;
  }

  const segments = [
    { id: 'single', label: 'Single', icon: Square, active: !splitView, on: false },
    { id: 'split', label: 'Split', icon: Columns2, active: splitView, on: true },
  ] as const;

  return (
    <div className="flex h-full items-center border-r border-border-default" role="group" aria-label="Chart layout">
      {segments.map(({ id, label, icon: Icon, active, on }) => (
        <button
          key={id}
          type="button"
          id={`split-view-${id}`}
          aria-pressed={active}
          onClick={() => setSplitView(on)}
          className={`flex h-full items-center gap-1.5 px-3 text-[11px] font-semibold transition-all ${
            active
              ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
              : 'bg-surface text-text-secondary hover:bg-elevated hover:text-text-primary'
          }`}
        >
          <Icon
            size={11}
            className={active ? 'text-emerald-600 dark:text-emerald-400' : 'text-text-muted'}
          />
          <span>{label}</span>
        </button>
      ))}
    </div>
  );
}
