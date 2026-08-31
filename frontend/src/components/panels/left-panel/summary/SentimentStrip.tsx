'use client';

import React from 'react';
import { Newspaper } from 'lucide-react';
import type { SentimentPayload } from '../../../../store/useQuantStore';
import SummaryStrip from './SummaryStrip';

export interface SentimentStripProps {
  /** The symbol currently on the chart — the subject the user believes they are reading about. */
  symbol?: string;
  sentiment: SentimentPayload | null;
  isLoading: boolean;
  error: string | null;
  onClick: () => void;
}

/** Where a -100..+100 score sits on a 0..100 track, with 50 as neutral. */
export function scoreToTrackPercent(score: number): number {
  const clamped = Math.max(-100, Math.min(100, score));
  return (clamped + 100) / 2;
}

function toneFor(score: number): { text: string; bar: string } {
  if (score > 0) return { text: 'text-bull', bar: 'bg-bull' };
  if (score < 0) return { text: 'text-bear', bar: 'bg-bear' };
  return { text: 'text-neutral', bar: 'bg-neutral' };
}

/**
 * The diverging gauge.
 *
 * One score, one bar, growing out of a centre line — NOT a pair of bullish /
 * bearish percentages. `SentimentPayload` carries a single `score`; splitting it
 * into two independent-looking figures would present an invented second
 * measurement as if it had been computed.
 */
function ScoreGauge({ score }: { score: number }) {
  const tone = toneFor(score);
  const trackPercent = scoreToTrackPercent(score);
  const magnitude = Math.abs(trackPercent - 50);
  const left = Math.min(50, trackPercent);

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[8px] font-bold uppercase tracking-wider text-text-muted/60">Bear</span>
      <div
        role="meter"
        aria-valuenow={score}
        aria-valuemin={-100}
        aria-valuemax={100}
        aria-label="News sentiment score"
        className="relative h-1 flex-1 overflow-hidden rounded-none border border-border-default/50 bg-elevated/60"
      >
        <div
          className={`absolute top-0 h-full ${tone.bar}`}
          style={{ left: `${left}%`, width: `${magnitude}%` }}
        />
        {/* Neutral datum. Without it a short bar reads as "slightly bullish"
            regardless of which side of zero it actually sits on. */}
        <div className="absolute left-1/2 top-0 h-full w-px -translate-x-px bg-text-muted/40" />
      </div>
      <span className="text-[8px] font-bold uppercase tracking-wider text-text-muted/60">Bull</span>
    </div>
  );
}

export default function SentimentStrip({
  symbol,
  sentiment,
  isLoading,
  error,
  onClick,
}: SentimentStripProps) {
  // An option contract has no news of its own, so the store scores its
  // underlying instead. Naming the subject is not decoration: an unlabelled
  // RELIANCE verdict under RELIANCE26AUG1290CE silently attributes one
  // instrument's news to another.
  const subject = sentiment?.symbol?.trim() ?? '';
  const subjectDiffers =
    !!subject && !!symbol?.trim() && subject.toUpperCase() !== symbol.trim().toUpperCase();

  const state = isLoading
    ? 'loading'
    : // Error outranks a retained payload. The store keeps the previous reading
      // in `activeSentiment` across a failed fetch, so rendering it here would
      // show a stale score with no indication that the refresh failed.
      error
      ? 'error'
      : sentiment
        ? 'ready'
        : 'empty';

  const tone = sentiment ? toneFor(sentiment.score) : null;

  return (
    <SummaryStrip
      icon={<Newspaper size={10} />}
      label="AI News Sentiment"
      state={state}
      onClick={onClick}
      loadingMessage="Reading news"
      errorMessage={error ?? undefined}
      emptyMessage={symbol ? 'No reading' : 'Select a symbol'}
      badge={
        subjectDiffers && !isLoading ? (
          <span
            title={`No news is published about ${symbol}. This verdict is based on news about its underlying, ${subject}.`}
            className="inline-flex items-center rounded-none border border-border-default bg-elevated px-1 py-px text-[7.5px] font-bold uppercase tracking-wider text-text-muted"
          >
            on {subject}
          </span>
        ) : null
      }
      valueText={
        sentiment
          ? `${sentiment.label}, score ${sentiment.score > 0 ? 'plus ' : sentiment.score < 0 ? 'minus ' : ''}${Math.abs(sentiment.score)}${subjectDiffers ? `, based on news about ${subject}` : ''}`
          : undefined
      }
      value={
        sentiment && tone ? (
          <span className="flex items-center gap-1.5">
            <span className={`text-[11px] font-black tabular-nums ${tone.text}`}>
              {sentiment.score > 0 ? '+' : ''}
              {sentiment.score}
            </span>
            <span className="inline-flex items-center rounded-none border border-border-default bg-elevated px-1 py-px text-[8px] font-bold uppercase tracking-wider text-text-primary">
              {sentiment.label}
            </span>
          </span>
        ) : null
      }
      detail={sentiment ? <ScoreGauge score={sentiment.score} /> : null}
    />
  );
}
