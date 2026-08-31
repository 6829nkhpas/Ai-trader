'use client';

import React, { useEffect, useState, useCallback } from 'react';
import TerminalLayout from '../components/layout/TerminalLayout';
import LeftPanel from '../components/panels/LeftPanel';
import OrderExecutionPanel from '../components/panels/OrderExecutionPanel';
import TerminalChartPane from '../components/layouts/TerminalChartPane';
import SplitChartContainer from '../components/chart/SplitChartContainer';
import FnoSection from '../components/fno/FnoSection';
import RightSidebar from '../components/panels/RightSidebar';
import ToastContainer from '../components/common/ToastContainer';
import AuthGateScreen from '../components/auth/AuthGateScreen';
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
import { redirectToSignIn } from '../lib/authRedirect';
import type { ConsensusReport } from '../store/useQuantStore';

export default function Home() {
  // ── Auth & Feature gates ──────────────────────────────────────────
  const authStatus = useAuthStore((s) => s.status);
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const checkAuth = useAuthStore((s) => s.checkAuth);
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

  // ── Session check ─────────────────────────────────────────────────
  // The session is an httpOnly `.stratai.live` cookie, so whether we have one is
  // the server's answer to give — asking `/users/me` IS the check. This replaced
  // a `?token=` / `?session=` URL handoff, which is no longer needed now that the
  // cookie is already present on this origin when the user arrives from
  // auth.stratai.live.
  useEffect(() => {
    void checkAuth();
  }, [checkAuth]);

  // ── Unauthenticated → the auth surface ────────────────────────────
  // Only on a CONFIRMED `anonymous`. Redirecting while the status is still
  // `unknown` would bounce every returning user out of the terminal before their
  // own session had a chance to be confirmed.
  useEffect(() => {
    if (authStatus === 'anonymous') redirectToSignIn();
  }, [authStatus]);

  // ── Extracted hooks ───────────────────────────────────────────────
  const showConnectionLost = useConnectionMonitor(mounted);
  const { toasts } = useToast();
  const { sidebarWidth, isResizingSidebar, startResizingSidebar } = useSidebarDrag();

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
  // `fetchProfile` is gone from here: `checkAuth` above already loads the user
  // as part of establishing the session, so calling it too was a duplicate
  // `/users/me` on every mount.
  useEffect(() => {
    connectWebSocket();
    hydrateLegacyAgentBridge();
  }, [connectWebSocket]);

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
  // The terminal has no login form of its own — auth.stratai.live is the only
  // sign-in surface. Two distinct non-authenticated states, and conflating them
  // is what would break the experience:
  //
  //   `unknown`   — the session check is still in flight. Hold, do not redirect:
  //                 a returning user with a perfectly good cookie would be
  //                 thrown out to the auth page and (since they are signed in)
  //                 immediately bounced back, for no reason.
  //   `anonymous` — confirmed no session. The effect above has already started
  //                 the redirect; render the same quiet screen while the browser
  //                 navigates rather than flashing the terminal.
  if (!isAuthenticated) return <AuthGateScreen status={authStatus} />;
  if (showConnectionLost) return <ConnectionLost />;

  // ── Render ────────────────────────────────────────────────────────
  return (
    <div className="flex h-full flex-col bg-background">
      <div className="min-h-0 flex-1">
        <TerminalLayout
          leftPanel={<LeftPanel />}
          rightPanel={<RightSidebar activeProfile={activeProfile} sidebarOpen={sidebarOpen} setSidebarOpen={setSidebarOpen} sidebarWidth={sidebarWidth} isResizingSidebar={isResizingSidebar} startResizingSidebar={startResizingSidebar} />}
        >
          {/* ── Chart + Order Execution column ───────────── */}
          <div className={isFullscreen ? "fixed inset-0 z-150 flex flex-col bg-background p-2" : "relative flex h-full min-h-0 min-w-0 flex-col rounded-none bg-surface"}>
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
        </TerminalLayout>
      </div>

      <ToastContainer toasts={toasts} />
      {isResizingSidebar && <div className="fixed inset-0 z-9999 cursor-col-resize select-none pointer-events-auto bg-white/0" />}
    </div>
  );
}
