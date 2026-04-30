'use client';

import React from 'react';
import { useTradeStore } from '../../store/useTradeStore';

export default function LiveFeedPanel() {
  const { liveDecisions } = useTradeStore();

  // Create a reversed copy so the newest is at the top
  const recentDecisions = [...liveDecisions].reverse();

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="shrink-0 border-b border-slate-200 bg-slate-50 px-4 py-3">
        <h2 className="text-xs font-bold uppercase tracking-widest text-slate-500">Live Decision Feed</h2>
      </div>
      <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-3">
        {recentDecisions.length === 0 ? (
          <div className="p-4 text-center text-sm text-slate-500">Waiting for backend decisions...</div>
        ) : (
          recentDecisions.map((decision, i) => (
            <div key={`${decision.timestamp_ms}-${i}`} className="flex flex-col rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm">
              <div className="mb-2 flex items-center justify-between gap-3">
                <span className="font-bold text-slate-900">{decision.symbol}</span>
                <span className="text-xs text-slate-500">{new Date(decision.timestamp_ms).toLocaleTimeString()}</span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className={`font-bold ${decision.action_type === 'BUY' ? 'text-green-500' : decision.action_type === 'SELL' ? 'text-red-500' : 'text-yellow-500'}`}>
                  {decision.action_type}
                </span>
                <span className="font-mono text-xs text-slate-600">Score: {decision.final_conviction_score}%</span>
              </div>
              <div className="mt-2 flex items-center justify-between text-[11px] uppercase tracking-wider text-slate-500">
                <span>Tech {(decision.technical_weight_used * 100).toFixed(0)}%</span>
                <span>Sent {(decision.sentiment_weight_used * 100).toFixed(0)}%</span>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
