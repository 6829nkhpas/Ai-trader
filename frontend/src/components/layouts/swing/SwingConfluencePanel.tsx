'use client';

import React, { useEffect, useRef, useState, useMemo } from 'react';
import { Loader2, TrendingUp, TrendingDown, Minus, Sparkles, Activity, Newspaper, AlertTriangle, RefreshCw } from 'lucide-react';
import { motion } from 'framer-motion';
import { MarketInsight, useTradeStore } from '../../../store/useTradeStore';
import { useMultiTimeframeTrend, TrendBias } from '../../../hooks/useMultiTimeframeTrend';
import { useQuantStore, sentimentSubject } from '../../../store/useQuantStore';
import ClockIcon from '../ClockIcon';
import InsightCard from './InsightCard';
import { staggerContainer, fadeInUp } from '../../../lib/motionVariants';

function getBiasTheme(bias: TrendBias) {
  switch (bias) {
    case 'BULLISH':
      return {
        text: 'text-emerald-600 dark:text-emerald-400',
        bg: 'bg-emerald-500/10 dark:bg-emerald-500/10 border-emerald-500/30 dark:border-emerald-500/20',
        bar: 'bg-emerald-500 dark:bg-emerald-400'
      };
    case 'BEARISH':
      return {
        text: 'text-rose-600 dark:text-rose-400',
        bg: 'bg-rose-500/10 dark:bg-rose-500/10 border-rose-500/30 dark:border-rose-500/20',
        bar: 'bg-rose-500 dark:bg-rose-400'
      };
    default:
      return {
        text: 'text-amber-600 dark:text-amber-400',
        bg: 'bg-amber-500/10 dark:bg-amber-500/10 border-amber-500/30 dark:border-amber-500/20',
        bar: 'bg-amber-500 dark:bg-amber-400'
      };
  }
}

function sentimentColor(s: number) {
  return s >= 65 ? 'text-emerald-600 dark:text-emerald-400' : s >= 40 ? 'text-amber-600 dark:text-amber-400' : 'text-rose-600 dark:text-rose-400';
}

function sentimentBarColor(s: number) {
  return s >= 65 ? 'bg-emerald-500' : s >= 40 ? 'bg-amber-500' : 'bg-rose-500';
}

function sentimentLabel(s: number) {
  if (s >= 80) return 'Extreme Greed';
  if (s >= 65) return 'Bullish';
  if (s >= 40) return 'Neutral';
  if (s >= 25) return 'Bearish';
  return 'Extreme Fear';
}

