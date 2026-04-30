'use client';

import React from 'react';
import { Activity, Brain, Cpu, MessageSquare } from 'lucide-react';
import { useTradeStore } from '../../store/useTradeStore';

export default function AgentStatusPanel() {
  const connectionStatus = useTradeStore((state) => state.connectionStatus);

  const agents = [
    { name: 'Ingestion Engine', icon: Activity, status: 'LIVE' },
    { name: 'Technical Agent', icon: Cpu, status: 'LIVE' },
    { name: 'NLP Sentiment Agent', icon: MessageSquare, status: 'LIVE' },
    { name: 'Aggregator', icon: Brain, status: connectionStatus === 'CONNECTED' ? 'CONNECTED' : connectionStatus },
  ];

  return (
    <div className="flex shrink-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-200 bg-slate-50 px-4 py-3">
        <h2 className="text-xs font-bold uppercase tracking-widest text-slate-500">AI Swarm Status</h2>
      </div>
      <div className="flex flex-col gap-3 p-4">
        {agents.map((agent, index) => (
          <div key={index} className="flex items-center justify-between rounded-xl border border-slate-200 bg-slate-50 p-3">
            <div className="flex items-center gap-3">
              <agent.icon size={18} className={agent.status === 'CONNECTED' || agent.status === 'LIVE' ? 'text-emerald-500' : 'text-blue-500'} />
              <span className="text-sm font-medium text-slate-800">{agent.name}</span>
            </div>
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-widest text-slate-500">
              <span className={`h-2.5 w-2.5 rounded-full ${agent.status === 'CONNECTED' || agent.status === 'LIVE' ? 'bg-emerald-500' : 'bg-yellow-500'}`} />
              <span>{agent.status}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
