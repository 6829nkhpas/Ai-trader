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

import { useTradeStore, hydratePaperPortfolio } from '../store/useTradeStore';
import { useQuantStore } from '../store/useQuantStore';
import { useChartUIStore } from '../store/useChartUIStore';
import { useFeatureStore } from '../store/useFeatureStore';
import { useAuthStore } from '../store/useAuthStore';
import { useCredit } from '../hooks/useApi';
import { useConnectionMonitor } from '../hooks/useConnectionMonitor';
import { useSymbolQuote } from '../hooks/useSymbolQuote';
import { useSidebarDrag } from '../hooks/useSidebarDrag';
import { useToast } from '../hooks/useToast';
import { bridgeListen } from '../lib/bridge';
import type { ConsensusReport } from '../store/useQuantStore';

export default function Home() {
  // ── Auth & Feature gates ──────────────────────────────────────────
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const fetchProfile = useAuthStore((s) => s.fetchProfile);
  const setFeatureAccessFlags = useFeatureStore((s) => s.setAccessFlags);
  const hydrateFeatureConfig = useFeatureStore((s) => s.hydrateConfig);
  const resetFeatureAccess = useFeatureStore((s) => s.reset);
  const { data: creditData } = useCredit();

  // ── Store selectors ───────────────────────────────────────────────
  const { connectWebSocket, connectAlphaWebSocket, connectPredictiveWebSocket, connectInsightWebSocket, connectOrderFlowWebSocket, activeDecision, liveDecisions, activeProfile, activeTimeframe, selectedSymbol, paperPortfolio } = useTradeStore();
  const isFullscreen = useChartUIStore((s) => s.isFullscreen);
  const setIsFullscreen = useChartUIStore((s) => s.setIsFullscreen);
  const splitView = useChartUIStore((s) => s.splitView);
  const sidebarOpen = useChartUIStore((s) => s.sidebarOpen);
  const setSidebarOpen = useChartUIStore((s) => s.setSidebarOpen);
  const setConsensusData = useQuantStore((s) => s.setConsensusData);
  const loadConsensusForSymbol = useQuantStore((s) => s.loadConsensusForSymbol);
  const fetchConsensusForSymbol = useQuantStore((s) => s.fetchConsensusForSymbol);
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
  // Two independent inputs, ANDed in `computeFeatureAccess`:
  //   • the DEPLOYMENT kill switches — asked of the backend once on mount, so
  //     they are not a constant in this bundle (see lib/featureFlags.ts).
  //   • the USER's plan flags — from the /credit API, refreshed with the session.
  useEffect(() => {
    void hydrateFeatureConfig();
  }, [hydrateFeatureConfig]);

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

  // ── Live feed bootstrap ───────────────────────────────────────────
  // `/ws/*` is the one gateway prefix with no basic auth
  // (`infra/caddy/Caddyfile`), so these sockets are opened straight from the
  // browser. They must be `wss://` in production — an https:// page cannot open a
  // ws:// socket (see `wsUrlIsUsable` in useTradeStore).
  useEffect(() => {
    connectAlphaWebSocket(process.env.NEXT_PUBLIC_ALPHA_WS_URL || 'ws://127.0.0.1:8081');
    connectPredictiveWebSocket(process.env.NEXT_PUBLIC_PREDICTIVE_WS_URL || 'ws://127.0.0.1:8082');
    connectInsightWebSocket(process.env.NEXT_PUBLIC_INSIGHT_WS_URL || 'ws://127.0.0.1:8083');
    connectOrderFlowWebSocket(process.env.NEXT_PUBLIC_ORDER_FLOW_WS_URL || 'ws://127.0.0.1:8089');
  }, [
    connectAlphaWebSocket,
    connectPredictiveWebSocket,
    connectInsightWebSocket,
    connectOrderFlowWebSocket,
  ]);

  // ── Quant consensus listener ──────────────────────────────────────
  useEffect(() => {
    // Serve the cached report instantly, then refresh from tool-server.
    //
    // The fetch is the fix for a panel that read "No patterns detected" for every
    // symbol on a fresh load: `consensusData` used to arrive ONLY as a side effect
    // of a deep-quant agent run streaming its consensus tool result onto the bridge
    // bus, so until the user launched an analysis nothing had computed it. The
    // detectors were never broken — nothing had asked them.
    loadConsensusForSymbol(symbol);
    void fetchConsensusForSymbol(symbol, activeTimeframe);
    clearAiPlan();
  }, [symbol, activeTimeframe, loadConsensusForSymbol, fetchConsensusForSymbol, clearAiPlan]);

  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    (async () => {
      try {
        // Tauri IPC on desktop; in a browser the bridge bus, fed by the agent's
        // `get_consensus_report` tool result as it streams.
        const u = await bridgeListen<ConsensusReport>('quant-consensus', (event) => {
          if (!cancelled) setConsensusData(event.payload);
        });
        if (cancelled) { u(); } else { unlisten = u; }
      } catch (err) { console.warn('[page] consensus listener unavailable:', err); }
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
      {isResizingSidebar && <div className="fixed inset-0 z-9999 cursor-col-resize select-none pointer-events-auto bg-white/0" />}
      {isDraggingRight && <div className="fixed inset-0 z-9999 cursor-row-resize select-none pointer-events-auto bg-white/0" />}
    </div>
  );
}
