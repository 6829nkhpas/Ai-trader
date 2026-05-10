'use client';

import React from 'react';
import { Loader2 } from 'lucide-react';
import AlphaPredictiveChart from '../AlphaPredictiveChart';
import type { Timeframe } from '../AlphaPredictiveChart';
import { TradeProfile, useTradeStore } from '../../store/useTradeStore';
import { useMacroIndicators } from '../../hooks/useMacroIndicators';

interface InvestorLayoutProps { activeProfile?: TradeProfile; timeframe?: string; isExpanded?: boolean; onToggleExpand?: () => void; }

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

function timeAgo(ms: number): string {
  const secs = Math.floor((Date.now() - ms) / 1000);
  if (secs < 5) return 'just now';
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  return `${mins}m ago`;
}

// ── Shimmer Skeleton ────────────────────────────────────────────────────────

function IndicatorSkeleton() {
  return (
    <div className="flex flex-col gap-0 px-3 pb-2">
      {[...Array(5)].map((_, i) => (
        <div key={i} className="flex items-center justify-between py-1.5 border-b border-border-subtle last:border-0">
          <div className="h-3 w-20 rounded bg-elevated/80 animate-pulse" />
          <div className="flex items-center gap-2">
            <div className="h-3 w-16 rounded bg-elevated/80 animate-pulse" />
            <div className="h-3 w-10 rounded bg-elevated/60 animate-pulse" />
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Exported Macro Sentiment Panel (used by page.tsx sidebar) ────────────

export function MacroSentimentPanel() {
  const latestInsight = useTradeStore((s) => s.latestInsight);
  const { indicators, portfolioMetrics, loading, error, lastUpdated } = useMacroIndicators();

  return (
    <div id="macro-sentiment-panel" className="flex h-full flex-col rounded-lg border border-border-default bg-surface text-sm select-none overflow-hidden">
      
      {/* ── Macro Indicators (Live) ──────────────────────────────── */}
      <div className="flex flex-col border-b border-border-default">
        <div className="flex items-center justify-between px-3 pt-2 pb-1">
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Macro Indicators</h3>
          <div className="flex items-center gap-1.5">
            {error && (
              <span className="text-[8px] text-red-400 font-medium">API Error</span>
            )}
            {lastUpdated && !loading && (
              <span className="text-[8px] text-text-muted/60 tabular-nums">{timeAgo(lastUpdated)}</span>
            )}
            {loading && (
              <Loader2 size={10} className="animate-spin text-text-muted" />
            )}
          </div>
        </div>

        {loading && indicators.every((i) => i.raw === null) ? (
          <IndicatorSkeleton />
        ) : (
          <div className="flex flex-col gap-0 px-3 pb-2">
            {indicators.map((ind) => (
              <div key={ind.label} className="flex items-center justify-between py-1.5 border-b border-border-subtle last:border-0 transition-colors hover:bg-elevated/30">
                <div className="flex items-center gap-1.5">
                  <span className="text-[11px] text-text-secondary">{ind.label}</span>
                  <span className={`rounded px-1 py-px text-[7px] font-semibold uppercase tracking-wider ${categoryColor(ind.category)}`}>
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
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Portfolio Risk Metrics (Live from Store) ─────────────── */}
      <div className="flex flex-col border-b border-border-default">
        <div className="px-3 pt-2 pb-1"><h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Portfolio Metrics</h3></div>
        <div className="grid grid-cols-2 gap-x-2 gap-y-1 px-3 pb-2">
          {portfolioMetrics.map((m) => (
            <div key={m.label} className="flex items-center justify-between" title={m.tooltip}>
              <span className="text-[10px] text-text-muted">{m.label}</span>
              <span className={`text-[11px] font-semibold tabular-nums ${
                m.value.startsWith('+') ? 'text-bull' :
                m.value.startsWith('-') ? 'text-bear' :
                'text-text-primary'
              }`}>{m.value}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Quant-RAG Outlook (Already Dynamic — Untouched) ──────── */}
      <div className="flex flex-1 min-h-0 flex-col">
        <div className="flex shrink-0 items-center justify-between px-3 pt-2 pb-1">
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Quant-RAG Outlook</h3>
          <span className={`rounded px-1.5 py-px text-[9px] font-bold uppercase tracking-widest ${latestInsight ? 'bg-cyan-500/10 text-cyan-600' : 'bg-amber-500/10 text-amber-500'}`}>{latestInsight ? 'AI Generated' : 'Standby'}</span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-3 pb-2">
          {latestInsight ? (
            <div className="flex flex-col gap-2.5">
              <div className="rounded-md border border-border-subtle bg-elevated/50 p-3">
                <div className="flex items-start gap-2">
                  <span className={`mt-0.5 inline-flex h-2 w-2 shrink-0 rounded-full ${latestInsight.sentiment_score >= 60 ? 'bg-bull' : latestInsight.sentiment_score >= 40 ? 'bg-neutral' : 'bg-bear'}`} />
                  <div>
                    <p className="text-[12px] font-semibold text-text-primary leading-snug">{latestInsight.headline}</p>
                    <div className="mt-1 flex items-center gap-2 text-[9px] text-text-muted"><span className="font-medium">{latestInsight.symbol}</span><span>·</span><span>{latestInsight.anomaly_pct.toFixed(1)}% anomaly</span><span>·</span><span>Sentiment: {latestInsight.sentiment_score}/100</span></div>
                  </div>
                </div>
              </div>
              <div className="rounded-md border border-border-subtle bg-elevated/50 p-3"><p className="text-[11px] leading-relaxed text-text-secondary whitespace-pre-line">{latestInsight.analysis_text}</p></div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center"><div className="flex flex-col items-center gap-2 text-center"><div className="flex h-8 w-8 items-center justify-center rounded-full bg-elevated"><span className="text-sm">🧠</span></div><p className="text-[11px] text-text-muted leading-snug">Awaiting Market Anomalies...</p><p className="text-[9px] text-text-muted/60">AI outlook appears when a ≥2% price swing is detected</p></div></div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Layout ──────────────────────────────────────────────────────────────

export default function InvestorLayout({ activeProfile = 'INVESTOR', timeframe = '1D', isExpanded = false, onToggleExpand }: InvestorLayoutProps) {
  return (
    <div id="investor-hud" className="flex h-full flex-col min-h-0 rounded-lg border border-border-default bg-surface overflow-hidden">
      <AlphaPredictiveChart
        activeProfile={activeProfile}
        timeframe={timeframe as Timeframe}
        isExpanded={isExpanded}
        onToggleExpand={onToggleExpand}
      />
    </div>
  );
}
