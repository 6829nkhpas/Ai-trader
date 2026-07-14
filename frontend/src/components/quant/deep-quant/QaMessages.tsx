'use client';

import React, { useState } from 'react';
import { Loader2, User, Cpu, Wrench, Copy, Check } from 'lucide-react';
import { useQuantStore } from '../../../store/useQuantStore';

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

// Renders the Q&A conversation turns (user prompts + assistant answers) INLINE
// within the agent console's scroll flow — there is no separate Q&A view. The
// turns appear as continuation of the agent's reasoning/tool cards so the whole
// session reads as one stream. Returns null when there is no conversation yet.
export default function QaMessages() {
  const qaMessages = useQuantStore((s) => s.qaMessages);

  if (!qaMessages || qaMessages.length === 0) return null;

  return (
    <div className="space-y-3.5">
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
              ) : (
                // The turn finished with no answer text (e.g. the stream ended
                // right after a tool call, or a synthetic completion arrived).
                // Never render nothing — that reads as a silent freeze. Surface a
                // graceful fallback so the user knows the turn ended and can retry.
                <div className="text-[10px] italic text-text-muted/60">
                  No answer was produced for this question. Please try rephrasing or ask again.
                </div>
              )}
            </div>
          </div>
        )
      )}
    </div>
  );
}
