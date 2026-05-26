import React from 'react';

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

interface MetricsHUDProps {
  hasDecision: boolean;
  liveQuote: SymbolQuote | null;
  entryPrice: number | null;
  targetPrice: number | null;
  stopPrice: number | null;
}

function formatINR(value: number): string {
  return '₹' + value.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatVolume(vol: number): string {
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
    <div className="flex items-center gap-4 text-xs">
      <div>
        <div className="text-[10px] uppercase tracking-wider text-text-secondary">
          {hasDecision ? 'Entry' : 'LTP'}
        </div>
        <div className="text-sm font-semibold text-text-primary tabular-nums">{entryDisplay}</div>
        {liveQuote && (
          <div className={`text-[9px] tabular-nums ${liveQuote.change >= 0 ? 'text-bull' : 'text-bear'}`}>
            {liveQuote.net_change >= 0 ? '+' : ''}{liveQuote.net_change.toFixed(2)}
          </div>
        )}
      </div>

      {/* OHLC Data — always visible for the selected symbol */}
      {liveQuote && (
        <>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary">Open</div>
            <div className="text-sm font-semibold text-text-primary tabular-nums">{formatINR(liveQuote.open)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary">High</div>
            <div className="text-sm font-semibold text-bull tabular-nums">{formatINR(liveQuote.high)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary">Low</div>
            <div className="text-sm font-semibold text-bear tabular-nums">{formatINR(liveQuote.low)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary">Vol</div>
            <div className="text-sm font-semibold text-text-secondary tabular-nums">{formatVolume(liveQuote.volume)}</div>
          </div>
        </>
      )}

      {/* ATR Target/Stop — only when AI decision is active */}
      {hasDecision && (
        <>
          <div className="border-l border-border-default pl-4">
            <div className="text-[10px] uppercase tracking-wider text-text-secondary">Target</div>
            <div className={`text-sm font-semibold tabular-nums ${targetPrice ? 'text-bull' : 'text-text-muted'}`}>{targetDisplay}</div>
            {targetPrice && entryPrice && (
              <div className="text-[9px] text-bull tabular-nums">
                +{(((targetPrice - entryPrice) / entryPrice) * 100).toFixed(1)}%
              </div>
            )}
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary">Stop</div>
            <div className={`text-sm font-semibold tabular-nums ${stopPrice ? 'text-bear' : 'text-text-muted'}`}>{stopDisplay}</div>
            {stopPrice && entryPrice && (
              <div className="text-[9px] text-bear tabular-nums">
                {(((stopPrice - entryPrice) / entryPrice) * 100).toFixed(1)}%
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
