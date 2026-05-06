'use client';

import React from 'react';
import AlphaPredictiveChart from '../AlphaPredictiveChart';
import type { Timeframe } from '../AlphaPredictiveChart';
import { TradeProfile, useTradeStore } from '../../store/useTradeStore';

// ── Types ──────────────────────────────────────────────────────────────
interface SwingLayoutProps {
  activeProfile?: TradeProfile;
  timeframe?: string;
}

type TrendBias = 'BULLISH' | 'BEARISH' | 'NEUTRAL';

interface TimeframeTrend {
  timeframe: string;
  bias: TrendBias;
  strength: number; // 0-100
}

// ── Mock Data (Multi-Timeframe Trends remain mock until Phase 10) ──────
const TIMEFRAME_TRENDS: TimeframeTrend[] = [
  { timeframe: '1H', bias: 'BULLISH', strength: 72 },
  { timeframe: '4H', bias: 'NEUTRAL', strength: 50 },
  { timeframe: '1D', bias: 'BULLISH', strength: 84 },
  { timeframe: '1W', bias: 'BULLISH', strength: 91 },
];

// ── Helpers ────────────────────────────────────────────────────────────
function biasColor(bias: TrendBias): string {
  switch (bias) {
    case 'BULLISH':
      return 'text-bull';
    case 'BEARISH':
      return 'text-bear';
    case 'NEUTRAL':
      return 'text-neutral';
  }
}

function strengthBarWidth(strength: number): string {
  return `${Math.min(Math.max(strength, 0), 100)}%`;
}

function sentimentScoreColor(score: number): string {
  if (score >= 70) return 'text-bull';
  if (score >= 40) return 'text-neutral';
  return 'text-bear';
}

function sentimentBarColor(score: number): string {
  if (score >= 70) return 'bg-bull';
  if (score >= 40) return 'bg-neutral';
  return 'bg-bear';
}

