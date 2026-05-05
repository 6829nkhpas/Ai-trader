'use client';

import React from 'react';
import { useTradeStore, TradeProfile } from '../store/useTradeStore';

const PROFILES: { key: TradeProfile; label: string; shortcut: string }[] = [
  { key: 'INTRADAY', label: 'Intraday', shortcut: 'Scalp' },
  { key: 'SWING', label: 'Swing', shortcut: '1H-4H' },
  { key: 'INVESTOR', label: 'Investor', shortcut: 'Macro' },
];

export default function ProfileSwitcher() {
  const activeProfile = useTradeStore((s) => s.activeProfile);
  const setActiveProfile = useTradeStore((s) => s.setActiveProfile);

  return (
    <div
      id="profile-switcher-bar"
      className="flex w-full items-center justify-between border-b border-border-default bg-surface px-4 py-2"
    >
      {/* ── Brand Mark ─────────────────────────────────────── */}
      <div className="flex items-center gap-2.5 select-none">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br from-[#059669] to-emerald-400 shadow-sm">
          <span className="text-xs font-black text-white tracking-tight">A</span>
        </div>
        <span className="text-sm font-semibold tracking-wide text-text-primary">
          Alpha Suite
        </span>
      </div>

      {/* ── Segmented Profile Control ──────────────────────── */}
      <div className="flex items-center gap-1 rounded-lg border border-border-default bg-surface p-0.5 shadow-sm">
        {PROFILES.map(({ key, label, shortcut }) => {
          const isActive = activeProfile === key;
          return (
            <button
              key={key}
              id={`profile-btn-${key.toLowerCase()}`}
              type="button"
              onClick={() => setActiveProfile(key)}
              className={`
                relative flex items-center gap-1.5 rounded-md px-3.5 py-1.5 text-xs font-semibold
                transition-all duration-200 ease-out select-none
                focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/60
                ${
                  isActive
                    ? 'bg-[#ECFDF5] text-[#059669]'
                    : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                }
              `}
            >
              {/* Active glow dot */}
              {isActive && (
                <span className="absolute -top-px right-2 h-1.5 w-1.5 rounded-full bg-[#059669]" />
              )}
              <span>{label}</span>
              <span
                className={`rounded px-1 py-px text-[10px] font-medium leading-none ${
                  isActive
                    ? 'bg-emerald-500/10 text-[#059669]'
                    : 'bg-elevated text-text-secondary'
                }`}
              >
                {shortcut}
              </span>
            </button>
          );
        })}
      </div>

      {/* ── Status Indicator ───────────────────────────────── */}
      <div className="flex items-center gap-2 text-[11px] font-medium text-text-secondary">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-40" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
        </span>
        <span className="uppercase tracking-widest">Live</span>
      </div>
    </div>
  );
}
