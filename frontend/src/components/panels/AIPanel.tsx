'use client';

import React from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import AgentStatusPanel from './AgentStatusPanel';

const clampScore = (value: number) => Math.max(0, Math.min(100, value));

export default function AIPanel() {
  const { activeDecision, liveDecisions } = useTradeStore();
  const latestDecision = activeDecision ?? liveDecisions[liveDecisions.length - 1] ?? null;

  const rawScore = Math.round(latestDecision?.final_conviction_score ?? 0);
  const score = clampScore(rawScore);
  const action = latestDecision?.action_type ?? 'HOLD';
  const technicalScore = clampScore(Math.round((latestDecision?.technical_weight_used ?? 0) * 100));
  const newsScore = clampScore(Math.round((latestDecision?.sentiment_weight_used ?? 0) * 100));
  const optionsScore = clampScore(Math.round(score * 0.55 + technicalScore * 0.45));
  const volumeScore = clampScore(Math.round(score * 0.45 + newsScore * 0.55));

  const tone = action === 'BUY' ? 'Bullish' : action === 'SELL' ? 'Bearish' : 'Neutral';
  const headline = latestDecision?.reasoning?.trim() || 'Awaiting live signal commentary.';
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
