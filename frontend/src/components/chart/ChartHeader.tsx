'use client';

import React, { useState, useRef, useEffect } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { BarChart3, Activity, Footprints, ChevronDown } from 'lucide-react';

export default function ChartModeToggle() {
  const chartMode = useTradeStore((s) => s.chartMode);
  const setChartMode = useTradeStore((s) => s.setChartMode);
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const modes = [
    { mode: 'STANDARD' as const, label: 'Standard', icon: BarChart3 },
    { mode: 'VOLUME_PROFILE' as const, label: 'Vol Profile', icon: Activity },
    { mode: 'FOOTPRINT' as const, label: 'Footprint', icon: Footprints },
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
    <div className="relative" ref={dropdownRef}>
      <button
        type="button"
        id="chart-mode-dropdown-trigger"
        onClick={() => setIsOpen(!isOpen)}
        className={`flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-semibold transition-all border ${
          isOpen
            ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40 shadow-[0_0_12px_rgba(16,185,129,0.15)]'
            : 'bg-surface text-text-secondary hover:bg-elevated border-border-default hover:text-text-primary'
        }`}
      >
        <CurrentIcon size={11} className={isOpen ? 'text-emerald-400' : 'text-text-muted'} />
        <span>{currentMode.label}</span>
        <ChevronDown size={11} className={`transition-transform duration-200 ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full z-50 mt-1 w-40 rounded-xl border border-border-default bg-surface/90 backdrop-blur-xl shadow-2xl p-1.5 animate-in fade-in slide-in-from-top-2 duration-200">
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
                className={`flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[11px] transition-all duration-150 border border-transparent ${
                  isActive
                    ? 'bg-emerald-500/10 text-emerald-400 font-bold border-emerald-500/30'
                    : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                }`}
              >
                <Icon size={12} className={isActive ? 'text-emerald-400' : 'text-slate-400'} />
                <span>{label}</span>
                {isActive && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.8)]" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
