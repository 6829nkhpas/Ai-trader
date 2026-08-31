'use client';

import React, { useMemo } from 'react';
import { Coins, Zap, Loader2, Shield, ChevronDown, Cpu, Square } from 'lucide-react';
import { useQuantStore } from '../../store/useQuantStore';
import type { StreamEventPayload } from '../../store/useQuantStore';
import { useTradeStore } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import AgentTerminal from './AgentTerminal';
import TradeQaPanel from './TradeQaPanel';
import ModelSelector from './deep-quant/ModelSelector';
import { useAuthStore } from '../../store/useAuthStore';

// ── Subcomponents ──────────────────────────────────────────────────────
import LoadingState from './deep-quant/LoadingState';
import VerificationForm from './deep-quant/VerificationForm';
import AiExecutionPlanView from './deep-quant/AiExecutionPlanView';
import PremiumPaywall from './deep-quant/PremiumPaywall';
import ErrorState from './deep-quant/ErrorState';
import EmptyState from './deep-quant/EmptyState';
import { useVerificationForm } from './deep-quant/useVerificationForm';
import { useFeature } from '../../store/useFeatureStore';
import { dashboardUrl, openExternalUrl } from '../../lib/redirect';
import { useCredit } from '../../hooks/useApi';
import { bridgeInvoke, bridgeListen } from '../../lib/bridge';

