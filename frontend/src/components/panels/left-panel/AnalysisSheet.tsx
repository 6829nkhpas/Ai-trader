'use client';

import React from 'react';
import { Dialog } from '@base-ui/react/dialog';
import { Tabs } from '@base-ui/react/tabs';
import { AlertTriangle, Newspaper, Sparkles, TrendingUp, X } from 'lucide-react';
import type {
  ConsensusReport,
  MultiTfChartPatterns,
  SentimentPayload,
} from '../../../store/useQuantStore';
import SentimentBlock from './SentimentBlock';
import LiveAssetHUD from './LiveAssetHUD';
import MultiTfPatternsView from '../../quant/deep-quant/MultiTfPatternsView';
import { consensusMatchesSymbol } from './consensusView';
import { totalPatternCount } from './patternsSummary';

/** The sections the sheet can show. A `null` tab means the sheet is closed. */
export type AnalysisTab = 'sentiment' | 'technical' | 'patterns';

const TAB_ORDER: AnalysisTab[] = ['sentiment', 'technical', 'patterns'];

const TAB_LABELS: Record<AnalysisTab, string> = {
  sentiment: 'AI News Sentiment',
  technical: 'Technical Consensus',
  patterns: 'Pattern Scanner',
};

/** Short forms for the tab bar, which has three of these to fit in ~420px. */
const TAB_SHORT_LABELS: Record<AnalysisTab, string> = {
  sentiment: 'Sentiment',
  technical: 'Technical',
  patterns: 'Patterns',
};

const TAB_ICONS: Record<AnalysisTab, React.ComponentType<{ size?: number; className?: string }>> = {
  sentiment: Newspaper,
  technical: TrendingUp,
  patterns: Sparkles,
};

export interface AnalysisSheetProps {
  /** The section to show, or `null` to keep the sheet closed. */
  tab: AnalysisTab | null;
  /** Switching section from the tab bar. Does not close the sheet. */
  onTabChange: (tab: AnalysisTab) => void;
  onClose: () => void;
  /** The symbol the analyses on show belong to. */
  symbol?: string;

  sentiment: SentimentPayload | null;
  isSentimentLoading: boolean;
  sentimentError: string | null;

  consensus: ConsensusReport | null;
  /** When `consensus` was computed (epoch ms), or null if unknown. */
  consensusComputedAt: number | null;

  multiTfPatterns: MultiTfChartPatterns[] | null;
  isPatternsLoading: boolean;
  patternsError: string | null;
}

/**
 * What the technical tab says when nothing has been computed for this symbol.
 *
 * Stating the reason and the remedy, rather than rendering an empty gauge: a
 * consensus of zero and an absent consensus look identical on a dial.
 */
function EmptyTechnical({ symbol }: { symbol?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      <TrendingUp size={20} aria-hidden="true" className="text-text-muted/40" />
      <p className="text-xs font-bold tracking-tight text-text-primary">
        No technical reading for{' '}
        <span className="font-extrabold text-primary">{symbol || 'this symbol'}</span>
      </p>
      <p className="max-w-70 text-[11px] leading-relaxed text-text-secondary">
        Run Deep Quant Analysis to compute the technical consensus, patterns and active
        strategies for this instrument.
      </p>
    </div>
  );
}

/**
 * The badge on a tab: what that section is currently reporting, in one token.
 *
 * A failed fetch shows a warning glyph rather than a count, so a section that
 * could not be read is not advertised as an empty one — the same distinction the
 * strips make in the rail.
 */
function TabBadge({
  tone,
  children,
}: {
  tone: 'neutral' | 'warn';
  children: React.ReactNode;
}) {
  return (
    <span
      className={`
        flex h-3.5 min-w-3.5 items-center justify-center rounded-none border px-0.5
        text-[8px] font-black tabular-nums
        ${
          tone === 'warn'
            ? 'border-neutral/40 bg-neutral/15 text-neutral'
            : 'border-border-default bg-elevated text-text-secondary'
        }
      `}
    >
      {children}
    </span>
  );
}

/**
 * The Market Watch detail dialog.
 *
 * Built on Base UI's `Dialog` for two reasons that are not cosmetic:
 *
 * 1. `Dialog.Portal` escapes the panel's DOM subtree. The Market Watch column in
 *    `TerminalLayout` carries an inline `transform: translateX(...)` for its
 *    slide animation, and a transformed ancestor becomes the containing block for
 *    `position: fixed` descendants — so a dialog rendered in place would be
 *    trapped inside a 224px column instead of covering the screen.
 * 2. Focus trapping, Escape handling, scroll locking and `aria-modal` wiring come
 *    with the component rather than being reimplemented (and partly forgotten,
 *    as in the app's hand-rolled overlays).
 *
 * Centered rather than a side sheet: a popup that appears in the middle of the
 * screen reads as "the data you asked for", where a panel sliding in from the
 * edge reads as permanent chrome — and it keeps the chart fully visible around
 * it instead of covering a vertical slice of the workspace.
 *
 * The tab bar is Base UI `Tabs` for the same reason: roving tabindex, arrow-key
 * navigation and `aria-selected`/`aria-controls` pairing are the whole job of a
 * tablist, and hand-rolling them is how tablists end up keyboard-inert.
 */
