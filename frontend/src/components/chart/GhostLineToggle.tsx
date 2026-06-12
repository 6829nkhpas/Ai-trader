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
import { Spline, ChevronDown } from 'lucide-react';
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

export default function GhostLineToggle() {
  const ghostLineMode = useChartUIStore((s) => s.ghostLineMode);
  const setGhostLineMode = useChartUIStore((s) => s.setGhostLineMode);

  const [open, setOpen] = useState(false);
  const ref = useOutsideClose<HTMLDivElement>(() => setOpen(false));

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Projection engine"
        title="Predictive projection engine"
        className="flex h-7 items-center gap-1.5 rounded-md border border-border-default bg-surface px-2.5 text-[11px] font-semibold text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
      >
        <Spline size={13} className="text-text-muted" />
        <span>{MODE_LABELS[ghostLineMode]}</span>
        <ChevronDown size={11} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
      </button>
      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 w-52 rounded-lg border border-border-default bg-surface/95 p-1 shadow-2xl backdrop-blur-xl">
          {MODES.map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => {
                setGhostLineMode(m);
                setOpen(false);
              }}
              className={`flex w-full flex-col items-start rounded-md px-2.5 py-1.5 text-left transition-colors ${m === ghostLineMode
                  ? 'bg-primary/10 text-primary'
                  : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                }`}
            >
              <span className="flex w-full items-center justify-between text-[11px] font-semibold">
                {MODE_LABELS[m]}
                {m === ghostLineMode && <span className="h-1.5 w-1.5 rounded-full bg-primary" />}
              </span>
              <span className="text-[9px] text-text-muted">{MODE_DESCRIPTIONS[m]}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
