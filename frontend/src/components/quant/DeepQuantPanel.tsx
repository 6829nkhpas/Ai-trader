'use client';

import React from 'react';
import {
  Zap,
  Loader2,
  Shield,
  Target,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  RotateCcw,
  Rocket,
  Radar,
  ChevronDown,
} from 'lucide-react';
import { useQuantStore } from '../../store/useQuantStore';
import { useTradeStore } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import { useMemo } from 'react';
import { listen } from '@tauri-apps/api/event';
import AgentTerminal from './AgentTerminal';

// ── Conviction Helpers ──────────────────────────────────────────────────

function convictionColor(score: number) {
  if (score >= 80) return { text: 'text-emerald-400', bg: 'bg-emerald-500', ring: 'ring-emerald-500/30', glow: 'shadow-emerald-500/20' };
  if (score >= 60) return { text: 'text-emerald-400/80', bg: 'bg-emerald-500/70', ring: 'ring-emerald-500/20', glow: '' };
  if (score >= 40) return { text: 'text-amber-400', bg: 'bg-amber-500', ring: 'ring-amber-500/20', glow: '' };
  return { text: 'text-rose-400', bg: 'bg-rose-500', ring: 'ring-rose-500/20', glow: 'shadow-rose-500/20' };
}

function convictionLabel(score: number) {
  if (score >= 80) return 'HIGH CONVICTION';
  if (score >= 60) return 'MODERATE';
  if (score >= 40) return 'LOW CONVICTION';
  return 'VERY WEAK';
}

function convictionIcon(score: number) {
  if (score >= 60) return <CheckCircle2 size={14} />;
  if (score >= 40) return <AlertTriangle size={14} />;
  return <XCircle size={14} />;
}

// ── Loading Phrases ─────────────────────────────────────────────────────

const LOADING_PHASES = [
  'Aggregating 50+ Technical Indicators...',
  'Scanning Candlestick Patterns...',
  'Evaluating Institutional Strategies...',
  'Fetching Live News Context...',
  'Constructing Master Prompt...',
  'Awaiting DeepSeek Analysis...',
];