export default function AnalysisSheet({
  tab,
  onTabChange,
  onClose,
  symbol,
  sentiment,
  isSentimentLoading,
  sentimentError,
  consensus,
  consensusComputedAt,
  multiTfPatterns,
  isPatternsLoading,
  patternsError,
}: AnalysisSheetProps) {
  const open = tab !== null;

  // Same rule as the strip: a report for a different symbol is no report. The
  // sheet has more room, which makes it more tempting — and more misleading — to
  // render another symbol's numbers under this one's heading.
  const technical = consensus && consensusMatchesSymbol(consensus.symbol, symbol) ? consensus : null;

  const patternTotal = totalPatternCount(multiTfPatterns);

  const badges: Record<AnalysisTab, React.ReactNode> = {
    sentiment: sentimentError ? (
      <TabBadge tone="warn">
        <AlertTriangle size={7} aria-hidden="true" />
      </TabBadge>
    ) : sentiment && !isSentimentLoading ? (
      <TabBadge tone="neutral">{sentiment.headlines.length}</TabBadge>
    ) : null,

    technical: technical ? (
      <TabBadge tone="neutral">
        {technical.trend_score > 0 ? '+' : ''}
        {technical.trend_score}
      </TabBadge>
    ) : null,

    patterns: patternsError ? (
      <TabBadge tone="warn">
        <AlertTriangle size={7} aria-hidden="true" />
      </TabBadge>
    ) : patternTotal > 0 && !isPatternsLoading ? (
      <TabBadge tone="neutral">{patternTotal}</TabBadge>
    ) : null,
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        // Backdrop press, Escape, and the close button all arrive here.
        if (!next) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop
          className="
            fixed inset-0 z-9998 min-h-dvh bg-black/50 backdrop-blur-[2px]
            transition-opacity duration-200
            data-ending-style:opacity-0 data-starting-style:opacity-0
            motion-reduce:transition-none
          "
        />

        <Dialog.Popup
          className="
            fixed top-1/2 left-1/2 z-9999 flex h-[min(640px,85vh)] w-125 max-w-[calc(100vw-2rem)]
            -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden
            rounded-lg border border-border-default bg-surface shadow-2xl
            transition-[transform,opacity] duration-200 ease-out
            data-ending-style:scale-95 data-ending-style:opacity-0
            data-starting-style:scale-95 data-starting-style:opacity-0
            motion-reduce:transition-none
            focus-visible:outline-none
          "
        >
          {/* ── Header ─────────────────────────────────────────── */}
          <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border-default bg-elevated/20 px-4">
            <Dialog.Title className="text-xs font-black uppercase tracking-wider text-text-primary">
              Market Watch
            </Dialog.Title>

            {symbol && (
              <span className="rounded-none border border-border-default bg-elevated px-1.5 py-px text-[9px] font-bold uppercase tracking-wider text-text-secondary">
                {symbol}
              </span>
            )}

            <Dialog.Close
              aria-label="Close details"
              className="
                ml-auto flex h-6 w-6 items-center justify-center rounded-none
                text-text-muted transition-colors hover:bg-elevated hover:text-text-primary
                focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary
              "
            >
              <X size={14} aria-hidden="true" />
            </Dialog.Close>
          </div>

          {/* `tab` doubles as the open flag, so it is null exactly when the sheet
              is closed and this fallback is never the visible state. */}
          <Tabs.Root
            value={tab ?? 'sentiment'}
            onValueChange={(value) => onTabChange(value as AnalysisTab)}
            className="flex min-h-0 flex-1 flex-col"
          >
            <Tabs.List
              aria-label="Analysis sections"
              // Arrow keys change the section outright rather than only moving
              // focus: with three tabs and no destructive side effects, making the
              // user press Enter as well is friction without a payoff.
              activateOnFocus
              className="flex shrink-0 items-stretch border-b border-border-default bg-elevated/10"
            >
              {TAB_ORDER.map((value) => {
                const Icon = TAB_ICONS[value];
                return (
                  <Tabs.Tab
                    key={value}
                    value={value}
                    // The visible label is shortened to fit three tabs; the full
                    // section name goes to assistive tech.
                    aria-label={TAB_LABELS[value]}
                    className="
                      flex flex-1 items-center justify-center gap-1 px-2 py-2
                      text-[9px] font-bold uppercase tracking-wider text-text-muted
                      transition-colors select-none
                      hover:bg-elevated/40 hover:text-text-secondary
                      data-selected:bg-surface data-selected:text-text-primary
                      data-selected:shadow-[inset_0_-2px_0_0_var(--color-primary)]
                      focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-primary
                    "
                  >
                    <Icon size={10} aria-hidden="true" />
                    <span>{TAB_SHORT_LABELS[value]}</span>
                    {badges[value]}
                  </Tabs.Tab>
                );
              })}
            </Tabs.List>

            <Tabs.Panel
              value="sentiment"
              className="min-h-0 flex-1 overflow-y-auto scrollbar-thin focus-visible:outline-none"
            >
              <SentimentBlock
                symbol={symbol}
                sentiment={sentiment}
                isLoading={isSentimentLoading}
                error={sentimentError}
                variant="sheet"
              />
            </Tabs.Panel>

            <Tabs.Panel
              value="technical"
              className="min-h-0 flex-1 overflow-y-auto scrollbar-thin focus-visible:outline-none"
            >
              {technical ? (
                <LiveAssetHUD data={technical} computedAt={consensusComputedAt} variant="sheet" />
              ) : (
                <EmptyTechnical symbol={symbol} />
              )}
            </Tabs.Panel>

            {/* Reads `useQuantStore` itself, including the timeframe tabs and the
                click-to-visualise handler that pushes a pattern onto the chart —
                all of which keep working from in here. */}
            <Tabs.Panel
              value="patterns"
              className="min-h-0 flex-1 overflow-y-auto scrollbar-thin focus-visible:outline-none"
            >
              <MultiTfPatternsView variant="sheet" />
            </Tabs.Panel>
          </Tabs.Root>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
