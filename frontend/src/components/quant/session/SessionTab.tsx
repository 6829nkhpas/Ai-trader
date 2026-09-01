'use client';

// components/quant/session/SessionTab.tsx
//
// One tab. Presentational: it owns no data and decides nothing about archiving or activation, so
// the bar above it can be tested for behaviour and this can be tested for markup.

import React from 'react';
import { Loader2, X } from 'lucide-react';

import type { SessionSummary } from '../../../lib/fq/api';
import { sessionTabAriaLabel, sessionTabLabel, sessionTabTooltip } from './sessionLabel';

export interface SessionTabProps {
  session: SessionSummary;
  isActive: boolean;
  /** A run is streaming into this session — possibly one the user is not looking at. */
  isStreaming: boolean;
  /** True while this tab's archive request is in flight. */
  isClosing?: boolean;
  onActivate: (sessionId: string) => void;
  onClose: (sessionId: string) => void;
  /** Set by the bar so arrow-key navigation has exactly one tab stop. */
  tabIndex: number;
  registerRef?: (sessionId: string, el: HTMLButtonElement | null) => void;
}

export default function SessionTab({
  session,
  isActive,
  isStreaming,
  isClosing = false,
  onActivate,
  onClose,
  tabIndex,
  registerRef,
}: SessionTabProps) {
  const label = sessionTabLabel(session);
  const tooltip = sessionTabTooltip(session);

  return (
    // `group` drives the close button's hover reveal. The wrapper is a plain div, not a second
    // button: nesting an interactive element inside a `role="tab"` button is invalid markup and
    // makes the close control unreachable for keyboard and screen-reader users.
    <div
      className={`group relative flex shrink-0 items-center gap-1.5 rounded-t-md border-b-2 pl-3 pr-1 transition-colors ${
        isActive
          ? 'border-primary bg-elevated text-text-primary'
          : 'border-transparent text-text-secondary hover:bg-elevated/40 hover:text-text-primary'
      }`}
      style={{ scrollSnapAlign: 'start' }}
      data-session-id={session.session_id}
      data-active={isActive || undefined}
    >
      <button
        type="button"
        role="tab"
        id={`fq-tab-${session.session_id}`}
        aria-selected={isActive}
        aria-controls="fq-session-workspace"
        // Roving tabindex: only the active tab is in the tab order, and the arrow keys move
        // between the rest. Leaving every tab focusable would mean eight tab presses to get past
        // the bar.
        tabIndex={tabIndex}
        ref={registerRef ? (el) => registerRef(session.session_id, el) : undefined}
        title={tooltip}
        aria-label={sessionTabAriaLabel(session, isStreaming)}
        onClick={() => onActivate(session.session_id)}
        className="flex max-w-[14rem] items-center gap-2 py-2 text-xs font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60 focus-visible:ring-offset-0"
      >
        {isStreaming && (
          // Communicates that a BACKGROUND session is working. Under the old single-session store
          // this state could not exist, because a second run overwrote the first.
          <span
            className="inline-flex h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-primary"
            // Decorative: the state is already in the tab's `aria-label`, so announcing it twice
            // would be noise.
            aria-hidden="true"
          />
        )}
        <span className="truncate">{label}</span>
      </button>

      <button
        type="button"
        // Named, because "X" tells a screen-reader user nothing about what closes.
        aria-label={`Close ${label}`}
        title={`Close ${label}`}
        disabled={isClosing}
        onClick={(e) => {
          // Without this the click also lands on the tab, activating the session on its way out.
          e.stopPropagation();
          onClose(session.session_id);
        }}
        className={`flex h-5 w-5 shrink-0 items-center justify-center rounded transition-opacity hover:bg-surface focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60 disabled:opacity-40 ${
          // Hidden controls are also POINTER-DISABLED, not just transparent. An `opacity-0`
          // button still receives clicks, so on touch — where there is no hover to reveal it —
          // an invisible 20px target sits on the edge of every inactive tab, and the tap that
          // was meant to switch sessions archives one instead.
          //
          // The close path on touch is therefore: tap to activate, then close. One extra tap,
          // and nothing destructive is ever invisible.
          isActive
            ? 'opacity-100'
            : 'pointer-events-none opacity-0 group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100'
        }`}
      >
        {isClosing ? (
          <Loader2 size={11} className="animate-spin" aria-hidden="true" />
        ) : (
          <X size={11} aria-hidden="true" />
        )}
      </button>
    </div>
  );
}
