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
  const out: React.ReactNode[] = [];

  // Detect whether a line is a markdown table row (| a | b |).
  const isTableRow = (l: string) => {
    const t = l.trim();
    return t.startsWith('|') && t.endsWith('|') && t.length > 1;
  };
  // Detect the header/body separator row (|---|:--:|---|).
  const isTableDivider = (l: string) =>
    isTableRow(l) && /^\|?[\s:|-]+\|?$/.test(l.trim()) && l.includes('-');

  const splitCells = (l: string) =>
    l
      .trim()
      .replace(/^\|/, '')
      .replace(/\|$/, '')
      .split('|')
      .map((c) => c.trim());

  for (let idx = 0; idx < lines.length; idx++) {
    const line = lines[idx];
    const trimmed = line.trim();

    // ── Markdown table (header row + divider + body rows) ──────────────
    if (isTableRow(line) && idx + 1 < lines.length && isTableDivider(lines[idx + 1])) {
      const header = splitCells(line);
      const rows: string[][] = [];
      let j = idx + 2;
      while (j < lines.length && isTableRow(lines[j]) && !isTableDivider(lines[j])) {
        rows.push(splitCells(lines[j]));
        j++;
      }
      out.push(
        <div key={`tbl-${idx}`} className="my-2 overflow-x-auto rounded border border-border-default/50">
          <table className="w-full border-collapse text-[10px]">
            <thead>
              <tr className="bg-elevated/60">
                {header.map((h, hi) => (
                  <th
                    key={hi}
                    className="border-b border-border-default/50 px-2 py-1 text-left font-bold text-text-primary"
                  >
                    {parseInlineMarkdown(h, simple)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className="odd:bg-elevated/10">
                  {r.map((c, ci) => (
                    <td
                      key={ci}
                      className="border-b border-border-default/25 px-2 py-1 align-top text-text-secondary"
                    >
                      {parseInlineMarkdown(c, simple)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      idx = j - 1;
      continue;
    }

    if (!trimmed) {
      out.push(<div key={idx} className="h-1" />);
      continue;
    }

    // Horizontal rule (--- or ***)
    if (/^([-*_])\1{2,}$/.test(trimmed)) {
      out.push(<hr key={idx} className="my-2 border-t border-border-default/40" />);
      continue;
    }

    // Header 3 (### Header)
    if (line.startsWith('### ')) {
      out.push(
        <h3
          key={idx}
          className={`text-[11px] font-black border-b border-border-default/40 pb-1 mt-3 mb-1.5 uppercase tracking-widest flex items-center gap-1.5 select-none ${
            simple ? 'text-text-primary' : 'text-reasoning-green-400'
          }`}
        >
          <Target size={11} className={simple ? 'text-text-muted' : 'text-reasoning-green-400'} />
          {line.replace('### ', '')}
        </h3>,
      );
      continue;
    }

    // Header 2 (## Header)
    if (line.startsWith('## ')) {
      out.push(
        <h2
          key={idx}
          className={`text-xs font-black border-b pb-1 mt-4 mb-2 tracking-widest uppercase flex items-center gap-1.5 select-none ${
            simple ? 'text-text-primary border-border-default/40' : 'text-reasoning-green-300 border-green-500/10'
          }`}
        >
          <Cpu size={12} className={simple ? 'text-text-muted' : 'text-reasoning-green-400'} />
          {line.replace('## ', '')}
        </h2>,
      );
      continue;
    }

    // Header 1 (# Header) — treat like H2 styling
    if (line.startsWith('# ')) {
      out.push(
        <h2
          key={idx}
          className={`text-xs font-black border-b pb-1 mt-4 mb-2 tracking-widest uppercase flex items-center gap-1.5 select-none ${
            simple ? 'text-text-primary border-border-default/40' : 'text-reasoning-green-300 border-green-500/10'
          }`}
        >
          <Cpu size={12} className={simple ? 'text-text-muted' : 'text-reasoning-green-400'} />
          {line.replace('# ', '')}
        </h2>,
      );
      continue;
    }

    // Blockquote (> quote)
    if (trimmed.startsWith('> ')) {
      out.push(
        <blockquote
          key={idx}
          className="border-l-2 border-reasoning-green-500/50 pl-2.5 my-1 italic text-text-secondary"
        >
          {parseInlineMarkdown(trimmed.substring(2), simple)}
        </blockquote>,
      );
      continue;
    }

    // Bullet lists (- item or * item)
    if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
      const listContent = trimmed.substring(2);
      out.push(
        <div key={idx} className="flex items-start gap-2 pl-2 my-0.5 text-inherit">
          <span className={`font-bold select-none mt-0.5 ${simple ? 'text-text-muted' : 'text-reasoning-green-500/80'}`}>•</span>
          <span className="flex-1">{parseInlineMarkdown(listContent, simple)}</span>
        </div>,
      );
      continue;
    }

    // Numbered lists (1. item, etc.)
    const numMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
    if (numMatch) {
      const num = numMatch[1];
      const listContent = numMatch[2];
      out.push(
        <div key={idx} className="flex items-start gap-2.5 pl-2 my-1.5 text-inherit">
          <span className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded text-[8.5px] font-black font-mono border mt-0.5 select-none ${
            simple
              ? 'bg-elevated text-text-secondary border-border-default'
              : 'bg-reasoning-green-500/15 text-reasoning-green-400 border-reasoning-green-500/20'
          }`}>
            {num}
          </span>
          <span className="flex-1">{parseInlineMarkdown(listContent, simple)}</span>
        </div>,
      );
      continue;
    }

    // Standard line
    out.push(
      <p key={idx} className="text-inherit opacity-80">
        {parseInlineMarkdown(line, simple)}
      </p>,
    );
  }

  return (
    <div className="space-y-1.5 text-[10.5px] font-sans leading-relaxed tracking-wide text-inherit">
      {out}
    </div>
  );
}
