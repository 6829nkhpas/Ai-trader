'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Terminal, Shield, Target, Zap, Rocket, CheckCircle2, Cpu, Loader2 } from 'lucide-react';
import { useTradeStore } from '../../store/useTradeStore';
import { useQuantStore } from '../../store/useQuantStore';

// Pulsing indicator when AI is waiting for market conditions
const WatchingIndicator = () => (
  <div className="flex items-center gap-3 p-3 bg-teal-500/10 border border-teal-500/20 rounded-xl animate-pulse mt-2 shadow-inner shadow-teal-500/5">
    <div className="flex h-5 w-5 items-center justify-center rounded-full bg-teal-500/20 text-teal-400 text-xs font-bold animate-ping">
      ⏸️
    </div>
    <div className="flex flex-col">
      <span className="text-[11px] font-bold text-teal-300">⏸️ AI paused. Waiting for market condition to trigger...</span>
      <span className="text-[9px] text-teal-400/60 font-mono">Condition watcher registered in background</span>
    </div>
  </div>
);

// Premium Markdown inline bold parser helper
function parseInlineMarkdown(text: string) {
  const parts = text.split(/\*\*([\s\S]*?)\*\*/g);
  return parts.map((part, i) => {
    if (i % 2 === 1) {
      return (
        <strong key={i} className="font-bold text-teal-300">
          {part}
        </strong>
      );
    }
    return part;
  });
}

// Custom-styled beautiful markdown renderer for agent terminal
const MarkdownRenderer = ({ content }: { content: string }) => {
  const lines = content.split('\n');
  return (
    <div className="space-y-1.5 text-[10.5px] font-sans leading-relaxed tracking-wide text-slate-100/90">
      {lines.map((line, idx) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={idx} className="h-1" />;

        // Header 3 (### Header)
        if (line.startsWith('### ')) {
          return (
            <h3
              key={idx}
              className="text-[11px] font-black text-emerald-400 border-b border-slate-800/40 pb-1 mt-3 mb-1.5 uppercase tracking-widest flex items-center gap-1.5 select-none"
            >
              <Target size={11} className="text-emerald-400" />
              {line.replace('### ', '')}
            </h3>
          );
        }

        // Header 2 (## Header)
        if (line.startsWith('## ')) {
          return (
            <h2
              key={idx}
              className="text-xs font-black text-teal-300 border-b border-teal-500/10 pb-1 mt-4 mb-2 tracking-widest uppercase flex items-center gap-1.5 select-none"
            >
              <Cpu size={12} className="text-teal-400" />
              {line.replace('## ', '')}
            </h2>
          );
        }

        // Bullet lists (- item or * item)
        if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
          const listContent = trimmed.substring(2);
          return (
            <div key={idx} className="flex items-start gap-2 pl-2 my-0.5 text-slate-200">
              <span className="text-emerald-500/80 font-bold select-none mt-0.5">•</span>
              <span className="flex-1">{parseInlineMarkdown(listContent)}</span>
            </div>
          );
        }

        // Numbered lists (1. item, etc.)
        const numMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
        if (numMatch) {
          const num = numMatch[1];
          const listContent = numMatch[2];
          return (
            <div key={idx} className="flex items-start gap-2.5 pl-2 my-1.5 text-slate-200">
              <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded bg-emerald-500/15 text-emerald-400 text-[8.5px] font-black font-mono border border-emerald-500/20 mt-0.5 select-none">
                {num}
              </span>
              <span className="flex-1">{parseInlineMarkdown(listContent)}</span>
            </div>
          );
        }

        // Standard line
        return (
          <p key={idx} className="text-slate-300">
            {parseInlineMarkdown(line)}
          </p>
        );
      })}
    </div>
  );
};

