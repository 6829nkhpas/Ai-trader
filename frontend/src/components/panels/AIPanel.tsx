'use client';

import React from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import AgentStatusPanel from './AgentStatusPanel';

const clampScore = (value: number) => Math.max(0, Math.min(100, value));

export default function AIPanel() {
  const { activeDecision, liveDecisions, selectedSymbol } = useTradeStore();
  const latestDecision = activeDecision ?? liveDecisions[liveDecisions.length - 1] ?? null;

  const rawScore = Math.round(latestDecision?.final_conviction_score ?? 0);
  const score = clampScore(rawScore);
  const action = latestDecision?.action_type ?? 'HOLD';
  // These two are REAL values off the decision: the fusion weights the
  // aggregator actually applied to each signal family for this decision.
  const technicalScore = clampScore(Math.round((latestDecision?.technical_weight_used ?? 0) * 100));
  const newsScore = clampScore(Math.round((latestDecision?.sentiment_weight_used ?? 0) * 100));
  //
  // There used to be two more bars here, "Options" and "Volume":
  //   optionsScore = score * 0.55 + technicalScore * 0.45
  //   volumeScore  = score * 0.45 + newsScore    * 0.55
  //
  // Neither reads an options or volume input. They are arithmetic on the other
  // two numbers, rendered in the same factor list and therefore indistinguishable
  // from measured values — this is the "mock data" in the factor breakdown. The
  // decision payload carries no options or volume weight, so there is nothing
  // honest to put in their place and they are gone. Add them back only when the
  // aggregator actually emits them.

  const tone = action === 'BUY' ? 'Bullish' : action === 'SELL' ? 'Bearish' : 'Neutral';
  
  // Real-time commentary from live decisions
  const headline = React.useMemo(() => {
    if (!latestDecision) {
      return `Awaiting live quant decisions for ${selectedSymbol} from the Aggregator fusion engine...`;
    }
    const raw = latestDecision.reasoning?.trim() || '';
    if (raw && raw !== 'Live backend decision' && !raw.includes('without a reasoning string') && raw.length > 5) {
      return raw;
    }
    return `Quant decision: ${action} with ${score}% conviction at ₹${latestDecision.price?.toLocaleString('en-IN', { minimumFractionDigits: 2 }) || '—'}.`;
  }, [latestDecision, selectedSymbol, action, score]);

  const timestamp = latestDecision ? new Date(latestDecision.timestamp_ms).toLocaleTimeString() : '--:--';

  const insights = latestDecision
    ? [
      `Conviction ${score}% with ${tone.toLowerCase()} bias.`,
      `Technical weight ${technicalScore}% and sentiment ${newsScore}%.`,
      latestDecision.price ? `Last execution price ₹${latestDecision.price.toLocaleString('en-IN', { minimumFractionDigits: 2 })}.` : 'Live price pending.',
    ]
    : ['Connect to the live feed for AI insights.'];

  // Labelled as WEIGHTS, because that is what they are — how much the fusion
  // engine leaned on each signal family for this decision. "News: 40" previously
  // read as a news sentiment score of 40/100, which it never was.
  const factors = [
    { label: 'Sentiment weight', value: newsScore },
    { label: 'Technical weight', value: technicalScore },
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
        {/* Was titled "News". Its body is the DECISION's reasoning string (see
            `headline` above), never a news item — so a user reading this panel
            was told the engine's rationale under a News heading. The sentiment
            headlines live in the left panel's Sentiment block, fed by
            /api/sentiment. */}
        <div className="text-xs font-semibold uppercase tracking-widest text-text-secondary">Decision Rationale</div>
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
