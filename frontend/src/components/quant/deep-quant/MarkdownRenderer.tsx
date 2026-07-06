'use client';

import React from 'react';
import { Target, Cpu } from 'lucide-react';

// Premium Markdown inline bold parser helper
export function parseInlineMarkdown(text: string) {
  const parts = text.split(/\*\*([\s\S]*?)\*\*/g);
  return parts.map((part, i) => {
    if (i % 2 === 1) {
      return (
        <strong key={i} className="font-bold text-emerald-600 dark:text-teal-400">
          {part}
        </strong>
      );
    }
    return part;
  });
}

interface MarkdownRendererProps {
  content: string;
}

// Custom-styled beautiful markdown renderer for agent terminal
export default function MarkdownRenderer({ content }: MarkdownRendererProps) {
  const lines = content.split('\n');
  return (
    <div className="space-y-1.5 text-[10.5px] font-sans leading-relaxed tracking-wide text-text-primary/95">
      {lines.map((line, idx) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={idx} className="h-1" />;

        // Header 3 (### Header)
        if (line.startsWith('### ')) {
          return (
            <h3
              key={idx}
              className="text-[11px] font-black text-emerald-400 border-b border-border-default/40 pb-1 mt-3 mb-1.5 uppercase tracking-widest flex items-center gap-1.5 select-none"
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
              className="text-xs font-black text-emerald-600 dark:text-teal-400 border-b border-teal-500/10 pb-1 mt-4 mb-2 tracking-widest uppercase flex items-center gap-1.5 select-none"
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
            <div key={idx} className="flex items-start gap-2 pl-2 my-0.5 text-text-primary">
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
            <div key={idx} className="flex items-start gap-2.5 pl-2 my-1.5 text-text-primary">
              <span className="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded bg-emerald-500/15 text-emerald-400 text-[8.5px] font-black font-mono border border-emerald-500/20 mt-0.5 select-none">
                {num}
              </span>
              <span className="flex-1">{parseInlineMarkdown(listContent)}</span>
            </div>
          );
        }

        // Standard line
        return (
          <p key={idx} className="text-text-secondary">
            {parseInlineMarkdown(line)}
          </p>
        );
      })}
    </div>
  );
}