export default function SwingConfluencePanel() {
  const latestInsight = useTradeStore((s) => s.latestInsight);
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const timeframeTrends = useMultiTimeframeTrend();
  
  const activeSentiment = useQuantStore((s) => s.activeSentiment);
  const loadSentimentForSymbol = useQuantStore((s) => s.loadSentimentForSymbol);
  // The panel used to subscribe to `activeSentiment` ONLY, so a 503 from
  // /api/sentiment ("no sentiment computed yet") rendered a permanent
  // "Awaiting market sentiment signals..." with no spinner and no error — the
  // feature looked idle rather than unavailable (BUG-007). The store already
  // tracks both of these; they just were not being read.
  const isFetchingSentiment = useQuantStore((s) => s.isFetchingSentiment);
  const sentimentError = useQuantStore((s) => s.sentimentError);

  const [insightHistory, setInsightHistory] = useState<MarketInsight[]>([]);
  const [newestId, setNewestId] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Trigger loading of AI News Sentiment from the store
  useEffect(() => {
    if (selectedSymbol) {
      loadSentimentForSymbol(selectedSymbol);
    }
  }, [selectedSymbol, loadSentimentForSymbol]);

  // Reset insight history when selectedSymbol changes
  useEffect(() => {
    setInsightHistory([]);
  }, [selectedSymbol]);

  // Handle live ticks from WebSocket
  useEffect(() => {
    if (!latestInsight || !latestInsight.headline) return;

    setInsightHistory((prev) => {
      // Skip duplicates (same timestamp + symbol)
      if (prev.length > 0) {
        const last = prev[0];
        if (last.timestamp_ms === latestInsight.timestamp_ms && last.symbol === latestInsight.symbol) {
          return prev;
        }
      }
      return [latestInsight, ...prev].slice(0, 20);
    });

    setNewestId(latestInsight.timestamp_ms);
    const timer = setTimeout(() => setNewestId(null), 3000);
    return () => clearTimeout(timer);
  }, [latestInsight]);

  // Determine active score & display payload.
  //
  // The payload is keyed by the NEWS SUBJECT, not by the charted symbol: news on
  // RELIANCE26AUG1290CE is news on RELIANCE. Comparing against the raw
  // `selectedSymbol` would never match for an F&O contract and this panel would
  // sit on "Awaiting market sentiment signals..." forever while a perfectly good
  // verdict was in the store.
  const sentimentPayload = useMemo(() => {
    const subject = sentimentSubject(selectedSymbol).toUpperCase();
    if (activeSentiment && activeSentiment.symbol.toUpperCase() === subject) {
      return activeSentiment;
    }
    return null;
  }, [activeSentiment, selectedSymbol]);

  const score = sentimentPayload ? Math.round((sentimentPayload.score + 100) / 2) : null;

  return (
    <div id="swing-confluence-panel" className="flex h-full flex-col rounded-none border-0 bg-surface text-sm select-none overflow-hidden">
      
      {/* ── Multi-Timeframe Trend ────────────────────────────── */}
      <div className="shrink-0 flex flex-col border-b border-border-default/80 pb-2">
        <div className="px-3 pt-2.5 pb-1.5 flex items-center justify-between">
          <h3 className="text-[10px] font-black text-text-muted/90 uppercase tracking-widest flex items-center gap-1.5">
            <Activity size={10} className="text-cyan-600 dark:text-cyan-400" />
            Multi-Timeframe Trend
          </h3>
        </div>
        
        <motion.div variants={staggerContainer} initial="hidden" animate="show" className="flex flex-col gap-1.5 px-3">
          {timeframeTrends.map((t) => {
            const theme = getBiasTheme(t.bias);
            return (
              <motion.div 
                key={t.timeframe}
                variants={fadeInUp}
                className="flex items-center justify-between py-1 px-2 rounded-lg border border-border-default/60 bg-card/70 shadow-xs"
              >
                <div className="flex items-center gap-2.5 min-w-8">
                  <span className="text-[11px] font-extrabold text-text-secondary leading-none">{t.timeframe}</span>
                </div>
                
                {/* Compact progress bar */}
                <div className="flex-1 max-w-35 mx-2 h-1.5 rounded-full bg-elevated/80 overflow-hidden">
                  <motion.div
                    className={`h-full rounded-full ${theme.bar}`}
                    initial={{ width: 0 }}
                    animate={{ width: `${t.strength}%` }}
                    transition={{ type: 'spring', stiffness: 100, damping: 15, delay: 0.1 }}
                  />
                </div>
                
                {/* Bias Pill Badge */}
                <span className={`inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[8.5px] font-extrabold border leading-none tracking-wide ${theme.bg} ${theme.text}`}>
                  {t.bias === 'BULLISH' ? <TrendingUp size={8} /> : t.bias === 'BEARISH' ? <TrendingDown size={8} /> : <Minus size={8} />}
                  {t.bias}
                </span>
              </motion.div>
            );
          })}
        </motion.div>
      </div>

      {/* ── AI News Sentiment ────────────────────────────────── */}
      <div className="shrink-0 flex flex-col border-b border-border-default/80 pb-2">
        <div className="flex items-center justify-between px-3 pt-2 pb-1.5">
          <h3 className="text-[10px] font-black text-text-muted/90 uppercase tracking-widest flex items-center gap-1.5">
            <Newspaper size={10} className="text-emerald-600 dark:text-emerald-400" />
            AI News Sentiment
          </h3>
          <div className="flex items-center gap-1">
            {score !== null ? (
              <>
                <span className={`text-[12px] font-black tabular-nums ${sentimentColor(score)}`}>{score}</span>
                <span className="text-[8.5px] text-text-muted/60 font-medium">/100</span>
              </>
            ) : (
              <span className="text-[9px] text-text-muted/60 italic font-medium">—</span>
            )}
          </div>
        </div>

        {/* Sentiment Gauge Bar */}
        <div className="px-3 mb-1.5">
          <div className="h-1.5 w-full rounded-full bg-elevated/80 overflow-hidden relative">
            {score !== null ? (
              <div
                className={`h-full rounded-full transition-all duration-500 ease-out ${sentimentBarColor(score)}`}
                style={{ width: `${score}%` }}
              />
            ) : (
              <div className="h-full w-0 rounded-full" />
            )}
          </div>
          <div className="flex justify-between mt-1 text-[8px] font-bold tracking-wider leading-none">
            <span className="text-rose-600/80 dark:text-rose-500/70 uppercase">Fear</span>
            {score !== null && (
              <span className={`uppercase font-extrabold ${sentimentColor(score)}`}>{sentimentLabel(score)}</span>
            )}
            <span className="text-emerald-600/80 dark:text-emerald-500/70 uppercase">Greed</span>
          </div>
        </div>

        {/* Active Sentiment News Card */}
        <div className="px-3">
          {sentimentPayload ? (
            <div className="rounded-lg border border-border-default/60 bg-card p-2 text-left shadow-xs">
              <div className="flex items-center gap-1 mb-1">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="animate-pulse absolute inline-flex h-full w-full rounded-full bg-cyan-500/60"></span>
                  <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-cyan-500"></span>
                </span>
                <span className="text-[8px] font-extrabold text-cyan-600 dark:text-cyan-400 uppercase tracking-widest leading-none">Latest Catalyst</span>
                {/* An option contract's catalyst is its underlying's news — say
                    so rather than letting the headline read as the contract's own. */}
                {sentimentPayload.symbol.toUpperCase() !== selectedSymbol.trim().toUpperCase() && (
                  <span
                    title={`No news is published about ${selectedSymbol}. This catalyst is news about its underlying, ${sentimentPayload.symbol}.`}
                    className="ml-auto text-[7.5px] font-bold uppercase tracking-wider text-text-muted leading-none"
                  >
                    on {sentimentPayload.symbol}
                  </span>
                )}
              </div>
              <p className="text-[10px] font-bold text-text-primary leading-snug line-clamp-1">
                {sentimentPayload.top_headline}
              </p>
              {sentimentPayload.headlines && sentimentPayload.headlines.length > 1 && (
                <div className="mt-1 pt-1 border-t border-border-default/30 space-y-0.5">
                  {sentimentPayload.headlines.slice(1, 3).map((headline, idx) => (
                    <div key={idx} className="text-[9px] text-text-muted/90 flex items-start gap-1">
                      <span className="text-cyan-600/60 dark:text-cyan-400/50 leading-none">•</span>
                      <span className="line-clamp-1 leading-none">{headline}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : isFetchingSentiment ? (
            /* Loading — the classifier can take up to ~25s on a cold symbol. */
            <div className="rounded-lg border border-dashed border-border-default/70 bg-card/50 p-2.5">
              <div className="flex items-center gap-1.5">
                <Loader2 size={9} className="animate-spin text-cyan-600 dark:text-cyan-400" />
                <span className="text-[8px] font-extrabold uppercase tracking-widest text-cyan-600 dark:text-cyan-400 leading-none">
                  Classifying news
                </span>
              </div>
              <div className="mt-1.5 space-y-1">
                <div className="h-2 w-full animate-pulse rounded bg-elevated/60" />
                <div className="h-2 w-3/5 animate-pulse rounded bg-elevated/40" />
              </div>
            </div>
          ) : sentimentError ? (
            /* Unavailable — say so, and say why. Silence here reads as "no news
               exists", which is a much stronger and usually wrong claim. */
            <div
              role="status"
              className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-2.5 text-left"
            >
              <div className="flex items-center gap-1.5">
                <AlertTriangle size={9} className="shrink-0 text-amber-500 dark:text-amber-400" />
                <span className="text-[8px] font-extrabold uppercase tracking-widest text-amber-600 dark:text-amber-400 leading-none">
                  Sentiment unavailable
                </span>
              </div>
              <p className="mt-1 text-[9px] leading-normal text-amber-700/90 dark:text-amber-300/80">
                {sentimentError}
              </p>
              <button
                type="button"
                onClick={() => { if (selectedSymbol) void loadSentimentForSymbol(selectedSymbol); }}
                className="mt-1.5 inline-flex items-center gap-1 rounded border border-amber-500/30 px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-wider text-amber-600 dark:text-amber-400 transition-colors hover:bg-amber-500/10"
              >
                <RefreshCw size={8} />
                Retry
              </button>
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-border-default/70 bg-card/50 p-2.5 text-center">
              <p className="text-[9.5px] text-text-muted/80 leading-normal">
                {selectedSymbol
                  ? 'Awaiting market sentiment signals...'
                  : 'Select a symbol to load sentiment.'}
              </p>
            </div>
          )}
        </div>
      </div>

      {/* ── Scrollable Anomalies Feed ──────────────────────── */}
      <div className="flex flex-1 min-h-0 flex-col">
        <div className="px-3 pt-2 pb-1.5 shrink-0 flex items-center justify-between">
          <h3 className="text-[10px] font-black text-text-muted/90 uppercase tracking-widest flex items-center gap-1.5">
            <Sparkles size={10} className="text-purple-600 dark:text-purple-400" />
            Quant Anomalies
          </h3>
        </div>

        <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto scrollbar-thin px-3 pb-3 space-y-0">
          {insightHistory.length > 0 ? (
            insightHistory.map((insight, i) => (
              <InsightCard
                key={`${insight.timestamp_ms}-${insight.symbol}`}
                insight={insight}
                isNew={newestId === insight.timestamp_ms && i === 0}
                index={i}
              />
            ))
          ) : (
            <div className="flex flex-col items-center gap-1.5 py-4 px-3 border border-dashed border-border-default/70 rounded-xl bg-card/50 text-center animate-in fade-in duration-200">
              <p className="text-[11px] font-black text-text-secondary tracking-tight">Awaiting Anomalies...</p>
              <p className="text-[9px] text-text-muted/80 leading-relaxed max-w-52.5">
                Quantitative insights trigger when a <span className="text-emerald-600 dark:text-emerald-400 font-bold">≥2% price swing</span> is detected.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
