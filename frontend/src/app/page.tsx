'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { SVGS } from '../components/chart/toolbarIcons';
import TerminalLayout from '../components/layout/TerminalLayout';
import LeftPanel from '../components/panels/LeftPanel';
import OrderExecutionPanel from '../components/panels/OrderExecutionPanel';
import TerminalChartPane from '../components/layouts/TerminalChartPane';
import SplitChartContainer from '../components/chart/SplitChartContainer';
import FnoSection from '../components/fno/FnoSection';
import RightSidebar from '../components/panels/RightSidebar';
import ToastContainer from '../components/common/ToastContainer';
import AuthOverlay from '../components/auth/AuthOverlay';
import ConnectionLost from '../components/common/ConnectionLost';

import { useTradeStore, hydrateLegacyAgentBridge } from '../store/useTradeStore';
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
  const completeLoginFromUrl = useAuthStore((s) => s.completeLoginFromUrl);
  const setFeatureAccessFlags = useFeatureStore((s) => s.setAccessFlags);
  const hydrateFeatureConfig = useFeatureStore((s) => s.hydrateConfig);
  const resetFeatureAccess = useFeatureStore((s) => s.reset);
  const { data: creditData } = useCredit();

  // ── Store selectors ───────────────────────────────────────────────
  const { connectWebSocket, connectAlphaWebSocket, connectPredictiveWebSocket, connectInsightWebSocket, connectOrderFlowWebSocket, activeDecision, liveDecisions, activeProfile, selectedSymbol } = useTradeStore();
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

  // ── Cross-surface login handoff ───────────────────────────────────
  // If we were opened with a `?token=` / `?session=` handoff from a login
  // performed on the dashboard surface, consume it so the user lands here
  // already signed in instead of at the login overlay. No-op otherwise.
  useEffect(() => {
    void completeLoginFromUrl();
  }, [completeLoginFromUrl]);

  // ── Extracted hooks ───────────────────────────────────────────────
  const showConnectionLost = useConnectionMonitor(mounted);
  const { toasts } = useToast();
  const { rightButtonTop, isDraggingRight, handleRightButtonMouseDown, sidebarWidth, isResizingSidebar, startResizingSidebar } = useSidebarDrag(() => setSidebarOpen(true));

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
    hydrateLegacyAgentBridge();
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
    // Cache-only. The consensus is NOT fetched here on purpose: it is a technical
    // read the user asks for by pressing FIND QUANT TRADE, not ambient state.
    // Fetching per symbol change would fire a tool-server computation on every
    // click through a watchlist and present an agent-run output as though it were
    // always-on telemetry. See `fetchConsensusForSymbol`, called from
    // `DeepQuantPanel`'s run handler.
    loadConsensusForSymbol(symbol);
    clearAiPlan();
  }, [symbol, loadConsensusForSymbol, clearAiPlan]);

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
  // Mode switching must NOT rebuild the chart.
  //
  // INTRADAY / SWING / INVESTOR all resolve to the SAME element type
  // (`TerminalChartPane`), so React reconciles the subtree instead of unmounting
  // it. Previously each mode returned its own layout component, and the resulting
  // type change forced a full remount of the TradingView widget — a
  // `widget.remove()`, a fresh construction, a new datafeed and another `getBars`
  // round trip — on every switch, even though nothing about the chart differs
  // between those three modes. See `TerminalChartPane` for the detail.
  //
  // F&O and split view stay separate branches: those really are different trees,
  // so remounting for them is correct.
  //
  // `activeTimeframe` / `sidebarOpen` are deliberately NOT dependencies. The old
  // layouts accepted them as props and `MainTerminalChart` discarded them, so
  // including them only churned this callback (and the returned element) on every
  // timeframe change and sidebar toggle. The chart reads the timeframe from the
  // store itself.
  const renderProfileContent = useCallback(() => {
    if (activeProfile === 'FNO') {
      return splitView ? <SplitChartContainer mode="FNO" /> : <FnoSection />;
    }
    if (activeProfile === 'INTRADAY' && splitView) {
      return <SplitChartContainer mode="INTRADAY" />;
    }
    return <TerminalChartPane activeProfile={activeProfile} />;
  }, [activeProfile, splitView]);

  // ── Early returns ─────────────────────────────────────────────────
  if (!mounted) return <div className="flex h-screen w-screen items-center justify-center bg-background" />;
  // Auth comes FIRST, before the feed health gate.
  //
  // `showConnectionLost` tracks the aggregator WebSocket, which is unauthenticated
  // and entirely independent of being logged in. Checking it first meant a
  // logged-out user on a machine where that socket is unreachable got the
  // "Connectivity Interrupted" health check (Internet / Server tiles + Retry)
  // INSTEAD of the login button, with no way to sign in. The feed-health screen
  // is only meaningful once you are inside the terminal, so it is gated on an
  // authenticated session.
  if (!isAuthenticated) return <AuthOverlay />;
  if (showConnectionLost) return <ConnectionLost />;

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
