import React from 'react';
import { Minus, TrendingUp, TrendingDown } from 'lucide-react';
import { type NaOr, type OptionsBiasState } from './viewModel';

export function NA() {
  return (
    <span className="inline-flex items-center rounded px-1 py-0.5 text-[8px] font-bold uppercase tracking-widest border border-border-default bg-elevated text-text-muted">
      N/A
    </span>
  );
}

export function fmt(v: NaOr<number>, dec = 2): React.ReactNode {
  if (v === null) return <NA />;
  return (
    <span className="font-mono text-text-primary text-right font-semibold">
      {v.toLocaleString('en-IN', { minimumFractionDigits: dec, maximumFractionDigits: dec })}
    </span>
  );
}

export function fmtStr(v: NaOr<string>): React.ReactNode {
  if (v === null || v.trim() === '') return <NA />;
  return (
    <span className="font-sans text-text-secondary text-right font-medium capitalize">
      {v.toLowerCase()}
    </span>
  );
}

export function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-border-default/20 pb-1.5 last:border-0 last:pb-0">
      <span className="text-[9px] font-medium uppercase tracking-wider text-text-muted">{label}</span>
      {children}
    </div>
  );
}

export function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2 rounded border border-border-default/40 bg-surface/50 p-2.5 shadow-sm">
      <h4 className="text-[8px] font-black uppercase tracking-widest text-text-muted">{title}</h4>
      <div className="flex flex-col gap-2">{children}</div>
    </div>
  );
}

export function BiasBadge({ state }: { state: NaOr<OptionsBiasState> }) {
  const cfg = (state ? {
    bullish: { cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', icon: <TrendingUp size={10} />, label: 'Bullish' },
    bearish: { cls: 'bg-rose-500/15 text-rose-400 border-rose-500/30', icon: <TrendingDown size={10} />, label: 'Bearish' },
    neutral: { cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30', icon: <Minus size={10} />, label: 'Neutral' },
  }[state] : null) ?? { cls: 'bg-elevated text-text-muted border-border-default', icon: <Minus size={10} />, label: 'N/A' };

  return (
    <span className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider ${cfg.cls}`}>
      {cfg.icon}{cfg.label}
    </span>
  );
}
