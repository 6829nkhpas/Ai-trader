'use client';

/**
 * MacroSentimentPanel — the INVESTOR mode's right-sidebar panel.
 *
 * Was the named export of `InvestorLayout.tsx`. That file's default export was a
 * chart wrapper identical to the Intraday and Swing ones; all three were replaced
 * by the single `TerminalChartPane` so that switching modes no longer remounts the
 * chart. This panel is the only real component that lived there, so it now owns
 * the file.
 */

import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { MarketInsight, useTradeStore } from '../../store/useTradeStore';
import { useMacroIndicators } from '../../hooks/useMacroIndicators';
import ClockIcon from './ClockIcon';
import { staggerContainer, fadeInUp } from '../../lib/motionVariants';

function dirIcon(d?: 'up' | 'down' | 'flat') { return d === 'up' ? '▲' : d === 'down' ? '▼' : '—'; }
function dirColor(d?: 'up' | 'down' | 'flat') { return d === 'up' ? 'text-bull' : d === 'down' ? 'text-bear' : 'text-text-muted'; }

function categoryColor(cat: string) {
  switch (cat) {
    case 'Benchmark': return 'bg-cyan-500/10 text-cyan-400';
    case 'Volatility': return 'bg-rose-500/10 text-rose-400';
    case 'Sectoral': return 'bg-amber-500/10 text-amber-400';
    default: return 'bg-elevated text-text-muted';
  }
}




// ── Shimmer Skeleton ────────────────────────────────────────────────────────

