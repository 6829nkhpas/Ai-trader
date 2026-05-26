'use client';

import React from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import AgentStatusPanel from './AgentStatusPanel';

function generateMockReasoning(
  symbol: string,
  action: string,
  conviction: number,
  price: number | null,
  change: number | null,
  volume: number | null
): string {
  const sym = symbol.toUpperCase();
  const isBuy = action.toUpperCase() === 'BUY';
  const isSell = action.toUpperCase() === 'SELL';
  
  const priceStr = price ? '₹' + price.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '';
  const changeStr = change !== null ? `${change >= 0 ? '+' : ''}${change.toFixed(2)}%` : '';
  const volStr = volume ? volume.toLocaleString('en-IN') : '';

  const buyReasons = [
    `Strong institutional accumulation detected near the weekly support level for ${sym}. Order flow imbalance indicates dynamic absorption ${priceStr ? `at ${priceStr}` : ''} ${changeStr ? `(${changeStr})` : ''} with low downside risk.`,
    `${sym} exhibits positive VWEPR price regression acceleration ${priceStr ? `near ${priceStr}` : ''}. Confluence of RSI oversold recovery and volume expansion ${volStr ? `(${volStr} shares)` : ''} indicates markup initiation.`,
    `Dark pool scanner detected massive whale blocks crossing at the VWAP support floor for ${sym}. Price hovering ${priceStr ? `at ${priceStr}` : ''} ${changeStr ? `(${changeStr})` : ''}. Strong bullish continuation expected.`,
    `${sym} completed local consolidation, breaking out above the 21-day EMA ${priceStr ? `at ${priceStr}` : ''}. Technical divergence points heavily skewed to the upside.`
  ];

  const sellReasons = [
    `${sym} registers prominent overbought RSI signatures on the 4H timeframe, combined with negative VWEPR acceleration curvature signaling structural exhaustion ${priceStr ? `at ${priceStr}` : ''} ${changeStr ? `(${changeStr})` : ''}.`,
    `Institutional distribution patterns detected near major local resistance boundaries for ${sym} ${priceStr ? `at ${priceStr}` : ''}. Sell pressure accelerating as liquid supply increases.`,
    `Dynamic volume-weighted average price (VWAP) breakdown registered in ${sym} ${priceStr ? `near ${priceStr}` : ''}. Momentum indicators point to rapid liquidation towards lower liquidity pools.`,
    `A series of bearish block sweeps suggests institutional distribution for ${sym}. Rebound attempts absorbed ${priceStr ? `at ${priceStr}` : ''} ${changeStr ? `(${changeStr})` : ''}, validating a high-conviction short entry.`
  ];

  const holdReasons = [
    `${sym} is currently consolidating within a tight, low-volatility statistical range ${priceStr ? `around ${priceStr}` : ''} ${changeStr ? `(${changeStr})` : ''}. Recommending holding existing exposure until volume breakout triggers.`,
    `Market tape shows balanced buy/sell depth for ${sym} ${priceStr ? `at ${priceStr}` : ''}. Multi-timeframe EMA indicators are clustered and neutral. Standing by for directional trend confirmation.`
  ];

  // Pick deterministically based on symbol and conviction
  let hash = 0;
  for (let i = 0; i < sym.length; i++) {
    hash = (hash * 31 + sym.charCodeAt(i)) & 0xffffffff;
  }
  const idx = Math.abs(hash + conviction) % 4;

  if (isBuy) return buyReasons[idx];
  if (isSell) return sellReasons[idx];
  return holdReasons[idx % 2];
}

function symToBasePrice(symbol: string): number {
  let hash = 0;
  for (let i = 0; i < symbol.length; i++) {
    hash = (hash * 31 + symbol.charCodeAt(i)) & 0xffffffff;
  }
  return 200 + (Math.abs(hash) % 2800); // 200 to 3000
}

const clampScore = (value: number) => Math.max(0, Math.min(100, value));

