'use client';

import React, { useEffect, useRef } from 'react';
import { Shield, Loader2, AlertTriangle, Lock } from 'lucide-react';
import { useQuantStore, isActionableTrade } from '../../store/useQuantStore';

import WatchingIndicator from './deep-quant/WatchingIndicator';
import QaMessages from './deep-quant/QaMessages';
import ReasoningStepRenderer from './deep-quant/ReasoningStepRenderer';
import ToolExecutionStep from './deep-quant/ToolExecutionStep';
import ActionableTradePlan from './deep-quant/ActionableTradePlan';
import ThinkingGroupRenderer from './deep-quant/ThinkingGroupRenderer';
import { classifyAgentError } from './deep-quant/agentErrorClassifier';
import { highlightNumbers } from './deep-quant/textHighlighter';

export default function AgentTerminal() {
  const reasoningSteps = useQuantStore((s) => s.reasoningSteps);
  const sessionStatus = useQuantStore((s) => s.sessionStatus);
  const finalTrade = useQuantStore((s) => s.finalTrade);
  const analysisError = useQuantStore((s) => s.analysisError);


  const qaMessages = useQuantStore((s) => s.qaMessages);
  const qaStatus = useQuantStore((s) => s.qaStatus);

  const terminalEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom of terminal when reasoningSteps changes
  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [reasoningSteps, sessionStatus, qaMessages, qaStatus]);

  // Group consecutive standard message steps (thinking steps) together to avoid excessive nested components
  const renderGroups: Array<
    | { type: 'thinking_group'; steps: typeof reasoningSteps; id: string }
    | { type: 'decision'; step: (typeof reasoningSteps)[0]; id: string }
    | { type: 'tool_start'; step: (typeof reasoningSteps)[0]; id: string }
    | { type: 'legacy'; step: (typeof reasoningSteps)[0]; id: string }
  > = [];

  let currentThinkingGroup: typeof reasoningSteps = [];

  for (const step of reasoningSteps) {
    if (step.type === 'message') {
      const cleanContent = step.content.replace(/\{[\s\S]*\}/g, '').trim();
      const isJsonDecision =
        !cleanContent &&
        (() => {
          try {
            const jsonMatch = step.content.match(/\{[\s\S]*\}/);
            if (jsonMatch) {
              const parsed = JSON.parse(jsonMatch[0]);
              const conviction = parsed.conviction_score ?? parsed.conviction;
              const validation = parsed.setup_validation ?? parsed.validation ?? parsed.setup;
              const plan = parsed.execution_plan ?? parsed.plan;
              return conviction !== undefined || validation || plan;
            }
          } catch {}
          return false;
        })();

      if (isJsonDecision) {
        if (currentThinkingGroup.length > 0) {
          renderGroups.push({
            type: 'thinking_group',
            steps: currentThinkingGroup,
            id: currentThinkingGroup[0].id,
          });
          currentThinkingGroup = [];
        }
        renderGroups.push({ type: 'decision', step, id: step.id });
      } else {
        currentThinkingGroup.push(step);
      }
    } else {
      if (currentThinkingGroup.length > 0) {
        renderGroups.push({
          type: 'thinking_group',
          steps: currentThinkingGroup,
          id: currentThinkingGroup[0].id,
        });
        currentThinkingGroup = [];
      }

      if (step.type === 'tool_start') {
        renderGroups.push({ type: 'tool_start', step, id: step.id });
      } else if (step.type === 'tool_end') {
        // Skip rendering tool_end
      } else {
        renderGroups.push({ type: 'legacy', step, id: step.id });
      }
    }
  }

  if (currentThinkingGroup.length > 0) {
    renderGroups.push({
      type: 'thinking_group',
      steps: currentThinkingGroup,
      id: currentThinkingGroup[0].id,
    });
  }

  return (
    <div className="flex h-full flex-col font-sans bg-surface overflow-hidden relative">
      {/* Terminal Scrolling Log */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3.5 scrollbar-thin scrollbar-track-slate-950/20 scrollbar-thumb-slate-800 select-text">
        {renderGroups.map((group) => {
          if (group.type === 'thinking_group') {
            return (
              <ThinkingGroupRenderer
                key={group.id}
                steps={group.steps}
                sessionStatus={sessionStatus}
              />
            );
          } else if (group.type === 'decision') {
            return <ReasoningStepRenderer key={group.id} step={group.step} />;
          } else if (group.type === 'tool_start') {
            return (
              <ToolExecutionStep
                key={group.id}
                step={group.step}
                reasoningSteps={reasoningSteps}
                sessionStatus={sessionStatus}
              />
            );
          } else {
            return (
              <div key={group.id} className="flex justify-start animate-fade-in font-sans pl-2">
                <div className="text-[10px] text-text-secondary font-semibold select-none flex items-center gap-1.5 py-0.5">
                  <span className="text-text-muted">&gt;</span>
                  {group.step.content}
                </div>
              </div>
            );
          }
        })}

        {/* Watching Indicator inside scroll log */}
        {sessionStatus === 'watching' && <WatchingIndicator />}

        {/* Empty-state guards — the console must NEVER render visually blank. */}
        {reasoningSteps.length === 0 && sessionStatus === 'running' && (
          <div className="flex items-center gap-2 pl-3 py-2 text-[10px] text-text-muted/60 animate-pulse">
            <Loader2 size={11} className="animate-spin text-text-muted" />
            <span>Connecting to Deep Quant agent — awaiting first reasoning step…</span>
          </div>
        )}

        {reasoningSteps.length === 0 && sessionStatus === 'complete' && (
          <div className="flex items-start gap-3 p-3.5 bg-amber-500/5 border border-amber-500/25 rounded mt-2 select-text font-sans shadow-lg shadow-amber-955/20">
            <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-sm bg-amber-500/20 text-amber-500 dark:text-amber-400 text-[10px] font-bold select-none mt-0.5">
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
                <span className="text-[9px] font-mono text-amber-500 dark:text-amber-400 bg-amber-500/5 rounded-sm border border-amber-500/20 px-2 py-1 mt-2 leading-normal">
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

        {/* Error message display.
            The explanation is DERIVED from the error, not hardcoded — see
            `agentErrorClassifier`. This box used to assert "your LLM API key is
            expired, rate-limited, or out of quota" for every failure, including a
            plan restriction that never issued a request, which sent people to
            audit a healthy key. */}
        {sessionStatus === 'error' && (() => {
          const err = classifyAgentError(analysisError);
          // A plan restriction or a deployment switch is not a fault; render it in
          // a neutral tone so it does not read as something broken.
          const isFault = err.kind !== 'research-locked' && err.kind !== 'feature-disabled';
          const tone = isFault
            ? {
                wrap: 'bg-rose-500/5 border-rose-500/20 shadow-rose-955/20',
                badge: 'bg-rose-500/20 text-rose-500 dark:text-rose-400',
                title: 'text-rose-500 dark:text-rose-400',
                body: 'text-rose-600 dark:text-rose-300/80',
                detail: 'text-rose-500 dark:text-rose-400 bg-rose-500/5 border-rose-500/15',
                glyph: <AlertTriangle size={11} />,
              }
            : {
                wrap: 'bg-amber-500/5 border-amber-500/20 shadow-amber-955/20',
                badge: 'bg-amber-500/20 text-amber-600 dark:text-amber-400',
                title: 'text-amber-600 dark:text-amber-400',
                body: 'text-amber-700 dark:text-amber-300/80',
                detail: 'text-amber-600 dark:text-amber-400 bg-amber-500/5 border-amber-500/15',
                glyph: <Lock size={11} />,
              };

          return (
            <div className={`flex items-start gap-3 p-3.5 border rounded mt-2 select-text font-sans shadow-lg ${tone.wrap}`}>
              <div className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-sm text-[10px] font-bold select-none mt-0.5 ${tone.badge}`}>
                {tone.glyph}
              </div>
              <div className="flex flex-col">
                <span className={`text-[11px] font-bold ${tone.title}`}>{err.title}</span>
                <span className={`text-[10px] mt-1 leading-relaxed ${tone.body}`}>
                  {err.explanation}
                </span>
                <span className={`text-[9px] font-mono rounded-sm border px-2 py-1 mt-2 leading-normal ${tone.detail}`}>
                  {err.detail}
                </span>
              </div>
            </div>
          );
        })()}

        {/* Stand-Aside decision rendered INLINE in the terminal log */}
        {sessionStatus === 'complete' && finalTrade && !isActionableTrade(finalTrade) && (
          <div className="flex justify-start animate-fade-in font-sans w-full my-2 select-text">
            <div className="w-full rounded border border-amber-500/15 bg-gradient-to-r from-amber-500/5 via-elevated/20 to-elevated/5 px-3 py-2.5 text-[11px] leading-relaxed shadow-sm">
              <div className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-wider text-text-muted mb-2 select-none">
                <Shield size={11} className="text-text-muted shrink-0" />
                <span>Stand Aside — No Trade</span>
                {finalTrade.action && (
                  <span className="ml-auto rounded-sm px-1.5 py-0.5 text-[8px] font-black tracking-widest bg-elevated text-text-muted border border-border-default">
                    {String(finalTrade.action).toUpperCase()}
                  </span>
                )}
              </div>
              {finalTrade.setup_validation && (
                <p className="text-text-secondary italic border-l-2 border-border-default/40 pl-2.5 mb-2 leading-relaxed">
                  &ldquo;{highlightNumbers(finalTrade.setup_validation)}&rdquo;
                </p>
              )}
              {finalTrade.execution_plan && (
                <p className="text-[10px] text-text-muted mt-2 border-t border-border-default/40 pt-2 leading-relaxed">
                  {highlightNumbers(finalTrade.execution_plan)}
                </p>
              )}
            </div>
          </div>
        )}

        {/* Q&A turns render INLINE here */}
        <QaMessages />

        {/* Actionable trade declaration */}
        {sessionStatus === 'complete' && isActionableTrade(finalTrade) && (
          <ActionableTradePlan finalTrade={finalTrade} />
        )}

        <div ref={terminalEndRef} />
      </div>
    </div>
  );
}
