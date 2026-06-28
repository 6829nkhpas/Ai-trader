'use client';

import React from 'react';
import { Layers } from 'lucide-react';
import { useTradeStore } from '../../store/useTradeStore';

/**
 * FnoModeToggle — the dedicated F&O-mode control in the command bar (R1.1).
 *
 * A single bordered command-bar button, distinct from the profile and
 * timeframe controls. It reads `fnoMode` from the single-source-of-truth
 * Zustand store (R1.4) and calls `toggleFnoMode()` on click. While F&O mode
 * is active the button shows the command bar's active emerald treatment so
 * the trader can see at a glance that the F&O workspace is engaged (R1.5).
 *
 * This component only owns the toggle. Wiring it into the command-bar row in
 * page.tsx is handled separately (task 6.2).
 */
export default function FnoModeToggle() {
  const fnoMode = useTradeStore((s) => s.fnoMode);
  const toggleFnoMode = useTradeStore((s) => s.toggleFnoMode);

  return (
    <button
      type="button"
      onClick={toggleFnoMode}
      aria-label="F&O Mode"
      aria-pressed={fnoMode}
      className={`flex h-full items-center gap-1.5 px-4 text-[11px] font-semibold transition-all border-r border-border-default ${
        fnoMode
          ? 'bg-emerald-500/10 text-emerald-400'
          : 'bg-surface text-text-secondary hover:bg-elevated hover:text-text-primary'
      }`}
    >
      <Layers size={13} className={fnoMode ? 'text-emerald-400' : 'text-text-muted'} />
      <span>F&amp;O</span>
    </button>
  );
}
