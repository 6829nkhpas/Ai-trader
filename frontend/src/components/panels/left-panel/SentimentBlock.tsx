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
  /**
   * Where this block is rendered.
   *
   * `panel` is the historical 224px sidebar layout and remains the default so
   * existing call sites are untouched. `sheet` is the detail view, which has room
   * to show the headline list without asking for a click first.
   */
  variant?: 'panel' | 'sheet';
}

export default function SentimentBlock({
  sentiment,
  isLoading,
  error,
  symbol,
  variant = 'panel',
}: SentimentBlockProps) {
  const inSheet = variant === 'sheet';
  // The collapse existed because the sidebar had no vertical room. In the sheet
  // it does, so the headlines are open on arrival.
  const [headlinesExpanded, setHeadlinesExpanded] = useState(inSheet);

  // An option contract has no news of its own, so the store looks up its
  // underlying instead (`sentimentSubject` in useQuantStore). Rendering
  // RELIANCE's headlines under the heading RELIANCE26AUG1290CE without saying so
  // would silently attribute one instrument's news to another, so name the
  // subject whenever it is not the symbol the user is looking at.
  const subject = sentiment?.symbol?.trim() ?? '';
  const subjectDiffers =
    !!subject && !!symbol?.trim() && subject.toUpperCase() !== symbol.trim().toUpperCase();

  // Type and spacing scale, applied per variant. The panel sizes are the ones
  // that shipped, so they stay exactly as they were; the sheet is simply allowed
  // the room a 420px column has that a 224px one does not.
  const t = inSheet
    ? {
        rowPadX: 'px-4',
        meta: 'text-[10px]',
        score: 'text-3xl',
        label: 'text-[10px]',
        body: 'text-[11px]',
        toggle: 'text-[9px]',
        headline: 'text-[11px]',
        headlineRowPad: 'px-4 py-2',
        index: 'h-5 w-5 text-[8px]',
      }
    : {
        rowPadX: 'px-3',
        meta: 'text-[8px]',
        score: 'text-xl',
        label: 'text-[8px]',
        body: 'text-[9px]',
        toggle: 'text-[8px]',
        headline: 'text-[9px]',
        headlineRowPad: 'px-3 py-1.5',
        index: 'h-4 w-4 text-[7px]',
      };

  return (
    <div className={`border-b border-border-default px-0 ${inSheet ? 'py-3' : 'py-2.5'}`}>
      <div className={`flex items-center gap-1.5 mb-1.5 ${t.rowPadX}`}>
        <Newspaper size={inSheet ? 12 : 10} className="text-text-muted" />
        {/* In the sheet the dialog header already names the section; repeating it
            here would give the same view two titles. */}
        {!inSheet && (
          <h3 className="text-[9px] font-bold text-text-secondary uppercase tracking-wider">
            AI News Sentiment
          </h3>
        )}
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
          <span className={`ml-auto ${t.meta} text-text-muted tabular-nums`}>
            {sentiment.headlines.length} headlines
          </span>
        )}
      </div>

      {isLoading ? (
        <SentimentSkeleton />
      ) : error ? (
        <div
          className={`flex items-center gap-2 rounded-none py-2 bg-rose-500/5 border-y border-x-0 border-rose-500/20 ${t.rowPadX}`}
        >
          <div className="h-1.5 w-1.5 shrink-0 rounded-none bg-rose-400" />
          {/* In the sheet the message wraps instead of truncating: this is the
              only place the failure is explained in full. */}
          <p
            className={`${t.body} text-rose-300/80 font-medium ${inSheet ? 'break-words' : 'truncate'}`}
          >
            {error}
          </p>
        </div>
      ) : sentiment ? (
        <div className="flex flex-col gap-2">
          {/* ── Summary Score ─────────────────────────────────── */}
          <motion.div
            initial="hidden" animate="show" variants={fadeInUp}
            className={`rounded-none border-y border-x-0 border-border-default bg-elevated/40 ${t.rowPadX} ${inSheet ? 'py-3' : 'py-2'}`}
          >
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center gap-1.5">
                <span className={`${t.score} font-black tabular-nums text-text-primary`}>
                  {sentiment.score > 0 ? '+' : ''}
                  {sentiment.score}
                </span>
                <span
                  className={`inline-flex items-center rounded-none px-1.5 py-0.5 ${t.label} font-bold border border-border-default bg-elevated text-text-primary`}
                >
                  {sentiment.label}
                </span>
              </div>
              <span className="h-1.5 w-1.5 rounded-none bg-text-secondary" />
            </div>
            <p className={`${t.body} leading-relaxed font-medium text-text-secondary`}>
              {sentiment.top_headline}
            </p>
          </motion.div>

          {/* ── Headlines Toggle + Scrollable List ────────────── */}
          {sentiment.headlines.length > 0 && (
            <div className="flex flex-col">
              <button
                type="button"
                onClick={() => setHeadlinesExpanded(!headlinesExpanded)}
                className={`flex w-full items-center justify-between py-1 ${t.rowPadX} ${t.toggle} font-bold uppercase tracking-wider text-text-muted/60 hover:text-text-muted transition-colors`}
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
                    {/* No inner scroll cap in the sheet — the dialog panel owns
                        the scrolling, and a nested one would trap the list in a
                        240px window inside a full-height view. */}
                    <div
                      className={`flex flex-col gap-0 mt-0.5 ${
                        inSheet ? '' : 'max-h-[240px] overflow-y-auto scrollbar-thin'
                      }`}
                    >
                      {sentiment.headlines.map((headline, i) => (
                        <div
                          key={i}
                          className={`group flex items-start gap-1.5 rounded-none border-b border-x-0 border-border-default/40 bg-elevated/10 hover:bg-elevated/20 transition-colors ${t.headlineRowPad}`}
                        >
                          <span
                            className={`flex shrink-0 items-center justify-center rounded-none bg-elevated border border-border-default font-bold text-text-muted mt-px ${t.index}`}
                          >
                            {i + 1}
                          </span>
                          <p
                            className={`${t.headline} leading-snug text-text-secondary group-hover:text-text-primary transition-colors`}
                          >
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
        <div
          className={`flex items-center gap-2 rounded-none py-2 bg-elevated/40 border-y border-x-0 border-border-default ${t.rowPadX}`}
        >
          <div className="h-1.5 w-1.5 rounded-none bg-border-default animate-pulse motion-reduce:animate-none" />
          <p className={`${t.body} text-text-muted/60 italic`}>Select a symbol to load sentiment</p>
        </div>
      )}
    </div>
  );
}
