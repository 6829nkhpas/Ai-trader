'use client';

import React, { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { IoSquareOutline } from 'react-icons/io5';
import { BsWindowSplit } from 'react-icons/bs';
import { useTradeStore } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import { useOutsideClose } from '../../hooks/useOutsideClose';

/**
 * Single / Split dropdown toggle for the chart command bar (Requirement 4).
 *
 * Switches the chart area between a single chart and the dual-pane
 * Split_Chart_View by driving `useChartUIStore.setSplitView`, and reads
 * `splitView` to highlight the active layout.
 *
 * The control is mode-gated (R4.7, R5.3): it renders only when the active
 * workspace profile is INTRADAY or FNO. In Swing / Investor it returns null
 * (hidden).
 */
export interface SplitViewToggleProps {
  noText?: boolean;
}

export default function SplitViewToggle({ noText = false }: SplitViewToggleProps) {
  const activeProfile = useTradeStore((s) => s.activeProfile);
  const splitView = useChartUIStore((s) => s.splitView);
  const setSplitView = useChartUIStore((s) => s.setSplitView);
  const [isOpen, setIsOpen] = useState(false);
  const ref = useOutsideClose<HTMLDivElement>(() => setIsOpen(false));

  // Mode-gated: only Intraday and F&O support the split view (R4.7, R5.3).
  if (activeProfile !== 'INTRADAY' && activeProfile !== 'FNO') {
    return null;
  }

  const CurrentIcon = splitView ? BsWindowSplit : IoSquareOutline;

  return (
    <div className="relative flex items-center justify-center" ref={ref} role="group" aria-label="Chart layout">
      <button
        type="button"
        id="split-view-dropdown-trigger"
        onClick={() => setIsOpen(!isOpen)}
        title={noText ? `Layout: ${splitView ? 'Split' : 'Single'}` : undefined}
        className={
          noText
            ? `flex h-7 w-7 items-center justify-center rounded-sm transition-all ${
                isOpen
                  ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                  : 'bg-transparent text-text-secondary hover:bg-elevated hover:text-text-primary'
              }`
            : `flex h-full items-center gap-1 px-2.5 text-[11px] font-semibold transition-all border-r border-border-default ${
                isOpen
                  ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                  : 'bg-surface text-text-secondary hover:bg-elevated hover:text-text-primary'
              }`
        }
      >
        <CurrentIcon size={noText ? 18 : 11} className={isOpen ? 'text-emerald-600 dark:text-emerald-400' : 'text-text-muted'} />
        {!noText && <span>{splitView ? 'Split' : 'Single'}</span>}
        {!noText && <ChevronDown size={11} className={`transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`} />}
      </button>

      {/* Dropdown Options (always rendered in DOM for unit test compatibility, hidden via class when closed) */}
      <div className={`absolute right-0 z-50 mt-px w-32 rounded-none border border-border-default bg-surface/95 p-1 shadow-2xl backdrop-blur-xl ${
        isOpen ? 'block' : 'hidden'
      } ${
        noText ? 'top-[32px]' : 'top-full'
      }`}>
        <button
          key="single"
          type="button"
          id="split-view-single"
          aria-pressed={!splitView}
          onClick={() => {
            setSplitView(false);
            setIsOpen(false);
          }}
          className={`flex w-full items-center gap-2 rounded-none px-2.5 py-1.5 text-left text-[11px] transition-colors ${
            !splitView
              ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-bold border border-emerald-500/30'
              : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
          }`}
        >
          <IoSquareOutline
            size={16}
            className={!splitView ? 'text-emerald-600 dark:text-emerald-400' : 'text-text-muted'}
          />
          <span>Single</span>
        </button>
        <button
          key="split"
          type="button"
          id="split-view-split"
          aria-pressed={splitView}
          onClick={() => {
            setSplitView(true);
            setIsOpen(false);
          }}
          className={`flex w-full items-center gap-2 rounded-none px-2.5 py-1.5 text-left text-[11px] transition-colors ${
            splitView
              ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-bold border border-emerald-500/30'
              : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
          }`}
        >
          <BsWindowSplit
            size={16}
            className={splitView ? 'text-emerald-600 dark:text-emerald-400' : 'text-text-muted'}
          />
          <span>Split</span>
        </button>
      </div>
    </div>
  );
}

