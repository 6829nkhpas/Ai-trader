'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Terminal, Shield, Target, Zap, Rocket, CheckCircle2, Cpu, Loader2 } from 'lucide-react';
import { useTradeStore } from '../../store/useTradeStore';
import { useQuantStore } from '../../store/useQuantStore';

export default function AgentTerminal() {
  const agentChatLog = useTradeStore((s) => s.agentChatLog);
  const finalTradePlan = useTradeStore((s) => s.finalTradePlan);
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  const clearAgentChatLog = useTradeStore((s) => s.clearAgentChatLog);
  
  const terminalEndRef = useRef<HTMLDivElement>(null);
  const [executed, setExecuted] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);

  // Auto-scroll to bottom of terminal
  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [agentChatLog]);

  // Reset execution status if the final trade plan changes
  useEffect(() => {
    setExecuted(false);
  }, [finalTradePlan]);

  // Parse entry, target, and stop loss from execution_plan text
  const parsePlanDetails = () => {
    if (!finalTradePlan) return null;
    
    const executionPlan = finalTradePlan.execution_plan || '';
    const closePrice = useTradeStore.getState().ohlcCandles.find(c => c.symbol === selectedSymbol)?.close || 0;
    
    let entryPrice = closePrice;
    let stopLoss = 0;
    let takeProfit = 0;
    let side = 'BUY';

    const entryMatch = executionPlan.match(/entry:\s*([\d.]+)/i);
    const slMatch = executionPlan.match(/stop-loss:\s*([\d.]+)/i) || executionPlan.match(/sl:\s*([\d.]+)/i);
    const tpMatch = executionPlan.match(/target\s*1?:\s*([\d.]+)/i) || executionPlan.match(/target:\s*([\d.]+)/i) || executionPlan.match(/tp:\s*([\d.]+)/i);
    const sideMatch = executionPlan.match(/side:\s*(buy|sell)/i) || executionPlan.match(/(buy|sell)/i);

    if (entryMatch) entryPrice = parseFloat(entryMatch[1]);
    if (slMatch) stopLoss = parseFloat(slMatch[1]);
    if (tpMatch) takeProfit = parseFloat(tpMatch[1]);
    if (sideMatch) {
      const matchedSide = sideMatch[1].toUpperCase();
      if (matchedSide === 'BUY' || matchedSide === 'SELL') {
        side = matchedSide;
      }
    }

    if (entryPrice <= 0) entryPrice = closePrice;
    if (stopLoss <= 0) stopLoss = side === 'BUY' ? entryPrice * 0.98 : entryPrice * 1.02;
    if (takeProfit <= 0) takeProfit = side === 'BUY' ? entryPrice * 1.05 : entryPrice * 0.95;

    return { side, entryPrice, stopLoss, takeProfit };
  };

  const parsedPlan = parsePlanDetails();

  const handleApproveAndExecute = async () => {
    if (!parsedPlan || executed || isExecuting) return;
    setIsExecuting(true);
    
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const resMsg = await invoke<string>('execute_paper_trade', {
        symbol: selectedSymbol,
        side: parsedPlan.side,
        entryPrice: parsedPlan.entryPrice,
        stopLoss: parsedPlan.stopLoss,
        takeProfit: parsedPlan.takeProfit,
      });

      useTradeStore.getState().addSystemLog('INFO', `🚀 [Paper Engine] ${resMsg}`);
      
      // Refresh positions
      await useTradeStore.getState().fetchPaperPortfolio();
      setExecuted(true);
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      console.error('Failed to execute paper trade:', err);
      useTradeStore.getState().addSystemLog('ERROR', `Failed to execute trade: ${errMsg}`);
    } finally {
      setIsExecuting(false);
    }
  };

  return (
    <div className="flex h-full flex-col font-mono bg-black/85 border border-slate-800 rounded-xl overflow-hidden shadow-2xl relative">
      {/* Terminal Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-slate-900/90 border-b border-slate-800 select-none">
        <div className="flex items-center gap-2">
          <Terminal size={14} className="text-emerald-400 animate-pulse" />
          <span className="text-[10px] text-slate-300 font-bold uppercase tracking-wider">
            Glass-Box Agent Console
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-red-500/80 hover:bg-red-400 cursor-pointer" onClick={clearAgentChatLog} title="Reset logs" />
          <span className="w-2 h-2 rounded-full bg-amber-500/80" />
          <span className="w-2 h-2 rounded-full bg-emerald-500/80" />
        </div>
      </div>

      {/* Terminal Scrolling Log */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3.5 scrollbar-thin scrollbar-track-slate-950/20 scrollbar-thumb-slate-800">
        {agentChatLog.map((log, idx) => {
          if (log.role === 'user') {
            return (
              <div key={idx} className="flex justify-end animate-fade-in">
                <div className="max-w-[85%] bg-slate-800/60 text-slate-200 border border-slate-700/50 rounded-2xl rounded-tr-sm px-3.5 py-2 text-[11px] leading-relaxed shadow-sm">
                  <div className="text-[9px] text-slate-400/80 font-bold uppercase tracking-widest mb-1 select-none">
                    user_trigger
                  </div>
                  {log.content}
                </div>
              </div>
            );
          } else if (log.role === 'system') {
            return (
              <div key={idx} className="flex justify-start animate-fade-in font-mono">
                <div className="max-w-[90%] bg-slate-950/80 text-slate-400 border border-slate-800/40 rounded-xl px-3.5 py-2 text-[10px] leading-relaxed">
                  <span className="text-blue-400 font-bold mr-1.5">[SYS]</span>
                  {log.content}
                </div>
              </div>
            );
          } else {
            // assistant (AI Thought Log)
            return (
              <div key={idx} className="flex justify-start animate-fade-in font-mono">
                <div className="max-w-[90%] text-emerald-400 rounded-xl py-0.5 text-[11px] leading-relaxed">
                  <div className="flex items-center gap-1 text-[9px] text-emerald-500/70 font-bold uppercase tracking-widest mb-1 select-none">
                    <Cpu size={10} className="animate-spin" style={{ animationDuration: '4s' }} />
                    agent_thought
                  </div>
                  <div className="pl-3 border-l border-emerald-500/20 whitespace-pre-wrap">
                    {log.content}
                  </div>
                </div>
              </div>
            );
          }
        })}

        {/* Streaming spinner inside console */}
        {!finalTradePlan && agentChatLog.length > 0 && (
          <div className="flex items-center gap-2 pl-3 py-2 text-[10px] text-emerald-500/60 animate-pulse">
            <Loader2 size={11} className="animate-spin text-emerald-500" />
            <span>Agent evaluating microstructure signals...</span>
          </div>
        )}

        <div ref={terminalEndRef} />
      </div>

      {/* Execution Plan Card Handoff */}
      {finalTradePlan && parsedPlan && (
        <div className="p-4 bg-slate-950 border-t border-slate-800/80 animate-slide-up shadow-xl shrink-0">
          <div className="flex items-center gap-2 mb-3">
            <Shield size={12} className="text-emerald-400" />
            <h3 className="text-[10px] font-bold text-slate-300 uppercase tracking-widest">
              Actionable Trade Plan Ready
            </h3>
            <span className="ml-auto rounded-md px-2 py-0.5 text-[9px] font-black tracking-widest bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              {finalTradePlan.conviction_score}% CONVICTION
            </span>
          </div>

          <div className="text-xs text-gray-300 italic mb-3 border-l-2 border-emerald-500 pl-2">
            "{finalTradePlan.setup_validation}"
          </div>

          {/* Parameters grid */}
          <div className="grid grid-cols-3 gap-2 mb-3">
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-2 flex flex-col justify-center">
              <span className="text-[9px] text-slate-500 font-bold uppercase tracking-wider select-none">Entry ({parsedPlan.side})</span>
              <span className="text-[13px] text-slate-200 font-extrabold mt-0.5 font-mono">₹{parsedPlan.entryPrice.toFixed(2)}</span>
            </div>
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-2 flex flex-col justify-center">
              <span className="text-[9px] text-slate-500 font-bold uppercase tracking-wider select-none">Target (TP)</span>
              <span className="text-[13px] text-emerald-400 font-extrabold mt-0.5 font-mono">₹{parsedPlan.takeProfit.toFixed(2)}</span>
            </div>
            <div className="bg-slate-900 border border-slate-800 rounded-lg p-2 flex flex-col justify-center">
              <span className="text-[9px] text-slate-500 font-bold uppercase tracking-wider select-none">Stop Loss (SL)</span>
              <span className="text-[13px] text-rose-400 font-extrabold mt-0.5 font-mono">₹{parsedPlan.stopLoss.toFixed(2)}</span>
            </div>
          </div>

          {/* Interactive Handoff Button */}
          <button
            type="button"
            disabled={executed || isExecuting}
            onClick={handleApproveAndExecute}
            className={`
              w-full flex items-center justify-center gap-2 rounded-xl py-2.5 text-xs font-black uppercase tracking-widest transition-all duration-300
              ${executed
                ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                : isExecuting
                  ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 cursor-wait'
                  : 'bg-emerald-500 text-black border border-emerald-400 hover:bg-emerald-400 hover:shadow-lg hover:shadow-emerald-500/20 active:scale-[0.98]'
              }
            `}
          >
            {isExecuting ? (
              <>
                <Loader2 size={13} className="animate-spin text-emerald-300" />
                Executing simulated trade...
              </>
            ) : executed ? (
              <>
                <CheckCircle2 size={13} className="text-emerald-400 animate-pulse" />
                Simulated Trade Executed
              </>
            ) : (
              <>
                <Rocket size={13} className="animate-bounce" />
                Approve & Execute (Virtual)
              </>
            )}
          </button>
        </div>
      )}
    </div>
  );
}