function LoadingState({ agentStatus }: { agentStatus: string }) {
  const [phaseIdx, setPhaseIdx] = React.useState(0);

  React.useEffect(() => {
    const timer = setInterval(() => {
      setPhaseIdx((prev) => (prev + 1) % LOADING_PHASES.length);
    }, 2500);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="flex flex-col items-center justify-center gap-4 py-8 px-4">
      {/* Pulsing orb */}
      <div className="relative">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-emerald-500/20 to-teal-500/20 border border-emerald-500/30">
          <Loader2 size={28} className="text-emerald-400 animate-spin" />
        </div>
        <div className="absolute -inset-2 rounded-3xl bg-emerald-500/5 animate-pulse" />
        <div className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-emerald-500 animate-ping" />
      </div>

      <div className="text-center">
        <p className="text-[11px] font-semibold text-emerald-300 animate-pulse transition-all duration-500">
          {LOADING_PHASES[phaseIdx]}
        </p>
        <p className="text-[9px] text-text-muted/50 mt-1.5">
          This may take 10–30 seconds
        </p>
      </div>

      {/* Real-time status display */}
      <div className="w-full max-w-[240px] p-2.5 bg-black/40 border border-emerald-500/20 rounded font-mono text-[10px] flex items-center space-x-2 animate-pulse text-emerald-400">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping shrink-0" />
        <span className="truncate">{agentStatus}</span>
      </div>

      {/* Phase dots */}
      <div className="flex gap-1">
        {LOADING_PHASES.map((_, i) => (
          <div
            key={i}
            className={`h-1 w-1 rounded-full transition-all duration-300 ${
              i <= phaseIdx ? 'bg-emerald-400' : 'bg-slate-700'
            }`}
          />
        ))}
      </div>
    </div>
  );
}

// ── Main Component ──────────────────────────────────────────────────────

export default function DeepQuantPanel() {
  const { aiPlan, isAnalyzing, analysisError, fetchDeepAnalysis, clearAiPlan } = useQuantStore();
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const historicalCache = useTradeStore((s) => s.historicalCache);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);
  const paperPortfolio = useTradeStore((s) => s.paperPortfolio);
  const agentChatLog = useTradeStore((s) => s.agentChatLog);
  const clearAgentChatLog = useTradeStore((s) => s.clearAgentChatLog);
  const symbol = selectedSymbol || 'RELIANCE';

  // ── AI Handoff State Guard ────────────────────────────────────────────
  // Check if the historicalCache has ANY entry for this symbol with > 0 candles.
  // This is the cross-component proxy for "mergedCandles" since DeepQuantPanel
  // doesn't have direct access to the chart's merged candle array.
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

  // Check if there's already an active position for this symbol from this plan
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

  // ── AI Handoff Handler (with diagnostic tracers) ──────────────────────
  const handleAIAnalysis = () => {
    console.log(`🧠 [AI HANDOFF] Requesting analysis for Symbol: ${symbol} | Timeframe: ${activeTimeframe}`);
    console.log(`🧠 [AI HANDOFF] Current cached candle count: ${symbolCandleCount}`);

    // ═══════════════════════════════════════════════════════════════════
    // 🕵️‍♂️ AUDIT 1 - UI SEND: Verify what the React UI fires to Tauri
    // ═══════════════════════════════════════════════════════════════════
    console.log("🕵️‍♂️ [AUDIT 1 - UI SEND] Firing AI Request.");
    console.log("🕵️‍♂️ [AUDIT 1 - UI SEND] Symbol:", symbol);
    console.log("🕵️‍♂️ [AUDIT 1 - UI SEND] Timeframe:", activeTimeframe);
    console.log("🕵️‍♂️ [AUDIT 1 - UI SEND] Acceleration Coeff from Store:", useChartUIStore.getState().accelerationCoefficient);
    console.log("🕵️‍♂️ [AUDIT 1 - UI SEND] Candles Length (cached proxy):", symbolCandleCount);
    console.log("🕵️‍♂️ [AUDIT 1 - UI SEND] Historical Cache Keys:", Object.keys(useTradeStore.getState().historicalCache));
    // ═══════════════════════════════════════════════════════════════════

    if (symbolCandleCount < 50) {
      console.warn(
        `🧠 [AI HANDOFF WARNING] Insufficient candles for AI analysis. ` +
        `DeepSeek requires at least 50 periods. Current: ${symbolCandleCount}`
      );
    }

    clearAgentChatLog();
    fetchDeepAnalysis(symbol, 'FIND');
  };

  const handleVerifyAnalysis = () => {
    const entryNum = parseFloat(entry);
    const slNum = parseFloat(stopLoss);
    const tpNum = parseFloat(takeProfit);

    if (isNaN(entryNum) || entryNum <= 0) {
      console.warn("Invalid entry price");
      return;
    }

    console.log(`🧠 [AI HANDOFF] Requesting VERIFY Mode analysis for Symbol: ${symbol}`);
    console.log(`🧠 [AI HANDOFF] Proposed Trade: ${side} Entry:${entryNum} SL:${slNum} TP:${tpNum}`);

    clearAgentChatLog();
    fetchDeepAnalysis(symbol, 'VERIFY', {
      side,
      entry: entryNum,
      stopLoss: isNaN(slNum) ? 0 : slNum,
      takeProfit: isNaN(tpNum) ? 0 : tpNum,
      userAnalysis: userAnalysis.trim() || "No user notes provided."
    });
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

        {/* Dropdown Menu (Glassmorphic) */}
        {isDropdownOpen && (
          <>
            {/* Overlay to close */}
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
        <div className="mx-3 mt-3 p-3 rounded-xl border border-slate-800 bg-slate-900/30 backdrop-blur-md flex flex-col gap-3">
          <div className="flex items-center justify-between border-b border-slate-800 pb-1.5">
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Configure Setup</span>
            <span className="text-[9px] text-slate-500">Auto-filled via NSE LTP</span>
          </div>

          {/* Side selector */}
          <div className="flex rounded-lg bg-slate-950 p-0.5 border border-slate-800/50">
            <button
              type="button"
              onClick={() => setSide('BUY')}
              className={`flex-grow py-1 rounded-md text-[10px] font-bold transition-all ${side === 'BUY' ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/20' : 'text-slate-400 hover:text-slate-200'}`}
            >
              BUY / LONG
            </button>
            <button
              type="button"
              onClick={() => setSide('SELL')}
              className={`flex-grow py-1 rounded-md text-[10px] font-bold transition-all ${side === 'SELL' ? 'bg-rose-500/15 text-rose-400 border border-rose-500/20' : 'text-slate-400 hover:text-slate-200'}`}
            >
              SELL / SHORT
            </button>
          </div>

          {/* Input fields */}
          <div className="grid grid-cols-3 gap-2">
            <div className="flex flex-col gap-1">
              <label className="text-[8px] font-semibold text-slate-400 uppercase">Entry Price</label>
              <input
                type="number"
                step="any"
                value={entry}
                onChange={(e) => {
                  setEntry(e.target.value);
                  setHasManuallySetEntry(true);
                }}
                className="w-full bg-slate-950/80 border border-slate-800 rounded px-2 py-1 text-xs text-white font-mono focus:border-emerald-500 focus:outline-none"
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-[8px] font-semibold text-slate-400 uppercase">Stop Loss</label>
              <input
                type="number"
                step="any"
                value={stopLoss}
                onChange={(e) => {
                  setStopLoss(e.target.value);
                  setHasManuallySetSL(true);
                }}
                className={`w-full bg-slate-950/80 border rounded px-2 py-1 text-xs text-white font-mono focus:outline-none ${side === 'BUY' ? 'border-rose-950/50 focus:border-rose-500' : 'border-emerald-950/50 focus:border-emerald-500'}`}
              />
              {slPercent && (
                <span className={`text-[8px] self-end font-mono ${parseFloat(slPercent) < 0 ? 'text-rose-400' : 'text-emerald-400'}`}>
                  {slPercent}%
                </span>
              )}
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-[8px] font-semibold text-slate-400 uppercase">Take Profit</label>
              <input
                type="number"
                step="any"
                value={takeProfit}
                onChange={(e) => {
                  setTakeProfit(e.target.value);
                  setHasManuallySetTP(true);
                }}
                className={`w-full bg-slate-950/80 border rounded px-2 py-1 text-xs text-white font-mono focus:outline-none ${side === 'BUY' ? 'border-emerald-950/50 focus:border-emerald-500' : 'border-rose-950/50 focus:border-rose-500'}`}
              />
              {tpPercent && (
                <span className={`text-[8px] self-end font-mono ${parseFloat(tpPercent) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                  {parseFloat(tpPercent) >= 0 ? '+' : ''}{tpPercent}%
                </span>
              )}
            </div>
          </div>

          {/* Risk-to-Reward Badge */}
          {riskToReward && (
            <div className="flex justify-between items-center rounded-lg bg-slate-950 p-2 border border-slate-800/40 text-[10px]">
              <span className="text-slate-400 font-semibold">Risk:Reward Ratio</span>
              <span className={`font-black font-mono px-2 py-0.5 rounded ${parseFloat(riskToReward) >= 2.0 ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : parseFloat(riskToReward) >= 1.5 ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20' : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'}`}>
                1 : {riskToReward}
              </span>
            </div>
          )}

          {/* User analysis note */}
          <div className="flex flex-col gap-1.5">
            <label className="text-[8px] font-semibold text-slate-400 uppercase">My Trade Logic / Notes</label>
            <textarea
              value={userAnalysis}
              onChange={(e) => setUserAnalysis(e.target.value)}
              placeholder="Describe your reasoning (e.g. buying the bounce on ema-21, MACD divergence)"
              className="w-full bg-slate-950/80 border border-slate-800 rounded px-2 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:border-emerald-500 focus:outline-none min-h-[60px] max-h-[120px] resize-y leading-relaxed"
            />
          </div>
        </div>
      )}

      {/* ── Content Area ──────────────────────────────────── */}
      <div className="flex-grow flex-shrink min-h-0 overflow-y-auto scrollbar-thin">
        {agentChatLog.length > 0 || isAnalyzing ? (
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
          /* ── AI Execution Plan ───────────────────────────── */
          <div className="flex flex-col gap-0">
            {/* Conviction Score */}
            <div className="px-3 py-3 border-b border-border-default">
              <div className="flex items-center gap-1.5 mb-2">
                <Shield size={11} className="text-text-muted" />
                <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
                  AI Conviction
                </h3>
              </div>

              <div className="flex items-center gap-3">
                {/* Big score */}
                <div className={`relative flex items-baseline gap-0.5 ${convictionColor(aiPlan.conviction_score).text}`}>
                  <span className="text-4xl font-black tabular-nums tracking-tighter">
                    {aiPlan.conviction_score}
                  </span>
                  <span className="text-base font-semibold text-text-muted/50">/100</span>
                </div>

                <div className="flex-1 flex flex-col gap-1.5">
                  {/* Label badge */}
                  <div className={`inline-flex items-center gap-1 self-start rounded-md px-2 py-0.5 text-[9px] font-bold ${convictionColor(aiPlan.conviction_score).text} ${convictionColor(aiPlan.conviction_score).bg}/15 ring-1 ${convictionColor(aiPlan.conviction_score).ring}`}>
                    {convictionIcon(aiPlan.conviction_score)}
                    {convictionLabel(aiPlan.conviction_score)}
                  </div>

                  {/* Progress bar */}
                  <div className="h-1.5 w-full rounded-full bg-elevated overflow-hidden">
                    <div
                      className={`h-1.5 rounded-full transition-all duration-1000 ease-out ${convictionColor(aiPlan.conviction_score).bg}`}
                      style={{ width: `${aiPlan.conviction_score}%` }}
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Setup Validation */}
            <div className="px-3 py-2.5 border-b border-border-default">
              <div className="flex items-center gap-1.5 mb-1.5">
                <Target size={11} className="text-text-muted" />
                <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
                  Setup Validation
                </h3>
              </div>
              <p className="text-[11px] leading-relaxed text-text-secondary whitespace-pre-line">
                {aiPlan.setup_validation}
              </p>
            </div>

            {/* Execution Plan */}
            <div className="px-3 py-2.5">
              <div className="flex items-center gap-1.5 mb-1.5">
                <Zap size={11} className="text-amber-400" />
                <h3 className="text-[10px] font-semibold text-amber-400 uppercase tracking-wider">
                  Execution Plan
                </h3>
              </div>
              <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2.5">
                <p className="text-[11px] leading-relaxed text-amber-200/90 font-medium whitespace-pre-line">
                  {aiPlan.execution_plan}
                </p>
              </div>
            </div>

            {/* Clear button */}
            <div className="px-3 py-2 flex flex-col gap-1.5">
              {/* Deploy Strategy Button */}
              <button
                id="btn-deploy-strategy"
                type="button"
                disabled={deployed || hasActivePosition}
                onClick={async () => {
                  if (!aiPlan) return;
                  
                  const closePrice = useTradeStore.getState().ohlcCandles.find(c => c.symbol === symbol)?.close || 0;
                  
                  let entryPrice = closePrice;
                  let stopLoss = 0;
                  let takeProfit = 0;
                  let side = 'BUY';

                  const executionPlan = aiPlan.execution_plan || '';

                  // Extract values with regex
                  const entryMatch = executionPlan.match(/entry:\s*([\d.]+)/i);
                  const slMatch = executionPlan.match(/stop-loss:\s*([\d.]+)/i) || executionPlan.match(/sl:\s*([\d.]+)/i);
                  const tpMatch = executionPlan.match(/target\s*1?:\s*([\d.]+)/i) || executionPlan.match(/target:\s*([\d.]+)/i) || executionPlan.match(/tp:\s*([\d.]+)/i);
                  const sideMatch = executionPlan.match(/side:\s*(buy|sell)/i) || executionPlan.match(/(buy|sell)/i);

                  if (entryMatch) {
                    entryPrice = parseFloat(entryMatch[1]);
                  }
                  if (slMatch) {
                    stopLoss = parseFloat(slMatch[1]);
                  }
                  if (tpMatch) {
                    takeProfit = parseFloat(tpMatch[1]);
                  }
                  if (sideMatch) {
                    const matchedSide = sideMatch[1].toUpperCase();
                    if (matchedSide === 'BUY' || matchedSide === 'SELL') {
                      side = matchedSide;
                    }
                  }

                  // Dynamic fallbacks
                  if (entryPrice <= 0) entryPrice = closePrice;
                  if (stopLoss <= 0) {
                    stopLoss = side === 'BUY' ? entryPrice * 0.98 : entryPrice * 1.02;
                  }
                  if (takeProfit <= 0) {
                    takeProfit = side === 'BUY' ? entryPrice * 1.05 : entryPrice * 0.95;
                  }

                  try {
                    const { invoke } = await import('@tauri-apps/api/core');
                    const resMsg = await invoke<string>('execute_paper_trade', {
                      symbol,
                      side,
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
                }}
                className={`
                  group relative w-full flex items-center justify-center gap-2
                  rounded-xl px-4 py-2.5 text-[11px] font-bold uppercase tracking-wider
                  transition-all duration-300 ease-out
                  ${deployed || hasActivePosition
                    ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 cursor-default'
                    : 'bg-gradient-to-r from-emerald-600 to-teal-600 text-white border border-emerald-500/40 hover:from-emerald-500 hover:to-teal-500 hover:shadow-lg hover:shadow-emerald-500/20 active:scale-[0.98]'
                  }
                `}
              >
                {!deployed && !hasActivePosition && (
                  <div className="absolute -inset-px rounded-xl bg-gradient-to-r from-emerald-400/20 to-teal-400/20 opacity-0 group-hover:opacity-100 transition-opacity duration-300 blur-sm" />
                )}
                <span className="relative flex items-center gap-2">
                  {deployed || hasActivePosition ? (
                    <>
                      <CheckCircle2 size={14} />
                      STRATEGY DEPLOYED
                    </>
                  ) : (
                    <>
                      <Rocket size={14} className="group-hover:animate-bounce" />
                      DEPLOY SIMULATED STRATEGY
                    </>
                  )}
                </span>
              </button>

              <button
                onClick={clearAiPlan}
                className="w-full flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-[10px] font-semibold text-text-muted bg-elevated border border-border-default hover:bg-surface hover:text-text-secondary transition-colors"
              >
                <RotateCcw size={10} />
                Clear & Reset
              </button>
            </div>
          </div>
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