export default function DeepQuantPanel() {
  const user = useAuthStore((s) => s.user);
  const deepseekGlmEnabled = useFeature('deepseekGlm');
  // Live credit balance — LLM usage is billed against the user's plan credits,
  // deducted server-side as OpenRouter usage syncs. Refetched when auth changes.
  const { data: credit } = useCredit();

  const handleUpgrade = async () => {
    await openExternalUrl(dashboardUrl());
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
    cancelAnalysis,
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
        const dispose = await bridgeListen<StreamEventPayload>('deep-quant-stream', (event) => {
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

  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const historicalCache = useTradeStore((s) => s.historicalCache);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);
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
      unlisten = await bridgeListen<string>('agent_status', (event) => {
        console.log(`🧠 [AGENT STATE UPDATE]: ${event.payload}`);
        setAgentStatus(event.payload);
      });
    };
    setupListener();
    return () => {
      unlisten?.();
    };
  }, []);

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

  if (!user || !deepseekGlmEnabled) {
    return <PremiumPaywall onUpgradeClick={handleUpgrade} />;
  }

  const handleAIAnalysis = () => {
    useQuantStore.getState().resetTerminal();
    // Compute the technical consensus for THIS press, alongside the agent run.
    //
    // Deliberately here and not on symbol change: it is a technical read the user
    // asks for, so a watchlist click should not fire a tool-server computation per
    // symbol, and an agent-run output should not be presented as ambient telemetry.
    //
    // Fired in parallel rather than awaited — the agent stream is the primary
    // result and must not wait on the HUD. If the agent's own
    // `get_consensus_report` tool result arrives first, `quant-consensus` sets the
    // same state; whichever lands later simply wins with equivalent data.
    void useQuantStore.getState().fetchConsensusForSymbol(activeSymbol, activeTimeframe);
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
    // VERIFY reads the same consensus indicators (ATR sizes the stop, RSI/MACD/EMA
    // corroborate the user's direction), so the HUD is populated for this press too
    // — same reasoning as handleAIAnalysis above.
    void useQuantStore.getState().fetchConsensusForSymbol(activeSymbol, activeTimeframe);
    fetchDeepAnalysis(activeSymbol, 'VERIFY', {
      side,
      entry: entryNum,
      stopLoss: slNum,
      takeProfit: tpNum,
      userAnalysis,
    });
  };


  return (
    <div className="flex h-full flex-col text-sm select-none overflow-hidden">
      {/* ── Trigger Button ────────────────────────────────── */}
      <div className="shrink-0 p-3 border-b border-border-default relative">
        <div className="flex items-center gap-0">
          <button
            id="btn-run-deep-quant"
            type="button"
            disabled={!isAnalyzing && !dataReady}
            onClick={() => {
              if (isAnalyzing) {
                cancelAnalysis();
              } else if (activeMode === 'FIND') {
                handleAIAnalysis();
              } else {
                handleVerifyAnalysis();
              }
            }}
            className={`
              relative flex-grow flex h-8 items-center justify-center gap-1.5
              rounded-l px-3 text-[10px] font-bold uppercase tracking-wider
              transition-all duration-300 ease-out border border-r-0
              ${!dataReady && !isAnalyzing
                ? 'bg-elevated/40 text-text-muted/50 border-border-default opacity-50 cursor-not-allowed'
                : isAnalyzing
                  ? 'bg-rose-600 text-white border-rose-600 hover:bg-rose-700 hover:border-rose-700 active:scale-[0.99] cursor-pointer'
                  : 'bg-text-primary text-surface border-text-primary hover:bg-text-secondary hover:border-text-secondary active:scale-[0.99]'
              }
            `}
          >
            <span className="relative flex items-center gap-1.5">
              {!dataReady && !isAnalyzing ? (
                <Loader2 size={11} className="animate-spin text-text-muted" />
              ) : isAnalyzing ? (
                <Square size={11} />
              ) : activeMode === 'VERIFY' ? (
                <Shield size={11} className="group-hover:animate-pulse" />
              ) : (
                <Zap size={11} className="group-hover:animate-pulse" />
              )}
              {!dataReady && !isAnalyzing
                ? 'AWAITING DATA…'
                : isAnalyzing
                  ? 'STOP ANALYSIS'
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
              h-8 w-8 rounded-r border transition-all duration-300 flex items-center justify-center
              ${isAnalyzing
                ? 'bg-elevated/40 border-border-default text-text-muted/50 cursor-not-allowed'
                : 'bg-text-primary text-surface border-text-primary hover:bg-text-secondary hover:border-text-secondary border-l-surface/20'
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
            <div className="absolute right-3 left-3 mt-1.5 z-50 rounded bg-surface/95 backdrop-blur-xl border border-border-default/60 shadow-2xl p-1.5 flex flex-col gap-1">
              <button
                type="button"
                onClick={() => {
                  setActiveMode('FIND');
                  setIsDropdownOpen(false);
                }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-xs font-semibold rounded-sm text-left transition-all ${activeMode === 'FIND' ? 'bg-elevated text-text-primary' : 'text-text-secondary hover:bg-elevated hover:text-text-primary'}`}
              >
                <Zap size={13} className="text-text-secondary" />
                <div className="flex flex-col">
                  {/* Compliance: was "Find High-Probability Trade" / "Autonomous
                      breakouts & quant scanning". "High-Probability" states a
                      probability about the outcome, which is the reading
                      docs/compliance/BRAND_GUIDELINES.md §1.2 exists to prevent, and
                      "Autonomous" reads as acting without the user (§1.1 rule 11).
                      The mode scans and proposes; it never acts. */}
                  <span>Find a Trade Setup</span>
                  <span className="text-[8px] font-normal text-text-muted">Scans breakouts & quant signals</span>
                </div>
              </button>

              <button
                type="button"
                onClick={() => {
                  setActiveMode('VERIFY');
                  setIsDropdownOpen(false);
                }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-xs font-semibold rounded-sm text-left transition-all ${activeMode === 'VERIFY' ? 'bg-elevated text-text-primary' : 'text-text-secondary hover:bg-elevated hover:text-text-primary'}`}
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

        {/* ── Footer Controls (Responsive 3 equal-width boxes) ── */}
        <div className="mt-2 pt-1.5 border-t border-border-default/20 flex flex-wrap items-center gap-1.5 text-[10px]">
          {/* 1. Model Selector Box */}
          <div className="flex-1 min-w-[110px]">
            <ModelSelector value={selectedModel} onChange={setSelectedModel} disabled={isAnalyzing} />
          </div>

          {/* 2. Candle Status Box */}
          <div
            className="flex-1 min-w-[100px] h-7 flex items-center justify-center rounded bg-elevated/35 border border-border-default/60 px-2 py-1 text-[9px] text-text-muted/70 text-center transition-all"
            title={`${symbol} • ${activeTimeframe}`}
          >
            <span className="truncate">
              {symbol} • {activeTimeframe} • {!dataReady
                ? 'Loading…'
                : insufficientData
                  ? `${symbolCandleCount} candles (low)`
                  : `${symbolCandleCount} candles`}
            </span>
          </div>

          {/* 3. Credits Box (Coins icon on left, circular progress ring on right) ── */}
          {credit ? (
            <button
              type="button"
              onClick={handleUpgrade}
              title={
                credit.hasActiveSubscription
                  ? `Plan: ${credit.planName} — ${credit.credits.toLocaleString()} credits remaining`
                  : 'No active plan — click to subscribe'
              }
              className="flex-1 min-w-[100px] h-7 flex items-center justify-between rounded bg-elevated/35 border border-border-default/60 px-2 py-1 text-[10px] transition-all hover:bg-elevated/65 hover:border-border-default/90 cursor-pointer"
            >
              {/* Left: Coin Icon + Credit Number */}
              <div className="flex items-center gap-1.5 min-w-0">
                <Coins size={11} className="text-amber-400 shrink-0" />
                <span className="font-mono font-semibold text-text-primary text-[10px] truncate">
                  {credit.credits.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                </span>
              </div>

              {/* Right: Plan text + Circular Progress Ring */}
              <div className="flex items-center gap-1 shrink-0 ml-1">
                <span className="text-[9px] font-sans font-normal text-text-muted truncate hidden sm:inline">
                  {credit.hasActiveSubscription ? credit.planName : 'no plan'}
                </span>
                <svg className="w-3.5 h-3.5 -rotate-90 shrink-0" viewBox="0 0 14 14">
                  <circle
                    cx="7"
                    cy="7"
                    r="5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    className="text-border-default/50"
                  />
                  <circle
                    cx="7"
                    cy="7"
                    r="5"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeDasharray={31.4}
                    strokeDashoffset={31.4 - (31.4 * Math.min(100, Math.max(10, (credit.credits / 100) * 100))) / 100}
                    strokeLinecap="round"
                    className={credit.credits > 0 ? "text-emerald-400 transition-all duration-300" : "text-amber-500"}
                  />
                </svg>
              </div>
            </button>
          ) : (
            <div className="flex-1 min-w-[100px]" />
          )}
        </div>
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
        <div className="flex-1 min-h-0 overflow-y-auto scrollbar-thin flex flex-col">
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
