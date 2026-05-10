'use client';

import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown, Zap, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import AlphaPredictiveChart from '../AlphaPredictiveChart';
import type { Timeframe } from '../AlphaPredictiveChart';
import { TradeProfile, MarketInsight, useTradeStore } from '../../store/useTradeStore';
import { useMultiTimeframeTrend } from '../../hooks/useMultiTimeframeTrend';
import type { TrendBias } from '../../hooks/useMultiTimeframeTrend';

interface SwingLayoutProps { activeProfile?: TradeProfile; timeframe?: string; isExpanded?: boolean; onToggleExpand?: () => void; }

function biasColor(b: TrendBias) { return b === 'BULLISH' ? 'text-bull' : b === 'BEARISH' ? 'text-bear' : 'text-neutral'; }
function biasBarColor(b: TrendBias) { return b === 'BULLISH' ? 'bg-bull' : b === 'BEARISH' ? 'bg-bear' : 'bg-neutral'; }
function sentimentColor(s: number) { return s >= 70 ? 'text-bull' : s >= 40 ? 'text-neutral' : 'text-bear'; }
function sentimentBarColor(s: number) { return s >= 70 ? 'bg-bull' : s >= 40 ? 'bg-neutral' : 'bg-bear'; }
function sentimentDotColor(s: number) { return s >= 60 ? 'bg-bull' : s >= 40 ? 'bg-neutral' : 'bg-bear'; }

function sentimentLabel(s: number) {
  if (s >= 80) return 'Extreme Greed';
  if (s >= 60) return 'Bullish';
  if (s >= 40) return 'Neutral';
  if (s >= 20) return 'Bearish';
  return 'Extreme Fear';
}

function SentimentIcon({ score }: { score: number }) {
  if (score >= 60) return <TrendingUp size={12} className="text-bull" />;
  if (score >= 40) return <Minus size={12} className="text-neutral" />;
  return <TrendingDown size={12} className="text-bear" />;
}

