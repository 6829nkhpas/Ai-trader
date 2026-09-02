'use client';

// components/quant/session/SessionHistory.tsx
//
// Session history: a cursor-paginated list of the user's sessions.
//
// The hard constraint is that this must NEVER load the whole history into the browser. A trader
// running several analyses a day accumulates thousands of sessions; a client-side list would grow
// without bound, and the search box would be searching a subset while looking like it searched
// everything. So paging and filtering are both the server's job (`?cursor=`, `?q=`), and this
// component only ever holds the pages it asked for.

import React from 'react';
import { AlertTriangle, Loader2, RefreshCw, Search } from 'lucide-react';

import { useArchiveSession, useRenameSession, useReopenSession, useSessions } from '../../../lib/fq/queries';
import { useSessionStore } from '../../../store/useSessionStore';
import SessionHistoryRow from './SessionHistoryRow';

export interface SessionHistoryProps {
  /** Opening a session must also rehydrate it, so the caller supplies the activation. */
  onOpen: (sessionId: string) => void;
  /** `active` is the tab bar's set; `archived` is everything closed. */
  status?: 'active' | 'archived';
}

/**
 * Search only appears once the list does not fit on one page.
 *
 * A search box over eight rows is noise, and worse, it invites the user to search when scanning is
 * faster. Past one page scanning stops working, and at that point the filter has to be
 * server-side anyway.
 */
const SEARCH_MIN_PAGES = 1;

/** Debounce for `?q=`, so typing eight characters is one request rather than eight. */
const SEARCH_DEBOUNCE_MS = 250;

