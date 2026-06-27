'use client';

import React, { useEffect, useRef, useState } from 'react';
import { MessageCircle, Send, Loader2, User, Cpu, Wrench } from 'lucide-react';
import { useQuantStore } from '../../store/useQuantStore';

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
    <div className="space-y-1 text-[10.5px] font-sans leading-relaxed tracking-wide text-slate-100/90">
      {lines.map((line, idx) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={idx} className="h-1" />;

        if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
          return (
            <div key={idx} className="flex items-start gap-2 pl-1 my-0.5 text-slate-200">
              <span className="text-text-secondary font-bold select-none mt-0.5">•</span>
              <span className="flex-1">{parseInlineMarkdown(trimmed.substring(2))}</span>
            </div>
          );
        }

        return (
          <p key={idx} className="text-slate-300">
            {parseInlineMarkdown(line)}
          </p>
        );
      })}
    </div>
  );
};

export default function TradeQaPanel() {
  const qaMessages = useQuantStore((s) => s.qaMessages);
  const qaStatus = useQuantStore((s) => s.qaStatus);
  const currentThreadId = useQuantStore((s) => s.currentThreadId);
  const askQuestion = useQuantStore((s) => s.askQuestion);

  const [draft, setDraft] = useState('');
  const endRef = useRef<HTMLDivElement>(null);

  const isStreaming = qaStatus === 'streaming';
  const canSend = !isStreaming && !!currentThreadId && draft.trim().length > 0;

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

  return (
    <div className="flex flex-col font-sans bg-black border border-border-default rounded-none overflow-hidden relative">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-elevated border-b border-border-default select-none">
        <div className="flex items-center gap-2">
          <MessageCircle size={14} className="text-text-primary" />
          <span className="text-[10px] text-text-primary font-bold uppercase tracking-wider">
            Trade Q&amp;A
          </span>
        </div>
        <span className="text-[8px] font-mono text-text-muted uppercase tracking-wider">
          Follow-up • same context
        </span>
      </div>

      {/* Message list */}
      <div className="max-h-[320px] min-h-[80px] overflow-y-auto p-3 space-y-3 scrollbar-thin scrollbar-track-slate-950/20 scrollbar-thumb-slate-800">
        {qaMessages.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 py-6 text-center select-none">
            <MessageCircle size={18} className="text-text-muted/50" />
            <p className="text-[10px] text-text-muted/70 max-w-[200px] leading-relaxed">
              Ask a follow-up about this analysis — e.g. &ldquo;Why this stop-loss?&rdquo;
              or &ldquo;What invalidates the setup?&rdquo;
            </p>
          </div>
        ) : (
          qaMessages.map((msg) =>
            msg.role === 'user' ? (
              <div key={msg.id} className="flex justify-end animate-fade-in font-sans">
                <div className="max-w-[85%] bg-elevated text-text-primary border border-border-default rounded-none px-3 py-2 text-[11px] leading-relaxed shadow-sm">
                  <div className="flex items-center gap-1.5 text-[9px] text-text-secondary font-bold uppercase tracking-wider mb-1 select-none">
                    <User size={10} />
                    You
                  </div>
                  <span className="text-text-primary">{msg.content}</span>
                </div>
              </div>
            ) : (
              <div key={msg.id} className="flex justify-start animate-fade-in font-sans w-full">
                <div
                  className={`max-w-[95%] w-full rounded-none px-3 py-2 text-[11px] leading-relaxed shadow-sm ${
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
                  </div>

                  {/* Tool-activity indicators (lightweight) */}
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
          )
        )}
        <div ref={endRef} />
      </div>

      {/* Composer */}
      <div className="shrink-0 border-t border-border-default bg-elevated/70 p-2.5">
        <div className="flex items-end gap-2">
          <textarea
            rows={1}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isStreaming || !currentThreadId}
            placeholder={
              currentThreadId ? 'Ask a follow-up question…' : 'Run an analysis first…'
            }
            className="flex-1 resize-none rounded-none bg-black border border-border-default px-3 py-2 text-[11px] font-sans text-text-primary placeholder:text-text-muted/65 focus:outline-none focus:border-text-primary/40 focus:ring-1 focus:ring-text-primary/20 disabled:opacity-50 disabled:cursor-not-allowed scrollbar-thin"
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={!canSend}
            title="Send question"
            className={`flex h-8 items-center justify-center gap-1.5 rounded-none px-3 text-[10px] font-bold uppercase tracking-wider transition-all duration-300 border ${
              canSend
                ? 'bg-text-primary text-surface border-text-primary hover:bg-text-secondary hover:border-text-secondary active:scale-[0.98]'
                : 'bg-elevated text-text-muted/50 border border-border-default opacity-50 cursor-not-allowed'
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
