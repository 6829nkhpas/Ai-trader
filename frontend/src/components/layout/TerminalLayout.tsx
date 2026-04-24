'use client';

import React from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { Activity } from 'lucide-react';

interface TerminalLayoutProps {
  children: React.ReactNode;
  sidebar: React.ReactNode;
}

export default function TerminalLayout({ children, sidebar }: TerminalLayoutProps) {
  const { wsStatus } = useTradeStore();

  return (
    <div className="flex flex-col h-screen bg-slate-950 text-slate-100 font-sans">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-slate-800 bg-slate-900 shadow-md z-10 shrink-0">
        <div className="flex items-center gap-3">
          <Activity className="text-blue-500" size={24} />
          <h1 className="text-xl font-bold tracking-wider text-slate-100">AI-TRADE TERMINAL</h1>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm text-slate-400 uppercase tracking-widest">WebSocket</span>
          <div className="flex items-center gap-2 bg-slate-800 px-3 py-1 rounded-full border border-slate-700">
            <span className={`w-2.5 h-2.5 rounded-full ${wsStatus === 'connected' ? 'bg-green-500 animate-pulse' : wsStatus === 'error' ? 'bg-red-500' : 'bg-yellow-500'}`}></span>
            <span className="text-xs font-semibold tracking-widest uppercase">{wsStatus}</span>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar */}
        <aside className="w-80 flex flex-col gap-4 p-4 border-r border-slate-800 bg-slate-900 overflow-y-auto">
          {sidebar}
        </aside>

        {/* Central Area */}
        <main className="flex-1 p-6 overflow-y-auto bg-slate-950">
          {children}
        </main>
      </div>
    </div>
  );
}
