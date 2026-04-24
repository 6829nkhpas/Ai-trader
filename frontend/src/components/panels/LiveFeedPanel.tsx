'use client';

import React from 'react';
import { useTradeStore } from '../../store/useTradeStore';

export default function LiveFeedPanel() {
  const { liveDecisions } = useTradeStore();

  // Create a reversed copy so the newest is at the top
  const recentDecisions = [...liveDecisions].reverse();

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-lg shadow-lg overflow-hidden flex flex-col flex-1 min-h-0">
      <div className="px-4 py-3 border-b border-slate-800 bg-slate-800/50 shrink-0">
        <h2 className="text-xs font-bold uppercase tracking-widest text-slate-400">Live Decision Feed</h2>
      </div>
      <div className="p-3 overflow-y-auto flex flex-col gap-2 flex-1">
        {recentDecisions.length === 0 ? (
          <div className="text-center p-4 text-slate-500 text-sm">Waiting for signals...</div>
        ) : (
          recentDecisions.map((decision, i) => (
            <div key={`${decision.timestamp_ms}-${i}`} className="flex flex-col p-3 rounded-md bg-slate-800/50 border border-slate-700/50 text-sm">
              <div className="flex items-center justify-between mb-2">
                <span className="font-bold text-slate-100">{decision.symbol}</span>
                <span className="text-xs text-slate-500">{new Date(decision.timestamp_ms).toLocaleTimeString()}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className={`font-bold ${decision.action_type === 'BUY' ? 'text-green-500' : decision.action_type === 'SELL' ? 'text-red-500' : 'text-yellow-500'}`}>
                  {decision.action_type}
                </span>
                <span className="text-slate-300 font-mono text-xs">Score: {decision.final_conviction_score}</span>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
