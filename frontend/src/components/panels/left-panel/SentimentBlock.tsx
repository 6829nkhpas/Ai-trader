'use client';

import React, { useState } from 'react';
import { Newspaper, Loader2, ChevronUp, ChevronDown } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import type { SentimentPayload } from '../../../store/useQuantStore';
import { collapseVariants, fadeInUp } from '../../../lib/motionVariants';
import SentimentSkeleton from './SentimentSkeleton';

interface SentimentBlockProps {
  sentiment: SentimentPayload | null;
  isLoading: boolean;
  error: string | null;
  /**
   * The symbol currently on the chart. Used only to decide whether the verdict
   * has to name its own subject — see `subjectDiffers` below.
   */
  symbol?: string;
}

export default function SentimentBlock({
  sentiment,
  isLoading,
  error,
  symbol,
}: SentimentBlockProps) {
  const [headlinesExpanded, setHeadlinesExpanded] = useState(false);

  // An option contract has no news of its own, so the store looks up its
  // underlying instead (`sentimentSubject` in useQuantStore). Rendering
  // RELIANCE's headlines under the heading RELIANCE26AUG1290CE without saying so
  // would silently attribute one instrument's news to another, so name the
  // subject whenever it is not the symbol the user is looking at.
  const subject = sentiment?.symbol?.trim() ?? '';
  const subjectDiffers =
    !!subject && !!symbol?.trim() && subject.toUpperCase() !== symbol.trim().toUpperCase();

  return (
    <div className="border-b border-border-default py-2.5 px-0">
      <div className="flex items-center gap-1.5 mb-1.5 px-3">
        <Newspaper size={10} className="text-text-muted" />
        <h3 className="text-[9px] font-bold text-text-secondary uppercase tracking-wider">
          AI News Sentiment
        </h3>
        {subjectDiffers && !isLoading && (
          <span
            title={`No news is published about ${symbol}. This verdict is based on news about its underlying, ${subject}.`}
            className="inline-flex items-center rounded-none border border-border-default bg-elevated px-1 py-px text-[7.5px] font-bold uppercase tracking-wider text-text-muted"
          >
            on {subject}
          </span>
        )}
        {isLoading && (
          <Loader2 size={9} className="ml-auto animate-spin text-text-muted" />
        )}
        {sentiment && !isLoading && (
          <span className="ml-auto text-[8px] text-text-muted tabular-nums">
            {sentiment.headlines.length} headlines
          </span>
        )}
      </div>

      {isLoading ? (
        <SentimentSkeleton />
      ) : error ? (
        <div className="flex items-center gap-2 rounded-none px-3 py-2 bg-rose-500/5 border-y border-x-0 border-rose-500/20">
          <div className="h-1.5 w-1.5 rounded-none bg-rose-400" />
          <p className="text-[9px] text-rose-300/80 font-medium truncate">{error}</p>
        </div>
      ) : sentiment ? (
        <div className="flex flex-col gap-2">
          {/* ── Summary Score ─────────────────────────────────── */}
          <motion.div
            initial="hidden" animate="show" variants={fadeInUp}
            className="rounded-none px-3 py-2 border-y border-x-0 border-border-default bg-elevated/40"
          >
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center gap-1.5">
                <span
                  className="text-xl font-black tabular-nums text-text-primary"
                >
                  {sentiment.score > 0 ? '+' : ''}
                  {sentiment.score}
                </span>
                <span className="inline-flex items-center rounded-none px-1.5 py-0.5 text-[8px] font-bold border border-border-default bg-elevated text-text-primary">
                  {sentiment.label}
                </span>
              </div>
              <span className="h-1.5 w-1.5 rounded-none bg-text-secondary" />
            </div>
            <p
              className="text-[9px] leading-relaxed font-medium text-text-secondary"
            >
              {sentiment.top_headline}
            </p>
          </motion.div>

          {/* ── Headlines Toggle + Scrollable List ────────────── */}
          {sentiment.headlines.length > 0 && (
            <div className="flex flex-col">
              <button
                type="button"
                onClick={() => setHeadlinesExpanded(!headlinesExpanded)}
                className="flex w-full items-center justify-between py-1 px-3 text-[8px] font-bold uppercase tracking-wider text-text-muted/60 hover:text-text-muted transition-colors"
              >
                <span>Headlines ({sentiment.headlines.length})</span>
                {headlinesExpanded ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
              </button>

              <AnimatePresence initial={false}>
                {headlinesExpanded && (
                  <motion.div
                    variants={collapseVariants}
                    initial="collapsed"
                    animate="expanded"
                    exit="collapsed"
                    className="overflow-hidden"
                  >
                    <div className="flex flex-col gap-0 max-h-[240px] overflow-y-auto scrollbar-thin mt-0.5">
                      {sentiment.headlines.map((headline, i) => (
                        <div
                          key={i}
                          className="group flex items-start gap-1.5 rounded-none px-3 py-1.5 border-b border-x-0 border-border-default/40 bg-elevated/10 hover:bg-elevated/20 transition-colors"
                        >
                          <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-none bg-elevated border border-border-default text-[7px] font-bold text-text-muted mt-px">
                            {i + 1}
                          </span>
                          <p className="text-[9px] leading-snug text-text-secondary group-hover:text-text-primary transition-colors">
                            {headline}
                          </p>
                        </div>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-2 rounded-none px-3 py-2 bg-elevated/40 border-y border-x-0 border-border-default">
          <div className="h-1.5 w-1.5 rounded-none bg-border-default animate-pulse" />
          <p className="text-[9px] text-text-muted/60 italic">Select a symbol to load sentiment</p>
        </div>
      )}
    </div>
  );
}
