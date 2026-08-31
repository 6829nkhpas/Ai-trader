'use client';

import React from 'react';
import { Radio, Sparkles } from 'lucide-react';
import type { MultiTfChartPatterns } from '../../../../store/useQuantStore';
import {
  bestPatternTimeframe,
  formingPatternCount,
  patternCountFor,
  totalPatternCount,
} from '../patternsSummary';
import SummaryStrip from './SummaryStrip';

export interface PatternsStripProps {
  multiTfPatterns: MultiTfChartPatterns[] | null;
  isLoading: boolean;
  error: string | null;
  onClick: () => void;
}

export default function PatternsStrip({
  multiTfPatterns,
  isLoading,
  error,
  onClick,
}: PatternsStripProps) {
  const total = totalPatternCount(multiTfPatterns);
  const forming = formingPatternCount(multiTfPatterns);
  const bestTf = bestPatternTimeframe(multiTfPatterns);
  const bestTfCount = patternCountFor(multiTfPatterns, bestTf);

  // Error outranks a zero count, and this is the whole point of the state.
  // `fetchMultiTfPatterns` used to swallow failures into an empty list, so an
  // unreachable tool-server rendered as the reassuring "No patterns forming" — a
  // broken scan reading as a calm market. The strip must not reintroduce that.
  const state = isLoading
    ? 'loading'
    : error
      ? 'error'
      : multiTfPatterns && total > 0
        ? 'ready'
        : 'empty';

  return (
    <SummaryStrip
      icon={<Sparkles size={10} />}
      label="Patterns"
      state={state}
      onClick={onClick}
      loadingMessage="Scanning"
      errorMessage={error ?? undefined}
      emptyMessage={multiTfPatterns ? 'None forming' : 'Not scanned'}
      valueText={
        total > 0
          ? `${total} pattern${total === 1 ? '' : 's'}${forming > 0 ? `, ${forming} still forming` : ''}, strongest on the ${bestTf} timeframe`
          : undefined
      }
      value={
        total > 0 ? (
          <span className="flex items-center gap-1.5">
            {forming > 0 && (
              <span
                title={`${forming} of ${total} still forming`}
                className="flex items-center gap-0.5 rounded-none border border-neutral/40 bg-neutral/12 px-1 py-px text-[7.5px] font-black uppercase tracking-wider text-neutral"
              >
                <Radio size={7} aria-hidden="true" />
                {forming}
              </span>
            )}
            <span className="text-[11px] font-black tabular-nums text-text-primary">{total}</span>
            <span className="text-[8px] font-bold uppercase tracking-wider text-text-muted">
              {total === 1 ? 'pattern' : 'patterns'}
            </span>
          </span>
        ) : null
      }
      detail={
        total > 0 ? (
          <p className="truncate text-[9px] leading-snug text-text-muted">
            Strongest on{' '}
            <span className="font-bold text-text-secondary">{bestTf}</span>
            {bestTfCount > 0 && <span> · {bestTfCount} there</span>}
          </p>
        ) : null
      }
    />
  );
}