function timeAgo(ms: number): string {
  const secs = Math.floor((Date.now() - ms) / 1000);
  if (secs < 10) return 'just now';
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ago`;
}

// ── Single Insight Card ─────────────────────────────────────────────────

interface InsightCardProps {
  insight: MarketInsight;
  isNew: boolean;
  index: number;
}

function InsightCard({ insight, isNew, index }: InsightCardProps) {
  const [expanded, setExpanded] = useState(false);
  const isError = insight.headline === 'LLM API Failure';

  return (
    <div
      className={`
        group relative rounded-lg border transition-all duration-300 ease-out cursor-pointer
        ${isNew ? 'animate-slide-in' : ''}
        ${isError
          ? 'border-red-500/30 bg-red-500/5 hover:bg-red-500/10'
          : 'border-border-subtle bg-elevated/40 hover:bg-elevated/70 hover:border-border-default'
        }
      `}
      onClick={() => setExpanded(!expanded)}
      style={{ animationDelay: `${index * 50}ms` }}
    >
      {/* Glow pulse for newest insight */}
      {isNew && !isError && (
        <div className="absolute inset-0 rounded-lg bg-emerald-500/5 animate-pulse pointer-events-none" />
      )}

      <div className="relative p-3">
        {/* Header row */}
        <div className="flex items-start gap-2.5">
          {/* Sentiment dot with ring animation */}
          <div className="mt-0.5 shrink-0 relative">
            {isNew && (
              <span className={`absolute inset-0 rounded-full animate-ping opacity-40 ${sentimentDotColor(insight.sentiment_score)}`} />
            )}
            <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${sentimentDotColor(insight.sentiment_score)} shadow-sm`} />
          </div>

          {/* Content */}
          <div className="min-w-0 flex-1">
            <p className={`text-[11px] font-semibold leading-snug ${isError ? 'text-red-400' : 'text-text-primary'}`}>
              {insight.headline}
            </p>

            {/* Meta row */}
            <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
              <span className="inline-flex items-center gap-1 rounded-full bg-surface px-1.5 py-0.5 text-[9px] font-semibold text-text-muted border border-border-subtle">
                <Zap size={8} className="text-amber-400" />
                {insight.symbol}
              </span>
              <span className={`inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[9px] font-bold tabular-nums ${
                insight.anomaly_pct >= 3 ? 'bg-red-500/10 text-red-400' : 'bg-amber-500/10 text-amber-400'
              }`}>
                {insight.anomaly_pct >= 0 ? '+' : ''}{insight.anomaly_pct.toFixed(1)}%
              </span>
              <span className="inline-flex items-center gap-0.5 rounded-full bg-surface px-1.5 py-0.5 text-[9px] text-text-muted border border-border-subtle">
                <SentimentIcon score={insight.sentiment_score} />
                {insight.sentiment_score}/100
              </span>
              <span className="text-[8px] text-text-muted/60 ml-auto tabular-nums">
                {timeAgo(insight.timestamp_ms)}
              </span>
            </div>
          </div>

          {/* Expand chevron */}
          <ChevronDown
            size={12}
            className={`shrink-0 text-text-muted/50 transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
          />
        </div>

        {/* Expandable analysis text */}
        <div className={`overflow-hidden transition-all duration-300 ease-out ${expanded ? 'max-h-40 mt-2.5 opacity-100' : 'max-h-0 opacity-0'}`}>
          <div className="rounded-md bg-surface/80 border border-border-subtle p-2.5">
            <p className="text-[10px] font-medium text-text-muted uppercase tracking-wider mb-1">AI Analysis</p>
            <p className={`text-[11px] leading-relaxed whitespace-pre-line ${isError ? 'text-red-300/80 font-mono text-[10px]' : 'text-text-secondary'}`}>
              {insight.analysis_text}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Exported Confluence Panel (used by page.tsx sidebar) ─────────────────

export function SwingConfluencePanel() {
  const latestInsight = useTradeStore((s) => s.latestInsight);
  const timeframeTrends = useMultiTimeframeTrend();
  const [insightHistory, setInsightHistory] = useState<MarketInsight[]>([]);
  const [newestId, setNewestId] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Accumulate insights into a scrollable history feed
  useEffect(() => {
    if (!latestInsight) return;

    setInsightHistory((prev) => {
      // Skip duplicates (same timestamp + symbol)
      if (prev.length > 0) {
        const last = prev[0];
        if (last.timestamp_ms === latestInsight.timestamp_ms && last.symbol === latestInsight.symbol) {
          return prev;
        }
      }
      // Prepend newest, cap at 20
      return [latestInsight, ...prev].slice(0, 20);
    });

    setNewestId(latestInsight.timestamp_ms);

    // Clear "new" animation after 3 seconds
    const timer = setTimeout(() => setNewestId(null), 3000);
    return () => clearTimeout(timer);
  }, [latestInsight]);

  const score = latestInsight?.sentiment_score ?? null;
  const insightCount = insightHistory.length;
  const errorCount = insightHistory.filter(i => i.headline === 'LLM API Failure').length;

  return (
    <div id="swing-confluence-panel" className="flex h-full flex-col rounded-lg border border-border-default bg-surface text-sm select-none overflow-hidden">
      {/* ── Header ──────────────────────────────────────────── */}
      

      {/* ── Multi-Timeframe Trend ────────────────────────────── */}
      <div className="shrink-0 flex flex-col border-b border-border-default">
        <div className="px-3 pt-2 pb-1"><h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Multi-Timeframe Trend</h3></div>
        <div className="flex flex-col gap-1.5 px-3 pb-2">
          {timeframeTrends.map((t) => (
            <div key={t.timeframe} className="flex flex-col gap-1">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-text-primary">{t.timeframe}</span>
                <span className={`text-xs font-bold ${biasColor(t.bias)}`}>{t.bias}</span>
              </div>
              <div className="h-1 w-full rounded-full bg-elevated">
                <div
                  className={`h-1 rounded-full transition-all duration-700 ease-out ${biasBarColor(t.bias)}`}
                  style={{ width: `${t.strength}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── AI News Sentiment ────────────────────────────────── */}
      <div className="flex flex-1 flex-col" style={{ minHeight: '200px' }}>
        {/* Sentiment Header */}
        <div className="flex shrink-0 items-center justify-between px-3 pt-2 pb-1">
          <div className="flex items-center gap-2">
            <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">AI News Sentiment</h3>
          </div>
          <div className="flex items-center gap-1.5">
            {score !== null ? (
              <>
                <span className={`text-sm font-bold tabular-nums ${sentimentColor(score)}`}>{score}</span>
                <span className="text-[9px] text-text-muted font-medium">/ 100</span>
              </>
            ) : (
              <span className="text-[9px] text-text-muted font-medium italic">—</span>
            )}
          </div>
        </div>

        {/* Sentiment Gauge Bar */}
        <div className="mx-3 mb-1.5">
          <div className="h-2 w-full rounded-full bg-elevated overflow-hidden relative">
            {score !== null ? (
              <>
                <div
                  className={`h-2 rounded-full transition-all duration-700 ease-out ${sentimentBarColor(score)}`}
                  style={{ width: `${score}%` }}
                />
                {/* Animated glow on the leading edge */}
                <div
                  className={`absolute top-0 h-2 w-3 rounded-full blur-sm transition-all duration-700 ${sentimentBarColor(score)} opacity-60`}
                  style={{ left: `calc(${score}% - 6px)` }}
                />
              </>
            ) : (
              <div className="h-2 w-0 rounded-full" />
            )}
          </div>
          <div className="flex justify-between mt-1 text-[8px]">
            <span className="text-bear/60 font-medium">Fear</span>
            {score !== null && (
              <span className={`font-semibold ${sentimentColor(score)}`}>{sentimentLabel(score)}</span>
            )}
            <span className="text-bull/60 font-medium">Greed</span>
          </div>
        </div>

        {/* ── Scrollable Insight Feed ──────────────────────── */}
        <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-2 pb-2 space-y-1.5 scrollbar-thin">
          {insightHistory.length > 0 ? (
            insightHistory.map((insight, i) => (
              <InsightCard
                key={`${insight.timestamp_ms}-${insight.symbol}`}
                insight={insight}
                isNew={newestId === insight.timestamp_ms && i === 0}
                index={i}
              />
            ))
          ) : (
            <div className="flex h-full items-center justify-center">
              <div className="flex flex-col items-center gap-3 text-center py-8">
                <div className="relative">
                  <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-elevated border border-border-subtle">
                    <span className="text-xl">🧠</span>
                  </div>
                  <div className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-amber-500/20 border border-amber-500/40 animate-pulse" />
                </div>
                <div>
                  <p className="text-[11px] text-text-muted font-medium leading-snug">Awaiting Market Anomalies...</p>
                  <p className="text-[9px] text-text-muted/50 mt-1 leading-snug">
                    Insights appear when a ≥2% price swing<br />triggers the DeepSeek AI engine
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

// ── Layout ──────────────────────────────────────────────────────────────

export default function SwingLayout({ activeProfile = 'SWING', timeframe = '1h', isExpanded = false, onToggleExpand }: SwingLayoutProps) {
  return (
    <div id="swing-hud" className="flex h-full flex-col min-h-0 rounded-lg border border-border-default bg-surface overflow-hidden">
      <AlphaPredictiveChart
        activeProfile={activeProfile}
        timeframe={timeframe as Timeframe}
        isExpanded={isExpanded}
        onToggleExpand={onToggleExpand}
      />
    </div>
  );
}
