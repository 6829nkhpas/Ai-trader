'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { SVGS } from '../components/chart/toolbarIcons';
import TerminalLayout from '../components/layout/TerminalLayout';
import LeftPanel from '../components/panels/LeftPanel';
import OrderExecutionPanel from '../components/panels/OrderExecutionPanel';
import IntradayLayout from '../components/layouts/IntradayLayout';
import SwingLayout from '../components/layouts/SwingLayout';
import InvestorLayout from '../components/layouts/InvestorLayout';
import SplitChartContainer from '../components/chart/SplitChartContainer';
import FnoSection from '../components/fno/FnoSection';
import ActivePositions from '../components/quant/ActivePositions';
import PortfolioDashboard from '../components/quant/PortfolioDashboard';
import PaperPortfolioBar from '../components/panels/PaperPortfolioBar';
import RightSidebar from '../components/panels/RightSidebar';
import ToastContainer from '../components/common/ToastContainer';
import AuthOverlay from '../components/auth/AuthOverlay';
import ConnectionLost from '../components/common/ConnectionLost';
import UpdateNotifier from '../components/common/UpdateNotifier';

import { useTradeStore, hydratePaperPortfolio } from '../store/useTradeStore';
import { useQuantStore } from '../store/useQuantStore';
import { useChartUIStore } from '../store/useChartUIStore';
import { useFeatureStore } from '../store/useFeatureStore';
import { useAuthStore } from '../store/useAuthStore';
import { useCredit } from '../hooks/useApi';
import { useTauriLiveData } from '../hooks/useTauriLiveData';
import { useConnectionMonitor } from '../hooks/useConnectionMonitor';
import { useSymbolQuote } from '../hooks/useSymbolQuote';
import { useSidebarDrag } from '../hooks/useSidebarDrag';
import { useToast } from '../hooks/useToast';
import type { ConsensusReport } from '../store/useQuantStore';

