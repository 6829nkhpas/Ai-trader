'use client';

import React from 'react';
import { Activity } from 'lucide-react';
import NetworkMetrics from '../panels/NetworkMetrics';

interface TerminalLayoutProps {
  children: React.ReactNode;
  sidebar: React.ReactNode;
}

export default function TerminalLayout({ children, sidebar }: TerminalLayoutProps) {
  return (
    <div className="flex flex-col h-screen bg-slate-950 text-slate-100 font-sans">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-4 border-b border-slate-800 bg-slate-900 shadow-md z-10 shrink-0">
        <div className="flex items-center gap-3">
          <Activity className="text-blue-500" size={24} />
          <h1 className="text-xl font-bold tracking-wider text-slate-100">AI-TRADE TERMINAL</h1>
        </div>
        <div className="ml-auto">
          <NetworkMetrics />
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
