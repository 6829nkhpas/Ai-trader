'use client';

import React from 'react';
import {
  TrendingUp,
  TrendingDown,
  Minus,
  Activity,
  Gauge,
  Waves,
  BarChart3,
  Hexagon,
  Target,
} from 'lucide-react';
import type { ConsensusReport } from '../../store/useQuantStore';

// ── Helpers ─────────────────────────────────────────────────────────────

function trendColor(score: number) {
  if (score > 50) return 'text-emerald-400';
  if (score > 0) return 'text-emerald-400/70';
  if (score < -50) return 'text-rose-400';
  if (score < 0) return 'text-rose-400/70';
  return 'text-amber-400';
}

function trendBg(score: number) {
  if (score > 50) return 'bg-emerald-500';
  if (score > 0) return 'bg-emerald-500/60';
  if (score < -50) return 'bg-rose-500';
  if (score < 0) return 'bg-rose-500/60';
  return 'bg-amber-500/60';
}

function trendGlow(score: number) {
  if (score > 50) return 'shadow-emerald-500/20';
  if (score < -50) return 'shadow-rose-500/20';
  return '';
}

function stateColor(state: string) {
  switch (state) {
    case 'OVERBOUGHT': return 'text-rose-400 bg-rose-500/10 border-rose-500/30';
    case 'OVERSOLD': return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30';
    case 'SQUEEZING': return 'text-amber-400 bg-amber-500/10 border-amber-500/30';
    case 'EXPANDING': return 'text-violet-400 bg-violet-500/10 border-violet-500/30';
    case 'ACCUMULATION': return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30';
    case 'DISTRIBUTION': return 'text-rose-400 bg-rose-500/10 border-rose-500/30';
    case 'NORMAL':
    case 'NEUTRAL':
    default: return 'text-slate-400 bg-slate-500/10 border-slate-500/30';
  }
}

function stateIcon(category: string) {
  switch (category) {
    case 'momentum': return <Gauge size={12} />;
    case 'volatility': return <Waves size={12} />;
    case 'volume': return <BarChart3 size={12} />;
    default: return <Activity size={12} />;
  }
}

// ── Component ───────────────────────────────────────────────────────────

interface Props {
  consensusData: ConsensusReport | null;
}

