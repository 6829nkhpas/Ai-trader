'use client';

import React from 'react';
import { Activity, RefreshCcw } from 'lucide-react';
import NetworkMetrics from '../panels/NetworkMetrics';
import OrderExecutionPanel from '../panels/OrderExecutionPanel';
import { useTradeStore } from '../../store/useTradeStore';

interface TerminalLayoutProps {
  children: React.ReactNode;
  sidebar: React.ReactNode;
}

export default function TerminalLayout({ children, sidebar }: TerminalLayoutProps) {
  const resetSession = useTradeStore((state) => state.resetSession);

  return (
    <div className="flex h-screen flex-col bg-slate-100 font-sans text-slate-900">
      {/* Header */}
      <header className="z-10 shrink-0 border-b border-slate-200 bg-white/95 px-6 py-4 shadow-sm backdrop-blur">
        <div className="flex items-center gap-3">
          <Activity className="text-blue-600" size={24} />
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-900">AI-TRADE TERMINAL</h1>
            <p className="text-xs text-slate-500">Live market decisions, signal flow, and execution review</p>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-4">
          <button
            onClick={resetSession}
            className="flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:border-slate-300 hover:bg-slate-100"
            title="Reset Session and Clear Orders"
          >
            <RefreshCcw size={16} />
            Reset Session
          </button>
          <NetworkMetrics />
        </div>
      </header>

      {/* Main Content */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Sidebar */}
        <aside className="flex w-90 min-h-0 flex-col gap-4 overflow-y-auto border-r border-slate-200 bg-white p-4">
          {sidebar}
          <div className="mt-4">
            <OrderExecutionPanel />
          </div>
        </aside>

        {/* Central Area */}
        <main className="min-h-0 flex-1 overflow-hidden bg-slate-100 p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
