import React, { useState } from 'react';
import { useQuantStore } from '../../../store/useQuantStore';
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

  return (
    <div className="mx-3 my-2 p-3 rounded-2xl border border-white/5 bg-slate-900/60 backdrop-blur-xl shadow-xl">
      <div className="flex items-center gap-1.5 mb-2.5">
        <Sparkles size={13} className="text-emerald-400 animate-pulse" />
        <span className="text-[11px] font-black uppercase tracking-wider text-slate-200">
          Live Pattern Scanner
        </span>
      </div>

      {/* Timeframe Selector */}
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
                flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] font-bold transition-all duration-200 shrink-0
                ${isActive 
                  ? 'bg-gradient-to-r from-emerald-600 to-teal-600 text-white shadow-md shadow-emerald-500/10 scale-[1.02]' 
                  : 'bg-white/5 text-slate-400 hover:bg-white/10 hover:text-slate-300'
                }
              `}
            >
              <span>{tf}</span>
              {isFetchingPatterns ? (
                <Loader2 size={8} className="animate-spin text-slate-500" />
              ) : count > 0 ? (
                <span className={`
                  flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[8px] font-black
                  ${isActive ? 'bg-white text-emerald-800' : 'bg-emerald-500/20 text-emerald-400'}
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
      <div className="mt-2 space-y-2 max-h-[220px] overflow-y-auto scrollbar-thin pr-1">
        {isFetchingPatterns ? (
          // Loading skeletons
          <div className="space-y-2 py-2">
            {[1, 2].map((i) => (
              <div key={i} className="animate-pulse flex flex-col gap-1.5 p-2 rounded-xl bg-white/5 border border-white/5">
                <div className="flex justify-between items-center">
                  <div className="h-3 w-20 bg-white/10 rounded" />
                  <div className="h-4 w-12 bg-white/10 rounded-full" />
                </div>
                <div className="h-2 w-full bg-white/5 rounded" />
                <div className="h-2 w-3/4 bg-white/5 rounded" />
              </div>
            ))}
          </div>
        ) : patterns.length === 0 ? (
          // Empty State
          <div className="flex flex-col items-center justify-center py-6 text-center bg-white/[0.02] border border-white/5 rounded-xl">
            <Activity size={16} className="text-slate-600 mb-1" />
            <span className="text-[10px] font-semibold text-slate-400">No active patterns detected</span>
            <span className="text-[8px] text-slate-600 mt-0.5">Timeframe: {selectedTf}</span>
          </div>
        ) : (
          patterns.map((p, idx) => {
            const isBullish = p.sentiment.toLowerCase() === 'bullish';
            const isBearish = p.sentiment.toLowerCase() === 'bearish';

            return (
              <div
                key={idx}
                className={`
                  group relative flex flex-col gap-1.5 p-2.5 rounded-xl border transition-all duration-300 hover:scale-[1.01] hover:shadow-lg
                  ${isBullish 
                    ? 'bg-gradient-to-br from-emerald-500/5 to-emerald-500/[0.02] border-emerald-500/10 hover:border-emerald-500/30' 
                    : isBearish 
                      ? 'bg-gradient-to-br from-rose-500/5 to-rose-500/[0.02] border-rose-500/10 hover:border-rose-500/30' 
                      : 'bg-gradient-to-br from-slate-500/5 to-slate-500/[0.02] border-slate-500/10 hover:border-slate-800'
                  }
                `}
              >
                {/* Glowing edge highlight */}
                <div className={`
                  absolute top-0 bottom-0 left-0 w-0.5 rounded-l-xl opacity-60 group-hover:opacity-100 transition-opacity
                  ${isBullish ? 'bg-emerald-400' : isBearish ? 'bg-rose-400' : 'bg-slate-400'}
                `} />

                {/* Pattern Header */}
                <div className="flex justify-between items-start pl-1">
                  <span className="text-[11px] font-extrabold text-slate-100 tracking-tight">
                    {p.pattern_type}
                  </span>
                  <span className={`
                    flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[8px] font-black uppercase tracking-wider
                    ${isBullish 
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' 
                      : isBearish 
                        ? 'bg-rose-500/10 text-rose-400 border border-rose-500/20' 
                        : 'bg-slate-500/10 text-slate-300 border border-slate-700'
                    }
                  `}>
                    {isBullish ? (
                      <TrendingUp size={9} />
                    ) : isBearish ? (
                      <TrendingDown size={9} />
                    ) : (
                      <Minus size={9} />
                    )}
                    {p.sentiment}
                  </span>
                </div>

                {/* Pattern Description */}
                <p className="text-[9px] text-slate-400 leading-normal pl-1">
                  {p.description}
                </p>

                {/* Confidence Bar */}
                <div className="flex items-center gap-2 pl-1 mt-1">
                  <span className="text-[8px] text-slate-500 font-bold">Conf:</span>
                  <div className="flex-grow h-1.5 bg-slate-950 rounded-full overflow-hidden border border-white/5">
                    <div 
                      className={`
                        h-full rounded-full transition-all duration-500
                        ${isBullish 
                          ? 'bg-gradient-to-r from-emerald-600 to-emerald-400' 
                          : isBearish 
                            ? 'bg-gradient-to-r from-rose-600 to-rose-400' 
                            : 'bg-gradient-to-r from-slate-600 to-slate-400'
                        }
                      `}
                      style={{ width: `${p.confidence * 100}%` }}
                    />
                  </div>
                  <span className="text-[9px] font-black text-slate-300">
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