export default function Home() {
  // ── Auth & Feature gates ──────────────────────────────────────────
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const fetchProfile = useAuthStore((s) => s.fetchProfile);
  const setFeatureAccessFlags = useFeatureStore((s) => s.setAccessFlags);
  const resetFeatureAccess = useFeatureStore((s) => s.reset);
  const { data: creditData } = useCredit();

  // ── Store selectors ───────────────────────────────────────────────
  const { connectWebSocket, connectAlphaWebSocket, connectPredictiveWebSocket, connectInsightWebSocket, activeDecision, liveDecisions, activeProfile, activeTimeframe, selectedSymbol, paperPortfolio } = useTradeStore();
  const isFullscreen = useChartUIStore((s) => s.isFullscreen);
  const setIsFullscreen = useChartUIStore((s) => s.setIsFullscreen);
  const splitView = useChartUIStore((s) => s.splitView);
  const sidebarOpen = useChartUIStore((s) => s.sidebarOpen);
  const setSidebarOpen = useChartUIStore((s) => s.setSidebarOpen);
  const setConsensusData = useQuantStore((s) => s.setConsensusData);
  const loadConsensusForSymbol = useQuantStore((s) => s.loadConsensusForSymbol);
  const clearAiPlan = useQuantStore((s) => s.clearAiPlan);

  // ── Mounted guard ─────────────────────────────────────────────────
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);

  // ── Extracted hooks ───────────────────────────────────────────────
  const showConnectionLost = useConnectionMonitor(mounted);
  const { toasts } = useToast();
  const { rightButtonTop, isDraggingRight, handleRightButtonMouseDown, sidebarWidth, isResizingSidebar, startResizingSidebar } = useSidebarDrag(() => setSidebarOpen(true));

  const [paperPortfolioOpen, setPaperPortfolioOpen] = useState(false);

  // ── Derived symbol ────────────────────────────────────────────────
  const latestDecision = activeDecision ?? liveDecisions[liveDecisions.length - 1] ?? null;
  const symbol = selectedSymbol || latestDecision?.symbol || '';
  useSymbolQuote(symbol);

  // ── Feature-access hydration ──────────────────────────────────────
  useEffect(() => {
    setFeatureAccessFlags(creditData?.accessFlags ?? null);
    // eslint-disable-next-line react-hooks/set-state-in-effect
  }, [creditData?.accessFlags, setFeatureAccessFlags]);

  useEffect(() => {
    if (!isAuthenticated) resetFeatureAccess();
    // eslint-disable-next-line react-hooks/set-state-in-effect
  }, [isAuthenticated, resetFeatureAccess]);

  // ── WebSocket bootstrap ───────────────────────────────────────────
  useEffect(() => {
    connectWebSocket();
    hydratePaperPortfolio();
    fetchProfile();
  }, [connectWebSocket, fetchProfile]);

  const isTauriEnv = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
  useTauriLiveData(symbol);

  useEffect(() => {
    if (isTauriEnv) return;
    connectAlphaWebSocket(process.env.NEXT_PUBLIC_ALPHA_WS_URL || 'ws://127.0.0.1:8081');
    connectPredictiveWebSocket(process.env.NEXT_PUBLIC_PREDICTIVE_WS_URL || 'ws://127.0.0.1:8082');
    connectInsightWebSocket(process.env.NEXT_PUBLIC_INSIGHT_WS_URL || 'ws://127.0.0.1:8083');
  }, [isTauriEnv, connectAlphaWebSocket, connectPredictiveWebSocket, connectInsightWebSocket]);

  // ── Quant consensus listener ──────────────────────────────────────
  useEffect(() => {
    loadConsensusForSymbol(symbol);
    clearAiPlan();
  }, [symbol, loadConsensusForSymbol, clearAiPlan]);

  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        if (cancelled) return;
        const u = await listen<ConsensusReport>('quant-consensus', (event) => {
          if (!cancelled) setConsensusData(event.payload);
        });
        if (cancelled) { u(); } else { unlisten = u; }
      } catch { /* Not in Tauri context */ }
    })();
    return () => { cancelled = true; unlisten?.(); };
  }, [setConsensusData]);

  // ── Fullscreen keyboard / cleanup ─────────────────────────────────
  useEffect(() => {
    if (!isFullscreen) return;
    const handleKeyDown = (e: KeyboardEvent) => { if (e.key === 'Escape') setIsFullscreen(false); };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isFullscreen, setIsFullscreen]);

  useEffect(() => () => setIsFullscreen(false), [setIsFullscreen]);

  // ── Profile content renderer ──────────────────────────────────────
  const renderProfileContent = useCallback(() => {
    const split = splitView && (activeProfile === 'INTRADAY' || activeProfile === 'FNO');
    switch (activeProfile) {
      case 'INTRADAY':
        return split ? <SplitChartContainer mode="INTRADAY" /> : <IntradayLayout activeProfile={activeProfile} timeframe={activeTimeframe} isExpanded={!sidebarOpen} onToggleExpand={() => setSidebarOpen(!sidebarOpen)} />;
      case 'SWING':
        return <SwingLayout activeProfile={activeProfile} timeframe={activeTimeframe} isExpanded={!sidebarOpen} onToggleExpand={() => setSidebarOpen(!sidebarOpen)} />;
      case 'INVESTOR':
        return <InvestorLayout activeProfile={activeProfile} timeframe={activeTimeframe} isExpanded={!sidebarOpen} onToggleExpand={() => setSidebarOpen(!sidebarOpen)} />;
      case 'FNO':
        return split ? <SplitChartContainer mode="FNO" /> : <FnoSection />;
      default:
        return <IntradayLayout activeProfile={activeProfile} timeframe={activeTimeframe} isExpanded={!sidebarOpen} onToggleExpand={() => setSidebarOpen(!sidebarOpen)} />;
    }
  }, [activeProfile, activeTimeframe, splitView, sidebarOpen, setSidebarOpen]);

  // ── Early returns ─────────────────────────────────────────────────
  if (!mounted) return <div className="flex h-screen w-screen items-center justify-center bg-background" />;
  if (showConnectionLost) return <ConnectionLost />;
  if (!isAuthenticated) return <AuthOverlay />;

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div className="flex h-full flex-col bg-background">
      <div className="min-h-0 flex-1">
        <TerminalLayout leftPanel={<LeftPanel />}>
          <div className="flex h-full min-h-0 min-w-0 w-full gap-0">
            {/* ── Chart + Order Execution column ───────────── */}
            <div className={isFullscreen ? "fixed inset-0 z-150 flex flex-col bg-background p-2" : `relative flex min-h-0 min-w-0 flex-col rounded-none bg-surface ${sidebarOpen ? 'flex-1' : 'w-full'}`}>
              {!isFullscreen && !sidebarOpen && (
                <button
                  onMouseDown={handleRightButtonMouseDown}
                  style={{ top: `${rightButtonTop}px` }}
                  className={`absolute right-0 z-100 flex h-7 w-6 items-center justify-center rounded-l border border-r-0 border-emerald-500/20 bg-surface/90 text-emerald-500 dark:text-emerald-400 hover:text-emerald-600 dark:hover:text-emerald-300 hover:bg-emerald-500/10 shadow-lg backdrop-blur-sm transition-all duration-200 ${isDraggingRight ? 'cursor-grabbing' : 'cursor-grab'}`}
                  title="Expand right panel (Drag to move)"
                >
                  <span dangerouslySetInnerHTML={{ __html: SVGS.sidebarOpen }} className="flex items-center justify-center scale-x-[-1] pointer-events-none" />
                </button>
              )}

              <div className="flex flex-1 min-h-0 w-full overflow-hidden">
                <div className="min-h-0 flex-1 bg-surface relative flex flex-col p-0 overflow-hidden">
                  {renderProfileContent()}
                </div>
              </div>

              {!isFullscreen && <ActivePositions />}

              {!isFullscreen && (
                paperPortfolioOpen ? (
                  <div className="border-t border-border-default bg-surface">
                    <PortfolioDashboard onCollapse={() => setPaperPortfolioOpen(false)} />
                  </div>
                ) : (
                  <PaperPortfolioBar paperPortfolio={paperPortfolio} onExpand={() => setPaperPortfolioOpen(true)} />
                )
              )}

              {!isFullscreen && (
                <div className="shrink-0 border-t border-border-default bg-surface rounded-none">
                  <OrderExecutionPanel />
                </div>
              )}
            </div>

            {/* ── Right sidebar ────────────────────────────── */}
            <RightSidebar activeProfile={activeProfile} sidebarOpen={sidebarOpen} setSidebarOpen={setSidebarOpen} sidebarWidth={sidebarWidth} isResizingSidebar={isResizingSidebar} startResizingSidebar={startResizingSidebar} />
          </div>
        </TerminalLayout>
      </div>

      <ToastContainer toasts={toasts} />
      <UpdateNotifier />
      {isResizingSidebar && <div className="fixed inset-0 z-9999 cursor-col-resize select-none pointer-events-auto bg-white/0" />}
      {isDraggingRight && <div className="fixed inset-0 z-9999 cursor-row-resize select-none pointer-events-auto bg-white/0" />}
    </div>
  );
}
