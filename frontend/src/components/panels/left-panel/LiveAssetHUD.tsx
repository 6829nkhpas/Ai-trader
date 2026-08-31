'use client';

import React from 'react';
import {
  TrendingUp,
  TrendingDown,
  Minus,
  Hexagon,
  Target,
  Gauge,
  Waves,
  BarChart3,
  Clock,
  AlertTriangle,
} from 'lucide-react';
import { motion } from 'framer-motion';
import type { ConsensusReport } from '../../../store/useQuantStore';
import { staggerContainer, fadeInUp } from '../../../lib/motionVariants';
import {
  CONSENSUS_STALE_AFTER_MS,
  formatAge,
  trendBg,
  trendColor,
  trendGaugePercent,
  trendVerdict,
} from './consensusView';

interface LiveAssetHUDProps {
  data: ConsensusReport;
  /**
   * When `data` was computed (epoch ms), or null if unknown.
   *
   * The consensus is only recomputed on an explicit FIND/VERIFY press, so the
   * panel legitimately shows a retained reading when you re-select a symbol.
   * Previously it did so with no age indicator at all, which made a reading from
   * a previous session look like current market data. Showing the age — and
   * flagging it once it is clearly not current — is the difference between a
   * retained measurement and an invented one.
   */
  computedAt?: number | null;
  /**
   * Where the HUD is rendered. `panel` is the historical 224px sidebar layout and
   * stays the default so existing call sites are untouched; `sheet` is the detail
   * view, which has room for larger type and drops the section title the dialog
   * header already carries.
   */
  variant?: 'panel' | 'sheet';
}

function stateColor(state: string) {
  switch (state) {
    case 'OVERBOUGHT':
      return 'text-rose-400 bg-rose-500/10 border-rose-500/30';
    case 'OVERSOLD':
      return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30';
    case 'SQUEEZING':
      return 'text-amber-400 bg-amber-500/10 border-amber-500/30';
    case 'EXPANDING':
      return 'text-violet-400 bg-violet-500/10 border-violet-500/30';
    case 'ACCUMULATION':
      return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30';
    case 'DISTRIBUTION':
      return 'text-rose-400 bg-rose-500/10 border-rose-500/30';
    default:
      return 'text-slate-400 bg-slate-500/10 border-slate-500/30';
  }
}

