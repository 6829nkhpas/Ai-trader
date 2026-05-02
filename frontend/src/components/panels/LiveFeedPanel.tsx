'use client';

import React, { useState } from 'react';
import { useTradeStore } from '../../store/useTradeStore';

export default function LiveFeedPanel() {
  const { liveDecisions } = useTradeStore();
  const [query, setQuery] = useState('');

  // Create a reversed copy so the newest is at the top
  const recentDecisions = [...liveDecisions].reverse();
  const normalizedQuery = query.trim().toLowerCase();
  const filteredDecisions = normalizedQuery
    ? recentDecisions.filter((decision) => decision.symbol.toLowerCase().includes(normalizedQuery))
    : recentDecisions;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-border-default bg-surface">
      <div className="shrink-0 border-b border-border-default bg-card px-4 py-3">
        <h2 className="text-xs font-bold uppercase tracking-widest text-text-secondary">Stock List</h2>
        <div className="mt-3">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search symbols"
            aria-label="Search symbols"
            className="w-full rounded-md border border-border-default bg-surface px-3 py-2 text-xs text-text-primary placeholder:text-text-muted transition-colors focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
          />
        </div>
      </div>
      <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-2">
        {filteredDecisions.length === 0 ? (
          <div className="p-4 text-center text-xs text-text-secondary">
            {recentDecisions.length === 0 ? 'Waiting for backend decisions...' : 'No matching symbols found.'}
          </div>
        ) : (
          filteredDecisions.map((decision, i) => (
            <div
              key={`${decision.timestamp_ms}-${i}`}
              className="flex min-h-11 flex-col gap-1 rounded-lg border border-transparent px-2 py-2 text-xs text-text-secondary transition-colors hover:bg-elevated"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 flex-col">
                  <span className="truncate text-sm font-semibold text-text-primary">{decision.symbol}</span>
                  <span className="text-[11px] text-text-muted">{new Date(decision.timestamp_ms).toLocaleTimeString()}</span>
                </div>
                <div className="flex flex-col items-end">
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
                  <span className="text-[11px] text-text-muted">Score {decision.final_conviction_score}%</span>
                </div>
              </div>
              <div className="flex items-center justify-between text-[10px] uppercase tracking-wider text-text-muted">
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
