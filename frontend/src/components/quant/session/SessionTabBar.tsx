'use client';

// components/quant/session/SessionTabBar.tsx
//
// The tab bar. Owns the list, the selection, keyboard navigation, overflow and archiving; the tabs
// themselves are presentational.
//
// The list comes from the SERVER (`GET /sessions`), not from whatever the client happens to have in
// `useSessionStore`. That is what makes a tab survive a reload, and it means the bar cannot show a
// session the server does not have — the failure mode where a "session" existed only in one
// browser tab is unrepresentable here.

import React from 'react';
import { AlertTriangle, MoreHorizontal, RefreshCw } from 'lucide-react';

import type { SessionSummary } from '../../../lib/fq/api';
import { useArchiveSession, useSessions } from '../../../lib/fq/queries';
import { useSessionStore } from '../../../store/useSessionStore';
import { selectStreamingSessionIds } from '../../../store/sessionSelectors';
import NewSessionButton from './NewSessionButton';
import SessionTab from './SessionTab';
import { sessionTabLabel } from './sessionLabel';

/**
 * Past this many tabs the overflow menu appears.
 *
 * Eight is where a tab's label stops being readable at a typical panel width; beyond it, scrolling
 * to find a session is slower than picking it from a list.
 */
const OVERFLOW_THRESHOLD = 8;

export interface SessionTabBarProps {
  /**
   * Called when the user picks a session.
   *
   * Injected rather than calling `setActiveSession` here, because activating a session the client
   * has not loaded also has to REHYDRATE it — and that belongs to the workspace that renders the
   * transcript, not to the bar. The default keeps the bar usable and testable on its own.
   */
  onActivate?: (sessionId: string) => void;
}

