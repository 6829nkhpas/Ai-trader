'use client';

import React, { useState } from 'react';
import { ArrowRight, Loader2, Eye, Plus, Mic } from 'lucide-react';
import { useQuantStore } from '../../store/useQuantStore';
import ModelSelector from './deep-quant/ModelSelector';

// Unified Q&A composer, rendered as a continuous footer of the agent working
// section (no separate "panel" chrome). The conversation turns themselves
// render INLINE inside the agent console (see AgentTerminal → QaMessages), so
// this component is ONLY the pinned input row.
//
// The input stays DISABLED until the agent reaches the AI-watcher state (or the
// run completes); once it is watching, the user can chat while the AI keeps
// watching for the price trigger. Includes a model-provider selector so the
// user can pick which LLM answers.
export default function TradeQaPanel() {
  const qaStatus = useQuantStore((s) => s.qaStatus);
  const currentThreadId = useQuantStore((s) => s.currentThreadId);
  const sessionStatus = useQuantStore((s) => s.sessionStatus);
  const askQuestion = useQuantStore((s) => s.askQuestion);
  const selectedModel = useQuantStore((s) => s.selectedModel);
  const setSelectedModel = useQuantStore((s) => s.setSelectedModel);

  const [draft, setDraft] = useState('');

  const isStreaming = qaStatus === 'streaming';
  // The input unlocks ONLY at the AI-watcher state or once the run is complete —
  // and only when a thread id has been captured so the backend can ground the
  // answer in this session's analysis.
  const isWatching = sessionStatus === 'watching';
  const isComplete = sessionStatus === 'complete';
  const canInteract = (isWatching || isComplete) && !!currentThreadId;
  const canSend = canInteract && !isStreaming && draft.trim().length > 0;

  const handleSend = () => {
    if (!canSend) return;
    const q = draft.trim();
    setDraft('');
    askQuestion(q);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends; Shift+Enter inserts a newline.
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const placeholder = isStreaming
    ? 'Answering…'
    : isWatching
      ? 'Ask while the AI watches for your price trigger…'
      : isComplete
        ? 'Ask anything, @ to mention, / for actions'
        : sessionStatus === 'running'
          ? 'Agent is analyzing — chat unlocks once it starts watching…'
          : 'Run an analysis first…';

  return (
    <div className="flex flex-col font-sans bg-surface p-3 shrink-0 border-t border-border-default/40">
      {/* Wrapper container with border and rounded corners */}
      <div className="flex flex-col rounded-lg border border-border-default/60 bg-elevated/10 p-2 relative shadow-md">

        {/* Text Area */}
        <textarea
          rows={2}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={!canInteract || isStreaming}
          placeholder={placeholder}
          className="w-full resize-none bg-transparent border-0 px-2 py-1.5 min-h-[48px] text-[11px] font-sans leading-relaxed text-text-primary placeholder:text-text-muted/65 focus:outline-none focus:ring-0 disabled:opacity-50 disabled:cursor-not-allowed scrollbar-thin"
        />

        {/* Action Row */}
        <div className="flex items-center justify-between mt-2 pt-1 border-t border-border-default/20 select-none">
          {/* Left items: Plus + Model Selector */}
          <div className="flex items-center gap-1.5">
            <ModelSelector
              value={selectedModel}
              onChange={setSelectedModel}
              variant="inline"
              disabled={!canInteract}
            />

            {isWatching && (
              <span className="flex items-center gap-1 text-[8px] font-mono font-bold uppercase tracking-wide text-amber-500 ml-2">
                <Eye size={9} className="animate-pulse" />
                Watching
              </span>
            )}
          </div>

          {/* Right items: Mic + Purple Circle Send Button */}
          <div className="flex items-center gap-2">


            <button
              type="button"
              onClick={handleSend}
              disabled={!canSend}
              title="Send question"
              className={`h-7 w-7 rounded-full flex items-center justify-center transition-all duration-300 ${canSend
                ? 'bg-emerald-500 text-black hover:bg-emerald-400 active:scale-[0.93] shadow-md shadow-emerald-500/20'
                : 'bg-elevated/40 text-text-muted/30 cursor-not-allowed opacity-50'
                }`}
            >
              {isStreaming ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <ArrowRight size={13} />
              )}
            </button>
          </div>
        </div>

      </div>
    </div>
  );
}
