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
  Radio
} from 'lucide-react';

export default function MultiTfPatternsView() {
  const { multiTfPatterns, isFetchingPatterns } = useQuantStore();
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
        <Sparkles size={10} className="text-text-muted" />
        <h3 className="text-[9px] font-bold text-text-secondary uppercase tracking-wider">
          Dynamic Pattern Scanner
        </h3>
        {isFetchingPatterns && (
          <Loader2 size={9} className="ml-auto animate-spin text-text-muted" />
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
              className={`
                flex items-center gap-1 px-2 py-0.5 rounded-none text-[9px] font-bold transition-all duration-150 shrink-0 border
                ${isActive
                  ? 'bg-text-primary text-surface border-text-primary scale-[1.01]'
                  : 'bg-elevated/40 text-text-muted hover:bg-elevated/70 hover:text-text-secondary border-border-default/40'
                }
              `}
            >
              <span>{tf}</span>
              {isFetchingPatterns ? (
                <Loader2 size={8} className="animate-spin text-text-muted" />
              ) : count > 0 ? (
                <span className={`
                  flex h-3.5 min-w-3.5 items-center justify-center rounded-none px-0.5 text-[8px] font-black border
                  ${isActive ? 'bg-text-primary text-surface border-text-primary' : 'bg-elevated text-text-primary border-border-default'}
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
                  <div className="h-3 w-16 bg-elevated/60 rounded-none" />
                  <div className="h-3 w-10 bg-elevated/60 rounded-none" />
                </div>
                <div className="h-2 w-full bg-elevated/30 rounded-none" />
              </div>
            ))}
          </div>
        ) : patterns.length === 0 ? (
          // Empty State
          <div className="flex flex-col items-center justify-center py-4 text-center border-y border-x-0 border-border-default/50 bg-elevated/10 rounded-none">
            <Activity size={12} className="text-text-muted mb-0.5" />
            <span className="text-[9px] font-medium text-text-muted">No patterns forming</span>
            <span className="text-[8px] text-text-muted/40">Timeframe: {selectedTf}</span>
          </div>
        ) : (
          patterns.map((p, idx) => {
            const isBullish = p.sentiment.toLowerCase() === 'bullish';
            const isBearish = p.sentiment.toLowerCase() === 'bearish';
            const isForming = p.is_forming ?? false;
            const progress = p.formation_progress ?? 0;
            const progressPct = Math.round(progress * 100);
            // Stable key from the pattern's identity (type + candle span) so
            // re-sorting by progress/confidence doesn't churn the DOM via
            // index-based reconciliation. Fall back to idx only on collision.
            const key = `${p.pattern_type}-${p.start_idx}-${p.end_idx}-${idx}`;

            return (
              <div
                key={key}
                onClick={() => handlePatternClick(p)}
                className={`
                  group relative flex flex-col gap-1 px-3 py-2 border-y border-x-0 border-border-default/45 bg-elevated/5 hover:bg-elevated/20 transition-all duration-200 cursor-pointer
                  ${isForming ? 'animate-[pulse_3s_ease-in-out_infinite]' : ''}
                `}
              >
                {/* Glowing edge indicator — pulsing for forming patterns */}
                <div className={`
                  absolute top-0 bottom-0 left-0 w-0.5 rounded-none transition-opacity
                  ${isForming ? 'opacity-80 animate-pulse' : 'opacity-40 group-hover:opacity-100'}
                  ${isBullish ? 'bg-text-primary' : isBearish ? 'bg-text-muted' : 'bg-border-default'}
                `} />

                {/* Pattern Header */}
                <div className="flex justify-between items-start pl-1">
                  <div className="flex items-center gap-1 truncate max-w-40">
                    {isForming && (
                      <Radio size={8} className="shrink-0 animate-pulse text-text-secondary" />
                    )}
                    <span className="text-[10px] font-bold text-text-primary tracking-tight truncate">
                      {p.pattern_type}
                    </span>
                  </div>
                  <div className="flex items-center gap-1">
                    {/* FORMING badge */}
                    {isForming && (
                      <span className="flex items-center gap-0.5 px-1 py-0.5 rounded-none text-[7px] font-black uppercase tracking-wider border bg-elevated text-text-primary border-border-default animate-pulse">
                        FORMING
                      </span>
                    )}
                    <span className="flex items-center gap-0.5 px-1 py-0.5 rounded-none text-[8px] font-black uppercase tracking-wider border bg-elevated text-text-primary border-border-default">
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
                    <div className="grow h-1.5 bg-surface border border-border-default rounded-none overflow-hidden">
                      <div
                        className="h-full rounded-none transition-all duration-500 bg-text-primary"
                        style={{ width: `${progressPct}%` }}
                      />
                    </div>
                    <span className="text-[8px] font-black text-text-primary">
                      {progressPct}%
                    </span>
                  </div>
                )}

                {/* Confidence Bar */}
                <div className="flex items-center gap-1.5 pl-1 mt-0.5">
                  <span className="text-[8px] text-text-muted/60 font-bold">Conf:</span>
                  <div className="grow h-1 bg-surface border border-border-default/45 rounded-none overflow-hidden">
                    <div
                      className={`
                        h-full rounded-none transition-all duration-300
                        ${isBullish
                          ? 'bg-text-primary'
                          : isBearish
                            ? 'bg-text-muted'
                            : 'bg-text-secondary'
                        }
                      `}
                      style={{ width: `${p.confidence * 100}%` }}
                    />
                  </div>
                  <span className="text-[8px] font-black text-text-secondary">
                    {Math.round(p.confidence * 100)}%
                  </span>
                </div>

                {/* Volume Validation & Breakout Status */}
                <div className="flex items-center gap-1 pl-1 mt-0.5 flex-wrap">
                  {p.volume_validation && (
                    <span className="inline-flex items-center gap-0.5 px-1 py-px rounded-none text-[7px] font-bold uppercase tracking-wider border bg-elevated text-text-primary border-border-default">
                      {p.volume_validation.includes('Confirmed') ? '✓' : p.volume_validation === 'Forming' ? '◎' : '○'} Vol
                    </span>
                  )}
                  {p.breakout_status && (
                    <span className="inline-flex items-center gap-0.5 px-1 py-px rounded-none text-[7px] font-bold tracking-wider border bg-elevated text-text-primary border-border-default">
                      {p.breakout_status}
                    </span>
                  )}
                  {p.structural_bias && (
                    <span className="inline-flex items-center px-1 py-px rounded-none text-[7px] font-bold tracking-wider bg-elevated text-text-primary border border-border-default">
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