export default function LiveAssetHUD({ data, computedAt, variant = 'panel' }: LiveAssetHUDProps) {
  const inSheet = variant === 'sheet';
  const {
    symbol,
    trend_score,
    momentum_state,
    volatility_state,
    volume_flow_state,
    active_patterns,
    active_strategies,
  } = data;
  const gaugePercent = trendGaugePercent(trend_score);

  // Re-tick so the age label stays truthful while the panel sits open, rather
  // than freezing at whatever it read on mount.
  const [now, setNow] = React.useState(() => Date.now());
  React.useEffect(() => {
    if (!computedAt) return;
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, [computedAt]);

  const ageMs = computedAt ? Math.max(0, now - computedAt) : null;
  const isStale = ageMs !== null && ageMs > CONSENSUS_STALE_AFTER_MS;

  const iconSize = inSheet ? 12 : 10;

  const stateEntries = [
    { label: 'Momentum', value: momentum_state, icon: <Gauge size={iconSize} /> },
    { label: 'Volatility', value: volatility_state, icon: <Waves size={iconSize} /> },
    { label: 'Vol Flow', value: volume_flow_state, icon: <BarChart3 size={iconSize} /> },
  ];

  // Type and spacing scale per variant. The `panel` column is unchanged from what
  // shipped; `sheet` uses the room a 420px dialog has and a 224px sidebar did not.
  const t = inSheet
    ? {
        section: 'px-4 py-3.5',
        heading: 'text-[10px]',
        provenance: 'text-[9px]',
        score: 'text-4xl',
        verdict: 'text-[11px]',
        pct: 'text-[10px]',
        stateLabel: 'text-[11px]',
        badge: 'text-[9px]',
        chip: 'text-[10px]',
        empty: 'text-[11px]',
        strategy: 'text-[12px]',
      }
    : {
        section: 'px-3 py-2.5',
        heading: 'text-[9px]',
        provenance: 'text-[8px]',
        score: 'text-2xl',
        verdict: 'text-[9px]',
        pct: 'text-[8px]',
        stateLabel: 'text-[10px]',
        badge: 'text-[8px]',
        chip: 'text-[8px]',
        empty: 'text-[9px]',
        strategy: 'text-[10px]',
      };

  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="show"
      className="flex flex-col text-sm"
    >
      {/* ── Section 1: Technical Consensus ──────────────────── */}
      <motion.div variants={fadeInUp} className={`border-b border-border-default ${t.section}`}>
        <div className="flex items-center gap-1.5 mb-2">
          <TrendingUp size={iconSize} className="text-text-muted" />
          {/* The dialog header already names this section in the sheet; a second
              title on the same view is noise. */}
          {!inSheet && (
            <h3 className={`${t.heading} font-bold text-text-secondary uppercase tracking-wider`}>
              Technical Consensus
            </h3>
          )}
          {/* The sheet header already carries the symbol badge. */}
          {symbol && !inSheet && (
            <span className="ml-auto rounded px-1.5 py-px text-[7px] font-bold uppercase tracking-wider bg-blue-500/10 text-blue-400 border border-blue-500/20">
              {symbol}
            </span>
          )}
        </div>

        {/* Provenance line. A technical read is computed on an explicit
            FIND/VERIFY press, so state WHEN it was measured — an unlabelled
            retained reading is indistinguishable from a live one. */}
        {ageMs !== null && (
          <div
            className={`mb-2 flex items-center gap-1 ${t.provenance} font-semibold uppercase tracking-wider ${
              isStale ? 'text-amber-600 dark:text-amber-400' : 'text-text-muted/70'
            }`}
            title={
              computedAt
                ? `Computed at ${new Date(computedAt).toLocaleTimeString()}`
                : undefined
            }
          >
            {isStale ? <AlertTriangle size={8} className="shrink-0" /> : <Clock size={8} className="shrink-0" />}
            <span>
              {isStale ? 'Previous reading · ' : 'Measured '}
              {formatAge(ageMs)}
            </span>
            {isStale && (
              <span className="font-normal normal-case text-text-muted/70">
                — re-run analysis to refresh
              </span>
            )}
          </div>
        )}

        {/* Trend Score */}
        <div className={`flex items-center gap-2.5 ${inSheet ? 'mb-3' : 'mb-2'}`}>
          <div className={`${t.score} font-black tabular-nums tracking-tight ${trendColor(trend_score)}`}>
            {trend_score > 0 ? '+' : ''}
            {trend_score}
          </div>
          <div className="flex-1 flex flex-col gap-0.5">
            <div className="flex items-center justify-between">
              <span className={`${t.verdict} font-bold uppercase tracking-wider ${trendColor(trend_score)}`}>
                {trendVerdict(trend_score)}
              </span>
              <span className={`${t.pct} text-text-muted tabular-nums`}>{gaugePercent}%</span>
            </div>
            <div
              className={`relative w-full rounded-full bg-elevated overflow-hidden ${inSheet ? 'h-2' : 'h-1.5'}`}
            >
              <motion.div
                className={`h-full rounded-full ${trendBg(trend_score)}`}
                initial={{ width: 0 }}
                animate={{ width: `${gaugePercent}%` }}
                transition={{ type: 'spring', stiffness: 80, damping: 15, delay: 0.2 }}
              />
              <div className="absolute top-0 left-1/2 -translate-x-px w-0.5 h-full bg-text-muted/30" />
            </div>
          </div>
        </div>

        {/* State Badges */}
        <div className={`flex flex-col ${inSheet ? 'gap-2' : 'gap-1'}`}>
          {stateEntries.map(({ label, value, icon }) => (
            <div key={label} className="flex items-center justify-between">
              <div className={`flex items-center gap-1 ${t.stateLabel} text-text-secondary`}>
                {icon}
                <span className="font-medium">{label}</span>
              </div>
              <span
                className={`inline-flex items-center rounded px-1.5 py-px ${t.badge} font-bold border ${stateColor(value)}`}
              >
                {value}
              </span>
            </div>
          ))}
        </div>
      </motion.div>

      {/* ── Section 2: Active Patterns ──────────────────────── */}
      <motion.div
        variants={fadeInUp}
        className={`border-b border-border-default ${inSheet ? 'px-4 py-3' : 'px-3 py-2'}`}
      >
        <div className="flex items-center gap-1.5 mb-1.5">
          <Hexagon size={iconSize} className="text-text-muted" />
          <h3 className={`${t.heading} font-bold text-text-secondary uppercase tracking-wider`}>
            Patterns
          </h3>
          {active_patterns.length > 0 && (
            <span
              className={`ml-auto flex h-3.5 w-3.5 items-center justify-center rounded-full bg-slate-500/20 ${t.badge} font-bold text-slate-400`}
            >
              {active_patterns.length}
            </span>
          )}
        </div>
        {active_patterns.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {active_patterns.map((p) => (
              <span
                key={p}
                className={`inline-flex items-center gap-0.5 rounded px-1.5 py-px ${t.chip} font-semibold bg-slate-500/8 text-slate-400 border border-slate-500/20`}
              >
                {p.includes('Bullish') || p === 'Hammer' ? (
                  <TrendingUp size={inSheet ? 9 : 7} />
                ) : p.includes('Bearish') || p === 'Shooting Star' ? (
                  <TrendingDown size={inSheet ? 9 : 7} />
                ) : (
                  <Minus size={inSheet ? 9 : 7} />
                )}
                {p}
              </span>
            ))}
          </div>
        ) : (
          <p className={`${t.empty} text-text-muted/50 italic`}>No patterns detected</p>
        )}
      </motion.div>

      {/* ── Section 3: Active Strategies ───────────────────── */}
      <motion.div
        variants={fadeInUp}
        className={`border-b border-border-default ${inSheet ? 'px-4 py-3' : 'px-3 py-2'}`}
      >
        <div className="flex items-center gap-1.5 mb-1.5">
          <Target size={iconSize} className="text-blue-400" />
          <h3 className={`${t.heading} font-bold text-blue-400 uppercase tracking-wider`}>
            Strategies
          </h3>
          {active_strategies.length > 0 && (
            <span
              className={`ml-auto flex h-3.5 w-3.5 items-center justify-center rounded-full bg-blue-500/20 ${t.badge} font-bold text-blue-400 animate-pulse motion-reduce:animate-none`}
            >
              {active_strategies.length}
            </span>
          )}
        </div>
        {active_strategies.length > 0 ? (
          <div className="flex flex-col gap-1">
            {active_strategies.map((s) => (
              <div
                key={s}
                className={`flex items-center gap-1.5 rounded-md border border-blue-500/30 bg-blue-500/5 transition-colors hover:bg-blue-500/10 ${
                  inSheet ? 'px-2.5 py-2' : 'px-2 py-1'
                }`}
              >
                <div
                  className={`flex shrink-0 items-center justify-center rounded bg-blue-500/15 ${
                    inSheet ? 'h-5 w-5' : 'h-4 w-4'
                  }`}
                >
                  {s.includes('Bullish') || s.includes('Golden') ? (
                    <TrendingUp size={inSheet ? 10 : 8} className="text-blue-400" />
                  ) : (
                    <TrendingDown size={inSheet ? 10 : 8} className="text-blue-400" />
                  )}
                </div>
                <span className={`${t.strategy} font-semibold text-blue-300`}>{s}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className={`${t.empty} text-text-muted/50 italic`}>No strategies active</p>
        )}
      </motion.div>
    </motion.div>
  );
}
