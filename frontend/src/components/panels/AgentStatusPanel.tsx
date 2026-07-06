'use client';

import React from 'react';
import { Activity, Brain, Cpu, MessageSquare } from 'lucide-react';
import { useTradeStore } from '../../store/useTradeStore';

export default function AgentStatusPanel() {
  const connectionStatus = useTradeStore((state) => state.connectionStatus);
  const getStatusColor = (status: string) => {
    if (status === 'LIVE' || status === 'CONNECTED') return 'text-status-live';
    if (status === 'CONNECTING') return 'text-status-warning';
    return 'text-status-error';
  };

  const getStatusDot = (status: string) => {
    if (status === 'LIVE' || status === 'CONNECTED') return 'bg-status-live';
    if (status === 'CONNECTING') return 'bg-status-warning';
    return 'bg-status-error';
  };

  const agents = [
    { name: 'Ingestion Engine', icon: Activity, status: 'LIVE' },
    { name: 'Technical Agent', icon: Cpu, status: 'LIVE' },
    { name: 'NLP Sentiment Agent', icon: MessageSquare, status: 'LIVE' },
    { name: 'Aggregator', icon: Brain, status: connectionStatus === 'CONNECTED' ? 'CONNECTED' : connectionStatus },
  ];

  return (
    <section className="rounded-lg border border-border-default bg-card p-4 panel-shadow">
      <div className="text-xs font-semibold uppercase tracking-widest text-text-secondary">AI Swarm Status</div>
      <div className="mt-3 flex flex-col gap-2">
        {agents.map((agent, index) => (
          <div key={index} className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <agent.icon size={14} className={getStatusColor(agent.status)} />
              <span className="text-xs font-medium text-text-primary">{agent.name}</span>
            </div>
            <div className="flex items-center gap-1.5 rounded-full border border-border-default bg-surface px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-text-secondary">
              <span className={`h-1.5 w-1.5 rounded-full ${getStatusDot(agent.status)}`} />
              <span>{agent.status}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
