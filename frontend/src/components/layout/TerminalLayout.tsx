'use client';

import React, { useState } from 'react';
import dynamic from 'next/dynamic';
import {
  Bell,
  ChevronDown,
  Settings,
  X as XIcon,
  Shield,
  User,
} from 'lucide-react';
import { useTradeStore, TradeProfile } from '../../store/useTradeStore';
import { useAuthStore } from '../../store/useAuthStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import QuantRadar from '../quant/QuantRadar';
import UserProfileModal from '../profile/UserProfileModal';
import ChartToolsBar from '../chart/ChartToolsBar';

// SSR-disabled dynamic import: Tauri plugins (Stronghold, Path API) are only
// available in the desktop WebView. Loading them during Next.js server render
// triggers `Module not found: Can't resolve '@tauri-apps/plugin-stronghold'`.
// `{ ssr: false }` ensures this component is mounted strictly on the client.
const SecurityVault = dynamic(
  () => import('../settings/SecurityVault'),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-32 items-center justify-center text-[10px] text-text-muted">
        Loading vault…
      </div>
    ),
  }
);

const PROFILES: { key: TradeProfile; label: string; shortcut: string }[] = [
  { key: 'INTRADAY', label: 'Intraday', shortcut: 'Scalp' },
  { key: 'SWING', label: 'Swing', shortcut: '1H-4H' },
  { key: 'INVESTOR', label: 'Investor', shortcut: 'Macro' },
];

interface TerminalLayoutProps {
  children: React.ReactNode;
  leftPanel: React.ReactNode;
}

export default function TerminalLayout({ children, leftPanel }: TerminalLayoutProps) {
  const { activeProfile, setActiveProfile, resetSession } = useTradeStore();
  const { user } = useAuthStore();
  const isFullscreen = useChartUIStore((s) => s.isFullscreen);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);

  const broker = user?.brokerConnection;

  const getInitials = (name: string | null | undefined) => {
    if (!name) return 'SA';
    const parts = name.trim().split(/\s+/);
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  };

  return (
    <div className="flex h-screen flex-col bg-background font-sans text-text-primary">
      {/* Header */}
      <header className="z-10 flex shrink-0 items-center gap-3 border-b border-border-default bg-surface px-3 py-1.5 panel-shadow-sm">
        <div className="flex flex-1 items-center gap-3">
          <img src="/strat.svg" alt="Strat Ai Logo" className="h-[22px] w-[22px] object-contain" />
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-text-primary">STRAT AI TERMINAL</h1>
            <p className="text-xs text-text-secondary">Live market decisions, signal flow, and execution review</p>
          </div>
        </div>

        {/* ── Segmented Profile Control ──────────────────────── */}
        <div className="flex shrink-0 items-center justify-center">
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
                    ${isActive
                      ? 'bg-emerald-500/15 text-emerald-400'
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
                    className={`rounded px-1 py-px text-[10px] font-medium leading-none ${isActive
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
        </div>
        <div className="flex flex-1 items-center justify-end gap-4 relative">
          <button className="relative text-text-secondary hover:text-text-primary transition-colors">
            <Bell size={18} />
            <span className="absolute top-0 right-0 h-2 w-2 rounded-full bg-red-500 border border-surface"></span>
          </button>

          {/* Quant Radar Dropdown */}
          <QuantRadar />

          {/* User Profile Avatar Icon */}
          <button
            onClick={() => setProfileOpen(true)}
            className="flex h-8 w-8 items-center justify-center rounded-lg border border-border-default bg-surface/50 hover:bg-elevated/45 text-text-secondary hover:text-white transition-all shadow-sm overflow-hidden"
            title="Account Profile & Settings"
          >
            {broker?.avatarUrl ? (
              <img 
                src={broker.avatarUrl} 
                alt={user?.name || 'Profile Avatar'} 
                className="h-full w-full object-cover"
              />
            ) : (
              <div className="flex h-full w-full items-center justify-center bg-emerald-500/10 text-emerald-400 font-bold text-xs tracking-wider">
                {getInitials(user?.name)}
              </div>
            )}
          </button>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex flex-1 min-h-0 min-w-0 overflow-visible bg-background p-2 gap-2">
        {/* Watchlist */}
        <aside className="flex w-56 shrink-0 min-h-0 flex-col overflow-visible border border-border-default rounded-lg bg-surface panel-shadow">
          {leftPanel}
        </aside>

        {/* Tools Bar — hidden in fullscreen so the chart card can mount its own
            without colliding on shared DOM ids (e.g. <input id="color-picker-component">). */}
        {!isFullscreen && (
          <ChartToolsBar className="border border-border-default rounded-lg bg-surface py-2 panel-shadow" />
        )}

        {/* Central Area — `min-w-0` lets this flex slot actually shrink back to
            its `flex-1` allocation after fullscreen exit. Without it, the chart
            canvas's intrinsic width (set while fullscreen) becomes the column's
            min-content and pushes the whole row past the viewport. */}
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-visible">
          {children}
        </main>

      </div>

      {/* User Profile Modal Overlay */}
      <UserProfileModal isOpen={profileOpen} onClose={() => setProfileOpen(false)} />
    </div>
  );
}