// ── Swing Confluence Panel ─────────────────────────────────────────────
function SwingConfluencePanel() {
  const latestInsight = useTradeStore((s) => s.latestInsight);

  const sentimentScore = latestInsight?.sentiment_score ?? null;

  return (
    <div
      id="swing-confluence-panel"
      className="flex h-full flex-col rounded-lg border border-border-default bg-surface text-sm select-none overflow-hidden"
    >
      {/* ── Panel Header ──────────────────────────────────── */}
      <div className="flex shrink-0 items-center justify-between border-b border-border-default px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-text-primary tracking-wide">
            Confluence
          </span>
          <span className="rounded bg-emerald-500/10 px-1.5 py-px text-[9px] font-bold text-emerald-400 uppercase tracking-widest">
            Swing
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-1.5 w-1.5">
            <span className={`absolute inline-flex h-full w-full animate-ping rounded-full opacity-50 ${latestInsight ? 'bg-emerald-400' : 'bg-amber-400'}`} />
            <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${latestInsight ? 'bg-emerald-500' : 'bg-amber-500'}`} />
          </span>
          <span className="text-[9px] font-medium text-text-muted uppercase tracking-widest">
            {latestInsight ? 'Live' : 'Awaiting'}
          </span>
        </div>
      </div>

      {/* ── Multi-Timeframe Trend ─────────────────────────── */}
      <div className="flex flex-col border-b border-border-default">
        <div className="px-4 pt-3 pb-1.5">
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
            Multi-Timeframe Trend
          </h3>
        </div>
        <div className="flex flex-col gap-2 px-4 pb-3">
          {TIMEFRAME_TRENDS.map((trend) => (
            <div key={trend.timeframe} className="flex flex-col gap-1">
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-text-primary">
                  {trend.timeframe}
                </span>
                <span className={`text-xs font-bold ${biasColor(trend.bias)}`}>
                  {trend.bias}
                </span>
              </div>
              {/* Strength bar */}
              <div className="h-1 w-full rounded-full bg-elevated">
                <div
                  className={`h-1 rounded-full transition-all duration-300 ${
                    trend.bias === 'BULLISH'
                      ? 'bg-bull'
                      : trend.bias === 'BEARISH'
                      ? 'bg-bear'
                      : 'bg-neutral'
                  }`}
                  style={{ width: strengthBarWidth(trend.strength) }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── AI News Sentiment (Live from Quant-RAG) ─────────── */}
      <div className="flex flex-1 min-h-0 flex-col">
        <div className="flex shrink-0 items-center justify-between px-4 pt-3 pb-1.5">
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
            AI News Sentiment
          </h3>
          <div className="flex items-center gap-1.5">
            {sentimentScore !== null ? (
              <>
                <span className={`text-sm font-bold tabular-nums ${sentimentScoreColor(sentimentScore)}`}>
                  {sentimentScore}
                </span>
                <span className="text-[9px] text-text-muted font-medium">/ 100</span>
              </>
            ) : (
              <span className="text-[9px] text-text-muted font-medium italic">—</span>
            )}
          </div>
        </div>

        {/* Sentiment gauge bar */}
        <div className="mx-4 mb-2">
          <div className="h-1.5 w-full rounded-full bg-elevated overflow-hidden">
            {sentimentScore !== null ? (
              <div
                className={`h-1.5 rounded-full transition-all duration-500 ${sentimentBarColor(sentimentScore)}`}
                style={{ width: `${sentimentScore}%` }}
              />
            ) : (
              <div className="h-1.5 w-0 rounded-full" />
            )}
          </div>
          <div className="flex justify-between mt-0.5 text-[8px] text-text-muted">
            <span>Fear</span>
            <span>Greed</span>
          </div>
        </div>

        {/* Insight content — live from Quant-RAG or awaiting state */}
        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-3">
          {latestInsight ? (
            <div className="flex flex-col gap-2.5">
              {/* Headline card */}
              <div className="flex gap-2 rounded-md border border-border-subtle bg-elevated/50 p-2.5 transition-colors hover:bg-elevated">
                <div className="mt-1 shrink-0">
                  <span className={`inline-flex h-2 w-2 rounded-full ${
                    latestInsight.sentiment_score >= 60 ? 'bg-bull' :
                    latestInsight.sentiment_score >= 40 ? 'bg-neutral' : 'bg-bear'
                  }`} />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] font-medium text-text-primary leading-snug line-clamp-2">
                    {latestInsight.headline}
                  </p>
                  <div className="mt-1 flex items-center gap-2 text-[9px] text-text-muted">
                    <span className="font-medium">Gemini AI</span>
                    <span>·</span>
                    <span>{latestInsight.symbol}</span>
                    <span>·</span>
                    <span>{latestInsight.anomaly_pct.toFixed(1)}% anomaly</span>
                  </div>
                </div>
              </div>

              {/* Analysis card */}
              <div className="rounded-md border border-border-subtle bg-elevated/50 p-2.5">
                <p className="text-[11px] leading-relaxed text-text-secondary whitespace-pre-line">
                  {latestInsight.analysis_text}
                </p>
              </div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center">
              <div className="flex flex-col items-center gap-2 text-center">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-elevated">
                  <span className="text-sm">🧠</span>
                </div>
                <p className="text-[11px] text-text-muted leading-snug">
                  Awaiting Market Anomalies...
                </p>
                <p className="text-[9px] text-text-muted/60">
                  Insights appear when a ≥2% price swing is detected
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Main Layout ────────────────────────────────────────────────────────
export default function SwingLayout({ activeProfile = 'SWING', timeframe = '1h' }: SwingLayoutProps) {
  const [isChartExpanded, setIsChartExpanded] = React.useState(false);

  return (
    <div id="swing-hud" className="grid h-full grid-cols-12 gap-3 p-3">
      {/* ── Primary Chart Area ──────────────────────────────── */}
      <div className={`flex flex-col gap-3 min-h-0 transition-all duration-300 ${isChartExpanded ? 'col-span-12' : 'col-span-9'}`}>
        {/* Chart Header Bar */}
        <div className="flex shrink-0 items-center justify-between rounded-lg border border-border-default bg-surface px-4 py-2">
          <div className="flex items-center gap-2.5">
            <h2 className="text-sm font-semibold text-text-primary tracking-wide">
              Swing Confluence Engine
            </h2>
            <span className="rounded bg-emerald-500/10 px-1.5 py-px text-[9px] font-bold text-emerald-400 uppercase tracking-widest">
              {timeframe} OHLC
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold text-amber-600 uppercase tracking-widest">
              Swing Mode
            </span>
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-40" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
            </span>
          </div>
        </div>

        {/* Chart Canvas */}
        <div className="flex-1 min-h-0 rounded-lg border border-border-default bg-surface overflow-hidden">
          <AlphaPredictiveChart
            activeProfile={activeProfile}
            timeframe={timeframe as Timeframe}
            isExpanded={isChartExpanded}
            onToggleExpand={() => setIsChartExpanded((prev) => !prev)}
          />
        </div>
      </div>

      {/* ── Confluence Sidebar (hidden when expanded) ──────────── */}
      {!isChartExpanded && (
        <div className="col-span-3 min-h-0">
          <SwingConfluencePanel />
        </div>
      )}
    </div>
  );
}
