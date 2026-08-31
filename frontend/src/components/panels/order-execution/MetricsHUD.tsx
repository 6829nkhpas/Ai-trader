import React from 'react';
import { Target, ShieldAlert } from 'lucide-react';

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

interface MetricsHUDProps {
  hasDecision: boolean;
  liveQuote: SymbolQuote | null;
  entryPrice: number | null;
  targetPrice: number | null;
  stopPrice: number | null;
}

// `null` means the upstream did not report it — render an em-dash rather than
// standing in a zero, which would read as a real reading of 0.
function formatINR(value: number | null): string {
  if (value === null) return '—';
  return '₹' + value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatVolume(vol: number | null): string {
  if (vol === null) return '—';
  if (vol >= 10_000_000) return (vol / 10_000_000).toFixed(2) + ' Cr';
  if (vol >= 100_000) return (vol / 100_000).toFixed(2) + ' L';
  if (vol >= 1_000) return (vol / 1_000).toFixed(1) + ' K';
  return vol.toString();
}

export default function MetricsHUD({
  hasDecision,
  liveQuote,
  entryPrice,
  targetPrice,
  stopPrice,
}: MetricsHUDProps) {
  const entryDisplay = entryPrice ? formatINR(entryPrice) : '--';
  const targetDisplay = targetPrice ? formatINR(targetPrice) : '--';
  const stopDisplay = stopPrice ? formatINR(stopPrice) : '--';

  return (
    <div className="flex items-center gap-5">
      <div>
        <div className="text-[9px] font-bold uppercase tracking-wider text-text-muted">
          {hasDecision ? 'Entry' : 'LTP'}
        </div>
        <div className="text-sm font-bold text-text-primary tabular-nums">{entryDisplay}</div>
        {liveQuote && liveQuote.net_change !== null && (
          <div className={`text-[9px] font-semibold tabular-nums ${liveQuote.net_change >= 0 ? 'text-bull' : 'text-bear'}`}>
            {liveQuote.net_change >= 0 ? '+' : ''}{liveQuote.net_change.toFixed(2)}
          </div>
        )}
      </div>

      {/* OHLC Data — always visible for the selected symbol */}
      {liveQuote && (
        <div className="flex items-center gap-4 border-l border-border-default/60 pl-5">
          <div>
            <div className="text-[9px] font-bold uppercase tracking-wider text-text-muted">Open</div>
            <div className="text-sm font-semibold text-text-primary tabular-nums">{formatINR(liveQuote.open)}</div>
          </div>
          <div>
            <div className="text-[9px] font-bold uppercase tracking-wider text-text-muted">High</div>
            <div className="text-sm font-semibold text-bull tabular-nums">{formatINR(liveQuote.high)}</div>
          </div>
          <div>
            <div className="text-[9px] font-bold uppercase tracking-wider text-text-muted">Low</div>
            <div className="text-sm font-semibold text-bear tabular-nums">{formatINR(liveQuote.low)}</div>
          </div>
          <div>
            <div className="text-[9px] font-bold uppercase tracking-wider text-text-muted">Vol</div>
            <div className="text-sm font-semibold text-text-secondary tabular-nums">{formatVolume(liveQuote.volume)}</div>
          </div>
        </div>
      )}

      {/* ATR Target/Stop — only when AI decision is active */}
      {hasDecision && (
        <div className="flex items-center gap-3 border-l border-border-default/60 pl-5">
          <div>
            <div className="flex items-center gap-1 text-[9px] font-bold uppercase tracking-wider text-text-muted">
              <Target size={9} className="text-bull" />
              Target
            </div>
            <div className={`text-sm font-semibold tabular-nums ${targetPrice ? 'text-bull' : 'text-text-muted'}`}>{targetDisplay}</div>
            {targetPrice && entryPrice && (
              <div className="text-[9px] font-medium text-bull tabular-nums">
                +{(((targetPrice - entryPrice) / entryPrice) * 100).toFixed(1)}%
              </div>
            )}
          </div>
          <div>
            <div className="flex items-center gap-1 text-[9px] font-bold uppercase tracking-wider text-text-muted">
              <ShieldAlert size={9} className="text-bear" />
              Stop
            </div>
            <div className={`text-sm font-semibold tabular-nums ${stopPrice ? 'text-bear' : 'text-text-muted'}`}>{stopDisplay}</div>
            {stopPrice && entryPrice && (
              <div className="text-[9px] font-medium text-bear tabular-nums">
                {(((stopPrice - entryPrice) / entryPrice) * 100).toFixed(1)}%
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
