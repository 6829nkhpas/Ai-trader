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
import ProfileSwitcher from '../components/ProfileSwitcher';
import IntradayLayout from '../components/layouts/IntradayLayout';
import { useTradeStore, TradeProfile } from '../store/useTradeStore';
import { isOnboardingComplete } from '@/lib/onboarding';

export default function Home() {
  const router = useRouter();
  const { connectWebSocket, connectAlphaWebSocket, connectPredictiveWebSocket, activeDecision, liveDecisions, activeProfile } = useTradeStore();
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
  }, [connectAlphaWebSocket, connectPredictiveWebSocket]);

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
    INTRADAY: { label: 'INTRADAY MODE', color: 'bg-purple-500/20 text-purple-400 border-purple-500/30' },
    SWING: { label: 'SWING MODE', color: 'bg-amber-500/20 text-amber-400 border-amber-500/30' },
    INVESTOR: { label: 'INVESTOR MODE', color: 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30' },
  };
  const badge = profileBadgeConfig[activeProfile];

  // ── Profile-Driven Content Renderer ────────────────────────────────
  const renderProfileContent = () => {
    switch (activeProfile) {
      case 'INTRADAY':
        return <IntradayLayout activeProfile={activeProfile} />;

      case 'SWING':
        return (
          <div className="flex h-full items-center justify-center">
            <div className="flex flex-col items-center gap-4 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-amber-500/20 bg-amber-500/5">
                <svg className="h-8 w-8 text-amber-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
                </svg>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-slate-200">Swing Mode</h3>
                <p className="mt-1 text-xs text-slate-500 max-w-xs">
                  Multi-timeframe confluence charts and momentum oscillator dashboards — arriving in Phase 8.3.
                </p>
              </div>
              <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-[10px] font-bold text-amber-400 uppercase tracking-widest">
                Coming Soon
              </span>
            </div>
          </div>
        );

      case 'INVESTOR':
        return (
          <div className="flex h-full items-center justify-center">
            <div className="flex flex-col items-center gap-4 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl border border-cyan-500/20 bg-cyan-500/5">
                <svg className="h-8 w-8 text-cyan-400" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0112 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 013 12c0-1.605.42-3.113 1.157-4.418" />
                </svg>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-slate-200">Investor Mode</h3>
                <p className="mt-1 text-xs text-slate-500 max-w-xs">
                  Macro sentiment dashboards and portfolio allocation views — arriving in Phase 8.3.
                </p>
              </div>
              <span className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-3 py-1 text-[10px] font-bold text-cyan-400 uppercase tracking-widest">
                Coming Soon
              </span>
            </div>
          </div>
        );

      default:
        return null;
    }
  };

  return (
    <div className="flex h-full flex-col bg-slate-950">
      {/* ── Master Profile Control Bar ─────────────────────── */}
      <ProfileSwitcher />

      {/* ── Profile-Driven Terminal ────────────────────────── */}
      <div className="min-h-0 flex-1">
        {activeProfile === 'INTRADAY' ? (
          // Intraday uses its own dedicated grid layout (no TerminalLayout wrapper)
          renderProfileContent()
        ) : (
          // Swing/Investor use the standard TerminalLayout with side panels
          <TerminalLayout leftPanel={<LiveFeedPanel />} rightPanel={<AIPanel />}>
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
                    className={`rounded-md px-2.5 py-1 text-xs font-semibold transition-colors ${
                      indicatorsEnabled
                        ? 'bg-[#ECFDF5] text-[#059669]'
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
                        ? 'bg-[#ECFDF5] text-[#059669]'
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
        )}
      </div>
    </div>
  );
}
