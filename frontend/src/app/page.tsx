'use client';

import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Loader2, PanelRightClose, PanelRightOpen } from 'lucide-react';
import TradingChart from '../components/TradingChart';
import TerminalLayout from '../components/layout/TerminalLayout';
import LiveFeedPanel from '../components/panels/LiveFeedPanel';
import OrderExecutionPanel from '../components/panels/OrderExecutionPanel';
import AlphaPredictiveChart from '../components/AlphaPredictiveChart';
import IntradayLayout from '../components/layouts/IntradayLayout';
import SwingLayout, { SwingConfluencePanel } from '../components/layouts/SwingLayout';
import InvestorLayout, { MacroSentimentPanel } from '../components/layouts/InvestorLayout';
import OrderBook from '../components/OrderBook';
import SystemConsole from '../components/SystemConsole';
import { useTradeStore, TradeProfile, ChartTimeframe } from '../store/useTradeStore';
import { isOnboardingComplete } from '@/lib/onboarding';

// ── Sidebar labels per profile ──────────────────────────────────────────
const SIDEBAR_CONFIG: Record<TradeProfile, { label: string; badge: string; badgeColor: string }> = {
  INTRADAY: { label: 'Order Book', badge: 'INTRADAY', badgeColor: 'bg-emerald-500/10 text-emerald-400' },
  SWING: { label: 'Confluence', badge: 'SWING', badgeColor: 'bg-amber-500/10 text-amber-400' },
  INVESTOR: { label: 'Macro Intelligence', badge: 'INVESTOR', badgeColor: 'bg-cyan-500/10 text-cyan-400' },
};

export default function Home() {
  const router = useRouter();
  const { connectWebSocket, connectAlphaWebSocket, connectPredictiveWebSocket, connectInsightWebSocket, activeDecision, liveDecisions, activeProfile, activeTimeframe, setActiveTimeframe } = useTradeStore();
  const [indicatorsEnabled, setIndicatorsEnabled] = useState(true);
  const [aiEnabled, setAiEnabled] = useState(true);
  const [isChecking, setIsChecking] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(true);

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
  const timeframes: ChartTimeframe[] = ['1m', '5m', '10m', '15m', '1H', '1D'];

  const profileBadgeConfig: Record<TradeProfile, { label: string; color: string }> = {
    INTRADAY: { label: 'INTRADAY MODE', color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' },
    SWING: { label: 'SWING MODE', color: 'bg-amber-500/10 text-amber-400 border-amber-500/30' },
    INVESTOR: { label: 'INVESTOR MODE', color: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30' },
  };
  const badge = profileBadgeConfig[activeProfile];
  const sidebarCfg = SIDEBAR_CONFIG[activeProfile];

  // ── Profile-Driven Content Renderer ────────────────────────────────
  const renderProfileContent = () => {
    switch (activeProfile) {
      case 'INTRADAY':
        return <IntradayLayout activeProfile={activeProfile} timeframe={activeTimeframe} isExpanded={!sidebarOpen} onToggleExpand={() => setSidebarOpen(!sidebarOpen)} />;

      case 'SWING':
        return <SwingLayout activeProfile={activeProfile} timeframe={activeTimeframe} isExpanded={!sidebarOpen} onToggleExpand={() => setSidebarOpen(!sidebarOpen)} />;

      case 'INVESTOR':
        return <InvestorLayout activeProfile={activeProfile} timeframe={activeTimeframe} isExpanded={!sidebarOpen} onToggleExpand={() => setSidebarOpen(!sidebarOpen)} />;

      default:
        return null;
    }
  };

  // ── Profile-Driven Sidebar Content ────────────────────────────────
  const renderSidebarContent = () => {
    switch (activeProfile) {
      case 'INTRADAY':
        return <OrderBook />;
      case 'SWING':
        return <SwingConfluencePanel />;
      case 'INVESTOR':
        return <MacroSentimentPanel />;
      default:
        return null;
    }
  };

  return (
    <div className="flex h-full flex-col bg-background">
      {/* ── Profile-Driven Terminal ────────────────────────── */}
      <div className="min-h-0 flex-1">
        <TerminalLayout leftPanel={<LiveFeedPanel />}>
          <div className="flex h-full min-h-0 w-full gap-0">
            {/* ── Left: Chart + Order Execution ──────────────── */}
            <div className={`flex min-h-0 min-w-0 flex-col rounded-lg border border-border-default bg-surface panel-shadow-lg transition-all duration-300 ease-out ${sidebarOpen ? 'flex-1' : 'w-full'}`}>
              <div className="flex h-10 shrink-0 items-center justify-between gap-3 border-b border-border-default px-3 bg-surface rounded-t-lg">
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
                  {/* Sidebar toggle button */}
                  <button
                    type="button"
                    onClick={() => setSidebarOpen(!sidebarOpen)}
                    className={`rounded-md p-1.5 text-xs font-semibold transition-colors ${
                      sidebarOpen
                        ? 'bg-emerald-500/15 text-emerald-400'
                        : 'bg-surface text-text-secondary hover:bg-elevated'
                    }`}
                    title={sidebarOpen ? `Hide ${sidebarCfg.label}` : `Show ${sidebarCfg.label}`}
                  >
                    {sidebarOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
                  </button>
                </div>
              </div>

              {/* Chart area - takes full width */}
              <div className="min-h-0 flex-1 bg-surface relative flex flex-col p-1.5 overflow-hidden">
                {renderProfileContent()}
              </div>

              {/* Buy/Sell Panel */}
              <div className="shrink-0 border-t border-border-default bg-surface rounded-b-lg">
                <OrderExecutionPanel />
              </div>
            </div>

            {/* ── Right: Collapsible Profile Sidebar ─────────── */}
            <div
              className={`
                flex flex-col min-h-0 overflow-hidden transition-all duration-300 ease-out
                ${sidebarOpen
                  ? 'w-[300px] min-w-[260px] max-w-[340px] opacity-100 ml-2'
                  : 'w-0 min-w-0 max-w-0 opacity-0 ml-0 pointer-events-none'
                }
              `}
            >
              {/* Sidebar Header with Collapse Toggle */}
              <div className="flex shrink-0 items-center justify-between rounded-t-lg border border-b-0 border-border-default bg-surface px-3 py-1.5">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-text-primary tracking-wide">{sidebarCfg.label}</span>
                  <span className={`rounded px-1.5 py-px text-[9px] font-bold uppercase tracking-widest ${sidebarCfg.badgeColor}`}>
                    {sidebarCfg.badge}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setSidebarOpen(false)}
                  className="rounded p-1 text-text-muted transition-colors hover:bg-elevated hover:text-text-primary"
                  title="Collapse sidebar"
                >
                  <PanelRightClose size={14} />
                </button>
              </div>

              {/* Sidebar Content */}
              <div className="flex-1 min-h-0 overflow-y-auto rounded-b-lg border border-t-0 border-border-default bg-surface">
                {renderSidebarContent()}
              </div>
            </div>
          </div>
        </TerminalLayout>
      </div>

      {/* ── System Status Console (Bottom Drawer) ─────── */}
      <SystemConsole />
    </div>
  );
}