export default function AgentTerminal() {
  const reasoningSteps = useQuantStore((s) => s.reasoningSteps);
  const sessionStatus = useQuantStore((s) => s.sessionStatus);
  const finalTrade = useQuantStore((s) => s.finalTrade);
  const handleStreamEvent = useQuantStore((s) => s.handleStreamEvent);
  const resetTerminal = useQuantStore((s) => s.resetTerminal);
  const analysisError = useQuantStore((s) => s.analysisError);

  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  
  const terminalEndRef = useRef<HTMLDivElement>(null);
  const [executed, setExecuted] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);

  // Dynamic listen/unlisten to "deep-quant-stream"
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    
    async function setupListener() {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        unlisten = await listen<any>('deep-quant-stream', (event) => {
          handleStreamEvent(event.payload);
        });
      } catch (err) {
        console.error('Failed to register deep-quant-stream listener:', err);
      }
    }
    
    setupListener();
    
    return () => {
      if (unlisten) {
        unlisten();
      }
    };
  }, [handleStreamEvent]);

  // Auto-scroll to bottom of terminal when reasoningSteps changes
  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [reasoningSteps, sessionStatus]);

  // Reset execution status if the final trade plan changes
  useEffect(() => {
    setExecuted(false);
  }, [finalTrade]);

  // Parse entry, target, and stop loss from execution_plan text
  const parsePlanDetails = () => {
    if (!finalTrade) return null;
    
    const executionPlan = finalTrade.execution_plan || '';
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
          <span className="w-2 h-2 rounded-full bg-red-500/80 hover:bg-red-400 cursor-pointer" onClick={resetTerminal} title="Reset logs" />
          <span className="w-2 h-2 rounded-full bg-amber-500/80" />
          <span className="w-2 h-2 rounded-full bg-emerald-500/80" />
        </div>
      </div>

      {/* Terminal Scrolling Log */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3.5 scrollbar-thin scrollbar-track-slate-950/20 scrollbar-thumb-slate-800">
        {reasoningSteps.map((step) => {
          if (step.type === 'message') {
            // Strip raw JSON objects from display but show all reasoning text progressively
            const cleanContent = step.content.replace(/\{[\s\S]*\}/g, '').trim();
            if (!cleanContent) return null;

            return (
              <div key={step.id} className="flex justify-start animate-fade-in font-sans w-full">
                <div className="max-w-[95%] bg-slate-900/40 text-slate-100 border border-slate-800/40 rounded-xl px-3 py-2 text-[11px] leading-relaxed shadow-sm w-full">
                  <div className="flex items-center gap-1.5 text-[9px] text-emerald-400 font-bold uppercase tracking-wider mb-1 select-none">
                    <Cpu size={10} className="animate-pulse" />
                    Agent Reasoning
                  </div>
                  <MarkdownRenderer content={cleanContent} />
                </div>
              </div>
            );
          } else if (step.type === 'tool_start') {
            // Check if a matching tool_end exists for this tool call
            const isCompleted = reasoningSteps.some(
              (s) => s.type === 'tool_end' && s.toolName === step.toolName && s.timestamp > step.timestamp
            );

            return (
              <div key={step.id} className="flex justify-start animate-fade-in font-mono pl-1 w-full my-1.5">
                <div className={`rounded-xl px-3 py-2 text-[10px] leading-relaxed shadow-sm w-full max-w-[95%] ${
                  isCompleted
                    ? 'bg-slate-900/20 border border-emerald-500/15'
                    : 'bg-slate-950/60 border border-teal-500/20'
                }`}>
                  <div className={`flex items-center gap-2 font-bold uppercase tracking-wider mb-1.5 select-none ${
                    isCompleted ? 'text-emerald-400' : 'text-teal-400'
                  }`}>
                    {isCompleted ? (
                      <CheckCircle2 size={10} className="text-emerald-400 shrink-0" />
                    ) : (
                      <Loader2 size={10} className="animate-spin text-teal-400 shrink-0" />
                    )}
                    <span>{isCompleted ? 'Tool Completed' : 'Executing Tool'}</span>
                    <span className={`ml-auto text-[8px] font-mono px-1.5 py-0.5 rounded ${
                      isCompleted
                        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/25'
                        : 'bg-teal-500/10 text-teal-400 border border-teal-500/20'
                    }`}>
                      {isCompleted ? 'SUCCESS' : 'ACTIVE'}
                    </span>
                  </div>
                  <div className={`text-[11px] font-extrabold font-mono tracking-wide ${
                    isCompleted ? 'text-emerald-300' : 'text-teal-300'
                  }`}>
                    {step.toolName}
                  </div>
                  {step.args && Object.keys(step.args).length > 0 && (
                    <div className="mt-1.5 bg-black/40 border border-slate-800/60 rounded px-2 py-1 text-[8.5px] text-slate-400 leading-normal font-sans space-y-0.5">
                      {Object.entries(step.args).map(([k, v]) => (
                        <div key={k} className="flex gap-1.5">
                          <span className="text-slate-500 font-semibold">{k}:</span>
                          <span className="text-teal-300/80 font-mono">{JSON.stringify(v)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          } else if (step.type === 'tool_end') {
            // Skip rendering — the tool_start card already shows completed state
            return null;
          } else {
            // Legacy / fallback 'tool'
            return (
              <div key={step.id} className="flex justify-start animate-fade-in font-mono pl-2">
                <div className="text-[10px] text-teal-400/80 font-semibold select-none flex items-center gap-1.5 py-0.5">
                  <span className="text-teal-500/60">&gt;</span>
                  {step.content}
                </div>
              </div>
            );
          }
        })}

        {/* Watching Indicator inside scroll log */}
        {sessionStatus === 'watching' && <WatchingIndicator />}

        {/* Streaming spinner inside console */}
        {sessionStatus === 'running' && (
          <div className="flex items-center gap-2 pl-3 py-2 text-[10px] text-emerald-500/60 animate-pulse">
            <Loader2 size={11} className="animate-spin text-emerald-500" />
            <span>Agent evaluating microstructure signals...</span>
          </div>
        )}

        {/* Error message display */}
        {sessionStatus === 'error' && (
          <div className="flex items-start gap-3 p-3.5 bg-rose-500/10 border border-rose-500/25 rounded-xl mt-2 select-text font-sans shadow-lg shadow-rose-950/20">
            <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-rose-500/20 text-rose-400 text-[10px] font-bold select-none mt-0.5">
              ⚠
            </div>
            <div className="flex flex-col">
              <span className="text-[11px] font-bold text-rose-400">Deep Quant Analysis Error</span>
              <span className="text-[10px] text-rose-300/80 mt-1 leading-relaxed">
                The LangGraph agent loop returned a pipeline error. This usually occurs if your third-party LLM key (e.g. HuggingFace, Groq, or OpenAI) is expired, rate-limited, or unpaid.
              </span>
              <span className="text-[9px] font-mono text-rose-400 bg-rose-950/30 rounded border border-rose-500/10 px-2 py-1 mt-2 leading-normal">
                {analysisError || "Connection refused: Python service port :8086 unreachable."}
              </span>
            </div>
          </div>
        )}

        <div ref={terminalEndRef} />
      </div>

      {/* Execution Plan Card Handoff */}
      {sessionStatus === 'complete' && finalTrade && parsedPlan && (
        <div className="p-4 bg-slate-950 border-t border-slate-800/80 animate-slide-up shadow-xl shrink-0">
          <div className="flex items-center gap-2 mb-3">
            <Shield size={12} className="text-emerald-400" />
            <h3 className="text-[10px] font-bold text-slate-300 uppercase tracking-widest">
              Actionable Trade Plan Ready
            </h3>
            <span className="ml-auto rounded-md px-2 py-0.5 text-[9px] font-black tracking-widest bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
              {finalTrade.conviction_score}% CONVICTION
            </span>
          </div>

          <div className="text-xs text-gray-300 italic mb-3 border-l-2 border-emerald-500 pl-2">
            "{finalTrade.setup_validation}"
          </div>

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
