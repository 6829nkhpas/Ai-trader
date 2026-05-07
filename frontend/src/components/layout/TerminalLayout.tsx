'use client';

import React, { useState } from 'react';
import {
  Activity,
  RefreshCcw,
  Crosshair,
  TrendingUp,
  Minus,
  Columns2,
  PenLine,
  Brush,
  Type,
  Smile,
  Ruler,
  Search,
  Magnet,
  Lock,
  Eye,
  Trash2,
  Layers,
  Bell,
  User as UserIcon,
  LogOut,
  HelpCircle,
  ChevronDown,
} from 'lucide-react';
import { useTradeStore, TradeProfile } from '../../store/useTradeStore';
import { useAuth } from '../../context/AuthContext';

const PROFILES: { key: TradeProfile; label: string; shortcut: string }[] = [
  { key: 'INTRADAY', label: 'Intraday', shortcut: 'Scalp' },
  { key: 'SWING', label: 'Swing', shortcut: '1H-4H' },
  { key: 'INVESTOR', label: 'Investor', shortcut: 'Macro' },
];

interface TerminalLayoutProps {
  children: React.ReactNode;
  leftPanel: React.ReactNode;
}

const toolOptions = [
  { id: 'crosshair', label: 'Crosshair Tool', icon: Crosshair },
  { id: 'trendline', label: 'Trend Line Tool', icon: TrendingUp },
  { id: 'horizontal-line', label: 'Horizontal Line Tool', icon: Minus },
  { id: 'parallel-channel', label: 'Parallel Channel Tool', icon: Columns2 },
  { id: 'polyline', label: 'Polyline Tool', icon: PenLine },
  { id: 'brush', label: 'Free Drawing Tool', icon: Brush },
  { id: 'text', label: 'Text Annotation Tool', icon: Type },
  { id: 'emoji', label: 'Icon / Emoji Marker Tool', icon: Smile },
  { id: 'ruler', label: 'Measure Tool', icon: Ruler },
  { id: 'zoom', label: 'Zoom Tool', icon: Search },
  { id: 'magnet', label: 'Magnet Tool', icon: Magnet },
  { id: 'lock', label: 'Lock Drawing Tool', icon: Lock },
  { id: 'eye', label: 'Hide / Show Drawings', icon: Eye },
  { id: 'delete', label: 'Clear Drawings Tool', icon: Trash2 },
  { id: 'layers', label: 'Layers', icon: Layers },
];

export default function TerminalLayout({ children, leftPanel }: TerminalLayoutProps) {
  const { activeProfile, setActiveProfile, resetSession } = useTradeStore();
  const { user, logout } = useAuth();
  const [activeTool, setActiveTool] = useState<string>(toolOptions[0].id);
  const [isProfileOpen, setIsProfileOpen] = useState(false);

  return (
    <div className="flex h-screen flex-col bg-background font-sans text-text-primary">
      {/* Header */}
      <header className="z-10 flex shrink-0 items-center gap-4 border-b border-border-default bg-surface px-4 py-3 panel-shadow-sm">
        <div className="flex flex-1 items-center gap-3">
          <Activity className="text-primary" size={22} />
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-text-primary">AI-TRADE TERMINAL</h1>
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
                    ${
                      isActive
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
        </div>
        <div className="flex flex-1 items-center justify-end gap-4 relative">
          <button
            onClick={resetSession}
            className="flex items-center gap-2 rounded-full border border-border-default bg-card px-3 py-1.5 text-xs font-semibold text-text-secondary transition-colors hover:bg-elevated mr-2"
            title="Reset Session and Clear Orders"
          >
            <RefreshCcw size={14} />
            Reset Session
          </button>
          
          <button className="relative text-text-secondary hover:text-text-primary transition-colors">
            <Bell size={18} />
            <span className="absolute top-0 right-0 h-2 w-2 rounded-full bg-red-500 border border-surface"></span>
          </button>

          <div className="relative">
            <button
              onClick={() => setIsProfileOpen(!isProfileOpen)}
              className="flex items-center gap-2 rounded-full border border-border-default bg-surface px-2 py-1 transition-colors hover:bg-elevated"
            >
              <div className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/20 text-primary">
                <UserIcon size={14} />
              </div>
              <ChevronDown size={14} className="text-text-secondary" />
            </button>

            {isProfileOpen && (
              <>
                <div 
                  className="fixed inset-0 z-40" 
                  onClick={() => setIsProfileOpen(false)}
                />
                
                <div className="absolute right-0 top-full z-50 mt-2 w-56 rounded-lg border border-border-default bg-surface py-2 shadow-lg panel-shadow">
                  <div className="border-b border-border-default px-4 pb-3 pt-2">
                    <p className="text-sm font-medium text-text-primary">
                      {user?.displayName || 'User'}
                    </p>
                    <p className="text-xs text-text-secondary truncate">
                      {user?.email || 'user@example.com'}
                    </p>
                  </div>
                  
                  <div className="flex flex-col py-1">
                    <button className="flex items-center gap-3 px-4 py-2 text-sm text-text-secondary hover:bg-elevated hover:text-text-primary text-left">
                      <HelpCircle size={16} />
                      Customer Support
                    </button>
                    <button 
                      onClick={async () => {
                        await logout();
                        setIsProfileOpen(false);
                      }}
                      className="flex items-center gap-3 px-4 py-2 text-sm text-red-500 hover:bg-elevated hover:text-red-400 text-left"
                    >
                      <LogOut size={16} />
                      Logout
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex flex-1 min-h-0 overflow-hidden bg-background p-4 gap-4">
        {/* Stock List */}
        <aside className="flex w-64 shrink-0 min-h-0 flex-col overflow-y-auto border border-border-default rounded-lg bg-surface panel-shadow">
          {leftPanel}
        </aside>

        {/* Tools Bar */}
        <div className="flex w-16 shrink-0 flex-col items-center gap-[20px] overflow-y-auto border border-border-default rounded-lg bg-surface py-4 panel-shadow">
          {toolOptions.map((tool) => {
            const Icon = tool.icon;
            const isActive = activeTool === tool.id;
            return (
              <button
                key={tool.id}
                type="button"
                onClick={() => setActiveTool(tool.id)}
                aria-pressed={isActive}
                title={tool.label}
                aria-label={tool.label}
                className={`flex h-10 w-10 items-center justify-center rounded-lg transition-colors ${isActive
                  ? 'text-primary'
                  : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                  }`}
              >
                <Icon size={18} />
              </button>
            );
          })}
        </div>

        {/* Central Area */}
        <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
          {children}
        </main>

      </div>
    </div>
  );
}
