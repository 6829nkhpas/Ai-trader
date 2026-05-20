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
} from 'lucide-react';
import { useQuantStore } from '../../store/useQuantStore';
import { useTradeStore } from '../../store/useTradeStore';
import { useMemo } from 'react';

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

function LoadingState() {
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
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500/20 to-violet-500/20 border border-blue-500/30">
          <Loader2 size={28} className="text-blue-400 animate-spin" />
        </div>
        <div className="absolute -inset-2 rounded-3xl bg-blue-500/5 animate-pulse" />
        <div className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-blue-500 animate-ping" />
      </div>

      <div className="text-center">
        <p className="text-[11px] font-semibold text-blue-300 animate-pulse transition-all duration-500">
          {LOADING_PHASES[phaseIdx]}
        </p>
        <p className="text-[9px] text-text-muted/50 mt-1.5">
          This may take 10–30 seconds
        </p>
      </div>

      {/* Phase dots */}
      <div className="flex gap-1">
        {LOADING_PHASES.map((_, i) => (
          <div
            key={i}
            className={`h-1 w-1 rounded-full transition-all duration-300 ${
              i <= phaseIdx ? 'bg-blue-400' : 'bg-slate-700'
            }`}
          />
        ))}
      </div>
    </div>
  );
}

// ── Main Component ──────────────────────────────────────────────────────

export default function DeepQuantPanel() {
  const { aiPlan, isAnalyzing, analysisError, fetchDeepAnalysis, clearAiPlan, openPosition, activePositions } = useQuantStore();
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const historicalCache = useTradeStore((s) => s.historicalCache);
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
  const hasActivePosition = activePositions.some((p) => p.symbol === symbol);
  const [deployed, setDeployed] = React.useState(false);

  // Reset deployed state when plan changes
  React.useEffect(() => {
    setDeployed(false);
  }, [aiPlan]);

  // ── AI Handoff Handler (with diagnostic tracers) ──────────────────────
  const handleAIAnalysis = () => {
    console.log(`🧠 [AI HANDOFF] Requesting analysis for Symbol: ${symbol}`);
    console.log(`🧠 [AI HANDOFF] Current cached candle count: ${symbolCandleCount}`);

    if (symbolCandleCount < 50) {
      console.warn(
        `🧠 [AI HANDOFF WARNING] Insufficient candles for AI analysis. ` +
        `DeepSeek requires at least 50 periods. Current: ${symbolCandleCount}`
      );
    }

    fetchDeepAnalysis(symbol);
  };

  return (
    <div className="flex h-full flex-col text-sm select-none overflow-hidden">
      {/* ── Trigger Button ────────────────────────────────── */}
      <div className="shrink-0 p-3 border-b border-border-default">
        <button
          id="btn-run-deep-quant"
          type="button"
          disabled={isAnalyzing || !dataReady}
          onClick={handleAIAnalysis}
          className={`
            group relative w-full flex items-center justify-center gap-2
            rounded-xl px-4 py-3 text-[13px] font-bold uppercase tracking-wider
            transition-all duration-300 ease-out
            ${!dataReady
              ? 'bg-slate-500/10 text-slate-400 border border-slate-500/20 opacity-50 cursor-not-allowed'
              : isAnalyzing
                ? 'bg-blue-500/10 text-blue-300 border border-blue-500/20 cursor-wait'
                : 'bg-gradient-to-r from-blue-600 to-violet-600 text-white border border-blue-500/40 hover:from-blue-500 hover:to-violet-500 hover:shadow-lg hover:shadow-blue-500/20 active:scale-[0.98]'
            }
          `}
        >
          {/* Glow ring */}
          {!isAnalyzing && dataReady && (
            <div className="absolute -inset-px rounded-xl bg-gradient-to-r from-blue-400/20 to-violet-400/20 opacity-0 group-hover:opacity-100 transition-opacity duration-300 blur-sm" />
          )}

          <span className="relative flex items-center gap-2">
            {!dataReady ? (
              <Loader2 size={16} className="animate-spin text-slate-400" />
            ) : isAnalyzing ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <Zap size={16} className="group-hover:animate-pulse" />
            )}
            {!dataReady
              ? 'AWAITING DATA…'
              : isAnalyzing
                ? 'ANALYZING...'
                : 'RUN DEEP QUANT ANALYSIS'}
          </span>
        </button>

        <p className="text-[9px] text-text-muted/50 text-center mt-1.5">
          {symbol} • {!dataReady
            ? 'Loading candle data from QuestDB…'
            : insufficientData
              ? `⚠ Only ${symbolCandleCount} candles — may reduce accuracy`
              : `${symbolCandleCount} candles • Consensus + News → DeepSeek AI`}
        </p>
      </div>

      {/* ── Content Area ──────────────────────────────────── */}
      <div className="flex-1 min-h-0 overflow-y-auto scrollbar-thin">
        {isAnalyzing ? (
          <LoadingState />
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
              onClick={handleAIAnalysis}
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
                onClick={() => {
                  if (aiPlan) {
                    openPosition(symbol, aiPlan);
                    setDeployed(true);
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
              <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500/10 to-violet-500/10 border border-blue-500/20">
                <Zap size={24} className="text-blue-400/60" />
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
