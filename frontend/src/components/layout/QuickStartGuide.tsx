'use client';

/**
 * Quick Start Guide — an in-app modal explaining the terminal's actual surfaces.
 *
 * Reported as "add Quick Start Guide — Not Found": there was no guide, no route
 * and no link anywhere in the app or in `docs/`.
 *
 * Every step below describes a control that really exists in this build, and the
 * workspace list is derived from `PROFILES` (the same source the header tabs use)
 * rather than being retyped here — so the guide cannot drift out of sync with the
 * tabs it is describing.
 */

import React from 'react';
import {
  X,
  Search,
  LayoutGrid,
  Zap,
  Radar,
  Bell,
  SunMoon,
  TrendingUp,
  BookOpen,
} from 'lucide-react';
import { PROFILES } from '../../utils/layoutHelpers';

interface QuickStartGuideProps {
  open: boolean;
  onClose: () => void;
}

interface Step {
  icon: React.ReactNode;
  title: string;
  body: string;
}

const STEPS: Step[] = [
  {
    icon: <Search size={13} />,
    title: 'Pick an instrument',
    body:
      'Use the search icon at the top of the navigation rail (or press Ctrl+K) to look up any NSE, BSE or F&O instrument. Selecting a result charts it and adds it to your watchlist.',
  },
  {
    icon: <LayoutGrid size={13} />,
    title: 'Choose a workspace',
    body:
      `The tabs at the top switch the whole layout — ${PROFILES.map((p) => p.label).join(', ')}. ` +
      'Each one arranges the chart, side panels and timeframe defaults for that style of trading.',
  },
  {
    icon: <TrendingUp size={13} />,
    title: 'Read the chart',
    body:
      'The toolbar inside the chart carries drawing tools, indicators, chart mode (standard, volume profile, footprint) and the projection engine that draws the ghost line. Timeframe is set from the chart header.',
  },
  {
    icon: <Zap size={13} />,
    title: 'Run an analysis',
    body:
      'In the right sidebar, "Find a Trade Setup" asks the quant agent to scan the current symbol and propose a setup; "Verify My Setup" checks a trade you already have in mind. The transcript streams live, and you can stop a run at any time.',
  },
  {
    icon: <Radar size={13} />,
    title: 'Track several symbols at once',
    body:
      'The Radar in the header watches a list of symbols on a chosen timeframe and badges how many candlestick patterns and strategies it has found. Click any detection to mark it on the chart.',
  },
  {
    icon: <Bell size={13} />,
    title: 'Keep an eye on the feed',
    body:
      'The bell shows warnings and errors from this session — a dropped data feed or a failed request shows up there, so an empty panel is never a mystery.',
  },
  {
    icon: <SunMoon size={13} />,
    title: 'Make it yours',
    body:
      'The sun/moon icon toggles light and dark, and your choice is remembered across reloads. Side panels can be dragged to resize or collapsed out of the way.',
  },
];

export default function QuickStartGuide({ open, onClose }: QuickStartGuideProps) {
  // Close on Escape, matching the app's other modals.
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="quick-start-title"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-lg flex-col rounded-xl border border-border-default bg-surface shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-border-default px-4 py-3">
          <div className="flex items-center gap-2">
            <BookOpen size={15} className="text-primary" />
            <h2 id="quick-start-title" className="text-sm font-black tracking-tight text-text-primary">
              Quick Start Guide
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close quick start guide"
            className="rounded p-1 text-text-muted transition-colors hover:bg-elevated hover:text-text-primary"
          >
            <X size={15} />
          </button>
        </div>

        {/* Steps */}
        <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin px-4 py-3">
          <p className="mb-3 text-xs leading-relaxed text-text-secondary">
            A market analysis and charting terminal for NSE and NFO. Seven things
            worth knowing before you start.
          </p>
          <ol className="flex flex-col gap-3">
            {STEPS.map((step, i) => (
              <li key={step.title} className="flex gap-3">
                <div className="flex shrink-0 flex-col items-center">
                  <div className="flex h-6 w-6 items-center justify-center rounded-full bg-primary/10 text-primary">
                    {step.icon}
                  </div>
                  {i < STEPS.length - 1 && <div className="mt-1 w-px flex-1 bg-border-default" />}
                </div>
                <div className="min-w-0 pb-1">
                  <h3 className="text-xs font-bold text-text-primary">{step.title}</h3>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-text-secondary">
                    {step.body}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </div>

        {/* Footer */}
        <div className="flex shrink-0 items-center justify-end border-t border-border-default px-4 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-primary px-5 py-2 text-xs font-extrabold uppercase tracking-wider text-black transition-colors hover:bg-primary-hover"
          >
            Start trading
          </button>
        </div>
      </div>
    </div>
  );
}
