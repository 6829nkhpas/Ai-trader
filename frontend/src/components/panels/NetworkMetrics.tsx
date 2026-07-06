'use client';

import React from 'react';
import { useTradeStore } from '../../store/useTradeStore';

const statusDotClass: Record<'DISCONNECTED' | 'CONNECTING' | 'CONNECTED', string> = {
  CONNECTED: 'bg-status-live',
  CONNECTING: 'bg-status-warning',
  DISCONNECTED: 'bg-status-error',
};

function getLatencyColor(latencyMs: number): string {
  if (latencyMs < 50) {
    return 'text-status-live';
  }
  if (latencyMs < 150) {
    return 'text-status-warning';
  }
  return 'text-status-error';
}

export default function NetworkMetrics() {
  const { connectionStatus, latencyMs } = useTradeStore();

  return (
    <div className="flex items-center gap-4 rounded-full border border-border-default bg-card px-4 py-2 text-xs uppercase tracking-wider text-text-secondary">
      <div className="flex items-center gap-2">
        <span className={`h-2.5 w-2.5 rounded-full ${statusDotClass[connectionStatus]}`} />
        <span className="text-text-muted">Status</span>
        <span className="font-semibold text-text-primary">{connectionStatus}</span>
      </div>

      <div className="h-4 w-px bg-border-subtle" />

      <div className="flex items-center gap-2">
        <span className="text-text-muted">Latency</span>
        <span className={`font-semibold ${getLatencyColor(latencyMs)}`}>{latencyMs}ms</span>
      </div>
    </div>
  );
}
