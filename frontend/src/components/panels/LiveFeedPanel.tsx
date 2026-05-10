'use client';

import React, { useState } from 'react';
import { useTradeStore } from '../../store/useTradeStore';

export default function LiveFeedPanel() {
  const { liveDecisions, activeDecision } = useTradeStore();
  const [query, setQuery] = useState('');

  // Create a reversed copy so the newest is at the top
  const recentDecisions = [...liveDecisions].reverse();
  const normalizedQuery = query.trim().toLowerCase();
  const filteredDecisions = normalizedQuery
    ? recentDecisions.filter((decision) => decision.symbol.toLowerCase().includes(normalizedQuery))
    : recentDecisions;

  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-border-default px-3 py-2">
        <h2 className="text-xs font-bold uppercase tracking-widest text-text-secondary">Stock List</h2>
        <div className="mt-1.5">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search symbols"
            aria-label="Search symbols"
            className="h-9 w-full rounded-md border border-border-default bg-surface px-3 text-xs text-text-primary placeholder:text-text-muted transition-colors focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
      </div>
      <div className="flex flex-1 flex-col gap-1 overflow-y-auto p-1.5">
        {filteredDecisions.length === 0 ? (
          <div className="p-4 text-center text-xs text-text-secondary">
            {recentDecisions.length === 0 ? 'Waiting for backend decisions...' : 'No matching symbols found.'}
          </div>
        ) : (
          filteredDecisions.map((decision, i) => {
            const isSelected = activeDecision?.symbol === decision.symbol && activeDecision?.timestamp_ms === decision.timestamp_ms;
            return (
              <div
                key={`${decision.timestamp_ms}-${i}`}
                className={`flex h-9 flex-col justify-center px-2.5 py-0.5 text-xs text-text-secondary transition-colors cursor-pointer rounded ${isSelected ? 'bg-emerald-500/15' : 'hover:bg-elevated'}`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="truncate text-sm font-semibold text-text-primary">{decision.symbol}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-xs font-bold ${decision.action_type === 'BUY'
                          ? 'text-bull'
                          : decision.action_type === 'SELL'
                            ? 'text-bear'
                            : 'text-neutral'
                        }`}
                    >
                      {decision.action_type}
                    </span>
                    <span className="text-[11px] text-text-muted">{decision.final_conviction_score}%</span>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