export default function SessionHistory({ onOpen, status = 'active' }: SessionHistoryProps) {
  const [rawQuery, setRawQuery] = React.useState('');
  const [query, setQuery] = React.useState('');
  const [busyId, setBusyId] = React.useState<string | null>(null);
  const [actionError, setActionError] = React.useState<string | null>(null);

  React.useEffect(() => {
    const id = setTimeout(() => setQuery(rawQuery.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [rawQuery]);

  const list = useSessions({ status, q: query || undefined });
  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const rename = useRenameSession();
  const archive = useArchiveSession();
  const reopen = useReopenSession();

  const sessions = React.useMemo(
    () => (list.data?.pages ?? []).flatMap((page) => page.items),
    [list.data],
  );

  /**
   * Whether the search control is warranted.
   *
   * Sticky once shown: it is based on having SEEN more than one page, not on the current filtered
   * result. A filter that narrows the list to three rows must not remove the box that produced it,
   * which would leave the user stuck in a filtered view with no way out.
   */
  const [everPaged, setEverPaged] = React.useState(false);
  React.useEffect(() => {
    if ((list.data?.pages.length ?? 0) > SEARCH_MIN_PAGES || list.hasNextPage) setEverPaged(true);
  }, [list.data, list.hasNextPage]);

  /**
   * Load the next page when the sentinel scrolls into view.
   *
   * `IntersectionObserver` rather than a scroll handler: a scroll handler fires per frame and has to
   * measure the container itself, which is both more work and wrong inside a nested scroller.
   */
  const sentinelRef = React.useRef<HTMLDivElement | null>(null);
  React.useEffect(() => {
    const el = sentinelRef.current;
    // Absent in jsdom and in older webviews. Without the guard the whole list fails to render rather
    // than merely losing infinite scroll, so the Load-more button below is the real fallback.
    if (!el || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver((entries) => {
      // `isFetchingNextPage` is what stops a fast scroll firing the same page request repeatedly.
      if (entries[0]?.isIntersecting && list.hasNextPage && !list.isFetchingNextPage) {
        void list.fetchNextPage();
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [list.hasNextPage, list.isFetchingNextPage, list.fetchNextPage]);

  /** Run a row mutation, surfacing failure instead of letting the rejection escape. */
  const run = async (sessionId: string, verb: string, op: () => Promise<unknown>) => {
    setBusyId(sessionId);
    setActionError(null);
    try {
      await op();
    } catch (err) {
      setActionError(
        err instanceof Error && err.message ? `Could not ${verb}: ${err.message}` : `Could not ${verb}.`,
      );
    } finally {
      setBusyId(null);
    }
  };

  const handleRename = (sessionId: string, title: string | null) =>
    void run(sessionId, 'rename this session', () => rename.mutateAsync({ sessionId, title }));

  const handleArchive = (sessionId: string) =>
    void run(sessionId, 'archive this session', async () => {
      await archive.mutateAsync(sessionId);
      // Dropped only after the server agrees, and only if it was on screen.
      if (useSessionStore.getState().activeSessionId === sessionId) {
        useSessionStore.getState().setActiveSession(null);
      }
      useSessionStore.getState().dropSession(sessionId);
    });

  const handleReopen = (sessionId: string) =>
    void run(sessionId, 'reopen this session', async () => {
      await reopen.mutateAsync(sessionId);
      // Reopening means "I want to work on this now", so it is opened too — the alternative is a row
      // that changes a status badge and appears to do nothing.
      onOpen(sessionId);
    });

  return (
    <div className="flex h-full min-h-0 flex-col">
      {everPaged && (
        <div className="shrink-0 border-b border-border-default/40 p-2">
          <div className="flex items-center gap-1.5 rounded border border-border-default/60 bg-surface px-2">
            <Search size={12} className="shrink-0 text-text-muted" aria-hidden="true" />
            <input
              type="search"
              value={rawQuery}
              onChange={(e) => setRawQuery(e.target.value)}
              placeholder="Search sessions"
              aria-label="Search sessions"
              className="w-full bg-transparent py-1 text-xs text-text-primary placeholder:text-text-muted focus:outline-none"
            />
            {list.isFetching && !list.isFetchingNextPage && (
              <Loader2 size={11} className="shrink-0 animate-spin text-text-muted" aria-hidden="true" />
            )}
          </div>
        </div>
      )}

      {actionError && (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-1.5 border-b border-border-default/40 bg-elevated p-2 text-xs text-text-secondary"
        >
          <AlertTriangle size={12} className="mt-0.5 shrink-0 text-status-error" aria-hidden="true" />
          <span className="min-w-0 flex-1">{actionError}</span>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => setActionError(null)}
            className="shrink-0 text-text-muted hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
          >
            ×
          </button>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {list.isLoading ? (
          // Deliberately NOT a `<ul>/<li>`. A skeleton built from list items is indistinguishable
          // from a loaded list by role, so assistive technology announces "list, 4 items" for
          // placeholder boxes — and any consumer asking for a `listitem` gets a fake one.
          <div className="p-2" role="status" aria-live="polite">
            <span className="sr-only">Loading session history…</span>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="mb-2 h-8 animate-pulse rounded bg-elevated" aria-hidden="true" />
            ))}
          </div>
        ) : list.isError ? (
          <div role="alert" className="flex flex-col items-start gap-2 p-3 text-xs text-text-secondary">
            <span className="flex items-start gap-1.5">
              <AlertTriangle size={13} className="mt-0.5 shrink-0 text-status-error" aria-hidden="true" />
              {/* The reason, not a generic apology: an expired session needs a re-login, a 500 needs
                  a retry, and the user cannot tell which unless we say. */}
              <span>
                Could not load your history
                {list.error instanceof Error && list.error.message ? `: ${list.error.message}` : ''}
              </span>
            </span>
            <button
              type="button"
              onClick={() => void list.refetch()}
              disabled={list.isFetching}
              className="flex items-center gap-1 rounded border border-border-default/60 px-2 py-0.5 hover:bg-elevated focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60 disabled:opacity-50"
            >
              <RefreshCw size={11} className={list.isFetching ? 'animate-spin' : ''} aria-hidden="true" />
              Retry
            </button>
          </div>
        ) : sessions.length === 0 ? (
          <p className="p-3 text-xs text-text-muted">
            {query
              ? `No sessions match “${query}”.`
              : status === 'archived'
                ? 'Nothing archived yet.'
                : 'No sessions yet. Start one to analyse a symbol.'}
          </p>
        ) : (
          <>
            <ul aria-label="Session history">
              {sessions.map((session) => (
                <SessionHistoryRow
                  key={session.session_id}
                  session={session}
                  isActive={session.session_id === activeSessionId}
                  isBusy={busyId === session.session_id}
                  onOpen={onOpen}
                  onRename={handleRename}
                  onArchive={handleArchive}
                  onReopen={handleReopen}
                />
              ))}
            </ul>

            {/* The sentinel drives infinite scroll; the button is what makes the list usable when
                `IntersectionObserver` is missing, and gives keyboard users a way to page that does
                not depend on scrolling. */}
            <div ref={sentinelRef} className="h-1" aria-hidden="true" />
            {list.hasNextPage && (
              <div className="p-2">
                <button
                  type="button"
                  onClick={() => void list.fetchNextPage()}
                  disabled={list.isFetchingNextPage}
                  className="flex w-full items-center justify-center gap-1.5 rounded border border-border-default/60 py-1 text-xs text-text-secondary hover:bg-elevated hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60 disabled:opacity-50"
                >
                  {list.isFetchingNextPage && (
                    <Loader2 size={11} className="animate-spin" aria-hidden="true" />
                  )}
                  {list.isFetchingNextPage ? 'Loading…' : 'Load more'}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
