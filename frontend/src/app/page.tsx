'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2 } from 'lucide-react';
import TradingChart from '../components/TradingChart';
import TerminalLayout from '../components/layout/TerminalLayout';
import LiveFeedPanel from '../components/panels/LiveFeedPanel';
import OrderExecutionPanel from '../components/panels/OrderExecutionPanel';
import AlphaPredictiveChart from '../components/AlphaPredictiveChart';
import IntradayLayout from '../components/layouts/IntradayLayout';
import SwingLayout from '../components/layouts/SwingLayout';
import InvestorLayout from '../components/layouts/InvestorLayout';
import SystemConsole from '../components/SystemConsole';
import { useTradeStore, TradeProfile } from '../store/useTradeStore';
import { isOnboardingComplete } from '@/lib/onboarding';

export default function Home() {
  const router = useRouter();
  const { connectWebSocket, connectAlphaWebSocket, connectPredictiveWebSocket, connectInsightWebSocket, activeDecision, liveDecisions, activeProfile } = useTradeStore();
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
    connectPredictiveWebSocket('ws://127.0.0.1:8082');
    connectInsightWebSocket('ws://127.0.0.1:8083');
  }, [connectAlphaWebSocket, connectPredictiveWebSocket, connectInsightWebSocket]);

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

  const profileBadgeConfig: Record<TradeProfile, { label: string; color: string }> = {
    INTRADAY: { label: 'INTRADAY MODE', color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' },
    SWING: { label: 'SWING MODE', color: 'bg-amber-500/10 text-amber-400 border-amber-500/30' },
    INVESTOR: { label: 'INVESTOR MODE', color: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30' },
  };
  const badge = profileBadgeConfig[activeProfile];

  // ── Profile-Driven Content Renderer ────────────────────────────────
  const renderProfileContent = () => {
    switch (activeProfile) {
      case 'INTRADAY':
        return <IntradayLayout activeProfile={activeProfile} timeframe={activeTimeframe} />;

      case 'SWING':
        return <SwingLayout activeProfile={activeProfile} timeframe={activeTimeframe} />;

      case 'INVESTOR':
        return <InvestorLayout activeProfile={activeProfile} timeframe={activeTimeframe} />;

      default:
        return null;
    }
  };

  return (
    <div className="flex h-full flex-col bg-background">
      {/* ── Profile-Driven Terminal ────────────────────────── */}
      <div className="min-h-0 flex-1">
        <TerminalLayout leftPanel={<LiveFeedPanel />}>
          <div className="flex h-full min-h-0 w-full flex-col rounded-lg border border-border-default bg-surface panel-shadow-lg">
            <div className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-border-default px-4 bg-surface rounded-t-lg">
              <div className="flex min-w-0 items-center gap-3">
                <div className="truncate text-sm font-semibold text-text-primary">{symbol}</div>
                <div className="text-xs text-text-secondary">{lastPrice ? `$${lastPrice.toFixed(2)}` : 'Price --'}</div>
                {/* Profile Mode Badge */}
                <span
                  id="profile-mode-badge"
                  className={`rounded-md border px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest ${badge.color}`}
                >
                  {badge.label}
                </span>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {timeframes.map((frame) => (
                  <button
                    key={frame}
                    type="button"
                    onClick={() => setActiveTimeframe(frame)}
                    className={`rounded-md px-2.5 py-1 text-xs font-semibold transition-colors ${
                      activeTimeframe === frame
                        ? 'bg-emerald-500/15 text-emerald-400'
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
                  className={`rounded-md px-2.5 py-1 text-xs font-semibold transition-colors ${
                    indicatorsEnabled
                      ? 'bg-emerald-500/15 text-emerald-400'
                      : 'bg-surface text-text-secondary hover:bg-elevated'
                  }`}
                >
                  Indicators
                </button>
                <button
                  type="button"
                  onClick={() => setAiEnabled((prev) => !prev)}
                  className={`rounded-md px-2.5 py-1 text-xs font-semibold transition-colors ${
                    aiEnabled
                      ? 'bg-emerald-500/15 text-emerald-400'
                      : 'bg-surface text-text-secondary hover:bg-elevated'
                  }`}
                >
                  AI
                </button>
              </div>
            </div>

            <div className="min-h-0 flex-1 bg-surface relative flex flex-col gap-4 p-4 overflow-y-auto">
              {renderProfileContent()}
            </div>

            <div className="shrink-0 border-t border-border-default bg-surface rounded-b-lg">
              <OrderExecutionPanel />
            </div>
          </div>
        </TerminalLayout>
      </div>

      {/* ── System Status Console (Bottom Drawer) ─────── */}
      <SystemConsole />
    </div>
  );
}
