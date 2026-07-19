'use client';

/**
 * FnoResultRow — Enhanced F&O result row for the Symbol Search Modal.
 *
 * Displays CE/PE/FUT badge, formatted contract label (underlying + expiry +
 * strike + type), and an NFO exchange badge. Extracted from SymbolSearchModal
 * to keep component files under 300 lines.
 */

import React from 'react';
import type { FnoResult } from '../../types/searchResult';

interface FnoResultRowProps {
  result: FnoResult;
  isSelected: boolean;
  query: string;
  onClick: () => void;
  onMouseEnter: () => void;
}

/** Enhanced F&O result row — shows CE/PE/FUT badge, formatted contract, NFO tag. */
export default function FnoResultRow({ result, isSelected, query, onClick, onMouseEnter }: FnoResultRowProps) {
  const badgeColor =
    result.optionType === 'CE'
      ? 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 border-emerald-500/30'
      : result.optionType === 'PE'
        ? 'bg-rose-500/20 text-rose-600 dark:text-rose-400 border-rose-500/30'
        : 'bg-sky-500/20 text-sky-600 dark:text-sky-400 border-sky-500/30';

  const label = [
    result.underlying,
    formatFnoExpiry(result.expiry),
    result.strike ? result.strike.toLocaleString('en-IN') : null,
    optionTypeLabel(result.optionType),
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div
      onClick={onClick}
      onMouseEnter={onMouseEnter}
      className={`flex justify-between items-center px-4 py-2.5 cursor-pointer transition-colors ${
        isSelected ? 'bg-emerald-500/10 dark:bg-emerald-500/15 text-text-primary font-medium' : 'hover:bg-elevated/40 text-text-secondary'
      }`}
    >
      <div className="flex items-center gap-3 min-w-0">
        {/* CE/PE/FUT badge */}
        <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md border text-[9px] font-black uppercase tracking-wider ${badgeColor}`}>
          {result.optionType}
        </span>
        <div className="flex flex-col min-w-0">
          <span className="text-xs font-semibold text-text-primary truncate">
            {highlightText(label, query)}
          </span>
          <span className="text-[10px] text-text-muted">
            {result.optionType === 'FUT' ? 'Future' : 'Option'}
          </span>
        </div>
      </div>
      <span className="text-[9px] uppercase font-bold tracking-wider shrink-0 text-text-primary/70">
        NFO
      </span>
    </div>
  );
}

// ── Exported Helpers ────────────────────────────────────────────────────────

/** Format an ISO date (e.g. "2026-07-07") into "07 Jul" for compact display. */
export function formatFnoExpiry(iso: string): string {
  try {
    const d = new Date(iso + 'T00:00:00');
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
  } catch {
    return iso;
  }
}

/** Map option type to a human-readable label. */
export function optionTypeLabel(t: string): string {
  switch (t) {
    case 'CE': return 'Call';
    case 'PE': return 'Put';
    case 'FUT': return 'Future';
    default: return t;
  }
}

/** Highlight matched portions of text in search results. */
export function highlightText(text: string, highlight: string) {
  if (!highlight.trim()) {
    return <span>{text}</span>;
  }
  const regex = new RegExp(`(${highlight.trim().replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&')})`, 'gi');
  const parts = text.split(regex);
  return (
    <>
      {parts.map((part, i) =>
        regex.test(part) ? (
          <span key={i} className="text-emerald-400 font-bold">
            {part}
          </span>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  );
}
