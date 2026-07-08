'use client';

import React, { useState } from 'react';
import { Send, Loader2, Eye } from 'lucide-react';
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
        ? 'Ask a follow-up about this analysis…'
        : sessionStatus === 'running'
          ? 'Agent is analyzing — chat unlocks once it starts watching…'
          : 'Run an analysis first…';

  return (
    <div className="flex flex-col font-sans bg-surface">
      {/* Composer — taller input, model selector, and a live status line. Merged
          into the agent section by a single top divider (no separate panel). */}
      <div className="shrink-0 border-t border-border-default bg-elevated/70 p-2.5 space-y-2">
        {/* Row 1: model provider selector + watcher status */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-wider text-text-muted select-none">
            <span>Model</span>
            <ModelSelector value={selectedModel} onChange={setSelectedModel} />
          </div>

          {isWatching && (
            <span className="flex items-center gap-1 text-[8.5px] font-mono font-bold uppercase tracking-wide text-amber-500">
              <Eye size={10} className="animate-pulse" />
              Watching — chat live
            </span>
          )}
        </div>

        {/* Row 2: taller textarea + send button */}
        <div className="relative flex items-end w-full">
          <textarea
            rows={3}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={!canInteract || isStreaming}
            placeholder={placeholder}
            className="w-full resize-none rounded-lg bg-surface border border-border-default pl-3 pr-11 py-2.5 min-h-[76px] text-[11px] font-sans leading-relaxed text-text-primary placeholder:text-text-muted/65 focus:outline-none focus:border-text-primary/40 focus:ring-1 focus:ring-text-primary/20 disabled:opacity-50 disabled:cursor-not-allowed scrollbar-thin"
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={!canSend}
            title="Send question"
            className={`absolute right-2 bottom-2 h-8 w-8 rounded-full flex items-center justify-center transition-all duration-300 border ${
              canSend
                ? 'bg-text-primary text-surface border-text-primary hover:bg-text-secondary hover:border-text-secondary active:scale-[0.95]'
                : 'bg-elevated/40 text-text-muted/30 border-transparent opacity-50 cursor-not-allowed'
            }`}
          >
            {isStreaming ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <Send size={13} />
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
