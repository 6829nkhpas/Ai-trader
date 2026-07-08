'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Terminal, Shield, Target, Zap, Rocket, CheckCircle2, Cpu, Loader2 } from 'lucide-react';
import { useTradeStore } from '../../store/useTradeStore';
import { useQuantStore } from '../../store/useQuantStore';

import WatchingIndicator from './deep-quant/WatchingIndicator';
import MarkdownRenderer, { parseInlineMarkdown } from './deep-quant/MarkdownRenderer';
import QaMessages from './deep-quant/QaMessages';

export default function AgentTerminal() {
  const reasoningSteps = useQuantStore((s) => s.reasoningSteps);
  const sessionStatus = useQuantStore((s) => s.sessionStatus);
  const finalTrade = useQuantStore((s) => s.finalTrade);
  const resetTerminal = useQuantStore((s) => s.resetTerminal);
  const analysisError = useQuantStore((s) => s.analysisError);

  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);

  // Track the inline Q&A conversation so the console auto-scrolls as questions
  // are asked and answers stream in (the turns render inside this same log).
  const qaMessages = useQuantStore((s) => s.qaMessages);
  const qaStatus = useQuantStore((s) => s.qaStatus);

  const terminalEndRef = useRef<HTMLDivElement>(null);
  const [executed, setExecuted] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);

  // NOTE: the `deep-quant-stream` listener is registered once at the panel
  // level in DeepQuantPanel (which is always mounted), so AgentTerminal no
  // longer registers it here — doing so would double-register the listener and
  // duplicate every reasoning step. AgentTerminal only mounts after a run has
  // started, which also raced the backend stream.

  // Auto-scroll to bottom of terminal when reasoningSteps changes
  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [reasoningSteps, sessionStatus, qaMessages, qaStatus]);

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
    <div className="flex h-full flex-col font-sans bg-surface overflow-hidden relative">

      {/* Terminal Scrolling Log */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3.5 scrollbar-thin scrollbar-track-slate-950/20 scrollbar-thumb-slate-800">
        {reasoningSteps.map((step) => {
          if (step.type === 'message') {
            // Strip raw JSON objects from display but show all reasoning text progressively.
            // Bug 5 fix: If stripping JSON leaves the content empty (i.e. the entire
            // message IS a JSON trade plan), render a readable summary instead of hiding it.
            const cleanContent = step.content.replace(/\{[\s\S]*\}/g, '').trim();
            
            if (!cleanContent) {
              // Entire content was JSON — try to parse and display as a trade plan summary
              try {
                const jsonMatch = step.content.match(/\{[\s\S]*\}/);
                if (jsonMatch) {
                  const parsed = JSON.parse(jsonMatch[0]);
                  const conviction = parsed.conviction_score ?? parsed.conviction;
                  const validation = parsed.setup_validation ?? parsed.validation ?? parsed.setup;
                  const plan = parsed.execution_plan ?? parsed.plan;
                  
                  if (conviction !== undefined || validation || plan) {
                    return (
                      <div key={step.id} className="flex justify-start animate-fade-in font-sans w-full">
                        <div className="max-w-[95%] bg-elevated/40 text-text-primary border border-border-default rounded-none px-3 py-2 text-[11px] leading-relaxed shadow-sm w-full">
                          <div className="flex items-center gap-1.5 text-[9px] text-text-primary font-bold uppercase tracking-wider mb-1.5 select-none">
                            <Target size={10} />
                            Final Trade Decision
                            {conviction !== undefined && (
                              <span className="ml-auto rounded-none px-1.5 py-0.5 text-[8px] font-black bg-elevated text-text-primary border border-border-default">
                                {conviction}% CONVICTION
                              </span>
                            )}
                          </div>
                          {validation && (
                            <p className="text-text-primary mb-1">{parseInlineMarkdown(String(validation))}</p>
                          )}
                          {plan && (
                            <p className="text-text-secondary text-[10px] font-mono mt-1 border-t border-border-default/40 pt-1">
                              {parseInlineMarkdown(String(plan))}
                            </p>
                          )}
                        </div>
                      </div>
                    );
                  }
                }
              } catch {
                // JSON parse failed — skip this empty message
              }
              return null;
            }

            return (
              <div key={step.id} className="flex justify-start animate-fade-in font-sans w-full">
                <div className="max-w-[95%] bg-elevated/40 text-text-primary border border-border-default/40 rounded-none px-3 py-2 text-[11px] leading-relaxed shadow-sm w-full">
                  <div className="flex items-center gap-1.5 text-[9px] text-text-primary font-bold uppercase tracking-wider mb-1 select-none">
                    <Cpu size={10} className="animate-pulse" />
                    Agent Reasoning
                  </div>
                  <MarkdownRenderer content={cleanContent} />
                </div>
              </div>
            );
          } else if (step.type === 'tool_start') {
            // Bug 11 fix: Match tool_start → tool_end by sequential counting
            // instead of timestamp comparison (which breaks with simultaneous calls).
            // Count how many starts for this toolName appear before/at this index,
            // then check if an equal number of ends exist.
            const stepIdx = reasoningSteps.indexOf(step);
            const startsUpToHere = reasoningSteps.slice(0, stepIdx + 1).filter(
              (s) => s.type === 'tool_start' && s.toolName === step.toolName
            ).length;
            const endsAfterHere = reasoningSteps.slice(stepIdx + 1).filter(
              (s) => s.type === 'tool_end' && s.toolName === step.toolName
            ).length;
            const isCompleted = endsAfterHere >= startsUpToHere;

            return (
              <div key={step.id} className="flex justify-start animate-fade-in font-sans pl-1 w-full my-1.5">
                <div className={`rounded-none px-3 py-2 text-[10px] leading-relaxed shadow-sm w-full max-w-[95%] ${
                  isCompleted
                    ? 'bg-elevated/20 border border-border-default'
                    : 'bg-elevated/40 border border-border-default/80'
                }`}>
                  <div className="flex items-center gap-2 font-bold uppercase tracking-wider mb-1.5 select-none text-text-primary">
                    {isCompleted ? (
                      <CheckCircle2 size={10} className="text-text-primary shrink-0" />
                    ) : (
                      <Loader2 size={10} className="animate-spin text-text-muted shrink-0" />
                    )}
                    <span>{isCompleted ? 'Tool Completed' : 'Executing Tool'}</span>
                    <span className={`ml-auto text-[8px] font-mono px-1.5 py-0.5 rounded-none ${
                      isCompleted
                        ? 'bg-elevated text-text-primary border border-border-default'
                        : 'bg-elevated/40 text-text-muted border border-border-default/45'
                    }`}>
                      {isCompleted ? 'SUCCESS' : 'ACTIVE'}
                    </span>
                  </div>
                  <div className="text-[11px] font-extrabold font-mono tracking-wide text-text-primary">
                    {step.toolName}
                  </div>
                  {step.args && Object.keys(step.args).length > 0 && (
                    <div className="mt-1.5 bg-elevated/10 border border-border-default/60 rounded-none px-2 py-1 text-[8.5px] text-text-secondary leading-normal font-sans space-y-0.5">
                      {Object.entries(step.args).map(([k, v]) => (
                        <div key={k} className="flex gap-1.5">
                          <span className="text-text-muted font-semibold">{k}:</span>
                          <span className="text-text-secondary font-mono">{JSON.stringify(v)}</span>
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
              <div key={step.id} className="flex justify-start animate-fade-in font-sans pl-2">
                <div className="text-[10px] text-text-secondary font-semibold select-none flex items-center gap-1.5 py-0.5">
                  <span className="text-text-muted">&gt;</span>
                  {step.content}
                </div>
              </div>
            );
          }
        })}

        {/* Watching Indicator inside scroll log */}
        {sessionStatus === 'watching' && <WatchingIndicator />}

        {/* Empty-state guards — the console must NEVER render visually blank.
            Covers the case where the run finished (or is starting) but no
            renderable reasoning/tool/decision steps were captured (missed-event
            race, an early-ending stream that triggered a synthetic RUN_FINISHED,
            or a graph update that produced no surfaced events). */}
        {reasoningSteps.length === 0 && sessionStatus === 'running' && (
          <div className="flex items-center gap-2 pl-3 py-2 text-[10px] text-text-muted/60 animate-pulse">
            <Loader2 size={11} className="animate-spin text-text-muted" />
            <span>Connecting to Deep Quant agent — awaiting first reasoning step…</span>
          </div>
        )}

        {reasoningSteps.length === 0 && sessionStatus === 'complete' && (
          <div className="flex items-start gap-3 p-3.5 bg-amber-500/5 border border-amber-500/25 rounded-none mt-2 select-text font-sans shadow-lg shadow-amber-955/20">
            <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-none bg-amber-500/20 text-amber-500 dark:text-amber-400 text-[10px] font-bold select-none mt-0.5">
              !
            </div>
            <div className="flex flex-col">
              <span className="text-[11px] font-bold text-amber-500 dark:text-amber-400">No reasoning was streamed</span>
              <span className="text-[10px] text-amber-600 dark:text-amber-300/80 mt-1 leading-relaxed">
                The agent run completed but produced no visible reasoning, tool, or
                decision steps. This usually means the Python agent (:8086) returned
                an empty response or the stream ended early. Press
                {' '}<span className="font-bold text-amber-500 dark:text-amber-200">Find Quant Trade</span>{' '}
                again to retry.
              </span>
              {analysisError && (
                <span className="text-[9px] font-mono text-amber-500 dark:text-amber-400 bg-amber-500/5 rounded-none border border-amber-500/20 px-2 py-1 mt-2 leading-normal">
                  {analysisError}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Streaming spinner inside console */}
        {sessionStatus === 'running' && (
          <div className="flex items-center gap-2 pl-3 py-2 text-[10px] text-text-muted/60 animate-pulse">
            <Loader2 size={11} className="animate-spin text-text-muted" />
            <span>Agent evaluating microstructure signals...</span>
          </div>
        )}

        {/* Error message display */}
        {sessionStatus === 'error' && (
          <div className="flex items-start gap-3 p-3.5 bg-rose-500/5 border border-rose-500/20 rounded-none mt-2 select-text font-sans shadow-lg shadow-rose-955/20">
            <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-none bg-rose-500/20 text-rose-500 dark:text-rose-400 text-[10px] font-bold select-none mt-0.5">
              ⚠
            </div>
            <div className="flex flex-col">
              <span className="text-[11px] font-bold text-rose-500 dark:text-rose-400">Deep Quant Analysis Error</span>
              <span className="text-[10px] text-rose-600 dark:text-rose-300/80 mt-1 leading-relaxed">
                The LangGraph agent loop returned a pipeline error. This usually occurs if your LLM API key (e.g. Google Gemini or OpenAI) is expired, rate-limited, or out of quota.
              </span>
              <span className="text-[9px] font-mono text-rose-500 dark:text-rose-400 bg-rose-500/5 rounded-none border border-rose-500/15 px-2 py-1 mt-2 leading-normal">
                {analysisError || "Connection refused: Python service port :8086 unreachable."}
              </span>
            </div>
          </div>
        )}

        {/* Q&A turns render INLINE here — the user's questions and the AI's
            answers flow as a continuation of the agent's reasoning/tool log in
            the same scroll region, so there is no separate Q&A view. */}
        <QaMessages />

        <div ref={terminalEndRef} />
      </div>

      {/* Execution Plan Card Handoff */}
      {sessionStatus === 'complete' && finalTrade && parsedPlan && (
        <div className="p-4 bg-surface border-t border-border-default animate-slide-up shadow-xl shrink-0">
          <div className="flex items-center gap-2 mb-3">
            <Shield size={12} className="text-text-primary" />
            <h3 className="text-[10px] font-bold text-text-primary uppercase tracking-widest">
              Actionable Trade Plan Ready
            </h3>
            <span className="ml-auto rounded-none px-2 py-0.5 text-[9px] font-black tracking-widest bg-elevated text-text-primary border border-border-default">
              {finalTrade.conviction_score}% CONVICTION
            </span>
          </div>

          <div className="text-xs text-text-secondary italic mb-3 border-l-2 border-text-primary pl-2">
            "{finalTrade.setup_validation}"
          </div>

          <div className="grid grid-cols-3 gap-2 mb-3">
            <div className="bg-elevated border border-border-default rounded-none p-2 flex flex-col justify-center">
              <span className="text-[9px] text-text-muted font-bold uppercase tracking-wider select-none">Entry ({parsedPlan.side})</span>
              <span className="text-[13px] text-text-primary font-extrabold mt-0.5 font-mono">₹{parsedPlan.entryPrice.toFixed(2)}</span>
            </div>
            <div className="bg-elevated border border-border-default rounded-none p-2 flex flex-col justify-center">
              <span className="text-[9px] text-text-muted font-bold uppercase tracking-wider select-none">Target (TP)</span>
              <span className="text-[13px] text-emerald-500 dark:text-emerald-400 font-extrabold mt-0.5 font-mono">₹{parsedPlan.takeProfit.toFixed(2)}</span>
            </div>
            <div className="bg-elevated border border-border-default rounded-none p-2 flex flex-col justify-center">
              <span className="text-[9px] text-text-muted font-bold uppercase tracking-wider select-none">Stop Loss (SL)</span>
              <span className="text-[13px] text-rose-500 dark:text-rose-400 font-extrabold mt-0.5 font-mono">₹{parsedPlan.stopLoss.toFixed(2)}</span>
            </div>
          </div>

          <button
            type="button"
            disabled={executed || isExecuting}
            onClick={handleApproveAndExecute}
            className={`
              w-full flex items-center justify-center gap-2 rounded-none py-2.5 text-xs font-black uppercase tracking-widest transition-all duration-300
              ${executed
                ? 'bg-emerald-500/10 text-emerald-500 dark:text-emerald-400 border border-emerald-500/30'
                : isExecuting
                  ? 'bg-emerald-500/20 text-emerald-500 dark:text-emerald-400 border border-emerald-500/40 cursor-wait'
                  : 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/20 active:scale-[0.98]'
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
                <CheckCircle2 size={13} className="text-emerald-500 animate-pulse" />
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