function IndicatorSkeleton() {
  return (
    <div className="flex flex-col gap-0 px-3 pb-2">
      {[...Array(5)].map((_, i) => (
        <div key={i} className="flex items-center justify-between py-1.5 border-b border-border-subtle last:border-0">
          <div className="h-3 w-20 rounded skeleton-shimmer" />
          <div className="flex items-center gap-2">
            <div className="h-3 w-16 rounded skeleton-shimmer" />
            <div className="h-3 w-10 rounded skeleton-shimmer" />
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Exported Macro Sentiment Panel (used by page.tsx sidebar) ────────────

export function MacroSentimentPanel() {
  const latestInsight = useTradeStore((s) => s.latestInsight);
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const { indicators, portfolioMetrics, loading } = useMacroIndicators();

  const [activeInsight, setActiveInsight] = useState<MarketInsight | null>(null);

  // Active insight is derived from `selectedSymbol` and `latestInsight`: it
  // resets when the symbol changes, and picks up the live WebSocket insight
  // once it matches the newly active symbol. Adjusted during render (the
  // React-recommended pattern for deriving state from props) rather than in
  // two separate effects.
  const [prevSymbol, setPrevSymbol] = useState(selectedSymbol);
  if (selectedSymbol !== prevSymbol) {
    setPrevSymbol(selectedSymbol);
    setActiveInsight(null);
  }
  if (
    latestInsight &&
    latestInsight.headline &&
    latestInsight.symbol.toUpperCase() === selectedSymbol.toUpperCase() &&
    activeInsight !== latestInsight
  ) {
    setActiveInsight(latestInsight);
  }

  return (
    <div id="macro-sentiment-panel" className="flex h-full flex-col rounded-none border-0 bg-surface text-sm select-none overflow-hidden">

      {/* ── Macro Indicators (Live) ──────────────────────────────── */}
      <div className="flex flex-col border-b border-border-default">

        {loading && indicators.every((i) => i.raw === null) ? (
          <IndicatorSkeleton />
        ) : (
          <motion.div variants={staggerContainer} initial="hidden" animate="show" className="flex flex-col gap-0 px-3 pb-2">
            {indicators.map((ind) => (
              <motion.div key={ind.label} variants={fadeInUp} className="flex items-center justify-between py-1.5 border-b border-border-subtle last:border-0 transition-colors hover:bg-elevated/30">
                <div className="flex items-center gap-1.5">
                  <span className="text-[11px] text-text-secondary">{ind.label}</span>
                  <span className={`rounded-none px-1 py-px text-[7px] font-semibold uppercase tracking-wider ${categoryColor(ind.category)}`}>
                    {ind.category === 'Volatility' ? 'VIX' : ind.category === 'Benchmark' ? 'IDX' : 'SEC'}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] font-semibold text-text-primary tabular-nums">{ind.value}</span>
                  {ind.change && (
                    <span className={`text-[10px] font-medium tabular-nums ${dirColor(ind.direction)}`}>
                      {dirIcon(ind.direction)} {ind.change}
                    </span>
                  )}
                </div>
              </motion.div>
            ))}
          </motion.div>
        )}
      </div>

      {/* ── Discipline Metrics (Live from Store) ──────────────────
          Compliance blocker P6: this block reported Total Return / Win Rate /
          Max Drawdown / Avg Conviction. Those are performance representations
          and are gone — see `computeDisciplineMetrics` in
          `hooks/useMacroIndicators.ts`. The bull/bear ± colouring went with
          them: a green "+12" next to a discipline count reads as profit. */}
      <div className="flex flex-col border-b border-border-default">
        <div className="px-3 pt-2 pb-1"><h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Discipline Metrics</h3></div>
        <div className="grid grid-cols-2 gap-x-2 gap-y-1 px-3 pb-2">
          {portfolioMetrics.map((m) => (
            <div key={m.label} className="flex items-center justify-between" title={m.tooltip}>
              <span className="text-[10px] text-text-muted">{m.label}</span>
              <span className="text-[11px] font-semibold tabular-nums text-text-primary">{m.value}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Quant-RAG Outlook (Already Dynamic) ──────── */}
      <div className="flex flex-1 min-h-0 flex-col">
        <div className="flex shrink-0 items-center justify-between px-3 pt-2 pb-1">
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Quant-RAG Outlook</h3>
          <span className={`rounded-none px-1.5 py-px text-[9px] font-bold uppercase tracking-widest ${activeInsight ? 'bg-cyan-500/10 text-cyan-600' : 'bg-amber-500/10 text-amber-500'}`}>{activeInsight ? 'AI Generated' : 'Standby'}</span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-0 pb-2">
          {activeInsight ? (
            <div className="flex flex-col gap-0">
              <div className="rounded-none border-y border-x-0 border-border-subtle bg-elevated/50 p-3">
                <div className="flex items-start gap-2">
                  <span className={`mt-0.5 inline-flex h-2 w-2 shrink-0 rounded-none ${activeInsight.sentiment_score >= 60 ? 'bg-bull' : activeInsight.sentiment_score >= 40 ? 'bg-neutral' : 'bg-bear'}`} />
                  <div>
                    <p className="text-[12px] font-semibold text-text-primary leading-snug">{activeInsight.headline}</p>
                    <div className="mt-1 flex items-center gap-2 text-[9px] text-text-muted"><span className="font-medium">{activeInsight.symbol}</span><span>·</span><span>{activeInsight.anomaly_pct.toFixed(1)}% anomaly</span><span>·</span><span>Sentiment: {activeInsight.sentiment_score}/100</span></div>
                  </div>
                </div>
              </div>
              <div className="rounded-none border-b border-t-0 border-x-0 border-border-subtle bg-elevated/50 p-3"><p className="text-[11px] leading-relaxed text-text-secondary whitespace-pre-line">{activeInsight.analysis_text}</p></div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center p-6 text-center animate-in fade-in duration-200">
              <div className="flex flex-col items-center gap-3 py-6">
                <div className="w-32 h-28 flex items-center justify-center shrink-0">
                  <ClockIcon className="w-full h-full object-contain" />
                </div>
                <div className="space-y-1">
                  <p className="text-xs font-bold text-text-primary tracking-tight">Awaiting Market Anomalies...</p>
                  <p className="text-[10px] text-text-secondary leading-relaxed max-w-50 mx-auto">
                    AI outlook appears when a <span className="text-emerald-500 font-bold">≥2% price swing</span> is detected
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