export default function ConsensusBoard({ consensusData }: Props) {
  if (!consensusData) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-4">
        <div className="relative">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-elevated border border-border-subtle">
            <Activity size={24} className="text-text-muted animate-pulse" />
          </div>
          <div className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-amber-500/30 border border-amber-500/50 animate-ping" />
        </div>
        <div className="text-center">
          <p className="text-[11px] font-semibold text-text-muted">Awaiting Tick Data...</p>
          <p className="text-[9px] text-text-muted/50 mt-1">
            Consensus engine activates on<br />first candle ingestion
          </p>
        </div>
      </div>
    );
  }

  const { trend_score, momentum_state, volatility_state, volume_flow_state, active_patterns, active_strategies } = consensusData;

  // Normalize trend for gauge (0–100 where 50 is neutral)
  const gaugePercent = Math.round(((trend_score + 100) / 200) * 100);

  const stateEntries = [
    { label: 'Momentum', value: momentum_state, category: 'momentum' },
    { label: 'Volatility', value: volatility_state, category: 'volatility' },
    { label: 'Volume Flow', value: volume_flow_state, category: 'volume' },
  ];

  return (
    <div className="flex h-full flex-col text-sm select-none overflow-y-auto scrollbar-thin">
      {/* ── Trend Score Gauge ──────────────────────────────── */}
      <div className="shrink-0 border-b border-border-default px-3 py-3">
        <div className="flex items-center gap-1.5 mb-2.5">
          <TrendingUp size={11} className="text-text-muted" />
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
            Trend Score
          </h3>
        </div>

        <div className="flex items-center gap-3">
          {/* Big number */}
          <div className={`text-3xl font-black tabular-nums tracking-tight ${trendColor(trend_score)} ${trendGlow(trend_score)}`}>
            {trend_score > 0 ? '+' : ''}{trend_score}
          </div>

          <div className="flex-1 flex flex-col gap-1">
            {/* Label */}
            <div className="flex items-center justify-between">
              <span className={`text-[10px] font-bold uppercase tracking-wider ${trendColor(trend_score)}`}>
                {trend_score > 50 ? 'STRONG BULL' : trend_score > 0 ? 'BULLISH' : trend_score < -50 ? 'STRONG BEAR' : trend_score < 0 ? 'BEARISH' : 'NEUTRAL'}
              </span>
              <span className="text-[9px] text-text-muted tabular-nums">
                {gaugePercent}%
              </span>
            </div>

            {/* Gauge bar */}
            <div className="relative h-2 w-full rounded-full bg-elevated overflow-hidden">
              <div
                className={`h-2 rounded-full transition-all duration-700 ease-out ${trendBg(trend_score)}`}
                style={{ width: `${gaugePercent}%` }}
              />
              {/* Center marker at 50% (neutral) */}
              <div className="absolute top-0 left-1/2 -translate-x-px w-0.5 h-2 bg-text-muted/30" />
            </div>

            <div className="flex justify-between text-[8px] text-text-muted/50">
              <span>-100</span>
              <span>0</span>
              <span>+100</span>
            </div>
          </div>
        </div>
      </div>

      {/* ── State Badges ──────────────────────────────────── */}
      <div className="shrink-0 border-b border-border-default px-3 py-2.5">
        <div className="flex items-center gap-1.5 mb-2">
          <Activity size={11} className="text-text-muted" />
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
            Market Regime
          </h3>
        </div>

        <div className="flex flex-col gap-1.5">
          {stateEntries.map(({ label, value, category }) => (
            <div key={label} className="flex items-center justify-between">
              <div className="flex items-center gap-1.5 text-[11px] text-text-secondary">
                {stateIcon(category)}
                <span className="font-medium">{label}</span>
              </div>
              <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-[10px] font-bold border ${stateColor(value)}`}>
                {value}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Active Patterns ───────────────────────────────── */}
      <div className="shrink-0 border-b border-border-default px-3 py-2.5">
        <div className="flex items-center gap-1.5 mb-2">
          <Hexagon size={11} className="text-text-muted" />
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
            Candlestick Patterns
          </h3>
          {active_patterns.length > 0 && (
            <span className="ml-auto flex h-4 w-4 items-center justify-center rounded-full bg-slate-500/20 text-[9px] font-bold text-slate-400">
              {active_patterns.length}
            </span>
          )}
        </div>

        {active_patterns.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {active_patterns.map((p) => (
              <span
                key={p}
                className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[10px] font-semibold bg-slate-500/8 text-slate-400 border border-slate-500/20"
              >
                {p.includes('Bullish') || p === 'Hammer' ? <TrendingUp size={9} /> : p.includes('Bearish') || p === 'Shooting Star' ? <TrendingDown size={9} /> : <Minus size={9} />}
                {p}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-[10px] text-text-muted/50 italic">No patterns detected</p>
        )}
      </div>

      {/* ── Active Strategies (High Visibility) ───────────── */}
      <div className="shrink-0 px-3 py-2.5">
        <div className="flex items-center gap-1.5 mb-2">
          <Target size={11} className="text-blue-400" />
          <h3 className="text-[10px] font-semibold text-blue-400 uppercase tracking-wider">
            Institutional Strategies
          </h3>
          {active_strategies.length > 0 && (
            <span className="ml-auto flex h-4 w-4 items-center justify-center rounded-full bg-blue-500/20 text-[9px] font-bold text-blue-400 animate-pulse">
              {active_strategies.length}
            </span>
          )}
        </div>

        {active_strategies.length > 0 ? (
          <div className="flex flex-col gap-1">
            {active_strategies.map((s) => (
              <div
                key={s}
                className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 border border-blue-500/30 bg-blue-500/5 transition-colors hover:bg-blue-500/10"
              >
                <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-blue-500/15">
                  {s.includes('Bullish') || s.includes('Golden') ? (
                    <TrendingUp size={10} className="text-blue-400" />
                  ) : (
                    <TrendingDown size={10} className="text-blue-400" />
                  )}
                </div>
                <span className="text-[11px] font-semibold text-blue-300">{s}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[10px] text-text-muted/50 italic">No strategies active</p>
        )}
      </div>
    </div>
  );
}