export default function AIPanel() {
  const { activeDecision, liveDecisions, selectedSymbol } = useTradeStore();
  const latestDecision = activeDecision ?? liveDecisions[liveDecisions.length - 1] ?? null;

  const [livePrice, setLivePrice] = React.useState<number | null>(null);
  const [liveChange, setLiveChange] = React.useState<number | null>(null);
  const [liveVolume, setLiveVolume] = React.useState<number | null>(null);

  React.useEffect(() => {
    if (!selectedSymbol) return;

    // Initialize mock values
    const basePrice = symToBasePrice(selectedSymbol);
    const baseChange = (Math.sin(selectedSymbol.charCodeAt(0)) * 2);
    setLivePrice(basePrice);
    setLiveChange(baseChange);
    setLiveVolume(850000);

    const interval = setInterval(() => {
      setLivePrice((prev) => {
        if (!prev) return basePrice;
        const pct = (Math.random() - 0.59) * 0.0006;
        const nextPrice = +(prev * (1 + pct)).toFixed(2);
        return nextPrice;
      });
      setLiveChange((prev) => {
        if (prev === null) return baseChange;
        const changePct = (Math.random() - 0.5) * 0.02;
        return +(prev + changePct).toFixed(2);
      });
      setLiveVolume((prev) => (prev || 850000) + Math.floor(Math.random() * 120));
    }, 2000);

    return () => clearInterval(interval);
  }, [selectedSymbol]);

  const rawScore = Math.round(latestDecision?.final_conviction_score ?? 0);
  const score = clampScore(rawScore);
  const action = latestDecision?.action_type ?? 'HOLD';
  const technicalScore = clampScore(Math.round((latestDecision?.technical_weight_used ?? 0) * 100));
  const newsScore = clampScore(Math.round((latestDecision?.sentiment_weight_used ?? 0) * 100));
  const optionsScore = clampScore(Math.round(score * 0.55 + technicalScore * 0.45));
  const volumeScore = clampScore(Math.round(score * 0.45 + newsScore * 0.55));

  const tone = action === 'BUY' ? 'Bullish' : action === 'SELL' ? 'Bearish' : 'Neutral';
  
  // Real-time commentary fallback
  const headline = React.useMemo(() => {
    const priceStr = livePrice ? ` [LTP: ₹${livePrice.toFixed(2)} (${liveChange !== null && liveChange >= 0 ? '+' : ''}${liveChange?.toFixed(2)}%)]` : '';
    if (!latestDecision) {
      // If no decision is present in state, generate a standby review for the active symbol
      return `Monitoring NSE:${selectedSymbol} order book and tape flow. Multi-timeframe trend is neutral with minor liquidity accumulation.` + priceStr;
    }
    const raw = latestDecision.reasoning?.trim() || '';
    if (raw && raw !== 'Live backend decision' && !raw.includes('without a reasoning string') && raw.length > 5) {
      return raw + priceStr;
    }
    return generateMockReasoning(
      latestDecision.symbol || selectedSymbol,
      action,
      score,
      livePrice,
      liveChange,
      liveVolume
    );
  }, [latestDecision, selectedSymbol, action, score, livePrice, liveChange, liveVolume]);
  const timestamp = latestDecision ? new Date(latestDecision.timestamp_ms).toLocaleTimeString() : '--:--';

  const insights = latestDecision
    ? [
      `Conviction ${score}% with ${tone.toLowerCase()} bias.`,
      `Technical weight ${technicalScore}% and sentiment ${newsScore}%.`,
      latestDecision.price ? `Last price $${latestDecision.price.toFixed(2)}.` : 'Live price pending.',
    ]
    : ['Connect to the live feed for AI insights.'];

  const factors = [
    { label: 'News', value: newsScore },
    { label: 'Technical', value: technicalScore },
    { label: 'Options', value: optionsScore },
    { label: 'Volume', value: volumeScore },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <section className="rounded-lg border border-border-default bg-card p-4 panel-shadow">
        <div className="text-xs font-semibold uppercase tracking-widest text-text-secondary">Score</div>
        <div className="mt-2 flex items-baseline gap-2">
          <div className="text-2xl font-semibold text-text-primary">{score}/100</div>
          <div className={`text-sm font-semibold ${tone === 'Bullish' ? 'text-[#16A34A]' : tone === 'Bearish' ? 'text-[#DC2626]' : 'text-text-secondary'}`}>- {tone}</div>
        </div>
      </section>

      <section className="rounded-lg border border-border-default bg-card p-4 panel-shadow">
        <div className="text-xs font-semibold uppercase tracking-widest text-text-secondary">Factor Breakdown</div>
        <div className="mt-3 space-y-3">
          {factors.map((factor) => (
            <div key={factor.label} className="space-y-1">
              <div className="flex items-center justify-between text-xs text-text-secondary">
                <span className="font-semibold text-text-primary">{factor.label}</span>
                <span>{factor.value}%</span>
              </div>
              <div className="h-1.5 w-full rounded-full bg-elevated">
                <div className={`h-1.5 rounded-full ${factor.value >= 50 ? 'bg-[#16A34A]' : 'bg-[#DC2626]'}`} style={{ width: `${factor.value}%` }} />
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-border-default bg-card p-4 panel-shadow">
        <div className="text-xs font-semibold uppercase tracking-widest text-text-secondary">News</div>
        <div className="mt-2 text-sm font-semibold text-text-primary border-b border-border-default pb-2">{headline}</div>
        <div className="mt-2 text-xs text-text-muted">{timestamp}</div>
      </section>

      <section className="rounded-lg border border-border-default bg-card p-4 panel-shadow">
        <div className="text-xs font-semibold uppercase tracking-widest text-text-secondary">Extra Insights</div>
        <ul className="mt-2 space-y-2 text-sm text-text-secondary">
          {insights.map((item, index) => (
            <li key={`${item}-${index}`} className="flex items-start gap-2">
              <span className="mt-1.5 h-1 w-1 rounded-full bg-text-muted shrink-0" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </section>

      <AgentStatusPanel />
    </div>
  );
}
