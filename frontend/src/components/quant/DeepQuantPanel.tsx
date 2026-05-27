'use client';

import React from 'react';
import {
  Zap,
  Loader2,
  Shield,
  AlertTriangle,
  RotateCcw,
  ChevronDown,
} from 'lucide-react';
import { useQuantStore } from '../../store/useQuantStore';
import { useTradeStore } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import { useMemo } from 'react';
import { listen } from '@tauri-apps/api/event';
import AgentTerminal from './AgentTerminal';

// ── Subcomponents ──────────────────────────────────────────────────────
import LoadingState from './deep-quant/LoadingState';
import VerificationForm from './deep-quant/VerificationForm';
import AiExecutionPlanView from './deep-quant/AiExecutionPlanView';

export default function DeepQuantPanel() {
  const {
    aiPlan,
    isAnalyzing,
    analysisError,
    fetchDeepAnalysis,
    clearAiPlan,
    reasoningSteps,
    sessionStatus,
  } = useQuantStore();
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const historicalCache = useTradeStore((s) => s.historicalCache);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);
  const paperPortfolio = useTradeStore((s) => s.paperPortfolio);
  const agentChatLog = useTradeStore((s) => s.agentChatLog);
  const clearAgentChatLog = useTradeStore((s) => s.clearAgentChatLog);
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

  // Verification Form State
  const [side, setSide] = React.useState<'BUY' | 'SELL'>('BUY');
  const [entry, setEntry] = React.useState<string>('');
  const [stopLoss, setStopLoss] = React.useState<string>('');
  const [takeProfit, setTakeProfit] = React.useState<string>('');
  const [userAnalysis, setUserAnalysis] = React.useState<string>('');

  const [hasManuallySetEntry, setHasManuallySetEntry] = React.useState(false);
  const [hasManuallySetSL, setHasManuallySetSL] = React.useState(false);
  const [hasManuallySetTP, setHasManuallySetTP] = React.useState(false);

  const livePrice = useTradeStore((s) => s.ohlcCandles.find(c => c.symbol === symbol)?.close) || 0;

  // Track live price and dynamically pre-fill fields
  React.useEffect(() => {
    if (livePrice > 0) {
      if (!hasManuallySetEntry) {
        setEntry(livePrice.toFixed(2));
      }
    }
  }, [livePrice, hasManuallySetEntry]);

  // Compute SL/TP based on entry price and side if not manually set
  React.useEffect(() => {
    const numericEntry = parseFloat(entry);
    if (!isNaN(numericEntry) && numericEntry > 0) {
      if (!hasManuallySetSL) {
        const computedSL = side === 'BUY' ? numericEntry * 0.98 : numericEntry * 1.02;
        setStopLoss(computedSL.toFixed(2));
      }
      if (!hasManuallySetTP) {
        const computedTP = side === 'BUY' ? numericEntry * 1.05 : numericEntry * 0.95;
        setTakeProfit(computedTP.toFixed(2));
      }
    }
  }, [entry, side, hasManuallySetSL, hasManuallySetTP]);

  // Reset manual inputs when active symbol changes
  React.useEffect(() => {
    setHasManuallySetEntry(false);
    setHasManuallySetSL(false);
    setHasManuallySetTP(false);
    setUserAnalysis('');
  }, [symbol]);

  // R:R and % deviations
  const riskToReward = React.useMemo(() => {
    const e = parseFloat(entry);
    const sl = parseFloat(stopLoss);
    const tp = parseFloat(takeProfit);
    if (isNaN(e) || isNaN(sl) || isNaN(tp) || e <= 0) return null;

    const risk = Math.abs(e - sl);
    const reward = Math.abs(tp - e);
    if (risk <= 0) return null;

    return (reward / risk).toFixed(2);
  }, [entry, stopLoss, takeProfit]);

  const slPercent = React.useMemo(() => {
    const e = parseFloat(entry);
    const sl = parseFloat(stopLoss);
    if (isNaN(e) || isNaN(sl) || e <= 0) return null;
    return (((sl - e) / e) * 100).toFixed(2);
  }, [entry, stopLoss]);

  const tpPercent = React.useMemo(() => {
    const e = parseFloat(entry);
    const tp = parseFloat(takeProfit);
    if (isNaN(e) || isNaN(tp) || e <= 0) return null;
    return (((tp - e) / e) * 100).toFixed(2);
  }, [entry, takeProfit]);

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
      if (unlisten) unlisten();
    };
  }, []);

  // Reset deployed state when plan changes
  React.useEffect(() => {
    setDeployed(false);
  }, [aiPlan]);

  const handleAIAnalysis = () => {
    console.log(`🧠 [AI HANDOFF] Requesting analysis for Symbol: ${activeSymbol} | Timeframe: ${activeTimeframe}`);
    console.log(`🧠 [AI HANDOFF] Current cached candle count: ${symbolCandleCount}`);

    console.log("🕵️‍♂️ [AUDIT 1 - UI SEND] Firing AI Request.");
    console.log("🕵️‍♂️ [AUDIT 1 - UI SEND] Symbol:", activeSymbol);
    console.log("🕵️‍♂️ [AUDIT 1 - UI SEND] Timeframe:", activeTimeframe);
    console.log("🕵️‍♂️ [AUDIT 1 - UI SEND] Acceleration Coeff from Store:", useChartUIStore.getState().accelerationCoefficient);
    console.log("🕵️‍♂️ [AUDIT 1 - UI SEND] Candles Length (cached proxy):", symbolCandleCount);
    console.log("🕵️‍♂️ [AUDIT 1 - UI SEND] Historical Cache Keys:", Object.keys(useTradeStore.getState().historicalCache));

    if (symbolCandleCount < 50) {
      console.warn(
        `🧠 [AI HANDOFF WARNING] Insufficient candles for AI analysis. ` +
        `DeepSeek requires at least 50 periods. Current: ${symbolCandleCount}`
      );
    }

    clearAgentChatLog();
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

    console.log(`🧠 [AI HANDOFF] Requesting VERIFY Mode analysis for Symbol: ${activeSymbol}`);
    console.log(`🧠 [AI HANDOFF] Proposed Trade: ${side} Entry:${entryNum} SL:${slNum} TP:${tpNum}`);

    clearAgentChatLog();
    fetchDeepAnalysis(activeSymbol, 'VERIFY', {
      side,
      entry: entryNum,
      stopLoss: slNum,
      takeProfit: tpNum,
      userAnalysis,
    });
  };

  const handleDeployStrategy = async () => {
    if (!aiPlan) return;
    
    const closePrice = useTradeStore.getState().ohlcCandles.find(c => c.symbol === symbol)?.close || 0;
    
    let entryPrice = closePrice;
    let stopLoss = 0;
    let takeProfit = 0;
    let tradeSide = 'BUY';

    const executionPlan = aiPlan.execution_plan || '';

    // Extract values with regex
    const entryMatch = executionPlan.match(/entry:\s*([\d.]+)/i);
    const slMatch = executionPlan.match(/stop-loss:\s*([\d.]+)/i) || executionPlan.match(/sl:\s*([\d.]+)/i);
    const tpMatch = executionPlan.match(/target\s*1?:\s*([\d.]+)/i) || executionPlan.match(/target:\s*([\d.]+)/i) || executionPlan.match(/tp:\s*([\d.]+)/i);
    const sideMatch = executionPlan.match(/side:\s*(buy|sell)/i) || executionPlan.match(/(buy|sell)/i);

    if (entryMatch) entryPrice = parseFloat(entryMatch[1]);
    if (slMatch) stopLoss = parseFloat(slMatch[1]);
    if (tpMatch) takeProfit = parseFloat(tpMatch[1]);
    if (sideMatch) {
      const matchedSide = sideMatch[1].toUpperCase();
      if (matchedSide === 'BUY' || matchedSide === 'SELL') {
        tradeSide = matchedSide;
      }
    }

    // Dynamic fallbacks
    if (entryPrice <= 0) entryPrice = closePrice;
    if (stopLoss <= 0) {
      stopLoss = tradeSide === 'BUY' ? entryPrice * 0.98 : entryPrice * 1.02;
    }
    if (takeProfit <= 0) {
      takeProfit = tradeSide === 'BUY' ? entryPrice * 1.05 : entryPrice * 0.95;
    }

    try {
      const { invoke: tauriInvoke } = await import('@tauri-apps/api/core');
      const resMsg = await tauriInvoke<string>('execute_paper_trade', {
        symbol,
        side: tradeSide,
        entryPrice,
        stopLoss,
        takeProfit,
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
              group relative flex-grow flex items-center justify-center gap-2
              rounded-l-xl px-3 py-2.5 text-xs font-bold uppercase tracking-wider
              transition-all duration-300 ease-out
              ${!dataReady
                ? 'bg-slate-500/10 text-slate-400 border-y border-l border-slate-500/20 opacity-50 cursor-not-allowed'
                : isAnalyzing
                  ? 'bg-emerald-500/10 text-emerald-300 border-y border-l border-emerald-500/20 cursor-wait'
                  : activeMode === 'VERIFY'
                    ? 'bg-gradient-to-r from-teal-600 to-cyan-600 text-white border-y border-l border-teal-500/40 hover:from-teal-500 hover:to-cyan-500 hover:shadow-lg hover:shadow-teal-500/20 active:scale-[0.99]'
                    : 'bg-gradient-to-r from-emerald-600 to-teal-600 text-white border-y border-l border-emerald-500/40 hover:from-emerald-500 hover:to-teal-500 hover:shadow-lg hover:shadow-emerald-500/20 active:scale-[0.99]'
              }
            `}
          >
            {/* Glow ring */}
            {!isAnalyzing && dataReady && (
              <div className="absolute -inset-px rounded-l-xl bg-gradient-to-r from-emerald-400/20 to-teal-400/20 opacity-0 group-hover:opacity-100 transition-opacity duration-300 blur-sm" />
            )}

            <span className="relative flex items-center gap-1.5">
              {!dataReady ? (
                <Loader2 size={14} className="animate-spin text-slate-400" />
              ) : isAnalyzing ? (
                <Loader2 size={14} className="animate-spin" />
              ) : activeMode === 'VERIFY' ? (
                <Shield size={14} className="group-hover:animate-pulse text-emerald-300" />
              ) : (
                <Zap size={14} className="group-hover:animate-pulse" />
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
              px-2 py-2.5 rounded-r-xl border transition-all duration-300 flex items-center justify-center
              ${isAnalyzing
                ? 'bg-slate-500/10 border-slate-500/20 text-slate-500 cursor-not-allowed'
                : activeMode === 'VERIFY'
                  ? 'bg-gradient-to-r from-teal-600 to-teal-700 text-white border-y border-r border-teal-500/40 hover:from-teal-500 hover:to-teal-600'
                  : 'bg-gradient-to-r from-emerald-600 to-emerald-700 text-white border-y border-r border-emerald-500/40 hover:from-emerald-500 hover:to-emerald-600'
              }
            `}
          >
            <ChevronDown size={14} className={`transition-transform duration-300 ${isDropdownOpen ? 'rotate-180' : ''}`} />
          </button>
        </div>

        {/* Dropdown Menu */}
        {isDropdownOpen && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setIsDropdownOpen(false)} />
            <div className="absolute right-3 left-3 mt-1.5 z-50 rounded-xl bg-slate-950/95 backdrop-blur-xl border border-slate-800/80 shadow-2xl p-1.5 flex flex-col gap-1">
              <button
                type="button"
                onClick={() => {
                  setActiveMode('FIND');
                  setIsDropdownOpen(false);
                }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-xs font-semibold rounded-lg text-left transition-all ${activeMode === 'FIND' ? 'bg-emerald-500/15 text-emerald-300' : 'text-slate-300 hover:bg-slate-800/50'}`}
              >
                <Zap size={13} className="text-emerald-400" />
                <div className="flex flex-col">
                  <span>Find High-Probability Trade</span>
                  <span className="text-[8px] font-normal text-slate-400">Autonomous breakouts & quant scanning</span>
                </div>
              </button>

              <button
                type="button"
                onClick={() => {
                  setActiveMode('VERIFY');
                  setIsDropdownOpen(false);
                }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-xs font-semibold rounded-lg text-left transition-all ${activeMode === 'VERIFY' ? 'bg-teal-500/15 text-teal-300' : 'text-slate-300 hover:bg-slate-800/50'}`}
              >
                <Shield size={13} className="text-teal-400" />
                <div className="flex flex-col">
                  <span>Verify My Trade Idea</span>
                  <span className="text-[8px] font-normal text-slate-400">Co-pilot critical Risk Manager critique</span>
                </div>
              </button>
            </div>
          </>
        )}

        <p className="text-[9px] text-text-muted/50 text-center mt-1.5">
          {symbol} • {activeTimeframe} • {!dataReady
            ? 'Loading candle data from QuestDB…'
            : insufficientData
              ? `⚠ Only ${symbolCandleCount} candles — may reduce accuracy`
              : `${symbolCandleCount} candles • Consensus + News → DeepSeek AI`}
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
      <div className="flex-grow flex-shrink min-h-0 overflow-y-auto scrollbar-thin">
        {reasoningSteps.length > 0 || sessionStatus !== 'idle' ? (
          <div className="h-full p-2 min-h-[380px]">
            <AgentTerminal />
          </div>
        ) : analysisError ? (
          /* ── Error State ─────────────────────────────────── */
          <div className="flex flex-col items-center justify-center gap-3 p-4 py-8">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-rose-500/10 border border-rose-500/30">
              <AlertTriangle size={20} className="text-rose-400" />
            </div>
            <div className="text-center">
              <p className="text-[11px] font-semibold text-rose-400">Analysis Failed</p>
              <p className="text-[9px] text-text-muted/60 mt-1 max-w-[200px] leading-relaxed">
                {analysisError}
              </p>
            </div>
            <button
              onClick={() => {
                if (activeMode === 'FIND') {
                  handleAIAnalysis();
                } else {
                  handleVerifyAnalysis();
                }
              }}
              disabled={!dataReady}
              className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[10px] font-semibold text-text-secondary bg-elevated border border-border-default hover:bg-surface transition-colors ${!dataReady ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              <RotateCcw size={10} />
              Retry
            </button>
          </div>
        ) : aiPlan ? (
          /* ── AI Execution Plan ── */
          <AiExecutionPlanView
            aiPlan={aiPlan}
            deployed={deployed}
            hasActivePosition={hasActivePosition}
            onDeploy={handleDeployStrategy}
            onClear={clearAiPlan}
          />
        ) : (
          /* ── Empty State ─────────────────────────────────── */
          <div className="flex flex-col items-center justify-center gap-4 p-4 py-10">
            <div className="relative">
              <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-emerald-500/10 to-teal-500/10 border border-emerald-500/20">
                <Zap size={24} className="text-emerald-400/60" />
              </div>
            </div>
            <div className="text-center">
              <p className="text-[11px] font-semibold text-text-muted">Deep Quant Engine Ready</p>
              <p className="text-[9px] text-text-muted/50 mt-1 leading-relaxed max-w-[180px]">
                Press the button above to run<br />
                the full AI analysis pipeline<br />
                for <span className="text-text-secondary font-semibold">{symbol}</span>
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
