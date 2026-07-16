'use client';

import React, { useMemo } from 'react';
import { Zap, Loader2, Shield, ChevronDown, Cpu } from 'lucide-react';
import { useQuantStore, isActionableTrade } from '../../store/useQuantStore';
import type { StreamEventPayload } from '../../store/useQuantStore';
import { useTradeStore } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import { listen } from '@tauri-apps/api/event';
import AgentTerminal from './AgentTerminal';
import TradeQaPanel from './TradeQaPanel';
import ModelSelector from './deep-quant/ModelSelector';
import { useAuthStore } from '../../store/useAuthStore';
import { invoke } from '@tauri-apps/api/core';

// ── Subcomponents ──────────────────────────────────────────────────────
import LoadingState from './deep-quant/LoadingState';
import VerificationForm from './deep-quant/VerificationForm';
import AiExecutionPlanView from './deep-quant/AiExecutionPlanView';
import PremiumPaywall from './deep-quant/PremiumPaywall';
import ErrorState from './deep-quant/ErrorState';
import EmptyState from './deep-quant/EmptyState';
import { useVerificationForm } from './deep-quant/useVerificationForm';

export default function DeepQuantPanel() {
  const user = useAuthStore((s) => s.user);

  const handleUpgrade = async () => {
    const token = useAuthStore.getState().token;
    try {
      const res = await fetch('http://localhost:3002/api/payments/phonepe/checkout', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ amount: 599, tier: 'PRO' })
      });
      if (!res.ok) throw new Error('Failed to initiate checkout session');
      const data = await res.json();
      if (data.redirectUrl) {
        console.log('[Paywall] Redirecting to PhonePe checkout:', data.redirectUrl);
        await invoke('open_browser', { url: data.redirectUrl });
      }
    } catch (err) {
      console.error('[Paywall] Checkout initiation failed:', err);
    }
  };

  const {
    aiPlan,
    isAnalyzing,
    analysisError,
    fetchDeepAnalysis,
    clearAiPlan,
    reasoningSteps,
    sessionStatus,
    currentThreadId,
    selectedModel,
    setSelectedModel,
  } = useQuantStore();

  // Register the deep-quant-stream listener at the PANEL level so it is mounted
  // before any analysis run starts. (AgentTerminal only mounts once a run is in
  // flight, which raced the backend SSE stream and intermittently dropped the
  // opening REASONING/TOOL events — leaving the glass-box blank.) Placed before
  // the paywall early-return so hook order stays stable.
  React.useEffect(() => {
    let cancelled = false;
    let unlistenFn: (() => void) | undefined;
    (async () => {
      try {
        const dispose = await listen<StreamEventPayload>('deep-quant-stream', (event) => {
          if (!cancelled) {
            useQuantStore.getState().handleStreamEvent(event.payload);
          }
        });
        if (cancelled) {
          dispose();
        } else {
          unlistenFn = dispose;
        }
      } catch (err) {
        console.error('Failed to register deep-quant-stream listener:', err);
      }
    })();
    return () => {
      cancelled = true;
      unlistenFn?.();
    };
  }, []);

  if (!user || user.tier === 'FREE') {
    return <PremiumPaywall onUpgradeClick={handleUpgrade} />;
  }

  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const historicalCache = useTradeStore((s) => s.historicalCache);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);
  const paperPortfolio = useTradeStore((s) => s.paperPortfolio);
  const symbol = selectedSymbol || 'RELIANCE';
  const activeSymbol = symbol;

  // ── AI Handoff State Guard ────────────────────────────────────────────
  const symbolCandleCount = useMemo(() => {
    const symUpper = symbol.toUpperCase();
    let maxCount = 0;
    for (const [key, val] of Object.entries(historicalCache)) {
      if (key.startsWith(`${symUpper}::`) && val && val.length > maxCount) {
        maxCount = val.length;
      }
    }
    return maxCount;
  }, [historicalCache, symbol]);

  const dataReady = symbolCandleCount > 0;
  const insufficientData = symbolCandleCount > 0 && symbolCandleCount < 50;

  const hasActivePosition = paperPortfolio?.active_positions.some(
    (p) => p.symbol.toUpperCase() === symbol.toUpperCase()
  ) || false;
  const [deployed, setDeployed] = React.useState(false);
  const [agentStatus, setAgentStatus] = React.useState<string>("Awaiting trigger...");

  // ── Split Dropdown & Verification State ──
  const [activeMode, setActiveMode] = React.useState<'FIND' | 'VERIFY'>('FIND');
  const [isDropdownOpen, setIsDropdownOpen] = React.useState(false);

  const livePrice = useTradeStore((s) => s.ohlcCandles.find(c => c.symbol === symbol)?.close) || 0;

  // Use modular verification form hook
  const {
    side,
    setSide,
    entry,
    setEntry,
    setHasManuallySetEntry,
    stopLoss,
    setStopLoss,
    setHasManuallySetSL,
    takeProfit,
    setTakeProfit,
    setHasManuallySetTP,
    userAnalysis,
    setUserAnalysis,
    riskToReward,
    slPercent,
    tpPercent,
  } = useVerificationForm(symbol, livePrice);

  React.useEffect(() => {
    let unlisten: (() => void) | undefined;
    const setupListener = async () => {
      unlisten = await listen<string>('agent_status', (event) => {
        console.log(`🧠 [AGENT STATE UPDATE]: ${event.payload}`);
        setAgentStatus(event.payload);
      });
    };
    setupListener();
    return () => {
      unlisten?.();
    };
  }, []);

  // Reset deployed state when plan changes
  React.useEffect(() => {
    setDeployed(false);
  }, [aiPlan]);

  // Persist analysis per (symbol, profile): when the active chart symbol OR the
  // workspace mode changes, load that combination's saved session (reasoning,
  // tool calls, decision, Q&A) into the view. A run launched for another
  // symbol/mode keeps streaming into its own session in the background, so
  // switching away and back — across symbols AND across INTRADAY/SWING/INVESTOR/
  // FNO — never wipes or stalls the analysis.
  const activeProfile = useTradeStore((s) => s.activeProfile);
  React.useEffect(() => {
    useQuantStore.getState().activateSymbolSession(activeSymbol, activeProfile);
  }, [activeSymbol, activeProfile]);

  const handleAIAnalysis = () => {
    useQuantStore.getState().resetTerminal();
    fetchDeepAnalysis(activeSymbol);
  };

  const handleVerifyAnalysis = () => {
    const entryNum = parseFloat(entry);
    const slNum = parseFloat(stopLoss);
    const tpNum = parseFloat(takeProfit);

    if (isNaN(entryNum) || entryNum <= 0) {
      console.warn("Invalid entry price");
      return;
    }

    useQuantStore.getState().resetTerminal();
    fetchDeepAnalysis(activeSymbol, 'VERIFY', {
      side,
      entry: entryNum,
      stopLoss: slNum,
      takeProfit: tpNum,
      userAnalysis,
    });
  };

  // A plan is deployable only when it is a validated directional trade. A
  // HOLD / stand_aside, an unknown/absent action, or a plan missing structured
  // execution_levels is never deployable (R1.5/R1.6/R1.8). The deploy control
  // itself is gated on this below so the action is not even offered.
  const planActionable = isActionableTrade(aiPlan);

  const handleDeployStrategy = async () => {
    // Fail safe: never deploy a non-actionable plan, and never synthesize
    // levels from prose or the last close. Levels come only from the validated
    // Declare_Trade_Args carried in aiPlan.execution_levels.
    if (!isActionableTrade(aiPlan)) return;

    const { entry, stop_loss, take_profit } = aiPlan.execution_levels;
    const tradeSide = aiPlan.action === 'SELL' ? 'SELL' : 'BUY';

    try {
      const { invoke: tauriInvoke } = await import('@tauri-apps/api/core');
      const resMsg = await tauriInvoke<string>('execute_paper_trade', {
        symbol,
        side: tradeSide,
        entryPrice: entry,
        stopLoss: stop_loss,
        takeProfit: take_profit,
      });
      useTradeStore.getState().addSystemLog('INFO', `🚀 [Paper Engine] ${resMsg}`);

      // Trigger dynamic positions fetch
      await useTradeStore.getState().fetchPaperPortfolio();

      // Set local deployed state
      setDeployed(true);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      console.error('Failed to deploy strategy:', err);
      useTradeStore.getState().addSystemLog('ERROR', `Failed to deploy strategy: ${errMsg}`);
    }
  };

  return (
    <div className="flex h-full flex-col text-sm select-none overflow-hidden">
      {/* ── Trigger Button ────────────────────────────────── */}
      <div className="shrink-0 p-3 border-b border-border-default relative">
        <div className="flex items-center gap-1">
          <button
            id="btn-run-deep-quant"
            type="button"
            disabled={isAnalyzing || !dataReady}
            onClick={() => {
              if (activeMode === 'FIND') {
                handleAIAnalysis();
              } else {
                handleVerifyAnalysis();
              }
            }}
            className={`
              relative flex-grow flex h-8 items-center justify-center gap-1.5
              rounded-none px-3 text-[10px] font-bold uppercase tracking-wider
              transition-all duration-300 ease-out border border-r-0
              ${!dataReady
                ? 'bg-elevated/40 text-text-muted/50 border-border-default opacity-50 cursor-not-allowed'
                : isAnalyzing
                  ? 'bg-elevated text-text-primary border-border-default cursor-wait'
                  : 'bg-text-primary text-surface border-text-primary hover:bg-text-secondary hover:border-text-secondary active:scale-[0.99]'
              }
            `}
          >
            <span className="relative flex items-center gap-1.5">
              {!dataReady ? (
                <Loader2 size={11} className="animate-spin text-text-muted" />
              ) : isAnalyzing ? (
                <Loader2 size={11} className="animate-spin text-surface" />
              ) : activeMode === 'VERIFY' ? (
                <Shield size={11} className="group-hover:animate-pulse" />
              ) : (
                <Zap size={11} className="group-hover:animate-pulse" />
              )}
              {!dataReady
                ? 'AWAITING DATA…'
                : isAnalyzing
                  ? 'ANALYZING...'
                  : activeMode === 'VERIFY'
                    ? 'VERIFY MY SETUP'
                    : 'FIND QUANT TRADE'}
            </span>
          </button>

          {/* Dropdown Toggle */}
          <button
            type="button"
            disabled={isAnalyzing}
            onClick={() => setIsDropdownOpen(!isDropdownOpen)}
            className={`
              h-8 w-8 rounded-none border transition-all duration-300 flex items-center justify-center
              ${isAnalyzing
                ? 'bg-elevated/40 border-border-default text-text-muted/50 cursor-not-allowed'
                : 'bg-text-primary text-surface border-text-primary hover:bg-text-secondary hover:border-text-secondary border-l-border-default/20'
              }
            `}
          >
            <ChevronDown size={11} className={`transition-transform duration-300 ${isDropdownOpen ? 'rotate-180' : ''}`} />
          </button>
        </div>

        {/* Dropdown Menu */}
        {isDropdownOpen && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setIsDropdownOpen(false)} />
            <div className="absolute right-3 left-3 mt-1.5 z-50 rounded-none bg-surface/95 backdrop-blur-xl border border-border-default shadow-2xl p-1.5 flex flex-col gap-1">
              <button
                type="button"
                onClick={() => {
                  setActiveMode('FIND');
                  setIsDropdownOpen(false);
                }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-xs font-semibold rounded-none text-left transition-all ${activeMode === 'FIND' ? 'bg-elevated text-text-primary' : 'text-text-secondary hover:bg-elevated hover:text-text-primary'}`}
              >
                <Zap size={13} className="text-text-secondary" />
                <div className="flex flex-col">
                  <span>Find High-Probability Trade</span>
                  <span className="text-[8px] font-normal text-text-muted">Autonomous breakouts & quant scanning</span>
                </div>
              </button>

              <button
                type="button"
                onClick={() => {
                  setActiveMode('VERIFY');
                  setIsDropdownOpen(false);
                }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-xs font-semibold rounded-none text-left transition-all ${activeMode === 'VERIFY' ? 'bg-elevated text-text-primary' : 'text-text-secondary hover:bg-elevated hover:text-text-primary'}`}
              >
                <Shield size={13} className="text-text-secondary" />
                <div className="flex flex-col">
                  <span>Verify My Trade Idea</span>
                  <span className="text-[8px] font-normal text-text-muted">Co-pilot critical Risk Manager critique</span>
                </div>
              </button>
            </div>
          </>
        )}

        {/* ── Model Selector Row ── */}
        <div className="flex items-center justify-between gap-2 mt-1.5">
          <span className="text-[9px] font-bold uppercase tracking-wider text-text-muted select-none">Model</span>
          <ModelSelector value={selectedModel} onChange={setSelectedModel} disabled={isAnalyzing} />
        </div>

        <p className="text-[9px] text-text-muted/50 text-center mt-1.5">
          {symbol} • {activeTimeframe} • {!dataReady
            ? 'Loading candle data from QuestDB…'
            : insufficientData
              ? `⚠ Only ${symbolCandleCount} candles — may reduce accuracy`
              : `${symbolCandleCount} candles`}
        </p>
      </div>

      {/* ── Verification Input Form ── */}
      {activeMode === 'VERIFY' && !isAnalyzing && !aiPlan && !analysisError && (
        <VerificationForm
          side={side}
          setSide={setSide}
          entry={entry}
          setEntry={setEntry}
          setHasManuallySetEntry={setHasManuallySetEntry}
          stopLoss={stopLoss}
          setStopLoss={setStopLoss}
          setHasManuallySetSL={setHasManuallySetSL}
          takeProfit={takeProfit}
          setTakeProfit={setTakeProfit}
          setHasManuallySetTP={setHasManuallySetTP}
          userAnalysis={userAnalysis}
          setUserAnalysis={setUserAnalysis}
          slPercent={slPercent}
          tpPercent={tpPercent}
          riskToReward={riskToReward}
          onSubmit={handleVerifyAnalysis}
          isAnalyzing={isAnalyzing}
          dataReady={dataReady}
        />
      )}

      {/* ── Content Area ──────────────────────────────────── */}
      {/* A flex column: ONLY the agent/analysis region scrolls; the Q&A composer
          is pinned as a fixed footer so the input never scrolls away with the
          agent log. */}
      <div className="flex-grow flex-shrink min-h-0 flex flex-col overflow-hidden">
        {/* Scrollable agent / analysis region */}
        <div className="flex-1 min-h-0 overflow-y-auto scrollbar-thin">
          {reasoningSteps.length > 0 || sessionStatus !== 'idle' ? (
            <div className="h-full p-0 min-h-[380px]">
              <AgentTerminal />
            </div>
          ) : analysisError ? (
            <ErrorState 
              error={analysisError} 
              dataReady={dataReady} 
              activeMode={activeMode} 
              onRetryFind={handleAIAnalysis} 
              onRetryVerify={handleVerifyAnalysis} 
            />
          ) : aiPlan ? (
            <AiExecutionPlanView
              aiPlan={aiPlan}
              actionable={planActionable}
              deployed={deployed}
              hasActivePosition={hasActivePosition}
              onDeploy={handleDeployStrategy}
              onClear={clearAiPlan}
            />
          ) : (
            <EmptyState symbol={symbol} />
          )}
        </div>

        {/* ── Pinned unified Q&A composer — sits OUTSIDE the scroll region so it
            stays fixed at the bottom of the agent section. It renders whenever a
            session is active (disabled during the run) and unlocks the moment the
            agent hits the AI-watcher state, letting the user chat while the AI
            keeps watching for the price trigger. Its own message list scrolls
            internally within a bounded height. */}
        {(reasoningSteps.length > 0 || sessionStatus !== 'idle') && (
          <div className="shrink-0">
            <TradeQaPanel />
          </div>
        )}
      </div>
    </div>
  );
}
