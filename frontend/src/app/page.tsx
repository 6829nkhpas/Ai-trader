'use client';

import React, { useEffect, useState, useCallback, useRef } from 'react';
import { PanelRightClose, PanelRightOpen, ArrowUpRight, ArrowDownRight, ChevronUp, TrendingUp, TrendingDown, Maximize2, Minimize2 } from 'lucide-react';
import ChartModeToggle from '../components/chart/ChartHeader';
import StrategySelector from '../components/chart/StrategySelector';
import GhostLineToggle from '../components/chart/GhostLineToggle';
import TerminalLayout from '../components/layout/TerminalLayout';
import LeftPanel from '../components/panels/LeftPanel';
import OrderExecutionPanel from '../components/panels/OrderExecutionPanel';
import IntradayLayout from '../components/layouts/IntradayLayout';
import SwingLayout, { SwingConfluencePanel } from '../components/layouts/SwingLayout';
import InvestorLayout, { MacroSentimentPanel } from '../components/layouts/InvestorLayout';
import OrderBook from '../components/OrderBook';

import DeepQuantPanel from '../components/quant/DeepQuantPanel';
import ActivePositions from '../components/quant/ActivePositions';
import PortfolioDashboard from '../components/quant/PortfolioDashboard';
import FnoSection from '../components/fno/FnoSection';
import FnoSidebarPanel from '../components/fno/FnoSidebarPanel';
import SplitChartContainer from '../components/chart/SplitChartContainer';
import SplitViewToggle from '../components/chart/SplitViewToggle';
import { useTradeStore, TradeProfile, hydratePaperPortfolio } from '../store/useTradeStore';
import { useQuantStore } from '../store/useQuantStore';
import { useChartUIStore } from '../store/useChartUIStore';
import type { ConsensusReport } from '../store/useQuantStore';
import type { DataRange } from '../utils/chartTypes';
import { useAuthStore } from '../store/useAuthStore';
import AuthOverlay from '../components/auth/AuthOverlay';
import BrokerConnectCard from '../components/broker/BrokerConnectCard';

// ── Sidebar labels per profile ──────────────────────────────────────────
type SidebarTab = 'profile' | 'deepquant';

const SIDEBAR_CONFIG: Record<TradeProfile, { label: string; badge: string; badgeColor: string }> = {
  INTRADAY: { label: 'Order Book', badge: 'INTRADAY', badgeColor: 'bg-emerald-500/10 text-emerald-400' },
  SWING: { label: 'Confluence', badge: 'SWING', badgeColor: 'bg-amber-500/10 text-amber-400' },
  INVESTOR: { label: 'Macro Intelligence', badge: 'INVESTOR', badgeColor: 'bg-cyan-500/10 text-cyan-400' },
  FNO: { label: 'Options Flow', badge: 'F&O', badgeColor: 'bg-emerald-500/10 text-emerald-400' },
};

