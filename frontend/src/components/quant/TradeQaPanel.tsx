'use client';

import React, { useEffect, useRef, useState } from 'react';
import { Send, Loader2, User, Cpu, Wrench, Eye, Copy, Check } from 'lucide-react';
import { useQuantStore } from '../../store/useQuantStore';
import ModelSelector from './deep-quant/ModelSelector';

// Small copy-to-clipboard button with transient "copied" feedback. Used to copy
// either a user prompt or the assistant's Q&A answer verbatim.
function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const value = (text || '').trim();
    if (!value) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      } else {
        // Fallback for non-secure contexts / older webviews.
        const ta = document.createElement('textarea');
        ta.value = value;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Ignore clipboard failures silently — nothing actionable for the user.
    }
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={copied ? 'Copied!' : label}
      aria-label={label}
      className="shrink-0 inline-flex items-center justify-center h-5 w-5 rounded-none text-text-muted hover:text-text-primary hover:bg-elevated/60 transition-colors"
    >
      {copied ? <Check size={11} className="text-emerald-500" /> : <Copy size={11} />}
    </button>
  );
}

// Inline bold (**text**) parser — mirrors AgentTerminal's helper so Q&A
// answers render with the same emphasis treatment as the agent console.
function parseInlineMarkdown(text: string) {
  const parts = text.split(/\*\*([\s\S]*?)\*\*/g);
  return parts.map((part, i) => {
    if (i % 2 === 1) {
      return (
        <strong key={i} className="font-bold text-text-primary">
          {part}
        </strong>
      );
    }
    return part;
  });
}

// Lightweight multi-line renderer for streamed assistant answers.
const AnswerText = ({ content }: { content: string }) => {
  const lines = content.split('\n');
  return (
    <div className="space-y-1 text-[10.5px] font-sans leading-relaxed tracking-wide text-text-primary/95">
      {lines.map((line, idx) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={idx} className="h-1" />;

        if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
          return (
            <div key={idx} className="flex items-start gap-2 pl-1 my-0.5 text-text-primary">
              <span className="text-text-secondary font-bold select-none mt-0.5">•</span>
              <span className="flex-1">{parseInlineMarkdown(trimmed.substring(2))}</span>
            </div>
          );
        }

        return (
          <p key={idx} className="text-text-secondary">
            {parseInlineMarkdown(line)}
          </p>
        );
      })}
    </div>
  );
};

// Unified Q&A composer, rendered as a continuous footer of the agent working
// section (no separate "panel" chrome). The input stays DISABLED until the
// agent reaches the AI-watcher state (or the run completes); once it is
// watching, the user can chat while the AI keeps watching for the price
// trigger. Includes a model-provider selector so the user can pick which LLM
// answers.
export default function TradeQaPanel() {
  const qaMessages = useQuantStore((s) => s.qaMessages);
  const qaStatus = useQuantStore((s) => s.qaStatus);
  const currentThreadId = useQuantStore((s) => s.currentThreadId);
  const sessionStatus = useQuantStore((s) => s.sessionStatus);
  const askQuestion = useQuantStore((s) => s.askQuestion);
  const selectedModel = useQuantStore((s) => s.selectedModel);
  const setSelectedModel = useQuantStore((s) => s.setSelectedModel);

  const [draft, setDraft] = useState('');
  const endRef = useRef<HTMLDivElement>(null);

  const isStreaming = qaStatus === 'streaming';
  // The input unlocks ONLY at the AI-watcher state or once the run is complete —
  // and only when a thread id has been captured so the backend can ground the
  // answer in this session's analysis.
  const isWatching = sessionStatus === 'watching';
  const isComplete = sessionStatus === 'complete';
  const canInteract = (isWatching || isComplete) && !!currentThreadId;
  const canSend = canInteract && !isStreaming && draft.trim().length > 0;

  // Auto-scroll to the latest turn as the answer streams in.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [qaMessages, qaStatus]);

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
      {/* Message list — only rendered once there is a conversation, so the
          composer sits flush under the agent log when the chat is empty. */}
      {qaMessages.length > 0 && (
        <div className="max-h-[300px] overflow-y-auto px-3 py-3 space-y-3 border-t border-border-default/60 scrollbar-thin scrollbar-track-slate-950/20 scrollbar-thumb-slate-800">
          {qaMessages.map((msg) =>
            msg.role === 'user' ? (
              <div key={msg.id} className="flex justify-end animate-fade-in font-sans">
                <div className="group max-w-[85%] bg-elevated text-text-primary border border-border-default rounded-none px-3 py-2 text-[11px] leading-relaxed shadow-sm">
                  <div className="flex items-center gap-1.5 text-[9px] text-text-secondary font-bold uppercase tracking-wider mb-1 select-none">
                    <User size={10} />
                    You
                    <span className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity">
                      <CopyButton text={msg.content} label="Copy your message" />
                    </span>
                  </div>
                  <span className="text-text-primary">{msg.content}</span>
                </div>
              </div>
            ) : (
              <div key={msg.id} className="flex justify-start animate-fade-in font-sans w-full">
                <div
                  className={`group max-w-[95%] w-full rounded-none px-3 py-2 text-[11px] leading-relaxed shadow-sm ${
                    msg.error
                      ? 'bg-rose-500/5 text-text-primary border border-rose-500/20'
                      : 'bg-elevated/40 text-text-primary border border-border-default/40'
                  }`}
                >
                  <div
                    className={`flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-wider mb-1 select-none ${
                      msg.error ? 'text-rose-500' : 'text-text-primary'
                    }`}
                  >
                    <Cpu size={10} className={msg.streaming ? 'animate-pulse' : ''} />
                    Quant AI
                    {msg.content && !msg.streaming && (
                      <span className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity">
                        <CopyButton text={msg.content} label="Copy AI response" />
                      </span>
                    )}
                  </div>

                  {msg.activity && msg.activity.length > 0 && (
                    <div className="mb-1.5 flex flex-col gap-0.5">
                      {msg.activity.map((line, i) => (
                        <div
                          key={i}
                          className="flex items-center gap-1.5 text-[8.5px] font-mono text-text-muted"
                        >
                          <Wrench size={8} className="shrink-0" />
                          <span>{line}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {msg.content ? (
                    <AnswerText content={msg.content} />
                  ) : msg.streaming ? (
                    <div className="flex items-center gap-2 text-[10px] text-text-muted/60 animate-pulse">
                      <Loader2 size={11} className="animate-spin text-text-muted" />
                      <span>Thinking…</span>
                    </div>
                  ) : null}
                </div>
              </div>
            )
          )}
          <div ref={endRef} />
        </div>
      )}

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
