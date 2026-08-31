import React, { useState, useMemo } from 'react';
import { useQuantStore, ChartPattern } from '../../../store/useQuantStore';
import { useTradeStore, ChartTimeframe } from '../../../store/useTradeStore';
import { useRadarStore } from '../../../store/useRadarStore';
import {
  TrendingUp,
  TrendingDown,
  Minus,
  Sparkles,
  Activity,
  Loader2,
  Radio,
  AlertTriangle,
  RefreshCw
} from 'lucide-react';
import {
  CONFIDENCE_BAND_OPACITY,
  MUTED_TAG,
  breakoutTag,
  confidenceBand,
  patternSentiment,
  sentimentTheme,
  volumeTag,
} from './patternStyles';

export default function MultiTfPatternsView() {
  const { multiTfPatterns, isFetchingPatterns, patternsError } = useQuantStore();
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  // `null` = no explicit user choice yet, so fall back to the auto-picked tab
  // (the timeframe carrying the most patterns). A manual tab click pins it.
  const [userSelectedTf, setUserSelectedTf] = useState<string | null>(null);

  const timeframes = ['1m', '5m', '10m', '15m', '1h', '4h', '1d'];

  // Timeframe with the most patterns — used as the default view so the panel
  // opens on a tab that actually has results instead of a hardcoded empty one.
  const bestTf = useMemo(() => {
    if (!multiTfPatterns) return '10m';
    const best = multiTfPatterns.reduce<{ tf: string; count: number }>(
      (acc, p) => (p.patterns.length > acc.count ? { tf: p.timeframe, count: p.patterns.length } : acc),
      { tf: '10m', count: -1 }
    );
    return best.count > 0 ? best.tf : '10m';
  }, [multiTfPatterns]);

  const selectedTf = userSelectedTf ?? bestTf;
  const setSelectedTf = setUserSelectedTf;

  // Find patterns for selected timeframe
  const currentTfData = multiTfPatterns?.find(p => p.timeframe === selectedTf);
  const patterns = currentTfData?.patterns || [];

  // Helper to count patterns for each timeframe
  const getPatternCount = (tf: string) => {
    const data = multiTfPatterns?.find(p => p.timeframe === tf);
    return data?.patterns.length || 0;
  };

  /** Across every timeframe — drives the header's live badge. */
  const totalPatterns = useMemo(
    () => (multiTfPatterns ?? []).reduce((n, p) => n + p.patterns.length, 0),
    [multiTfPatterns],
  );

  const handlePatternClick = (p: ChartPattern) => {
    const symbol = useTradeStore.getState().selectedSymbol || 'RELIANCE';

    // 1. Shift symbol and timeframe to match the pattern
    useTradeStore.getState().setSelectedSymbol(symbol);
    useTradeStore.getState().setActiveTimeframe(selectedTf as ChartTimeframe);

    // 2. Map to LocatedPattern for the chart drawing overlay
    const isBullish = p.sentiment.toLowerCase() === 'bullish';
    const isBearish = p.sentiment.toLowerCase() === 'bearish';
    const bias: 'BULLISH' | 'BEARISH' | 'NEUTRAL' = isBullish ? 'BULLISH' : isBearish ? 'BEARISH' : 'NEUTRAL';

    const locatedPattern = {
      name: p.pattern_type,
      bias: bias,
      candle_index: p.end_idx,
      time: p.time ?? 0,
      start_time: p.start_time,
      open: 0,
      close: 0,
      high: p.high ?? 0,
      low: p.low ?? 0,
    };

    // 3. Set the viz target in RadarStore to trigger overlay drawing
    const target = {
      symbol,
      timeframe: selectedTf as any,
      kind: 'pattern' as const,
      pattern: locatedPattern,
    };

    console.log(`[PatternsView] Visualizing pattern:`, target);
    useRadarStore.getState().setVizTarget(target);
  };

  return (
    <div className="border-b border-border-default py-2.5 px-0 bg-transparent select-none">
      <div className="flex items-center gap-1.5 mb-2 px-3">
        <Sparkles size={10} className="text-neutral" />
        <h3 className="text-[9px] font-bold text-text-secondary uppercase tracking-wider">
          Dynamic Pattern Scanner
        </h3>
        {isFetchingPatterns ? (
          <span className="ml-auto flex items-center gap-1">
            <Loader2 size={9} className="animate-spin text-neutral" />
            <span className="text-[8px] font-bold uppercase tracking-wider text-neutral">
              Scanning
            </span>
          </span>
        ) : (
          totalPatterns > 0 && (
            <span className="ml-auto flex items-center gap-1">
              {/* A live indicator, not decoration: the scan is current and found
                  something. The dot is paired with a count so the state is not
                  conveyed by colour alone. */}
              <span className="relative flex h-1.5 w-1.5 shrink-0">
                <span className="absolute inline-flex h-full w-full rounded-full bg-bull animate-pattern-ping" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-bull" />
              </span>
              <span className="text-[8px] font-bold uppercase tracking-wider text-text-muted">
                {totalPatterns} live
              </span>
            </span>
          )
        )}
      </div>

      {/* Timeframe Selector Tabs */}
      <div className="flex gap-1 overflow-x-auto pb-1.5 px-3 scrollbar-none">
        {timeframes.map((tf) => {
          const count = getPatternCount(tf);
          const isActive = selectedTf === tf;
          return (
            <button
              key={tf}
              type="button"
              onClick={() => setSelectedTf(tf)}
              aria-pressed={isActive}
              aria-label={`${tf} timeframe, ${count} pattern${count === 1 ? '' : 's'}`}
              className={`
                flex items-center gap-1 px-2 py-0.5 rounded-md text-[9px] font-bold shrink-0 border
                transition-colors duration-150
                focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-neutral
                ${isActive
                  ? 'bg-text-primary text-surface border-text-primary'
                  : count > 0
                    // A timeframe carrying patterns is worth looking at even when
                    // it is not the open tab, so it keeps a tint of the accent
                    // instead of sitting at the same weight as an empty one.
                    ? 'bg-neutral/8 text-text-secondary border-neutral/30 hover:bg-neutral/15 hover:text-text-primary'
                    : 'bg-elevated/40 text-text-muted hover:bg-elevated/70 hover:text-text-secondary border-border-default/40'
                }
              `}
            >
              <span>{tf}</span>
              {isFetchingPatterns ? (
                <Loader2 size={8} className="animate-spin text-neutral" />
              ) : count > 0 ? (
                <span className={`
                  flex h-3.5 min-w-3.5 items-center justify-center rounded-full px-1 text-[8px] font-black border
                  ${isActive
                    ? 'bg-surface text-text-primary border-surface'
                    : 'bg-neutral/15 text-neutral border-neutral/40'}
                `}>
                  {count}
                </span>
              ) : (
                <span className="text-[8px] opacity-40">0</span>
              )}
            </button>
          );
        })}
      </div>

      {/* Patterns list */}
      <div className="mt-2 space-y-0 max-h-47.5 overflow-y-auto scrollbar-thin">
        {isFetchingPatterns ? (
          // Loading skeletons
          <div className="space-y-0 py-1">
            {[1, 2].map((i) => (
              <div key={i} className="animate-pulse flex flex-col gap-1 px-3 py-2 border-y border-x-0 border-border-default/40 bg-elevated/10">
                <div className="flex justify-between items-center">
                  <div className="h-3 w-16 bg-elevated/60 rounded-full" />
                  <div className="h-3 w-10 bg-elevated/60 rounded-full" />
                </div>
                <div className="h-2 w-full bg-elevated/30 rounded-full" />
              </div>
            ))}
          </div>
        ) : patternsError ? (
          /* Scan FAILED — distinct from "no patterns forming".
             Reporting a failed scan as an empty result is what made a
             heartbeat / tool-server error look like a calm, healthy market. */
          <div
            role="status"
            className="mx-3 flex flex-col gap-1 rounded-lg border border-neutral/30 bg-neutral/5 px-3 py-2.5"
          >
            <div className="flex items-center gap-1.5">
              <AlertTriangle size={10} className="shrink-0 text-neutral" />
              <span className="text-[8px] font-black uppercase tracking-widest text-neutral">
                Scan unavailable
              </span>
            </div>
            {/* Was `text-amber-700/90 dark:text-amber-300/80`. The `dark:` half
                never applied — nothing in this app adds a `.dark` class — so the
                dark theme was rendering amber-700 on near-black. The `neutral`
                token is defined per theme and lands the right way up in both. */}
            <p className="text-[9px] leading-relaxed text-text-secondary break-words">
              {patternsError}
            </p>
            <button
              type="button"
              onClick={() => {
                const sym = selectedSymbol || 'RELIANCE';
                void useQuantStore.getState().fetchMultiTfPatterns(sym);
              }}
              className="mt-0.5 inline-flex w-fit items-center gap-1 rounded-full border border-neutral/40 px-2 py-0.5 text-[8px] font-bold uppercase tracking-wider text-neutral transition-colors hover:bg-neutral/10"
            >
              <RefreshCw size={8} />
              Retry scan
            </button>
          </div>
        ) : patterns.length === 0 ? (
          // Empty State
          <div className="mx-3 flex flex-col items-center justify-center rounded-lg border border-border-default/50 bg-elevated/10 py-4 text-center">
            <Activity size={12} className="text-text-muted mb-0.5" />
            <span className="text-[9px] font-medium text-text-muted">No patterns forming</span>
            <span className="text-[8px] text-text-muted/40">Timeframe: {selectedTf}</span>
          </div>
        ) : (
          patterns.map((p, idx) => {
            const sentiment = patternSentiment(p.sentiment);
            const tone = sentimentTheme(sentiment);
            const isBullish = sentiment === 'bullish';
            const isBearish = sentiment === 'bearish';
            const isForming = p.is_forming ?? false;
            const progress = p.formation_progress ?? 0;
            const progressPct = Math.round(progress * 100);
            const confPct = Math.round((p.confidence ?? 0) * 100);
            const confOpacity = CONFIDENCE_BAND_OPACITY[confidenceBand(p.confidence ?? 0)];
            // Stable key from the pattern's identity (type + candle span) so
            // re-sorting by progress/confidence doesn't churn the DOM via
            // index-based reconciliation. Fall back to idx only on collision.
            const key = `${p.pattern_type}-${p.start_idx}-${p.end_idx}-${idx}`;

            return (
              <div
                key={key}
                role="button"
                tabIndex={0}
                aria-label={`${p.pattern_type}, ${p.sentiment}${isForming ? `, forming ${progressPct}%` : ''}, confidence ${confPct}%`}
                onClick={() => handlePatternClick(p)}
                onKeyDown={(e) => {
                  // The card was a plain onClick div: unreachable by keyboard and
                  // invisible to assistive tech. It behaves as a button, so it now
                  // says so and answers to Enter/Space like one.
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    handlePatternClick(p);
                  }
                }}
                className={`
                  group relative flex flex-col gap-1 px-3 py-2 pl-3.5 cursor-pointer
                  border-y border-x-0 border-border-default/45 bg-elevated/5
                  transition-colors duration-200 ${tone.wash}
                  focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-neutral
                `}
              >
                {/* Leading edge, carrying the pattern's direction. A live pattern
                    keeps a soft glow; a settled one lights up on hover. The card
                    itself no longer animates — see the note in globals.css. */}
                <div className={`
                  absolute top-1 bottom-1 left-0 w-0.5 rounded-full transition-opacity duration-200
                  ${tone.edge}
                  ${isForming ? `opacity-100 ${tone.glow}` : 'opacity-50 group-hover:opacity-100'}
                `} />

                {/* Pattern Header */}
                <div className="flex justify-between items-start pl-1">
                  <div className="flex items-center gap-1 truncate max-w-40">
                    {isForming && (
                      <Radio size={8} className={`shrink-0 ${tone.text}`} />
                    )}
                    <span className="text-[10px] font-bold text-text-primary tracking-tight truncate">
                      {p.pattern_type}
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    {/* FORMING: amber, because "incomplete" is a caution rather
                        than a direction — it must not read as the pattern's own
                        bias. The ping is the only motion left on the badge. */}
                    {isForming && (
                      <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[7px] font-black uppercase tracking-wider border bg-neutral/12 text-neutral border-neutral/40">
                        <span className="relative flex h-1 w-1 shrink-0">
                          <span className="absolute inline-flex h-full w-full rounded-full bg-neutral animate-pattern-ping" />
                          <span className="relative inline-flex h-1 w-1 rounded-full bg-neutral" />
                        </span>
                        FORMING
                      </span>
                    )}
                    <span className={`flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[8px] font-black uppercase tracking-wider border ${tone.badge}`}>
                      {isBullish ? (
                        <TrendingUp size={8} />
                      ) : isBearish ? (
                        <TrendingDown size={8} />
                      ) : (
                        <Minus size={8} />
                      )}
                      {p.sentiment}
                    </span>
                  </div>
                </div>

                {/* Pattern Description */}
                <p className="text-[9px] text-text-muted leading-relaxed pl-1">
                  {p.description}
                </p>

                {/* Formation Progress Bar (for forming patterns) */}
                {isForming && progress > 0 && (
                  <div className="flex items-center gap-1.5 pl-1 mt-0.5">
                    <span className="text-[8px] text-text-secondary font-bold">Progress:</span>
                    <div
                      role="progressbar"
                      aria-valuenow={progressPct}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-label={`${p.pattern_type} formation progress`}
                      className="relative grow h-1.5 bg-surface border border-border-default rounded-full overflow-hidden"
                    >
                      <div
                        className={`relative h-full rounded-full bg-gradient-to-r ${tone.bar} transition-[width] duration-500 ease-out`}
                        style={{ width: `${progressPct}%` }}
                      >
                        {/* A sheen travelling along the filled portion: the bar is
                            still growing. Purely decorative — the width and the
                            percentage already state the value — so it is removed
                            outright under prefers-reduced-motion. */}
                        <span
                          aria-hidden="true"
                          className="pointer-events-none absolute inset-y-0 -left-4 w-4 bg-gradient-to-r from-transparent via-white/45 to-transparent animate-pattern-sheen"
                        />
                      </div>
                    </div>
                    <span className={`text-[8px] font-black tabular-nums ${tone.text}`}>
                      {progressPct}%
                    </span>
                  </div>
                )}

                {/* Confidence Bar */}
                <div className="flex items-center gap-1.5 pl-1 mt-0.5">
                  <span className="text-[8px] text-text-muted/60 font-bold">Conf:</span>
                  <div
                    role="progressbar"
                    aria-valuenow={confPct}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`${p.pattern_type} confidence`}
                    className="grow h-1 bg-surface border border-border-default/45 rounded-full overflow-hidden"
                  >
                    {/* Same hue as the sentiment so the two bars read as one
                        object, but dimmed by confidence band — a 30% reading must
                        not look as emphatic as a 90% one just because both are
                        bearish. */}
                    <div
                      className={`h-full rounded-full bg-gradient-to-r ${tone.bar} ${confOpacity} transition-[width] duration-300 ease-out`}
                      style={{ width: `${confPct}%` }}
                    />
                  </div>
                  <span className="text-[8px] font-black tabular-nums text-text-secondary">
                    {confPct}%
                  </span>
                </div>

                {/* Volume Validation & Breakout Status */}
                <div className="flex items-center gap-1 pl-1 mt-0.5 flex-wrap">
                  {p.volume_validation && (() => {
                    const vol = volumeTag(p.volume_validation);
                    return (
                      <span
                        title={`Volume: ${p.volume_validation}`}
                        className={`inline-flex items-center gap-0.5 px-1.5 py-px rounded-full text-[7px] font-bold uppercase tracking-wider border ${vol.badge}`}
                      >
                        {vol.glyph} Vol
                      </span>
                    );
                  })()}
                  {p.breakout_status && (
                    <span className={`inline-flex items-center gap-0.5 px-1.5 py-px rounded-full text-[7px] font-bold tracking-wider border ${breakoutTag(p.breakout_status, sentiment)}`}>
                      {p.breakout_status}
                    </span>
                  )}
                  {p.structural_bias && (
                    <span className={`inline-flex items-center px-1.5 py-px rounded-full text-[7px] font-bold tracking-wider border ${MUTED_TAG}`}>
                      {p.structural_bias}
                    </span>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
