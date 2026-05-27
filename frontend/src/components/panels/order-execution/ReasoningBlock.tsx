import React, { useMemo } from 'react';
import { Briefcase } from 'lucide-react';

interface SymbolQuote {
  symbol: string;
  last_price: number;
  open: number;
  high: number;
  low: number;
  close: number;
  change: number;
  net_change: number;
  volume: number;
}

interface ReasoningBlockProps {
  hasDecision: boolean;
  matchedDecision: any;
  symbol: string;
  liveQuote: SymbolQuote | null;
  portfolioBalance: number;
  positions: Record<string, number>;
}

export default function ReasoningBlock({
  hasDecision,
  matchedDecision,
  symbol,
  liveQuote,
  portfolioBalance,
  positions,
}: ReasoningBlockProps) {
  // ── Compute real-time quantitative reasoning fallback ──────────────
  const reasoning = useMemo(() => {
    if (!matchedDecision) return '';
    const raw = matchedDecision.reasoning || '';
    const priceStr = liveQuote ? ` [LTP: ₹${liveQuote.last_price.toFixed(2)} (${liveQuote.change >= 0 ? '+' : ''}${liveQuote.change.toFixed(2)}%)]` : '';
    if (raw && raw !== 'Live backend decision' && !raw.includes('without a reasoning string') && raw.length > 5) {
      return raw + priceStr;
    }
    return `Quant signal: ${matchedDecision.action_type} with ${Math.round(matchedDecision.final_conviction_score)}% conviction.` + priceStr;
  }, [matchedDecision, symbol, liveQuote]);

  if (hasDecision) {
    return (
      <div className="flex min-w-48 flex-1 items-start gap-2 text-xs text-text-secondary">
        <span className="font-semibold text-text-secondary">Reasoning:</span>
        <span>{reasoning}</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 text-xs text-text-secondary">
      <div className="flex items-center gap-2">
        <Briefcase size={12} className="text-text-muted" />
        <span>Balance:</span>
        <span className="flex items-center font-bold text-text-primary">
          <span className="mr-0.5 text-bull font-semibold">₹</span>
          {portfolioBalance.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </span>
      </div>
      {Object.keys(positions).length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          {Object.entries(positions).map(([sym, qty]) => (
            <span key={sym} className="rounded-full border border-border-default bg-surface px-2 py-0.5 text-[10px] text-text-secondary">
              <span className="font-bold text-text-primary">{sym}</span>: {qty}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
