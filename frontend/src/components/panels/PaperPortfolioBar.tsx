'use client';

import React from 'react';
import { ChevronUp, TrendingUp, TrendingDown } from 'lucide-react';
import { calculateRealizedPnL } from '../../types/home';

interface PaperPortfolioBarProps {
  paperPortfolio: any;
  onExpand: () => void;
}

/** Collapsed paper-portfolio summary strip with live stats. */
const PaperPortfolioBar: React.FC<PaperPortfolioBarProps> = ({ paperPortfolio, onExpand }) => {
  const totalPnL = paperPortfolio?.trade_history.reduce(
    (sum: number, pos: any) => sum + calculateRealizedPnL(pos),
    0,
  ) ?? 0;

  return (
    <div className="px-3 py-1.5 border-t border-border-default bg-surface backdrop-blur-sm flex items-center justify-between transition-all duration-300">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
        {/* Live indicator and Title */}
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
          </span>
          <span className="text-[10px] font-black uppercase tracking-wider text-text-primary">
            Simulated Portfolio
          </span>
        </div>

        {/* Stats summary */}
        {paperPortfolio && (
          <div className="flex items-center gap-4 text-xs font-mono">
            <div className="flex items-center gap-1.5 border-r border-border-default/50 pr-4">
              <span className="text-[9px] uppercase font-bold text-text-muted font-sans">Equity:</span>
              <span className="font-bold text-text-primary">
                ₹{paperPortfolio.balance.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
            <div className="flex items-center gap-1.5 border-r border-border-default/50 pr-4">
              <span className="text-[9px] uppercase font-bold text-text-muted font-sans">PnL:</span>
              <span className={`font-black flex items-center gap-0.5 ${totalPnL >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`}>
                {totalPnL >= 0 ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                {totalPnL >= 0 ? '+' : ''}₹{totalPnL.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[9px] uppercase font-bold text-text-muted font-sans">Positions:</span>
              <span className="font-bold text-text-primary">{paperPortfolio.active_positions.length} Active</span>
            </div>
          </div>
        )}
      </div>

      <button
        type="button"
        onClick={onExpand}
        className="flex items-center gap-1 rounded bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-1 text-[9px] font-bold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider hover:bg-emerald-500/20 transition-all duration-150"
      >
        <ChevronUp size={10} />
        Expand Portfolio
      </button>
    </div>
  );
};

export default PaperPortfolioBar;
