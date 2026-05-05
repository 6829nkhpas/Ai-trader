'use client';

import React from 'react';
import AlphaPredictiveChart from '../AlphaPredictiveChart';
import { TradeProfile } from '../../store/useTradeStore';

// ── Types ──────────────────────────────────────────────────────────────
interface InvestorLayoutProps {
  activeProfile?: TradeProfile;
}

interface MacroIndicator {
  label: string;
  value: string;
  change?: string;
  direction?: 'up' | 'down' | 'flat';
}

// ── Mock Data ──────────────────────────────────────────────────────────
const MACRO_INDICATORS: MacroIndicator[] = [
  { label: 'Fed Funds Rate', value: '5.25%', change: '+25 bps', direction: 'up' },
  { label: 'Core CPI (YoY)', value: '3.8%', change: '-0.2%', direction: 'down' },
  { label: '10Y Treasury', value: '4.31%', change: '+5 bps', direction: 'up' },
  { label: 'US Dollar Index', value: '104.52', change: '-0.3%', direction: 'down' },
  { label: 'VIX', value: '14.82', change: '-1.2', direction: 'down' },
  { label: 'GDP Growth (Q1)', value: '2.1%', change: '+0.3%', direction: 'up' },
];

const QUANT_RAG_OUTLOOK = `Based on current macro-regime analysis, the model identifies a late-cycle expansionary environment with moderating inflation pressure. The Federal Reserve's rate trajectory suggests a terminal plateau, which historically correlates with a 6–12 month equity tailwind across growth-sensitive sectors.

Key sector allocations recommended:
• Technology (+12% overweight) — AI capex cycle acceleration
• Healthcare (+8% overweight) — Defensive rotation hedge
• Energy (−5% underweight) — Peak demand concerns
• Consumer Discretionary (neutral) — Bifurcated consumer signals

The Quant-RAG model assigns a 73% probability to a soft-landing scenario. Portfolio beta should target 1.05–1.15 with a 60/30/10 equity-bond-alternative allocation framework for optimal risk-adjusted returns over the next fiscal quarter.`;

const PORTFOLIO_METRICS = [
  { label: 'Sharpe Ratio', value: '1.42' },
  { label: 'Max Drawdown', value: '-8.3%' },
  { label: 'Beta', value: '1.08' },
  { label: 'Alpha (ann.)', value: '+3.2%' },
];

// ── Helpers ────────────────────────────────────────────────────────────
function directionIcon(direction?: 'up' | 'down' | 'flat'): string {
  switch (direction) {
    case 'up':
      return '▲';
    case 'down':
      return '▼';
    default:
      return '—';
  }
}

function directionColor(direction?: 'up' | 'down' | 'flat'): string {
  switch (direction) {
    case 'up':
      return 'text-bull';
    case 'down':
      return 'text-bear';
    default:
      return 'text-text-muted';
  }
}

// ── Macro Sentiment Panel ──────────────────────────────────────────────
function MacroSentimentPanel() {
  return (
    <div
      id="macro-sentiment-panel"
      className="flex h-full flex-col rounded-lg border border-border-default bg-surface text-sm select-none overflow-hidden"
    >
      {/* ── Panel Header ──────────────────────────────────── */}
      <div className="flex shrink-0 items-center justify-between border-b border-border-default px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-text-primary tracking-wide">
            Macro Intelligence
          </span>
          <span className="rounded bg-emerald-500/10 px-1.5 py-px text-[9px] font-bold text-emerald-400 uppercase tracking-widest">
            Investor
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-1.5 w-1.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-50" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
          </span>
          <span className="text-[9px] font-medium text-text-muted uppercase tracking-widest">
            Live
          </span>
        </div>
      </div>

      {/* ── Macro Indicators ──────────────────────────────── */}
      <div className="flex flex-col border-b border-border-default">
        <div className="px-4 pt-3 pb-1.5">
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
            Macro Indicators
          </h3>
        </div>
        <div className="flex flex-col gap-0 px-4 pb-3">
          {MACRO_INDICATORS.map((indicator) => (
            <div
              key={indicator.label}
              className="flex items-center justify-between py-1.5 border-b border-border-subtle last:border-0"
            >
              <span className="text-[11px] text-text-secondary">
                {indicator.label}
              </span>
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-semibold text-text-primary tabular-nums">
                  {indicator.value}
                </span>
                {indicator.change && (
                  <span className={`text-[10px] font-medium tabular-nums ${directionColor(indicator.direction)}`}>
                    {directionIcon(indicator.direction)} {indicator.change}
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Portfolio Risk Metrics ─────────────────────────── */}
      <div className="flex flex-col border-b border-border-default">
        <div className="px-4 pt-3 pb-1.5">
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
            Portfolio Risk Metrics
          </h3>
        </div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 px-4 pb-3">
          {PORTFOLIO_METRICS.map((metric) => (
            <div key={metric.label} className="flex items-center justify-between">
              <span className="text-[10px] text-text-muted">{metric.label}</span>
              <span className="text-[11px] font-semibold text-text-primary tabular-nums">
                {metric.value}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Quant-RAG Outlook ─────────────────────────────── */}
      <div className="flex flex-1 min-h-0 flex-col">
        <div className="flex shrink-0 items-center justify-between px-4 pt-3 pb-1.5">
          <h3 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
            Quant-RAG Outlook
          </h3>
          <span className="rounded bg-cyan-500/10 px-1.5 py-px text-[9px] font-bold text-cyan-600 uppercase tracking-widest">
            AI Generated
          </span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-3">
          <div className="rounded-md border border-border-subtle bg-elevated/50 p-3">
            <p className="text-[11px] leading-relaxed text-text-secondary whitespace-pre-line">
              {QUANT_RAG_OUTLOOK}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main Layout ────────────────────────────────────────────────────────
export default function InvestorLayout({ activeProfile = 'INVESTOR' }: InvestorLayoutProps) {
  return (
    <div id="investor-hud" className="grid h-full grid-cols-12 gap-3 p-3">
      {/* ── Primary Chart Area ──────────────────────────────── */}
      <div className="col-span-9 flex flex-col gap-3 min-h-0">
        {/* Chart Header Bar */}
        <div className="flex shrink-0 items-center justify-between rounded-lg border border-border-default bg-surface px-4 py-2">
          <div className="flex items-center gap-2.5">
            <h2 className="text-sm font-semibold text-text-primary tracking-wide">
              Macro Allocation Engine
            </h2>
            <span className="rounded bg-emerald-500/10 px-1.5 py-px text-[9px] font-bold text-emerald-400 uppercase tracking-widest">
              1D–1W
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="rounded-md border border-cyan-500/30 bg-cyan-500/10 px-2 py-0.5 text-[10px] font-bold text-cyan-600 uppercase tracking-widest">
              Investor Mode
            </span>
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-40" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
            </span>
          </div>
        </div>

        {/* Chart Canvas */}
        <div className="flex-1 min-h-0 rounded-lg border border-border-default bg-surface overflow-hidden">
          <AlphaPredictiveChart activeProfile={activeProfile} />
        </div>
      </div>

      {/* ── Macro Sentiment Sidebar ─────────────────────────── */}
      <div className="col-span-3 min-h-0">
        <MacroSentimentPanel />
      </div>
    </div>
  );
}
