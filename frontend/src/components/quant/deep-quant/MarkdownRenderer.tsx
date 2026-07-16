'use client';

import React from 'react';
import { Target, Cpu } from 'lucide-react';

// Premium Markdown inline bold parser helper
export function parseInlineMarkdown(text: string, simple?: boolean) {
  const parts = text.split(/\*\*([\s\S]*?)\*\*/g);
  return parts.map((part, i) => {
    if (i % 2 === 1) {
      if (simple) {
        return (
          <span key={i} className="font-normal text-text-secondary">
            {part}
          </span>
        );
      }
      return (
        <strong key={i} className="font-bold text-reasoning-green-300">
          {part}
        </strong>
      );
    }
    return part;
  });
}

interface MarkdownRendererProps {
  content: string;
  simple?: boolean;
}

// Custom-styled beautiful markdown renderer for agent terminal
export default function MarkdownRenderer({ content, simple }: MarkdownRendererProps) {
  const lines = content.split('\n');
  return (
    <div className="space-y-1.5 text-[10.5px] font-sans leading-relaxed tracking-wide text-inherit">
      {lines.map((line, idx) => {
        const trimmed = line.trim();
        if (!trimmed) return <div key={idx} className="h-1" />;

        // Header 3 (### Header)
        if (line.startsWith('### ')) {
          return (
            <h3
              key={idx}
              className={`text-[11px] font-black border-b border-border-default/40 pb-1 mt-3 mb-1.5 uppercase tracking-widest flex items-center gap-1.5 select-none ${
                simple ? 'text-text-primary' : 'text-reasoning-green-400'
              }`}
            >
              <Target size={11} className={simple ? 'text-text-muted' : 'text-reasoning-green-400'} />
              {line.replace('### ', '')}
            </h3>
          );
        }

        // Header 2 (## Header)
        if (line.startsWith('## ')) {
          return (
            <h2
              key={idx}
              className={`text-xs font-black border-b pb-1 mt-4 mb-2 tracking-widest uppercase flex items-center gap-1.5 select-none ${
                simple ? 'text-text-primary border-border-default/40' : 'text-reasoning-green-300 border-green-500/10'
              }`}
            >
              <Cpu size={12} className={simple ? 'text-text-muted' : 'text-reasoning-green-400'} />
              {line.replace('## ', '')}
            </h2>
          );
        }

        // Bullet lists (- item or * item)
        if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
          const listContent = trimmed.substring(2);
          return (
            <div key={idx} className="flex items-start gap-2 pl-2 my-0.5 text-inherit">
              <span className={`font-bold select-none mt-0.5 ${simple ? 'text-text-muted' : 'text-reasoning-green-500/80'}`}>•</span>
              <span className="flex-1">{parseInlineMarkdown(listContent, simple)}</span>
            </div>
          );
        }

        // Numbered lists (1. item, etc.)
        const numMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
        if (numMatch) {
          const num = numMatch[1];
          const listContent = numMatch[2];
          return (
            <div key={idx} className="flex items-start gap-2.5 pl-2 my-1.5 text-inherit">
              <span className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded text-[8.5px] font-black font-mono border mt-0.5 select-none ${
                simple 
                  ? 'bg-elevated text-text-secondary border-border-default' 
                  : 'bg-reasoning-green-500/15 text-reasoning-green-400 border-reasoning-green-500/20'
              }`}>
                {num}
              </span>
              <span className="flex-1">{parseInlineMarkdown(listContent, simple)}</span>
            </div>
          );
        }

        // Standard line
        return (
          <p key={idx} className="text-inherit opacity-80">
            {parseInlineMarkdown(line, simple)}
          </p>
        );
      })}
    </div>
  );
}
