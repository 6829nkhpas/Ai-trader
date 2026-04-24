'use client';

import React from 'react';
import { useTradeStore } from '../../store/useTradeStore';

const statusDotClass: Record<'DISCONNECTED' | 'CONNECTING' | 'CONNECTED', string> = {
  CONNECTED: 'bg-green-500',
  CONNECTING: 'bg-yellow-500',
  DISCONNECTED: 'bg-red-500',
};

function getLatencyColor(latencyMs: number): string {
  if (latencyMs < 50) {
    return 'text-green-400';
  }
  if (latencyMs < 150) {
    return 'text-yellow-400';
  }
  return 'text-red-400';
}

export default function NetworkMetrics() {
  const { connectionStatus, latencyMs } = useTradeStore();

  return (
    <div className="flex items-center gap-4 rounded-full border border-slate-700 bg-slate-800/80 px-4 py-2 text-xs uppercase tracking-wider">
      <div className="flex items-center gap-2">
        <span className={`h-2.5 w-2.5 rounded-full ${statusDotClass[connectionStatus]}`} />
        <span className="text-slate-300">Status</span>
        <span className="font-semibold text-slate-100">{connectionStatus}</span>
      </div>

      <div className="h-4 w-px bg-slate-700" />

      <div className="flex items-center gap-2">
        <span className="text-slate-300">Latency</span>
        <span className={`font-semibold ${getLatencyColor(latencyMs)}`}>{latencyMs}ms</span>
      </div>
    </div>
  );
}
