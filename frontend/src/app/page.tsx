'use client';

import React, { useEffect, useState } from 'react';
import TradingChart from '../components/TradingChart';
import TerminalLayout from '../components/layout/TerminalLayout';
import LiveFeedPanel from '../components/panels/LiveFeedPanel';
import AIPanel from '../components/panels/AIPanel';
import OrderExecutionPanel from '../components/panels/OrderExecutionPanel';
import { useTradeStore } from '../store/useTradeStore';

export default function Home() {
  const { connectWebSocket, activeDecision, liveDecisions } = useTradeStore();
  const [activeTimeframe, setActiveTimeframe] = useState('1m');
  const [indicatorsEnabled, setIndicatorsEnabled] = useState(true);
  const [aiEnabled, setAiEnabled] = useState(true);

  useEffect(() => {
    connectWebSocket();
  }, [connectWebSocket]);

  const latestDecision = activeDecision ?? liveDecisions[liveDecisions.length - 1] ?? null;
  const symbol = latestDecision?.symbol ?? '---';
  const lastPrice = latestDecision?.price;
  const timeframes = ['1m', '5m', '15m', '1h', '1D'];

  return (
    <TerminalLayout leftPanel={<LiveFeedPanel />} rightPanel={<AIPanel />}>
      <div className="flex h-full min-h-0 w-full flex-col gap-4">
        <div className="flex h-12 items-center justify-between gap-4 rounded-xl border border-border-default bg-surface px-4">
          <div className="flex min-w-0 items-center gap-3">
            <div className="truncate text-sm font-semibold text-text-primary">{symbol}</div>
            <div className="text-xs text-text-secondary">{lastPrice ? `$${lastPrice.toFixed(2)}` : 'Price --'}</div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {timeframes.map((frame) => (
              <button
                key={frame}
                type="button"
                onClick={() => setActiveTimeframe(frame)}
                className={`rounded-md border px-2.5 py-1 text-xs font-semibold transition-colors ${activeTimeframe === frame
                    ? 'border-primary bg-primary text-text-primary hover:bg-primary-hover'
                    : 'border-border-default bg-card text-text-secondary hover:bg-elevated'
                  }`}
              >
                {frame}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => setIndicatorsEnabled((prev) => !prev)}
              className={`rounded-md border px-2.5 py-1 text-xs font-semibold transition-colors ${indicatorsEnabled
                  ? 'border-primary bg-primary text-text-primary hover:bg-primary-hover'
                  : 'border-border-default bg-surface text-text-secondary hover:bg-elevated'
                }`}
            >
              Indicators
            </button>
            <button
              type="button"
              onClick={() => setAiEnabled((prev) => !prev)}
              className={`rounded-md border px-2.5 py-1 text-xs font-semibold transition-colors ${aiEnabled
                  ? 'border-primary bg-primary text-text-primary hover:bg-primary-hover'
                  : 'border-border-default bg-surface text-text-secondary hover:bg-elevated'
                }`}
            >
              AI
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1">
          <TradingChart showHeader={false} />
        </div>

        <div className="shrink-0">
          <OrderExecutionPanel />
        </div>
      </div>
    </TerminalLayout>
  );
}
