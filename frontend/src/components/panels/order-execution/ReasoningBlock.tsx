import React, { useMemo } from 'react';
import type { AggregatedDecision } from '../../../store/useTradeStore';

interface SymbolQuote {
  symbol: string;
  last_price: number;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  change: number | null;
  net_change: number | null;
  volume: number | null;
}

interface ReasoningBlockProps {
  hasDecision: boolean;
  matchedDecision: AggregatedDecision | null;
  liveQuote: SymbolQuote | null;
  positions: Record<string, number>;
}

export default function ReasoningBlock({
  hasDecision,
  matchedDecision,
  liveQuote,
  positions,
}: ReasoningBlockProps) {
  // ── Compute real-time quantitative reasoning fallback ──────────────
  const reasoning = useMemo(() => {
    if (!matchedDecision) return '';
    const raw = matchedDecision.reasoning || '';
    // The percent move is appended only when the upstream reported one; with no
    // previous close there is no change to quote.
    const changeStr =
      liveQuote && liveQuote.change !== null
        ? ` (${liveQuote.change >= 0 ? '+' : ''}${liveQuote.change.toFixed(2)}%)`
        : '';
    const priceStr = liveQuote ? ` [LTP: ₹${liveQuote.last_price.toFixed(2)}${changeStr}]` : '';
    if (raw && raw !== 'Live backend decision' && !raw.includes('without a reasoning string') && raw.length > 5) {
      return raw + priceStr;
    }
    return `Quant signal: ${matchedDecision.action_type} with ${Math.round(matchedDecision.final_conviction_score)}% conviction.` + priceStr;
  }, [matchedDecision, liveQuote]);

  if (hasDecision) {
    return (
      <div className="flex min-w-48 flex-1 items-start gap-2 rounded-md border border-border-default/50 bg-elevated/40 px-3 py-1.5 text-xs text-text-secondary">
        <span className="shrink-0 font-bold uppercase tracking-wider text-[9px] text-text-muted pt-0.5">Reasoning</span>
        <span className="text-text-secondary">{reasoning}</span>
      </div>
    );
  }

  if (Object.keys(positions).length === 0) return null;

  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5">
      {Object.entries(positions).map(([sym, qty]) => (
        <span
          key={sym}
          className="rounded-full border border-border-default/60 bg-elevated/50 px-2.5 py-1 text-[10px] font-medium text-text-secondary"
        >
          <span className="font-bold text-text-primary">{sym}</span>
          <span className="text-text-muted"> · {qty}</span>
        </span>
      ))}
    </div>
  );
}
