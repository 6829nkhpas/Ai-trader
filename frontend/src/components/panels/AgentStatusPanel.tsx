import React from 'react';
import { Activity, Cpu, MessageSquare, Brain } from 'lucide-react';

export default function AgentStatusPanel() {
  const agents = [
    { name: 'Ingestion Engine', icon: Activity },
    { name: 'Technical Agent', icon: Cpu },
    { name: 'NLP Sentiment Agent', icon: MessageSquare },
    { name: 'Aggregator', icon: Brain },
  ];

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg shadow-lg overflow-hidden flex flex-col shrink-0">
      <div className="px-4 py-3 border-b border-slate-800 bg-slate-800/50">
        <h2 className="text-xs font-bold uppercase tracking-widest text-slate-400">AI Swarm Status</h2>
      </div>
      <div className="p-4 flex flex-col gap-3">
        {agents.map((agent, index) => (
          <div key={index} className="flex items-center justify-between p-3 rounded-md bg-slate-800/50 border border-slate-700/50">
            <div className="flex items-center gap-3">
              <agent.icon size={18} className="text-blue-400" />
              <span className="text-sm font-medium text-slate-200">{agent.name}</span>
            </div>
            <div className="relative flex h-3 w-3">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-3 w-3 bg-green-500"></span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
