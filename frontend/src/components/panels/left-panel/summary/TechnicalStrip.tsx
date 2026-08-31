'use client';

import React from 'react';
import { AlertTriangle, TrendingUp } from 'lucide-react';
import type { ConsensusReport } from '../../../../store/useQuantStore';
import {
  CONSENSUS_STALE_AFTER_MS,
  consensusMatchesSymbol,
  formatAge,
  trendBg,
  trendColor,
  trendGaugePercent,
  trendVerdict,
} from '../consensusView';
import SummaryStrip from './SummaryStrip';

export interface TechnicalStripProps {
  symbol?: string | null;
  consensus: ConsensusReport | null;
  /** When `consensus` was computed (epoch ms), or null if unknown. */
  computedAt?: number | null;
  onClick: () => void;
  /** Injectable clock, so the staleness boundary is testable without faking timers. */
  now?: number;
}

export default function TechnicalStrip({
  symbol,
  consensus,
  computedAt,
  onClick,
  now,
}: TechnicalStripProps) {
  // The store retains the last report no matter what is charted now, so a report
  // for another symbol is treated as no report at all. Showing RELIANCE's trend
  // score under TCS would be worse than showing nothing.
  const matches = consensus ? consensusMatchesSymbol(consensus.symbol, symbol) : false;
  const report = matches ? consensus : null;

  // Re-tick while the panel sits open so the age label stays truthful rather than
  // freezing at whatever it read on mount.
  const [tick, setTick] = React.useState(() => Date.now());
  React.useEffect(() => {
    if (!computedAt || now !== undefined) return;
    const id = setInterval(() => setTick(Date.now()), 30_000);
    return () => clearInterval(id);
  }, [computedAt, now]);

  const clock = now ?? tick;
  const ageMs = report && computedAt ? Math.max(0, clock - computedAt) : null;
  const isStale = ageMs !== null && ageMs > CONSENSUS_STALE_AFTER_MS;

  if (!report) {
    return (
      <SummaryStrip
        icon={<TrendingUp size={10} />}
        label="Technical Consensus"
        state="empty"
        // A call to action, not a placeholder: the reading is absent because
        // nothing has computed it yet, and the user is the one who triggers it.
        emptyMessage="Run Deep Quant"
        onClick={onClick}
      />
    );
  }

  const score = report.trend_score;
  const verdict = trendVerdict(score);

  return (
    <SummaryStrip
      icon={<TrendingUp size={10} />}
      label="Technical Consensus"
      onClick={onClick}
      badge={
        isStale ? (
          <span
            title={
              computedAt
                ? `Computed at ${new Date(computedAt).toLocaleTimeString()}. Re-run analysis to refresh.`
                : undefined
            }
            className="inline-flex items-center gap-0.5 rounded-none border border-neutral/40 bg-neutral/10 px-1 py-px text-[7.5px] font-bold uppercase tracking-wider text-neutral"
          >
            <AlertTriangle size={7} aria-hidden="true" />
            {ageMs !== null ? formatAge(ageMs) : 'stale'}
          </span>
        ) : null
      }
      valueText={`${verdict}, score ${score > 0 ? 'plus ' : score < 0 ? 'minus ' : ''}${Math.abs(score)}${
        isStale && ageMs !== null
          ? `, previous reading from ${formatAge(ageMs)}`
          : ageMs !== null
            ? `, measured ${formatAge(ageMs)}`
            : ''
      }`}
      value={
        <span className="flex items-center gap-1.5">
          <span className={`text-[11px] font-black tabular-nums ${trendColor(score)}`}>
            {score > 0 ? '+' : ''}
            {score}
          </span>
          <span className={`text-[8px] font-bold uppercase tracking-wider ${trendColor(score)}`}>
            {verdict}
          </span>
        </span>
      }
      detail={
        <div className="relative h-1 w-full overflow-hidden rounded-none border border-border-default/50 bg-elevated/60">
          <div
            className={`h-full ${trendBg(score)} ${isStale ? 'opacity-50' : ''}`}
            style={{ width: `${trendGaugePercent(score)}%` }}
          />
          {/* Neutral datum, so a half-filled bar is not mistaken for "no signal". */}
          <div className="absolute left-1/2 top-0 h-full w-px -translate-x-px bg-text-muted/40" />
        </div>
      }
    />
  );
}
