'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2 } from 'lucide-react';
import TradingChart from '../components/TradingChart';
import TerminalLayout from '../components/layout/TerminalLayout';
import LiveFeedPanel from '../components/panels/LiveFeedPanel';
import AIPanel from '../components/panels/AIPanel';
import OrderExecutionPanel from '../components/panels/OrderExecutionPanel';
import AlphaPredictiveChart from '../components/AlphaPredictiveChart';
import { useTradeStore } from '../store/useTradeStore';
import { isOnboardingComplete } from '@/lib/onboarding';

export default function Home() {
  const router = useRouter();
  const { connectWebSocket, connectAlphaWebSocket, activeDecision, liveDecisions } = useTradeStore();
  const [activeTimeframe, setActiveTimeframe] = useState('1m');
  const [indicatorsEnabled, setIndicatorsEnabled] = useState(true);
  const [aiEnabled, setAiEnabled] = useState(true);
  const [isChecking, setIsChecking] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function gate() {
      try {
        const complete = await isOnboardingComplete();
        if (cancelled) return;

        if (!complete) {
          router.replace('/auth/onboarding');
          return;
        }

        setIsChecking(false);
      } catch {
        if (!cancelled) {
          router.replace('/auth/login?reason=session_expired');
        }
      }
    }

    gate();
    return () => {
      cancelled = true;
    };
  }, [router]);

  useEffect(() => {
    if (!isChecking) {
      connectWebSocket();
    }
  }, [connectWebSocket, isChecking]);

  useEffect(() => {
    connectAlphaWebSocket('ws://127.0.0.1:8081');
  }, [connectAlphaWebSocket]);

  if (isChecking) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center gap-3 text-sm text-text-secondary">
        <Loader2 size={18} className="animate-spin" />
        <span>Preparing your workspace...</span>
      </div>
    );
  }

  const latestDecision = activeDecision ?? liveDecisions[liveDecisions.length - 1] ?? null;
  const symbol = latestDecision?.symbol ?? '---';
  const lastPrice = latestDecision?.price;
  const timeframes = ['1m', '5m', '15m', '1h', '1D'];

  return (
    <TerminalLayout leftPanel={<LiveFeedPanel />} rightPanel={<AIPanel />}>
      <div className="flex h-full min-h-0 w-full flex-col rounded-lg border border-border-default bg-surface panel-shadow-lg">
        <div className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-border-default px-4 bg-surface rounded-t-lg">
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
                className={`rounded-md px-2.5 py-1 text-xs font-semibold transition-colors ${activeTimeframe === frame
                  ? 'bg-[#ECFDF5] text-[#059669]'
                  : 'bg-surface text-text-secondary hover:bg-elevated'
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
              className={`rounded-md px-2.5 py-1 text-xs font-semibold transition-colors ${indicatorsEnabled
                ? 'bg-[#ECFDF5] text-[#059669]'
                : 'bg-surface text-text-secondary hover:bg-elevated'
                }`}
            >
              Indicators
            </button>
            <button
              type="button"
              onClick={() => setAiEnabled((prev) => !prev)}
              className={`rounded-md px-2.5 py-1 text-xs font-semibold transition-colors ${aiEnabled
                ? 'bg-[#ECFDF5] text-[#059669]'
                : 'bg-surface text-text-secondary hover:bg-elevated'
                }`}
            >
              AI
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 bg-surface relative flex flex-col gap-4 p-4 overflow-y-auto">
          <div>
            <h2 className="text-sm font-semibold text-text-primary mb-2">V2 Predictive Engine (10m OHLC)</h2>
            <AlphaPredictiveChart />
          </div>
          <div className="h-[400px] relative">
            <h2 className="text-sm font-semibold text-text-primary mb-2">V1 Reactive Feed</h2>
            <TradingChart showHeader={false} />
          </div>
        </div>

        <div className="shrink-0 border-t border-border-default bg-surface rounded-b-lg">
          <OrderExecutionPanel />
        </div>
      </div>
    </TerminalLayout>
  );
}
