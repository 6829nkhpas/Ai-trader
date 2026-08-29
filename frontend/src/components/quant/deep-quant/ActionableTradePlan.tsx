import React from 'react';
import { Shield } from 'lucide-react';
import { AiExecutionPlan, ExecutionLevels } from '../../../store/useQuantStore';
import { highlightNumbers } from './textHighlighter';

/**
 * The agent's committed trade plan, as a read-only research card.
 *
 * This used to carry an "Approve & Execute (Virtual)" button that opened a
 * position in a simulated paper portfolio. That whole feature has been removed
 * from the app, so the card is now purely what the analysis produced: the
 * conviction, the setup rationale, and the entry/target/stop levels. Those are
 * real outputs of the run and are unchanged.
 *
 * There is deliberately NO execution control here. The product has no order path
 * of any kind — see docs/compliance/BRAND_GUIDELINES.md §1.1.
 */
interface ActionableTradePlanProps {
  finalTrade: AiExecutionPlan & { execution_levels: ExecutionLevels };
}

export default function ActionableTradePlan({ finalTrade }: ActionableTradePlanProps) {
  const side = finalTrade.action === 'SELL' ? 'SELL' : 'BUY';
  const entryPrice = finalTrade.execution_levels.entry;
  const takeProfit = finalTrade.execution_levels.take_profit;
  const stopLoss = finalTrade.execution_levels.stop_loss;

  return (
    <div className="flex justify-start animate-fade-in font-sans w-full mt-2 mb-2 select-text">
      <div className="w-full rounded border border-emerald-500/15 bg-gradient-to-r from-emerald-500/5 via-elevated/40 to-elevated/10 shadow-md">
        {/* Header */}
        <div className="flex items-center gap-2 px-3 py-2.5 border-b border-border-default bg-surface/10 select-none">
          <Shield size={12} className="text-emerald-500" />
          <h3 className="text-[10px] font-bold text-emerald-500 uppercase tracking-widest">
            Actionable Trade Plan Ready
          </h3>
          <span className="ml-auto rounded-sm px-2 py-0.5 text-[9px] font-black tracking-widest bg-emerald-500/10 text-emerald-500 border border-emerald-500/20">
            {finalTrade.conviction_score}% CONVICTION
          </span>
        </div>

        {/* Setup validation */}
        {finalTrade.setup_validation && (
          <div className="px-3 py-2.5 border-b border-border-default/60">
            <p className="text-[11px] text-text-secondary leading-relaxed italic border-l-2 border-emerald-500/30 pl-2.5">
              &ldquo;{highlightNumbers(finalTrade.setup_validation)}&rdquo;
            </p>
          </div>
        )}

        {/* Price levels grid */}
        <div className="grid grid-cols-3 gap-px bg-border-default/40">
          <div className="bg-surface px-3 py-2.5 flex flex-col gap-0.5">
            <span className="text-[8px] text-text-muted font-bold uppercase tracking-widest select-none">
              Entry ({side})
            </span>
            <span className="text-sm text-text-primary font-extrabold font-sans">
              ₹{entryPrice.toFixed(2)}
            </span>
          </div>
          <div className="bg-surface px-3 py-2.5 flex flex-col gap-0.5">
            <span className="text-[8px] text-text-muted font-bold uppercase tracking-widest select-none">
              Target (TP)
            </span>
            <span className="text-sm text-emerald-400 font-extrabold font-sans">
              ₹{takeProfit.toFixed(2)}
            </span>
          </div>
          <div className="bg-surface px-3 py-2.5 flex flex-col gap-0.5">
            <span className="text-[8px] text-text-muted font-bold uppercase tracking-widest select-none">
              Stop Loss (SL)
            </span>
            <span className="text-sm text-rose-400 font-extrabold font-sans">
              ₹{stopLoss.toFixed(2)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
