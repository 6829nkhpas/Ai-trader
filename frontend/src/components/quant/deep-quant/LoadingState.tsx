'use client';

import React from 'react';
import { Loader2 } from 'lucide-react';

const LOADING_PHASES = [
  'Aggregating 50+ Technical Indicators...',
  'Scanning Candlestick Patterns...',
  'Evaluating Institutional Strategies...',
  'Fetching Live News Context...',
  'Constructing Master Prompt...',
  'Awaiting DeepSeek Analysis...',
];

interface LoadingStateProps {
  agentStatus: string;
}

export default function LoadingState({ agentStatus }: LoadingStateProps) {
  const [phaseIdx, setPhaseIdx] = React.useState(0);

  React.useEffect(() => {
    const timer = setInterval(() => {
      setPhaseIdx((prev) => (prev + 1) % LOADING_PHASES.length);
    }, 2500);
    return () => clearInterval(timer);
  }, []);

  return (
    <div className="flex flex-col items-center justify-center gap-4 py-8 px-4">
      {/* Pulsing orb */}
      <div className="relative">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-emerald-500/20 to-teal-500/20 border border-emerald-500/30">
          <Loader2 size={28} className="text-emerald-400 animate-spin" />
        </div>
        <div className="absolute -inset-2 rounded-3xl bg-emerald-500/5 animate-pulse" />
        <div className="absolute -top-1 -right-1 h-3 w-3 rounded-full bg-emerald-500 animate-ping" />
      </div>

      <div className="text-center">
        <p className="text-[11px] font-semibold text-emerald-300 animate-pulse transition-all duration-500">
          {LOADING_PHASES[phaseIdx]}
        </p>
        <p className="text-[9px] text-text-muted/50 mt-1.5">
          This may take 10–30 seconds
        </p>
      </div>

      {/* Real-time status display */}
      <div className="w-full max-w-[240px] p-2.5 bg-black/40 border border-emerald-500/20 rounded font-mono text-[10px] flex items-center space-x-2 animate-pulse text-emerald-400">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping shrink-0" />
        <span className="truncate">{agentStatus}</span>
      </div>

      {/* Phase dots */}
      <div className="flex gap-1">
        {LOADING_PHASES.map((_, i) => (
          <div
            key={i}
            className={`h-1 w-1 rounded-full transition-all duration-300 ${
              i <= phaseIdx ? 'bg-emerald-400' : 'bg-slate-700'
            }`}
          />
        ))}
      </div>
    </div>
  );
}
