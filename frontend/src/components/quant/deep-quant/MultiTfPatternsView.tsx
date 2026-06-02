import React, { useState } from 'react';
import { useQuantStore } from '../../../store/useQuantStore';
import { useTradeStore, ChartTimeframe } from '../../../store/useTradeStore';
import { useRadarStore } from '../../../store/useRadarStore';
import { 
  TrendingUp, 
  TrendingDown, 
  Minus, 
  Sparkles, 
  Activity, 
  Loader2 
} from 'lucide-react';

export default function MultiTfPatternsView() {
  const { multiTfPatterns, isFetchingPatterns } = useQuantStore();
  const [selectedTf, setSelectedTf] = useState<string>('10m');

  const timeframes = ['1m', '5m', '10m', '15m', '1h', '4h', '1d'];

  // Find patterns for selected timeframe
  const currentTfData = multiTfPatterns?.find(p => p.timeframe === selectedTf);
  const patterns = currentTfData?.patterns || [];

  // Helper to count patterns for each timeframe
  const getPatternCount = (tf: string) => {
    const data = multiTfPatterns?.find(p => p.timeframe === tf);
    return data?.patterns.length || 0;
  };

  const handlePatternClick = (p: any) => {
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
    <div className="border-b border-border-default px-3 py-2.5 bg-transparent select-none">
      <div className="flex items-center gap-1.5 mb-2">
        <Sparkles size={10} className="text-text-muted" />
        <h3 className="text-[9px] font-bold text-text-secondary uppercase tracking-wider">
          Dynamic Pattern Scanner
        </h3>
        {isFetchingPatterns && (
          <Loader2 size={9} className="ml-auto animate-spin text-blue-400" />
        )}
      </div>

      {/* Timeframe Selector Tabs */}
      <div className="flex gap-1 overflow-x-auto pb-1.5 scrollbar-none">
        {timeframes.map((tf) => {
          const count = getPatternCount(tf);
          const isActive = selectedTf === tf;
          return (
            <button
              key={tf}
              type="button"
              onClick={() => setSelectedTf(tf)}
              className={`
                flex items-center gap-1 px-2 py-0.5 rounded text-[9px] font-bold transition-all duration-150 shrink-0 border
                ${isActive 
                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20 shadow-[0_0_8px_rgba(16,185,129,0.08)] scale-[1.01]' 
                  : 'bg-elevated/40 text-text-muted hover:bg-elevated/70 hover:text-text-secondary border-border-default/40'
                }
              `}
            >
              <span>{tf}</span>
              {isFetchingPatterns ? (
                <Loader2 size={8} className="animate-spin text-text-muted" />
              ) : count > 0 ? (
                <span className={`
                  flex h-3.5 min-w-[14px] items-center justify-center rounded-full px-0.5 text-[8px] font-black
                  ${isActive ? 'bg-emerald-400 text-slate-950' : 'bg-emerald-500/10 text-emerald-400'}
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
      <div className="mt-2 space-y-1.5 max-h-[190px] overflow-y-auto scrollbar-thin pr-0.5">
        {isFetchingPatterns ? (
          // Loading skeletons
          <div className="space-y-1.5 py-1">
            {[1, 2].map((i) => (
              <div key={i} className="animate-pulse flex flex-col gap-1 p-2 rounded-lg bg-elevated/20 border border-border-default/40">
                <div className="flex justify-between items-center">
                  <div className="h-3 w-16 bg-elevated/60 rounded" />
                  <div className="h-3 w-10 bg-elevated/60 rounded-full" />
                </div>
                <div className="h-2 w-full bg-elevated/30 rounded" />
              </div>
            ))}
          </div>
        ) : patterns.length === 0 ? (
          // Empty State
          <div className="flex flex-col items-center justify-center py-4 text-center border border-border-default/50 bg-elevated/10 rounded-lg">
            <Activity size={12} className="text-text-muted mb-0.5" />
            <span className="text-[9px] font-medium text-text-muted">No patterns detected</span>
            <span className="text-[8px] text-text-muted/40">Timeframe: {selectedTf}</span>
          </div>
        ) : (
          patterns.map((p, idx) => {
            const isBullish = p.sentiment.toLowerCase() === 'bullish';
            const isBearish = p.sentiment.toLowerCase() === 'bearish';

            return (
              <div
                key={idx}
                onClick={() => handlePatternClick(p)}
                className={`
                  group relative flex flex-col gap-1 p-2 rounded-lg border transition-all duration-200 cursor-pointer hover:scale-[1.005] active:scale-[0.995]
                  ${isBullish 
                    ? 'bg-emerald-500/[0.03] border-emerald-500/15 hover:border-emerald-500/35' 
                    : isBearish 
                      ? 'bg-rose-500/[0.03] border-rose-500/15 hover:border-rose-500/35' 
                      : 'bg-elevated/10 border-border-default/60 hover:border-border-default'
                  }
                `}
              >
                {/* Glowing edge indicator */}
                <div className={`
                  absolute top-0 bottom-0 left-0 w-0.5 rounded-l-lg opacity-40 group-hover:opacity-100 transition-opacity
                  ${isBullish ? 'bg-emerald-400' : isBearish ? 'bg-rose-400' : 'bg-text-muted'}
                `} />

                {/* Pattern Header */}
                <div className="flex justify-between items-start pl-1">
                  <span className="text-[10px] font-bold text-text-primary tracking-tight truncate max-w-[160px]">
                    {p.pattern_type}
                  </span>
                  <span className={`
                    flex items-center gap-0.5 px-1 py-0.5 rounded text-[8px] font-black uppercase tracking-wider border
                    ${isBullish 
                      ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' 
                      : isBearish 
                        ? 'bg-rose-500/10 text-rose-400 border-rose-500/20' 
                        : 'bg-elevated text-text-muted border-border-default'
                    }
                  `}>
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

                {/* Pattern Description */}
                <p className="text-[9px] text-text-muted leading-relaxed pl-1">
                  {p.description}
                </p>

                {/* Confidence Bar */}
                <div className="flex items-center gap-1.5 pl-1 mt-0.5">
                  <span className="text-[8px] text-text-muted/60 font-bold">Conf:</span>
                  <div className="flex-grow h-1 bg-surface border border-border-default/40 rounded-full overflow-hidden">
                    <div 
                      className={`
                        h-full rounded-full transition-all duration-300
                        ${isBullish 
                          ? 'bg-gradient-to-r from-emerald-500 to-emerald-400' 
                          : isBearish 
                            ? 'bg-gradient-to-r from-rose-500 to-rose-400' 
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
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
