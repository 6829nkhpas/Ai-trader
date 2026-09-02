'use client';

import React, { useState } from 'react';
import { Loader2, User, Cpu, Wrench, Copy, Check, ThumbsUp, ThumbsDown, Share2 } from 'lucide-react';
import { QaChatMessage } from '../../../store/useQuantStore';
import { useFqQaMessages } from '../useFqSession';
import MarkdownRenderer from './MarkdownRenderer';

// Small copy-to-clipboard button with transient "copied" feedback. Used to copy
// either a user prompt or the assistant's Q&A answer verbatim.
function CopyButton({ text, label, className }: { text: string; label: string; className?: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const value = (text || '').trim();
    if (!value) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      } else {
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
      // Ignore clipboard failures silently
    }
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={copied ? 'Copied!' : label}
      aria-label={label}
      className={className || "shrink-0 inline-flex items-center justify-center h-5 w-5 rounded-none text-text-muted hover:text-text-primary hover:bg-elevated/60 transition-colors"}
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

// Renders individual Assistant message rows, managing its own Like/Dislike state.
function AssistantMessageRow({ msg }: { msg: QaChatMessage }) {
  const [liked, setLiked] = useState(false);
  const [disliked, setDisliked] = useState(false);
  // `askQuestion` and `qaMessages` were subscribed here and never read. Every assistant row
  // therefore re-rendered on every frame of a streaming answer, for nothing.

  return (
    <div className="flex justify-start items-start gap-2.5 animate-fade-in font-sans w-full my-2">
      {/* AI Avatar */}
      <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full border select-none ${
        msg.error 
          ? 'bg-rose-500/10 text-rose-500 border-rose-500/20' 
          : 'bg-elevated text-text-primary border-border-default/60'
      }`}>
        <Cpu size={13} className={`shrink-0 ${msg.streaming ? 'animate-pulse' : ''}`} />
      </div>

      {/* Bubble */}
      <div
        className={`group relative max-w-[80%] rounded pl-3 pr-7 py-2 text-[11px] leading-relaxed shadow-sm ${
          msg.error
            ? 'bg-rose-500/5 text-text-primary border border-rose-500/20'
            : 'bg-elevated/40 text-text-primary border border-border-default/40'
        }`}
      >
        {msg.activity && msg.activity.length > 0 && (
          <div className="mb-2 flex flex-col gap-0.5 border-b border-border-default/20 pb-1.5">
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
          <div>
            <MarkdownRenderer content={msg.content} simple />
          </div>
        ) : msg.streaming ? (
          <div className="flex items-center gap-2 text-[10px] text-text-muted/60 animate-pulse py-1">
            <Loader2 size={11} className="animate-spin text-text-muted" />
            <span>Thinking…</span>
          </div>
        ) : (
          <div className="text-[10px] italic text-text-muted/60 py-1">
            No answer was produced for this question. Please try rephrasing or ask again.
          </div>
        )}

        {/* Bottom Action Bar */}
        {msg.content && !msg.streaming && (
          <div className="flex items-center gap-1 mt-2.5 pt-1.5 border-t border-border-default/15 select-none text-text-muted">
            <CopyButton
              text={msg.content}
              label="Copy AI response"
              className="p-1 hover:bg-elevated rounded transition-all cursor-pointer flex items-center justify-center"
            />
            
            <button
              type="button"
              onClick={() => {
                setLiked(!liked);
                setDisliked(false);
              }}
              className={`p-1 hover:bg-elevated rounded transition-all cursor-pointer flex items-center justify-center ${
                liked ? 'text-emerald-500 bg-emerald-500/10' : 'hover:text-text-primary'
              }`}
              title="Like response"
            >
              <ThumbsUp size={11} className={liked ? 'fill-current' : ''} />
            </button>

            <button
              type="button"
              onClick={() => {
                setDisliked(!disliked);
                setLiked(false);
              }}
              className={`p-1 hover:bg-elevated rounded transition-all cursor-pointer flex items-center justify-center ${
                disliked ? 'text-rose-500 bg-rose-500/10' : 'hover:text-text-primary'
              }`}
              title="Dislike response"
            >
              <ThumbsDown size={11} className={disliked ? 'fill-current' : ''} />
            </button>

            <button
              type="button"
              onClick={() => {
                navigator.clipboard.writeText(`Quant AI Response:\n${msg.content}`);
              }}
              className="p-1 hover:bg-elevated rounded hover:text-text-primary transition-all cursor-pointer flex items-center justify-center"
              title="Share response"
            >
              <Share2 size={11} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// Renders the Q&A conversation turns (user prompts + assistant answers) INLINE
// within the agent console's scroll flow.
export default function QaMessages() {
  const qaMessages = useFqQaMessages();

  if (!qaMessages || qaMessages.length === 0) return null;

  return (
    <div className="space-y-4 mt-6 pt-4 border-t border-border-default/30">
      {qaMessages.map((msg) =>
        msg.role === 'user' ? (
          <div key={msg.id} className="flex justify-end items-start gap-2.5 animate-fade-in font-sans w-full my-2">
            {/* Bubble */}
            <div className="group relative max-w-[80%] bg-elevated text-text-primary border border-border-default/60 rounded pl-3 pr-7 py-2 text-[11px] leading-relaxed shadow-sm">
              <span className="text-text-primary break-words whitespace-pre-wrap">{msg.content}</span>
              <span className="absolute right-1.5 top-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                <CopyButton text={msg.content} label="Copy your message" />
              </span>
            </div>

            {/* User Avatar */}
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-500 border border-emerald-500/20 select-none">
              <User size={13} className="shrink-0" />
            </div>
          </div>
        ) : (
          <AssistantMessageRow key={msg.id} msg={msg} />
        )
      )}
    </div>
  );
}
