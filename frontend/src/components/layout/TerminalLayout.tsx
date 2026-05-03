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
} from 'lucide-react';
import NetworkMetrics from '../panels/NetworkMetrics';
import { useTradeStore } from '../../store/useTradeStore';

interface TerminalLayoutProps {
  children: React.ReactNode;
  leftPanel: React.ReactNode;
  rightPanel: React.ReactNode;
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

export default function TerminalLayout({ children, leftPanel, rightPanel }: TerminalLayoutProps) {
  const resetSession = useTradeStore((state) => state.resetSession);
  const [activeTool, setActiveTool] = useState<string>(toolOptions[0].id);

  return (
    <div className="flex h-screen flex-col bg-background font-sans text-text-primary">
      {/* Header */}
      <header className="z-10 flex shrink-0 items-center gap-4 border-b border-border-default bg-surface px-4 py-3 panel-shadow-sm">
        <div className="flex items-center gap-3">
          <Activity className="text-primary" size={22} />
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-text-primary">AI-TRADE TERMINAL</h1>
            <p className="text-xs text-text-secondary">Live market decisions, signal flow, and execution review</p>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <button
            onClick={resetSession}
            className="flex items-center gap-2 rounded-full border border-border-default bg-card px-3 py-1.5 text-xs font-semibold text-text-secondary transition-colors hover:bg-elevated"
            title="Reset Session and Clear Orders"
          >
            <RefreshCcw size={14} />
            Reset Session
          </button>
          <NetworkMetrics />
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

        {/* AI Panel */}
        <aside className="flex w-80 shrink-0 min-h-0 flex-col gap-4 overflow-y-auto bg-transparent">
          {rightPanel}
        </aside>
      </div>
    </div>
  );
}
