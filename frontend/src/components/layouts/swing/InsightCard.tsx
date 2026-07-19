'use client';

import React, { useState } from 'react';
import { ChevronDown, Zap, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { MarketInsight } from '../../../store/useTradeStore';
import { fadeInUp, collapseVariants } from '../../../lib/motionVariants';

interface InsightCardProps {
  insight: MarketInsight;
  isNew: boolean;
  index: number;
}

function sentimentDotColor(score: number) {
  return score >= 65 ? 'bg-emerald-400' : score >= 40 ? 'bg-amber-400' : 'bg-rose-400';
}

function timeAgo(ms: number): string {
  const secs = Math.floor((Date.now() - ms) / 1000);
  if (secs < 10) return 'just now';
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ago`;
}

export default function InsightCard({ insight, isNew, index }: InsightCardProps) {
  const [expanded, setExpanded] = useState(false);
  const isError = insight.headline === 'LLM API Failure';

  return (
    <motion.div
      variants={fadeInUp}
      initial="hidden"
      animate="show"
      onClick={() => setExpanded(!expanded)}
      className={`
        group relative rounded-xl border border-border-default/60 p-3 mb-2 transition-all duration-200 cursor-pointer
        ${isNew ? 'shadow-[0_0_12px_rgba(16,185,129,0.12)]' : 'shadow-xs'}
        ${isError
          ? 'bg-rose-500/5 hover:bg-rose-500/10 hover:border-rose-500/20'
          : 'bg-card hover:bg-elevated/50 hover:border-emerald-500/30'
        }
      `}
    >
      {/* Pulse effect for live WebSocket ticks */}
      {isNew && !isError && (
        <div className="absolute inset-0 rounded-xl bg-emerald-500/5 animate-pulse pointer-events-none" />
      )}

      <div className="relative flex flex-col gap-2">
        {/* Header line */}
        <div className="flex items-start gap-2.5 justify-between">
          <div className="flex items-start gap-2 min-w-0 flex-1">
            {/* Status dot */}
            <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${sentimentDotColor(insight.sentiment_score)} ${isNew ? 'animate-pulse' : ''}`} />
            
            <p className={`text-[11.5px] font-bold leading-relaxed ${isError ? 'text-rose-600 dark:text-rose-400' : 'text-text-primary group-hover:text-emerald-600 dark:group-hover:text-emerald-400 transition-colors'}`}>
              {insight.headline}
            </p>
          </div>

          <ChevronDown
            size={12}
            className={`shrink-0 mt-1 text-text-muted/50 transition-transform duration-200 ${expanded ? 'rotate-180 text-text-primary' : ''}`}
          />
        </div>

        {/* Metadata pill badges */}
        <div className="flex items-center gap-1.5 flex-wrap text-[9px]">
          <span className="inline-flex items-center gap-1 rounded bg-cyan-500/10 dark:bg-cyan-500/10 px-1.5 py-0.5 font-bold text-cyan-600 dark:text-cyan-400 border border-cyan-500/20">
            <Zap size={8} className="text-cyan-600 dark:text-cyan-400" />
            {insight.symbol}
          </span>
          <span className={`inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 font-bold tabular-nums ${
            insight.anomaly_pct >= 3 ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20' : 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20'
          }`}>
            {insight.anomaly_pct >= 0 ? '+' : ''}{insight.anomaly_pct.toFixed(1)}% Anomaly
          </span>
          <span className="inline-flex items-center gap-1 rounded bg-surface px-1.5 py-0.5 font-bold text-text-muted border border-border-default">
            {insight.sentiment_score >= 65 ? <TrendingUp size={9} className="text-emerald-600 dark:text-emerald-400" /> : 
             insight.sentiment_score >= 40 ? <Minus size={9} className="text-amber-600 dark:text-amber-400" /> : 
             <TrendingDown size={9} className="text-rose-600 dark:text-rose-400" />}
            {insight.sentiment_score}/100
          </span>
          <span className="text-[8.5px] text-text-muted/60 ml-auto tabular-nums font-medium">
            {timeAgo(insight.timestamp_ms)}
          </span>
        </div>

        {/* Dynamic expandable details */}
        <AnimatePresence initial={false}>
          {expanded && (
            <motion.div
              variants={collapseVariants}
              initial="collapsed"
              animate="expanded"
              exit="collapsed"
              className="overflow-hidden"
            >
              <div className="mt-2 pt-2.5 border-t border-border-default/45 text-[11px] leading-relaxed text-text-secondary/90 whitespace-pre-line">
                <p className="text-[9.5px] font-black text-text-muted/80 uppercase tracking-widest mb-1.5">AI Confluence Analysis</p>
                {insight.analysis_text}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
