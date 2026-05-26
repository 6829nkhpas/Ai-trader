'use client';

import React, { useState } from 'react';
import { Newspaper, Loader2, ChevronUp, ChevronDown } from 'lucide-react';
import type { SentimentPayload } from '../../../store/useQuantStore';

interface SentimentBlockProps {
  sentiment: SentimentPayload | null;
  isLoading: boolean;
  error: string | null;
}

function sentimentImpactColor(impact: string) {
  switch (impact) {
    case 'positive':
      return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30';
    case 'negative':
      return 'text-rose-400 bg-rose-500/10 border-rose-500/30';
    default:
      return 'text-slate-400 bg-slate-500/10 border-slate-500/30';
  }
}

export default function SentimentBlock({
  sentiment,
  isLoading,
  error,
}: SentimentBlockProps) {
  const [headlinesExpanded, setHeadlinesExpanded] = useState(false);

  return (
    <div className="border-b border-border-default px-3 py-2.5">
      <div className="flex items-center gap-1.5 mb-1.5">
        <Newspaper size={10} className="text-text-muted" />
        <h3 className="text-[9px] font-bold text-text-secondary uppercase tracking-wider">
          AI News Sentiment
        </h3>
        {isLoading && (
          <Loader2 size={9} className="ml-auto animate-spin text-blue-400" />
        )}
        {sentiment && !isLoading && (
          <span className="ml-auto text-[8px] text-text-muted tabular-nums">
            {sentiment.headlines.length} headlines
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 rounded-md px-2 py-2 bg-blue-500/5 border border-blue-500/20">
          <div className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" />
          <p className="text-[9px] text-blue-300/80 font-medium">Analyzing latest news...</p>
        </div>
      ) : error ? (
        <div className="flex items-center gap-2 rounded-md px-2 py-2 bg-rose-500/5 border border-rose-500/20">
          <div className="h-1.5 w-1.5 rounded-full bg-rose-400" />
          <p className="text-[9px] text-rose-300/80 font-medium truncate">{error}</p>
        </div>
      ) : sentiment ? (
        <div className="flex flex-col gap-2">
          {/* ── Summary Score ─────────────────────────────────── */}
          <div
            className={`rounded-lg px-2.5 py-2 border ${
              sentiment.impact === 'positive'
                ? 'border-emerald-500/25 bg-emerald-500/5'
                : sentiment.impact === 'negative'
                ? 'border-rose-500/25 bg-rose-500/5'
                : 'border-slate-500/25 bg-slate-500/5'
            }`}
          >
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center gap-1.5">
                <span
                  className={`text-xl font-black tabular-nums ${
                    sentiment.impact === 'positive'
                      ? 'text-emerald-400'
                      : sentiment.impact === 'negative'
                      ? 'text-rose-400'
                      : 'text-slate-400'
                  }`}
                >
                  {sentiment.score > 0 ? '+' : ''}
                  {sentiment.score}
                </span>
                <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-[8px] font-bold border ${sentimentImpactColor(sentiment.impact)}`}>
                  {sentiment.label}
                </span>
              </div>
              <span
                className={`h-2 w-2 rounded-full ${
                  sentiment.impact === 'positive'
                    ? 'bg-emerald-400 animate-pulse'
                    : sentiment.impact === 'negative'
                    ? 'bg-rose-400 animate-pulse'
                    : 'bg-slate-500'
                }`}
              />
            </div>
            <p
              className={`text-[9px] leading-relaxed font-medium ${
                sentiment.impact === 'positive'
                  ? 'text-emerald-300/90'
                  : sentiment.impact === 'negative'
                  ? 'text-rose-300/90'
                  : 'text-slate-300/90'
              }`}
            >
              {sentiment.top_headline}
            </p>
          </div>

          {/* ── Headlines Toggle + Scrollable List ────────────── */}
          {sentiment.headlines.length > 0 && (
            <div className="flex flex-col">
              <button
                type="button"
                onClick={() => setHeadlinesExpanded(!headlinesExpanded)}
                className="flex w-full items-center justify-between py-1 text-[8px] font-bold uppercase tracking-wider text-text-muted/60 hover:text-text-muted transition-colors"
              >
                <span>Headlines ({sentiment.headlines.length})</span>
                {headlinesExpanded ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
              </button>

              {headlinesExpanded && (
                <div className="flex flex-col gap-0.5 max-h-[240px] overflow-y-auto scrollbar-thin pr-0.5 mt-0.5">
                  {sentiment.headlines.map((headline, i) => (
                    <div
                      key={i}
                      className="group flex items-start gap-1.5 rounded-md px-2 py-1.5 border border-border-default/50 bg-elevated/30 hover:bg-elevated/60 transition-colors"
                    >
                      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded bg-slate-500/15 text-[7px] font-bold text-slate-500 mt-px">
                        {i + 1}
                      </span>
                      <p className="text-[9px] leading-snug text-text-secondary group-hover:text-text-primary transition-colors">
                        {headline}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-2 rounded-md px-2 py-2 bg-elevated/50 border border-border-default">
          <div className="h-1.5 w-1.5 rounded-full bg-slate-500/40 animate-pulse" />
          <p className="text-[9px] text-text-muted/60 italic">Select a symbol to load sentiment</p>
        </div>
      )}
    </div>
  );
}
