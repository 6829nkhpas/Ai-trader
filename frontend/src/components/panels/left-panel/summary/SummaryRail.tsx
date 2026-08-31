'use client';

import React from 'react';
import type { AnalysisTab } from '../AnalysisSheet';
import type {
  ConsensusReport,
  MultiTfChartPatterns,
  SentimentPayload,
} from '../../../../store/useQuantStore';
import SentimentStrip from './SentimentStrip';
import TechnicalStrip from './TechnicalStrip';
import PatternsStrip from './PatternsStrip';

export interface SummaryRailProps {
  symbol?: string;
  onOpen: (tab: AnalysisTab) => void;

  sentiment: SentimentPayload | null;
  isSentimentLoading: boolean;
  sentimentError: string | null;

  consensus: ConsensusReport | null;
  consensusComputedAt: number | null;

  multiTfPatterns: MultiTfChartPatterns[] | null;
  isPatternsLoading: boolean;
  patternsError: string | null;
}

/**
 * The three analytical readings, one line each, pinned below the watchlist.
 *
 * `shrink-0` is the point of this container. The rail is the only entry point to
 * the detail sheet, so it must not be scrolled out of reach by a long watchlist —
 * which is exactly what happened when these three sections were tall blocks
 * sharing one scroll container with the list above them.
 */
export default function SummaryRail({
  symbol,
  onOpen,
  sentiment,
  isSentimentLoading,
  sentimentError,
  consensus,
  consensusComputedAt,
  multiTfPatterns,
  isPatternsLoading,
  patternsError,
}: SummaryRailProps) {
  return (
    <div
      aria-label="Analysis summary"
      className="shrink-0 border-t border-border-default bg-surface"
    >
      <SentimentStrip
        symbol={symbol}
        sentiment={sentiment}
        isLoading={isSentimentLoading}
        error={sentimentError}
        onClick={() => onOpen('sentiment')}
      />

      <TechnicalStrip
        symbol={symbol}
        consensus={consensus}
        computedAt={consensusComputedAt}
        onClick={() => onOpen('technical')}
      />

      <PatternsStrip
        multiTfPatterns={multiTfPatterns}
        isLoading={isPatternsLoading}
        error={patternsError}
        onClick={() => onOpen('patterns')}
      />
    </div>
  );
}
