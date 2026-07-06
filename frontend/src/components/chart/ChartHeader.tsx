'use client';

import React, { useState, useRef, useEffect } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { ChevronDown } from 'lucide-react';
import { SlChart } from 'react-icons/sl';

export interface ChartModeToggleProps {
  noText?: boolean;
}

export default function ChartModeToggle({ noText = false }: ChartModeToggleProps) {
  const chartMode = useTradeStore((s) => s.chartMode);
  const setChartMode = useTradeStore((s) => s.setChartMode);
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const modes = [
    { mode: 'STANDARD' as const, label: 'Standard', icon: SlChart },
    { mode: 'VOLUME_PROFILE' as const, label: 'Vol Profile', icon: SlChart },
    { mode: 'FOOTPRINT' as const, label: 'Footprint', icon: SlChart },
  ];

  const currentMode = modes.find((m) => m.mode === chartMode) || modes[0];
  const CurrentIcon = currentMode.icon;

  // Close dropdown on outside click
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  return (
    <div className="relative flex items-center justify-center" ref={dropdownRef}>
      <button
        type="button"
        id="chart-mode-dropdown-trigger"
        onClick={() => setIsOpen(!isOpen)}
        title={noText ? `Chart Mode: ${currentMode.label}` : undefined}
        className={
          noText
            ? `flex h-7 w-7 items-center justify-center rounded-sm transition-all ${
                isOpen
                  ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                  : 'bg-transparent text-text-secondary hover:bg-elevated hover:text-text-primary'
              }`
            : `flex h-full items-center gap-1.5 px-2.5 text-[11px] font-semibold transition-all border-r border-border-default ${
                isOpen
                  ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                  : 'bg-surface text-text-secondary hover:bg-elevated hover:text-text-primary'
              }`
        }
      >
        <CurrentIcon size={noText ? 18 : 11} className={isOpen ? 'text-emerald-600 dark:text-emerald-400' : 'text-text-muted'} />
        {!noText && <span>{currentMode.label}</span>}
        {!noText && <ChevronDown size={11} className={`transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`} />}
      </button>

      {isOpen && (
        <div className={`absolute right-0 z-50 mt-px w-40 rounded-none border border-border-default bg-surface shadow-2xl p-1.5 animate-in fade-in slide-in-from-top-2 duration-200 ${
          noText ? 'top-[32px]' : 'top-full'
        }`}>
          {modes.map(({ mode, label, icon: Icon }) => {
            const isActive = chartMode === mode;
            return (
              <button
                key={mode}
                type="button"
                id={`chart-mode-option-${mode.toLowerCase()}`}
                onClick={() => {
                  setChartMode(mode);
                  setIsOpen(false);
                }}
                className={`flex w-full items-center gap-2 rounded-none px-2.5 py-1.5 text-left text-[11px] transition-all duration-150 border border-transparent ${
                  isActive
                    ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 font-bold border-emerald-500/30'
                    : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                }`}
              >
                <Icon size={16} className={isActive ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-400'} />
                <span>{label}</span>
                {isActive && <span className="ml-auto h-1.5 w-1.5 rounded-none bg-emerald-600 dark:bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.8)]" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
