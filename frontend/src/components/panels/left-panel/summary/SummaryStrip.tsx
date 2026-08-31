'use client';

import React from 'react';
import { AlertTriangle, ChevronRight, Loader2 } from 'lucide-react';

/**
 * The four states a summary strip can be in.
 *
 * `empty` and `error` are deliberately separate. A sentiment fetch that FAILED
 * and a symbol that simply has no reading yet are different facts, and
 * collapsing them into one grey row is what makes a broken service look like a
 * calm market. Each state gets its own visual and its own words.
 */
export type SummaryStripState = 'ready' | 'loading' | 'error' | 'empty';

export interface SummaryStripProps {
  /** Leading glyph. Decorative — the label carries the meaning. */
  icon: React.ReactNode;
  /** Short section name, rendered uppercase (e.g. "AI News Sentiment"). */
  label: string;
  /** Opens the detail sheet on this section's tab. */
  onClick: () => void;
  state?: SummaryStripState;
  /**
   * The compact reading, right-aligned. Rendered in `ready` only.
   *
   * Free-form so each strip can compose its own badges/numbers, but keep it to
   * one line — the panel is 224px wide at its default.
   */
  value?: React.ReactNode;
  /** Optional second line in `ready` (a gauge bar, a supporting phrase). */
  detail?: React.ReactNode;
  /**
   * Spoken description of `value`, since `value` is arbitrary JSX and often
   * conveys meaning through colour and bar position. Without this the strip
   * would announce only its label, which tells a screen-reader user nothing
   * about the reading they are being offered.
   */
  valueText?: string;
  /** Small qualifier beside the label (e.g. "on RELIANCE", "stale"). */
  badge?: React.ReactNode;
  errorMessage?: string;
  emptyMessage?: string;
  loadingMessage?: string;
}

/** Icon-chip background/foreground per state — the first thing the eye reads. */
const ICON_TONE: Record<SummaryStripState, string> = {
  ready: 'bg-elevated text-text-secondary',
  loading: 'bg-primary/10 text-primary',
  error: 'bg-bear/10 text-bear',
  empty: 'bg-elevated/70 text-text-muted',
};

export default function SummaryStrip({
  icon,
  label,
  onClick,
  state = 'ready',
  value,
  detail,
  valueText,
  badge,
  errorMessage,
  emptyMessage,
  loadingMessage = 'Loading',
}: SummaryStripProps) {
  // What the row is currently saying, in words. Drives both the visible
  // secondary line (for non-ready states) and the button's accessible name.
  const statusText =
    state === 'loading'
      ? loadingMessage
      : state === 'error'
        ? (errorMessage ?? 'Unavailable')
        : state === 'empty'
          ? (emptyMessage ?? 'No data')
          : (valueText ?? '');

  return (
    <button
      type="button"
      onClick={onClick}
      aria-haspopup="dialog"
      // Composed rather than left implicit: the visible value is colour-coded
      // JSX, so the accessible name has to restate it as text.
      aria-label={`${label}${statusText ? `, ${statusText}` : ''}. Open details.`}
      className="
        group relative flex w-full flex-col gap-1.5 border-0 border-b border-border-default/40
        bg-transparent px-2.5 py-2.5 text-left last:border-b-0
        transition-colors duration-150 hover:bg-elevated/40
        focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary
      "
    >
      <div className="flex items-start gap-2">
        <span
          aria-hidden="true"
          className={`mt-px flex h-5.5 w-5.5 shrink-0 items-center justify-center rounded-md transition-colors ${ICON_TONE[state]}`}
        >
          {icon}
        </span>

        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex items-center gap-1.5">
            {/* The label yields first when the column is dragged down to its
                180px minimum. The reading on the right is the point of the row,
                so it keeps its space and the name truncates instead. */}
            <span className="truncate text-[9.5px] font-bold uppercase tracking-wide text-text-secondary">
              {label}
            </span>
            {badge}
          </div>

          {/* Second line. In `ready` it belongs to the strip (a gauge, a
              headline). In `error` it carries the actual failure text, which
              has to be readable without opening the sheet — the status chip
              on the right only says that something went wrong, not what.
              `loading` and `empty` say everything they have to say in that
              chip, so they add no second line. */}
          {state === 'ready' && detail ? (
            <div className="min-w-0">{detail}</div>
          ) : state === 'error' && errorMessage ? (
            <p className="truncate text-[9px] leading-snug text-bear/90">{errorMessage}</p>
          ) : null}
        </div>

        <span className="ml-auto flex shrink-0 items-center gap-1 self-start pt-px">
          {state === 'ready' ? (
            value
          ) : state === 'loading' ? (
            <span
              role="status"
              className="flex items-center gap-1 rounded-full bg-primary/10 px-1.5 py-0.5"
            >
              <Loader2 size={9} aria-hidden="true" className="animate-spin text-primary" />
              <span className="text-[8px] font-bold uppercase tracking-wider text-primary">
                {loadingMessage}
              </span>
            </span>
          ) : state === 'error' ? (
            <span className="flex items-center gap-1 rounded-full bg-bear/10 px-1.5 py-0.5">
              <AlertTriangle size={9} aria-hidden="true" className="shrink-0 text-bear" />
              <span className="text-[8px] font-black uppercase tracking-wider text-bear">
                Unavailable
              </span>
            </span>
          ) : (
            <span className="rounded-full bg-elevated/70 px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-wider text-text-muted">
              {emptyMessage ?? 'No data'}
            </span>
          )}

          <ChevronRight
            size={12}
            aria-hidden="true"
            className="shrink-0 text-text-muted/50 transition-colors group-hover:text-text-secondary"
          />
        </span>
      </div>
    </button>
  );
}