export default function SessionTabBar({ onActivate }: SessionTabBarProps) {
  const { data, isLoading, isError, error, refetch, isFetching } = useSessions({ status: 'active' });
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const setActiveSession = useSessionStore((s) => s.setActiveSession);
  const streamingIds = useSessionStore(selectStreamingSessionIds);
  const archive = useArchiveSession();

  const [overflowOpen, setOverflowOpen] = React.useState(false);
  const [closingId, setClosingId] = React.useState<string | null>(null);
  const [confirming, setConfirming] = React.useState<SessionSummary | null>(null);
  const [closeError, setCloseError] = React.useState<string | null>(null);
  const tabRefs = React.useRef<Map<string, HTMLButtonElement>>(new Map());

  // Flattened once. `useInfiniteQuery` returns pages, and a `.flatMap` inline would build a new
  // array every render — re-rendering every tab on every parent render.
  // `page.items ?? []` rather than `page.items`: a page that arrives without an `items` array makes
  // `flatMap` yield a single `undefined` entry, which then reaches `session.session_id` in the render
  // below and throws — taking out the entire tab bar, not just the one tab. This is a network
  // boundary, so the page shape is not ours to assume.
  const sessions = React.useMemo(
    () => (data?.pages ?? []).flatMap((page) => page?.items ?? []),
    [data],
  );

  const activate = React.useCallback(
    (sessionId: string) => {
      setOverflowOpen(false);
      if (onActivate) onActivate(sessionId);
      else setActiveSession(sessionId);
    },
    [onActivate, setActiveSession],
  );

  const registerRef = React.useCallback((sessionId: string, el: HTMLButtonElement | null) => {
    if (el) tabRefs.current.set(sessionId, el);
    else tabRefs.current.delete(sessionId);
  }, []);

  /**
   * Archive, with a confirm ONLY when the run would be interrupted.
   *
   * Always confirming trains the user to dismiss the dialog without reading it, which is worse
   * than not having one. A session with nothing running is recoverable from history, so closing it
   * needs no ceremony.
   */
  const requestClose = React.useCallback(
    (sessionId: string) => {
      const session = sessions.find((s) => s.session_id === sessionId);
      if (!session) return;
      if (streamingIds.includes(sessionId)) {
        setConfirming(session);
        return;
      }
      void doArchive(session);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sessions, streamingIds],
  );

  const doArchive = React.useCallback(
    async (session: SessionSummary) => {
      setConfirming(null);
      setCloseError(null);
      setClosingId(session.session_id);
      try {
        await archive.mutateAsync(session.session_id);
        // Only AFTER the server has accepted it. An optimistic removal that has to come back
        // looks exactly like lost work — see the rename/archive asymmetry in the design record.
        const remaining = sessions.filter((s) => s.session_id !== session.session_id);
        if (useSessionStore.getState().activeSessionId === session.session_id) {
          if (remaining.length > 0) activate(remaining[0].session_id);
          else setActiveSession(null);
        }
        // Dropped from the client only once the server agrees it is archived, so a failed request
        // cannot discard a transcript that still exists.
        useSessionStore.getState().dropSession(session.session_id);
      } catch (err) {
        // Caught, not left to become an unhandled rejection. The tab stays where it was — correct,
        // since nothing was archived — but silence would read as "the close button is broken".
        setCloseError(
          err instanceof Error && err.message
            ? `Could not close ${sessionTabLabel(session)}: ${err.message}`
            : `Could not close ${sessionTabLabel(session)}.`,
        );
      } finally {
        setClosingId(null);
      }
    },
    [archive, sessions, activate, setActiveSession],
  );

  /**
   * Arrow keys move focus between tabs; Home/End jump to the ends.
   *
   * Focus moves WITHOUT activating. Activating on arrow would rehydrate a session per keypress
   * while the user is scanning, so selection is committed with Enter/Space — which the tab's own
   * button handles as a click.
   */
  const onKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End'];
    if (!keys.includes(e.key)) return;
    const ids = sessions.map((s) => s.session_id);
    if (ids.length === 0) return;

    const focused = document.activeElement as HTMLElement | null;
    const currentId = focused?.id?.replace(/^fq-tab-/, '') ?? '';
    const from = ids.indexOf(currentId);
    // Anchor to the active tab when focus is not on a tab yet, so the first arrow press moves from
    // where the user is looking rather than from index 0.
    const base = from >= 0 ? from : Math.max(0, ids.indexOf(activeSessionId ?? ''));

    let next = base;
    if (e.key === 'ArrowRight') next = (base + 1) % ids.length;
    else if (e.key === 'ArrowLeft') next = (base - 1 + ids.length) % ids.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = ids.length - 1;

    e.preventDefault();
    tabRefs.current.get(ids[next])?.focus();
  };

  /**
   * `Ctrl/⌘+1..9` switches session, matching every browser and editor.
   *
   * 9 means LAST, not the ninth — again matching browsers, and it stays useful past nine sessions.
   */
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
      if (e.key < '1' || e.key > '9') return;
      const ids = sessions.map((s) => s.session_id);
      if (ids.length === 0) return;
      const n = Number(e.key);
      const index = n === 9 ? ids.length - 1 : n - 1;
      if (index >= ids.length) return;
      e.preventDefault();
      activate(ids[index]);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [sessions, activate]);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 border-b border-border-default/40 bg-surface px-3 py-2">
        {/* Skeletons sized like real tabs, so the bar does not jump when they resolve. */}
        {[0, 1].map((i) => (
          <div key={i} className="h-6 w-28 animate-pulse rounded bg-elevated" aria-hidden="true" />
        ))}
        <span className="sr-only">Loading sessions…</span>
      </div>
    );
  }

  if (isError) {
    return (
      <div
        role="alert"
        className="flex items-center gap-2 border-b border-border-default/40 bg-surface px-3 py-2 text-xs text-text-secondary"
      >
        <AlertTriangle size={13} className="shrink-0 text-status-error" aria-hidden="true" />
        {/* The reason, not just "something went wrong" — a 401 needs a different action from a
            service being down, and the user can only tell if we say which happened. */}
        <span className="truncate">
          Could not load your sessions{error instanceof Error && error.message ? `: ${error.message}` : ''}
        </span>
        <button
          type="button"
          onClick={() => void refetch()}
          disabled={isFetching}
          className="ml-auto flex items-center gap-1 rounded border border-border-default/60 px-2 py-0.5 hover:bg-elevated focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60 disabled:opacity-50"
        >
          <RefreshCw size={11} className={isFetching ? 'animate-spin' : ''} aria-hidden="true" />
          Retry
        </button>
      </div>
    );
  }

  const visible = sessions.slice(0, OVERFLOW_THRESHOLD);
  const overflow = sessions.slice(OVERFLOW_THRESHOLD);

  return (
    <div className="relative border-b border-border-default/40 bg-surface">
      <div className="flex items-stretch gap-1 px-2">
        <div
          role="tablist"
          aria-label="Analysis sessions"
          aria-orientation="horizontal"
          onKeyDown={onKeyDown}
          className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto"
          // Horizontal scroll that settles on a tab edge rather than mid-label.
          style={{ scrollSnapType: 'x proximity', scrollbarWidth: 'thin' }}
        >
          {visible.map((session) => (
            <SessionTab
              key={session.session_id}
              session={session}
              isActive={session.session_id === activeSessionId}
              isStreaming={streamingIds.includes(session.session_id)}
              isClosing={closingId === session.session_id}
              onActivate={activate}
              onClose={requestClose}
              tabIndex={session.session_id === activeSessionId ? 0 : -1}
              registerRef={registerRef}
            />
          ))}

          {sessions.length === 0 && (
            <span className="flex items-center py-2 text-xs text-text-muted">
              No sessions yet — start one to analyse a symbol.
            </span>
          )}
        </div>

        {overflow.length > 0 && (
          <div className="relative flex items-center">
            <button
              type="button"
              aria-haspopup="menu"
              aria-expanded={overflowOpen}
              aria-label={`${overflow.length} more sessions`}
              onClick={() => setOverflowOpen((v) => !v)}
              className="flex h-7 items-center gap-1 rounded px-2 text-xs text-text-secondary hover:bg-elevated hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
            >
              <MoreHorizontal size={13} aria-hidden="true" />
              {overflow.length}
            </button>
            {overflowOpen && (
              <div
                role="menu"
                className="absolute right-0 top-full z-20 mt-1 max-h-72 w-64 overflow-y-auto rounded-md border border-border-default/60 bg-elevated py-1 shadow-lg"
              >
                {overflow.map((session) => (
                  <button
                    key={session.session_id}
                    type="button"
                    role="menuitem"
                    onClick={() => activate(session.session_id)}
                    className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-text-secondary hover:bg-surface hover:text-text-primary focus:outline-none focus-visible:bg-surface"
                  >
                    {streamingIds.includes(session.session_id) && (
                      <span
                        className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-primary"
                        aria-hidden="true"
                      />
                    )}
                    <span className="truncate">{sessionTabLabel(session)}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="flex items-center pl-1">
          <NewSessionButton onCreated={activate} />
        </div>
      </div>

      {closeError && (
        <div
          role="alert"
          className="absolute left-2 right-2 top-full z-30 mt-1 flex items-start gap-1.5 rounded-md border border-border-default/60 bg-elevated p-2 text-xs text-text-secondary shadow-lg"
        >
          <AlertTriangle size={12} className="mt-0.5 shrink-0 text-status-error" aria-hidden="true" />
          <span className="min-w-0 flex-1">{closeError}</span>
          <button
            type="button"
            onClick={() => setCloseError(null)}
            aria-label="Dismiss"
            className="shrink-0 text-text-muted hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
          >
            ×
          </button>
        </div>
      )}

      {confirming && (
        // A confirm ONLY for the destructive-while-running case. `alertdialog` rather than
        // `dialog`, because this interrupts to prevent data loss.
        <div
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="fq-close-confirm-title"
          className="absolute left-2 right-2 top-full z-30 mt-1 rounded-md border border-border-default/60 bg-elevated p-3 shadow-lg"
        >
          <p id="fq-close-confirm-title" className="text-xs text-text-primary">
            <strong className="font-medium">{sessionTabLabel(confirming)}</strong> is still running.
            Closing it now stops the analysis and you lose the rest of the answer.
          </p>
          <div className="mt-2 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setConfirming(null)}
              className="rounded border border-border-default/60 px-2 py-1 text-xs text-text-secondary hover:bg-surface focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
            >
              Keep it open
            </button>
            <button
              type="button"
              onClick={() => void doArchive(confirming)}
              className="rounded bg-status-error px-2 py-1 text-xs text-white hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
            >
              Close anyway
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
