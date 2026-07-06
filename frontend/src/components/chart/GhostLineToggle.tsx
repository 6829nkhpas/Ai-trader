'use client';

// Feature: professional-charting-suite
//
// GhostLineToggle — selects which predictive projection engine drives the
// forward "ghost line" overlay:
//   · OLS   → linear regression baseline   (ghostLineMode = 'linear')
//   · VWEPR → volume-weighted polynomial    (ghostLineMode = 'curved')
//
// The selection lives in `useChartUIStore.ghostLineMode`; `useChartDataSync`
// reads it to route the dataset returned by the Rust dual-engine projection.

import React, { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import { SlGraph } from 'react-icons/sl';
import { useOutsideClose } from '../../hooks/useOutsideClose';
import { useChartUIStore, type GhostLineMode } from '../../store/useChartUIStore';

const MODE_LABELS: Record<GhostLineMode, string> = {
  linear: 'OLS',
  curved: 'VWEPR',
};

const MODE_DESCRIPTIONS: Record<GhostLineMode, string> = {
  linear: 'Linear regression baseline',
  curved: 'Volume-weighted polynomial',
};

const MODES: GhostLineMode[] = ['linear', 'curved'];

export interface GhostLineToggleProps {
  noText?: boolean;
}

export default function GhostLineToggle({ noText = false }: GhostLineToggleProps) {
  const ghostLineMode = useChartUIStore((s) => s.ghostLineMode);
  const setGhostLineMode = useChartUIStore((s) => s.setGhostLineMode);

  const [open, setOpen] = useState(false);
  const ref = useOutsideClose<HTMLDivElement>(() => setOpen(false));

  return (
    <div className="relative flex items-center justify-center" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Projection engine"
        title={noText ? `Projection: ${MODE_LABELS[ghostLineMode]}` : "Predictive projection engine"}
        className={
          noText
            ? `flex h-7 w-7 items-center justify-center rounded-sm transition-colors ${
                open
                  ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                  : 'bg-transparent text-text-secondary hover:bg-elevated hover:text-text-primary'
              }`
            : "flex h-full items-center gap-1.5 px-2.5 text-[11px] font-semibold text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary border-r border-border-default bg-surface"
        }
      >
        <SlGraph size={noText ? 18 : 13} className="text-text-muted" />
        {!noText && <span>{MODE_LABELS[ghostLineMode]}</span>}
        {!noText && <ChevronDown size={11} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />}
      </button>
      {open && (
        <div className={`absolute right-0 z-50 mt-px w-52 rounded-none border border-border-default bg-surface/95 p-1 shadow-2xl backdrop-blur-xl ${
          noText ? 'top-[32px]' : 'top-full'
        }`}>
          {MODES.map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => {
                setGhostLineMode(m);
                setOpen(false);
              }}
              className={`flex w-full flex-col items-start rounded-none px-2.5 py-1.5 text-left transition-colors ${m === ghostLineMode
                  ? 'bg-primary/10 text-primary'
                  : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                }`}
            >
              <span className="flex w-full items-center justify-between text-[11px] font-semibold">
                {MODE_LABELS[m]}
                {m === ghostLineMode && <span className="h-1.5 w-1.5 rounded-none bg-primary" />}
              </span>
              <span className="text-[9px] text-text-muted">{MODE_DESCRIPTIONS[m]}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
