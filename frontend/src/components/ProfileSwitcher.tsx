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
      className="flex w-full items-center justify-between border-b border-slate-800/80 bg-slate-950 px-4 py-2"
    >
      {/* ── Brand Mark ─────────────────────────────────────── */}
      <div className="flex items-center gap-2.5 select-none">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br from-purple-600 to-indigo-500 shadow-[0_0_12px_rgba(139,92,246,0.35)]">
          <span className="text-xs font-black text-white tracking-tight">A</span>
        </div>
        <span className="text-sm font-semibold tracking-wide text-slate-300">
          Alpha Suite
        </span>
      </div>

      {/* ── Segmented Profile Control ──────────────────────── */}
      <div className="flex items-center gap-1 rounded-lg border border-slate-800 bg-slate-900/70 p-0.5 backdrop-blur-sm">
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
                focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-purple-500/60
                ${
                  isActive
                    ? 'bg-slate-800 text-purple-400 shadow-[0_0_10px_rgba(192,132,252,0.15)]'
                    : 'text-slate-500 hover:bg-slate-800/40 hover:text-slate-300'
                }
              `}
            >
              {/* Active glow dot */}
              {isActive && (
                <span className="absolute -top-px right-2 h-1 w-1 rounded-full bg-purple-400 shadow-[0_0_6px_rgba(192,132,252,0.7)] animate-pulse" />
              )}
              <span>{label}</span>
              <span
                className={`rounded px-1 py-px text-[10px] font-medium leading-none ${
                  isActive
                    ? 'bg-purple-500/15 text-purple-400'
                    : 'bg-slate-800 text-slate-600'
                }`}
              >
                {shortcut}
              </span>
            </button>
          );
        })}
      </div>

      {/* ── Status Indicator ───────────────────────────────── */}
      <div className="flex items-center gap-2 text-[11px] font-medium text-slate-500">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-40" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
        </span>
        <span className="uppercase tracking-widest">Live</span>
      </div>
    </div>
  );
}