export default function Home() {
  const { connectWebSocket, connectAlphaWebSocket, connectPredictiveWebSocket, connectInsightWebSocket, connectOrderFlowWebSocket, activeDecision, liveDecisions, activeProfile, activeTimeframe, activeRange, setActiveRange, selectedSymbol, paperPortfolio } = useTradeStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isBrokerConnected = useAuthStore((s) => s.isBrokerConnected);
  const setBrokerConnected = useAuthStore((s) => s.setBrokerConnected);
  const fetchProfile = useAuthStore((s) => s.fetchProfile);

  // ── Premium Toast Notification State ────────────────────────────────
  const [toasts, setToasts] = useState<{ id: string; message: string; type: 'success' | 'info' }[]>([]);
  const showToast = useCallback((message: string, type: 'success' | 'info' = 'success') => {
    const id = Math.random().toString(36).substring(2, 9);
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4500);
  }, []);

  const isFullscreen = useChartUIStore((s) => s.isFullscreen);
  const setIsFullscreen = useChartUIStore((s) => s.setIsFullscreen);
  const toggleFullscreen = useChartUIStore((s) => s.toggleFullscreen);
  const activeStrategyId = useChartUIStore((s) => s.activeStrategyId);
  const setActiveStrategyId = useChartUIStore((s) => s.setActiveStrategyId);
  const splitView = useChartUIStore((s) => s.splitView);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>('profile');

  // Listen for Escape key to exit fullscreen mode
  useEffect(() => {
    if (!isFullscreen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsFullscreen(false);
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isFullscreen, setIsFullscreen]);

  // Reset fullscreen state when this page unmounts so a stale `true` in the
  // shared store can never leak into a fresh mount.
  useEffect(() => {
    return () => setIsFullscreen(false);
  }, [setIsFullscreen]);

  const [paperPortfolioOpen, setPaperPortfolioOpen] = useState(false);
  const consensusData = useQuantStore((s) => s.consensusData);
  const setConsensusData = useQuantStore((s) => s.setConsensusData);
  const clearConsensusData = useQuantStore((s) => s.clearConsensusData);
  const loadConsensusForSymbol = useQuantStore((s) => s.loadConsensusForSymbol);
  const clearAiPlan = useQuantStore((s) => s.clearAiPlan);

  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  // Listen for Tauri consensus events
  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;

    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        // Bail if the component unmounted while we were importing
        if (cancelled) return;
        const u = await listen<ConsensusReport>('quant-consensus', (event) => {
          if (!cancelled) {
            setConsensusData(event.payload);
          }
        });
        if (cancelled) {
          // Already unmounted — clean up immediately
          u();
        } else {
          unlisten = u;
        }
      } catch {
        // Not in Tauri context — ignore
      }
    })();

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [setConsensusData]);

  // Listen for Tauri deep-link events emitted by Rust
  useEffect(() => {
    let cancelled = false;
    let unlistenBroker: (() => void) | undefined;
    let unlistenPayment: (() => void) | undefined;

    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        if (cancelled) return;

        // Listen for broker connection success
        unlistenBroker = await listen('broker-connection-success', () => {
          if (!cancelled) {
            console.log('[App] Intercepted broker-connection-success deep link event! Enabling trading terminal.');
            setBrokerConnected(true);
            showToast("Zerodha Kite Connected Successfully.", "success");
          }
        });

        // Listen for payment success
        unlistenPayment = await listen('payment-success', async () => {
          if (!cancelled) {
            console.log('[App] Intercepted payment-success deep link event! Refreshing user profile.');
            // Refresh user profile in Zustand (fetchUserProfile hits /api/auth/me)
            await useAuthStore.getState().fetchUserProfile();
            showToast("Payment Verified. Welcome to PRO.", "success");
          }
        });
      } catch {
        // Not in Tauri context (e.g. browser preview)
      }
    })();

    return () => {
      cancelled = true;
      unlistenBroker?.();
      unlistenPayment?.();
    };
  }, [setBrokerConnected, showToast]);

  // ── Real-time Kite quote for the active symbol ────────────────────
  interface SymbolQuote {
    symbol: string;
    last_price: number;
    open: number;
    high: number;
    low: number;
    close: number; // prev close
    change: number; // % change
    net_change: number;
    volume: number;
  }
  const [symbolQuote, setSymbolQuote] = useState<SymbolQuote | null>(null);

  useEffect(() => {
    connectWebSocket();
    hydratePaperPortfolio();
    fetchProfile();
  }, [connectWebSocket, fetchProfile]);

  useEffect(() => {
    connectAlphaWebSocket('ws://127.0.0.1:8081');
    connectPredictiveWebSocket('ws://127.0.0.1:8082');
    connectInsightWebSocket('ws://127.0.0.1:8083');
    connectOrderFlowWebSocket('ws://127.0.0.1:8089');
  }, [connectAlphaWebSocket, connectPredictiveWebSocket, connectInsightWebSocket, connectOrderFlowWebSocket]);

  // Derive symbol early so hooks below can reference it unconditionally.
  // selectedSymbol (watchlist click) takes priority over the AI decision symbol.
  const latestDecision = activeDecision ?? liveDecisions[liveDecisions.length - 1] ?? null;
  const symbol = selectedSymbol || latestDecision?.symbol || 'RELIANCE';

  // ── Clear stale quant data on symbol switch ───────────────────────────
  // When the user clicks a new symbol, immediately load cached consensus
  // (if we ran Deep Quant on it before) or clear to prevent stale cross-
  // symbol data. Also clear any AI plan from the previous symbol.
  useEffect(() => {
    loadConsensusForSymbol(symbol);
    clearAiPlan();
  }, [symbol, loadConsensusForSymbol, clearAiPlan]);

  // Fetch real-time quote for the active symbol
  const fetchSymbolQuote = useCallback(async (signal?: AbortSignal) => {
    if (!symbol || symbol === '---') return;
    try {
      const res = await fetch(`/kite/quote?i=NSE:${symbol}`, { signal });
      if (!res.ok) return;
      const data = await res.json();
      if (data.quotes && data.quotes.length > 0) {
        setSymbolQuote(data.quotes[0]);
      }
    } catch (err) {
      // Silence AbortError — expected on unmount
      if (err instanceof DOMException && err.name === 'AbortError') return;
      console.error('[Header] Quote fetch failed:', err);
    }
  }, [symbol]);

  useEffect(() => {
    const controller = new AbortController();
    fetchSymbolQuote(controller.signal);
    const interval = setInterval(() => fetchSymbolQuote(controller.signal), 30_000);
    return () => {
      controller.abort();
      clearInterval(interval);
    };
  }, [fetchSymbolQuote]);

  const rangeOptions: DataRange[] = ['60D', '1Y', '2Y', '3Y', '5Y'];
  const rangeLabels: Record<DataRange, string> = { '60D': '60D', '1Y': '1Y', '2Y': '2Y', '3Y': '3Y', '5Y': '5Y' };

  const profileBadgeConfig: Record<TradeProfile, { label: string; color: string }> = {
    INTRADAY: { label: 'INTRADAY MODE', color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' },
    SWING: { label: 'SWING MODE', color: 'bg-amber-500/10 text-amber-400 border-amber-500/30' },
    INVESTOR: { label: 'INVESTOR MODE', color: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30' },
    FNO: { label: 'F&O MODE', color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' },
  };
  const badge = profileBadgeConfig[activeProfile];
  const sidebarCfg = SIDEBAR_CONFIG[activeProfile];

  // ── Paper Portfolio Calculations ────────────────────────────────────
  const calculateRealizedPnL = (pos: any) => {
    const isWin = pos.status === 'CLOSED_WIN';
    const exitPrice = isWin ? pos.take_profit : pos.stop_loss;
    const diff = exitPrice - pos.entry_price;
    return pos.side === 'BUY' ? diff * pos.quantity : -diff * pos.quantity;
  };

  const totalPnL = paperPortfolio?.trade_history.reduce((sum, pos) => sum + calculateRealizedPnL(pos), 0) ?? 0;

  // ── Profile-Driven Content Renderer ────────────────────────────────
  const renderProfileContent = () => {
    // Split is only meaningful in Intraday & F&O (mode-gated). When on, the
    // chart area mounts the dual-pane SplitChartContainer for those modes.
    const split = splitView && (activeProfile === 'INTRADAY' || activeProfile === 'FNO');

    switch (activeProfile) {
      case 'INTRADAY':
        return split
          ? <SplitChartContainer mode="INTRADAY" />
          : <IntradayLayout activeProfile={activeProfile} timeframe={activeTimeframe} isExpanded={!sidebarOpen} onToggleExpand={() => setSidebarOpen(!sidebarOpen)} />;

      case 'SWING':
        return <SwingLayout activeProfile={activeProfile} timeframe={activeTimeframe} isExpanded={!sidebarOpen} onToggleExpand={() => setSidebarOpen(!sidebarOpen)} />;

      case 'INVESTOR':
        return <InvestorLayout activeProfile={activeProfile} timeframe={activeTimeframe} isExpanded={!sidebarOpen} onToggleExpand={() => setSidebarOpen(!sidebarOpen)} />;

      case 'FNO':
        return split ? <SplitChartContainer mode="FNO" /> : <FnoSection />;

      default:
        // Unknown/unset mode falls back to the Intraday workspace so an
        // unexpected stored value never blanks the terminal (design: Error Handling).
        return <IntradayLayout activeProfile={activeProfile} timeframe={activeTimeframe} isExpanded={!sidebarOpen} onToggleExpand={() => setSidebarOpen(!sidebarOpen)} />;
    }
  };

  // ── Profile-Driven Sidebar Content ────────────────────────────────
  const renderSidebarContent = () => {
    if (sidebarTab === 'deepquant') {
      return <DeepQuantPanel />;
    }
    // Default: profile-driven
    switch (activeProfile) {
      case 'INTRADAY':
        return <OrderBook />;
      case 'SWING':
        return <SwingConfluencePanel />;
      case 'INVESTOR':
        return <MacroSentimentPanel />;
      case 'FNO':
        return <FnoSidebarPanel />;
      default:
        return null;
    }
  };

  const sidebarTitle = sidebarTab === 'deepquant' ? 'Deep Quant' : sidebarCfg.label;

  if (!mounted) {
    return <div className="flex h-screen w-screen items-center justify-center bg-background" />;
  }

  if (!isAuthenticated) {
    return <AuthOverlay />;
  }

  if (!isBrokerConnected) {
    return <BrokerConnectCard />;
  }

  return (
    <div className="flex h-full flex-col bg-background">
      {/* ── Profile-Driven Terminal ────────────────────────── */}
      <div className="min-h-0 flex-1">
        <TerminalLayout leftPanel={<LeftPanel />}>
          <div className="flex h-full min-h-0 min-w-0 w-full gap-0">
            {/* ── Left: Chart + Order Execution ──────────────── */}
            <div className={
              isFullscreen
                ? "fixed inset-0 z-[150] flex flex-col bg-background p-2"
                : `flex min-h-0 min-w-0 flex-col rounded-none bg-surface ${
                    sidebarOpen ? 'flex-1' : 'w-full'
                  }`
            }>
              <div className="flex h-9 shrink-0 items-center justify-between border-b border-border-default bg-surface rounded-none pl-2">
                <div className="flex min-w-0 shrink items-center gap-1 overflow-hidden">
                  {symbolQuote ? (
                    <>
                      <div className="shrink-0 text-xs font-semibold text-text-primary tabular-nums">
                        ₹{symbolQuote.last_price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </div>
                      <div className={`flex shrink-0 items-center gap-0.5 text-[11px] font-medium tabular-nums ${symbolQuote.change >= 0 ? 'text-bull' : 'text-bear'}`}>
                        {symbolQuote.change >= 0 ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
                        {symbolQuote.change >= 0 ? '+' : ''}{symbolQuote.change.toFixed(2)}%
                      </div>
                    </>
                  ) : (
                    <div className="truncate text-xs font-semibold text-text-primary">{symbol}</div>
                  )}
                </div>

                <div className="flex h-full shrink-0 items-center border-l border-border-default">
                  {/* ── Chart cluster: mode · strategy · projection (R8.2) ── */}
                  {/* Chart type, indicators, timeframe, and drawing tools are now
                      provided natively by the TradingView Advanced Charts widget. */}
                  <div className="flex h-full items-center" role="group" aria-label="Chart controls">
                  {/* Chart-mode toggle (Standard / Volume Profile / Footprint) */}
                  <ChartModeToggle />

                  {/* Strategy selector */}
                  <StrategySelector
                    activeStrategyId={activeStrategyId}
                    onSelect={setActiveStrategyId}
                    onOpenSettings={() => {}}
                  />

                  {/* Projection engine toggle (OLS / VWEPR ghost line) */}
                  <GhostLineToggle />
                  </div>

                  {/* ── View cluster: single/split · timeframe · fullscreen (R8.2) ── */}
                  <div className="flex h-full items-center" role="group" aria-label="View controls">
                  {/* Single / Split chart layout toggle (self-gating: Intraday & F&O only) */}
                  <SplitViewToggle />

                  {/* Timeframe is now controlled natively by the TV widget's
                      built-in timeframe selector in its top toolbar. */}

                  {/* Fullscreen toggle */}
                  <button
                    type="button"
                    onClick={toggleFullscreen}
                    aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
                    className="flex h-full w-9 items-center justify-center border-r border-border-default bg-surface text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
                  >
                    {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                  </button>
                  </div>

                  {/* ── Analytics cluster: sidebar / Deep Quant (R8.2) ── */}
                  <div className="flex h-full items-center" role="group" aria-label="Analytics controls">
                  {/* Sidebar toggle button */}
                  {!isFullscreen && (
                    <button
                      type="button"
                      onClick={() => setSidebarOpen(!sidebarOpen)}
                      className={`flex h-full w-9 items-center justify-center border-r border-border-default bg-surface text-text-secondary transition-colors hover:bg-elevated ${sidebarOpen
                          ? 'text-emerald-600 dark:text-emerald-400'
                          : ''
                        }`}
                      title={sidebarOpen ? `Hide ${sidebarCfg.label}` : `Show ${sidebarCfg.label}`}
                    >
                      {sidebarOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
                    </button>
                  )}
                  </div>
                </div>
              </div>

              {/* Chart area - takes full width.
                  Drawing tools are now provided natively by the TV widget's left sidebar. */}
              <div className="flex flex-1 min-h-0 w-full overflow-hidden">
                <div className="min-h-0 flex-1 bg-surface relative flex flex-col p-0 overflow-hidden">
                  {renderProfileContent()}
                </div>
              </div>

              {/* Live PNL Positions Drawer */}
              {!isFullscreen && <ActivePositions />}

              {/* Simulated Paper Trading Dashboard */}
              {/* Simulated Paper Trading Dashboard */}
              {!isFullscreen && (
                paperPortfolioOpen ? (
                  <div className="border-t border-border-default bg-surface">
                    <PortfolioDashboard onCollapse={() => setPaperPortfolioOpen(false)} />
                  </div>
                ) : (
                  <div className="px-3 py-1.5 border-t border-border-default bg-surface backdrop-blur-sm flex items-center justify-between transition-all duration-300">
                    <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
                      {/* Live indicator and Title */}
                      <div className="flex items-center gap-2">
                        <span className="relative flex h-2 w-2">
                          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                          <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
                        </span>
                        <span className="text-[10px] font-black uppercase tracking-wider text-text-primary">
                          Simulated Portfolio
                        </span>
                      </div>

                      {/* Stats summary */}
                      {paperPortfolio && (
                        <div className="flex items-center gap-4 text-xs font-mono">
                          <div className="flex items-center gap-1.5 border-r border-border-default/50 pr-4">
                            <span className="text-[9px] uppercase font-bold text-text-muted font-sans">Equity:</span>
                            <span className="font-bold text-text-primary">
                              ₹{paperPortfolio.balance.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                            </span>
                          </div>
                          <div className="flex items-center gap-1.5 border-r border-border-default/50 pr-4">
                            <span className="text-[9px] uppercase font-bold text-text-muted font-sans">PnL:</span>
                            <span className={`font-black flex items-center gap-0.5 ${totalPnL >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`}>
                              {totalPnL >= 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                              {totalPnL >= 0 ? '+' : ''}₹{totalPnL.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                            </span>
                          </div>
                          <div className="flex items-center gap-1.5">
                            <span className="text-[9px] uppercase font-bold text-text-muted font-sans">Positions:</span>
                            <span className="font-bold text-text-primary">{paperPortfolio.active_positions.length} Active</span>
                          </div>
                        </div>
                      )}
                    </div>

                    <button
                      type="button"
                      onClick={() => setPaperPortfolioOpen(true)}
                      className="flex items-center gap-1 rounded bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 text-[9px] font-bold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider hover:bg-emerald-500/20 transition-all duration-150"
                    >
                      <ChevronUp size={10} />
                      Expand Portfolio
                    </button>
                  </div>
                )
              )}

              {/* Buy/Sell Panel */}
              {!isFullscreen && (
                <div className="shrink-0 border-t border-border-default bg-surface rounded-none">
                  <OrderExecutionPanel />
                </div>
              )}
            </div>

            {/* ── Right: Collapsible Profile Sidebar ─────────── */}
            <div
              className={`
                flex flex-col min-h-0 overflow-hidden transition-all duration-300 ease-out border-l border-border-default
                ${sidebarOpen
                  ? 'w-[300px] min-w-[260px] max-w-[340px] opacity-100'
                  : 'w-0 min-w-0 max-w-0 opacity-0 pointer-events-none'
                }
              `}
            >
              {/* Sidebar Header with Tab Switcher */}
              <div className="flex shrink-0 flex-col border-b border-border-default bg-surface rounded-none">
                <div className="flex items-center justify-between px-3 py-1.5">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-text-primary tracking-wide">{sidebarTitle}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setSidebarOpen(false)}
                    className="rounded-none p-1 text-text-muted transition-colors hover:bg-elevated hover:text-text-primary"
                    title="Collapse sidebar"
                  >
                    <PanelRightClose size={14} />
                  </button>
                </div>

                {/* Tab row */}
                <div className="flex border-t border-border-default bg-muted/20">
                  {[
                    { key: 'profile' as SidebarTab, label: sidebarCfg.badge },
                    { key: 'deepquant' as SidebarTab, label: 'AI QUANT' },
                  ].map(({ key, label }, idx) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setSidebarTab(key)}
                      className={`flex-1 rounded-none px-1.5 py-2 text-[9px] font-bold uppercase tracking-wider transition-all duration-200 ${
                        idx > 0 ? 'border-l border-border-default' : ''
                      } ${
                        sidebarTab === key
                          ? 'bg-surface text-text-primary border-b-2 border-emerald-500 dark:border-emerald-400'
                          : 'text-text-muted hover:text-text-secondary bg-transparent hover:bg-muted/10'
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Sidebar Content */}
              <div className="flex-1 min-h-0 overflow-y-auto bg-surface rounded-none">
                {renderSidebarContent()}
              </div>
            </div>
          </div>
        </TerminalLayout>
      </div>

      {/* ── System Status Console (Bottom Drawer) ─────── */}
      {/* <SystemConsole /> */}

      {/* ── Premium Toast Notifications ──────────────────────────────── */}
      <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-3 max-w-sm pointer-events-none">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className="flex items-center gap-3 px-4 py-3.5 rounded-xl border border-emerald-500/20 bg-surface-elevated/80 backdrop-blur-xl shadow-2xl pointer-events-auto animate-slide-in-right"
            style={{
              background: 'linear-gradient(135deg, rgba(16, 185, 129, 0.08) 0%, rgba(5, 150, 105, 0.03) 100%)',
              backgroundColor: 'rgba(15, 23, 42, 0.85)',
              borderColor: 'rgba(16, 185, 129, 0.25)',
              boxShadow: '0 10px 30px -5px rgba(0, 0, 0, 0.5), 0 0 15px 0 rgba(16, 185, 129, 0.05)',
            }}
          >
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <div className="flex flex-col">
              <span className="text-[10px] font-black tracking-widest text-emerald-400 uppercase">SYSTEM NOTIFICATION</span>
              <span className="text-xs font-semibold text-white/90 leading-tight mt-0.5">{toast.message}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
