import React, { useState, useEffect } from 'react';
import { Shield, Rocket, CheckCircle2, Loader2 } from 'lucide-react';
import { AiExecutionPlan, ExecutionLevels } from '../../../store/useQuantStore';
import { useTradeStore } from '../../../store/useTradeStore';
import { highlightNumbers } from './textHighlighter';

interface ActionableTradePlanProps {
  finalTrade: AiExecutionPlan & { execution_levels: ExecutionLevels };
  selectedSymbol: string;
}

export default function ActionableTradePlan({
  finalTrade,
  selectedSymbol,
}: ActionableTradePlanProps) {
  const [executed, setExecuted] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);

  // Reset execution status if the final trade plan changes
  useEffect(() => {
    setExecuted(false);
  }, [finalTrade]);

  const side = finalTrade.action === 'SELL' ? 'SELL' : 'BUY';
  const entryPrice = finalTrade.execution_levels.entry;
  const takeProfit = finalTrade.execution_levels.take_profit;
  const stopLoss = finalTrade.execution_levels.stop_loss;

  const handleApproveAndExecute = async () => {
    if (executed || isExecuting) return;
    setIsExecuting(true);

    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const resMsg = await invoke<string>('execute_paper_trade', {
        symbol: selectedSymbol,
        side,
        entryPrice,
        stopLoss,
        takeProfit,
      });

      useTradeStore.getState().addSystemLog('INFO', `🚀 [Paper Engine] ${resMsg}`);

      // Refresh positions
      await useTradeStore.getState().fetchPaperPortfolio();
      setExecuted(true);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      console.error('Failed to execute paper trade:', err);
      useTradeStore.getState().addSystemLog('ERROR', `Failed to execute paper trade: ${errMsg}`);
    } finally {
      setIsExecuting(false);
    }
  };

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
        <div className="grid grid-cols-3 gap-px bg-border-default/40 border-b border-border-default/60">
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

        {/* Execute button */}
        <div className="px-3 py-2.5">
          <button
            type="button"
            disabled={executed || isExecuting}
            onClick={handleApproveAndExecute}
            className={`
              w-full flex items-center justify-center gap-2 rounded-sm py-2 text-[10px] font-bold uppercase tracking-widest transition-all duration-300 border
              ${
                executed
                  ? 'bg-elevated text-text-muted border-border-default cursor-default'
                  : isExecuting
                  ? 'bg-elevated text-text-muted border-border-default cursor-wait'
                  : 'bg-text-primary text-surface border-text-primary hover:bg-text-secondary hover:border-text-secondary active:scale-[0.98]'
              }
            `}
          >
            {isExecuting ? (
              <>
                <Loader2 size={12} className="animate-spin" />
                Executing simulated trade...
              </>
            ) : executed ? (
              <>
                <CheckCircle2 size={12} />
                Simulated Trade Executed
              </>
            ) : (
              <>
                <Rocket size={12} />
                Approve & Execute (Virtual)
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
