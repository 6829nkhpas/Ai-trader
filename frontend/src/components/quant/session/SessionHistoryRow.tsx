'use client';

// components/quant/session/SessionHistoryRow.tsx
//
// One row of session history. Presentational plus its own inline-rename state; every mutation is
// handed upward so the list owns the server interaction and this stays testable as markup.

import React from 'react';
import { Archive, Check, Loader2, Pencil, RotateCcw, X } from 'lucide-react';

import type { SessionSummary } from '../../../lib/fq/api';
import { formatSessionDay, formatSessionTime, sessionTabLabel } from './sessionLabel';

export interface SessionHistoryRowProps {
  session: SessionSummary;
  isActive: boolean;
  /** A mutation for this row is in flight. */
  isBusy?: boolean;
  onOpen: (sessionId: string) => void;
  onRename: (sessionId: string, title: string | null) => void;
  onArchive: (sessionId: string) => void;
  onReopen: (sessionId: string) => void;
}

/**
 * How long ago, in words.
 *
 * Relative for anything recent because "3m ago" is what a trader is actually asking, and absolute
 * past a day because "412h ago" is not readable. Deliberately coarse — a history list that reflows
 * every second is a distraction, and nothing here needs second precision.
 */
export function relativeUpdated(epochSeconds: number, now: number = Date.now()): string {
  if (!Number.isFinite(epochSeconds) || epochSeconds <= 0) return '';
  const seconds = Math.floor((now - epochSeconds * 1000) / 1000);
  // A clock skew between server and browser can make a fresh row look like it is from the future.
  // "just now" is honest and does not print a negative.
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return `${formatSessionDay(epochSeconds)} ${formatSessionTime(epochSeconds)}`;
}

/** What the last run concluded, for the one-glance "was this useful" read. */
function lastRunSummary(session: SessionSummary): string | null {
  const run = session.last_run;
  if (!run) return null;
  const kind = run.kind === 'verify' ? 'VERIFY' : 'FIND';
  // The run's own status, not a guess from `ended_at`: `watching` is a live, non-terminal state that
  // a null `ended_at` would otherwise render as "still running" forever.
  return `${kind} · ${run.status}`;
}

export default function SessionHistoryRow({
  session,
  isActive,
  isBusy = false,
  onOpen,
  onRename,
  onArchive,
  onReopen,
}: SessionHistoryRowProps) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState('');
  const inputRef = React.useRef<HTMLInputElement>(null);

  const label = sessionTabLabel(session);
  const archived = session.status === 'archived';

  const beginEdit = () => {
    // Seeded with the CURRENT title, or empty when the label is derived — pre-filling the derived
    // label would turn "rename" into "accept this generated name", and the user would end up with a
    // pinned title they never chose.
    setDraft(session.title ?? '');
    setEditing(true);
  };

  React.useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const commit = () => {
    const next = draft.trim();
    setEditing(false);
    // Empty clears the title, which restores the derived label. That is the only way back once a
    // session has been named.
    const title = next.length > 0 ? next : null;
    if (title === (session.title ?? null)) return;
    onRename(session.session_id, title);
  };

  return (
    <li
      className={`group flex items-center gap-2 border-b border-border-default/30 px-3 py-2 text-xs last:border-b-0 ${
        isActive ? 'bg-elevated' : 'hover:bg-elevated/40'
      }`}
      data-session-id={session.session_id}
    >
      <div className="min-w-0 flex-1">
        {editing ? (
          <div className="flex items-center gap-1">
            <input
              ref={inputRef}
              value={draft}
              aria-label={`Rename ${label}`}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commit();
                // Escape must abandon the edit without saving — otherwise the only way out of a
                // half-typed rename is to save it.
                if (e.key === 'Escape') setEditing(false);
              }}
              className="w-full rounded border border-border-default/60 bg-surface px-1.5 py-0.5 text-xs text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
            />
            <button
              type="button"
              aria-label="Save name"
              onClick={commit}
              className="shrink-0 rounded p-1 text-text-secondary hover:bg-surface hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
            >
              <Check size={12} aria-hidden="true" />
            </button>
            <button
              type="button"
              aria-label="Cancel rename"
              onClick={() => setEditing(false)}
              className="shrink-0 rounded p-1 text-text-secondary hover:bg-surface hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
            >
              <X size={12} aria-hidden="true" />
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => onOpen(session.session_id)}
            // Named explicitly. Derived from content the name would be the label plus every metadata
            // fragment beneath it — "RELIANCE · 10m · 9:15 AM RELIANCE · 10m just now FIND · complete"
            // — which says nothing about what activating it does.
            aria-label={`Open ${label}`}
            className="block w-full truncate text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
          >
            <span className={`truncate font-medium ${isActive ? 'text-text-primary' : 'text-text-secondary'}`}>
              {label}
            </span>
            <span className="mt-0.5 flex items-center gap-1.5 text-[11px] text-text-muted">
              {/* The symbol and timeframe are repeated here on purpose: once a session is renamed the
                  label no longer carries them, and they are the one thing a user cannot re-derive
                  from a custom title. */}
              <span className="truncate">
                {session.symbol} · {session.timeframe}
              </span>
              <span aria-hidden="true">·</span>
              <span className="shrink-0">{relativeUpdated(session.updated_at)}</span>
              {lastRunSummary(session) && (
                <>
                  <span aria-hidden="true">·</span>
                  <span className="shrink-0 uppercase">{lastRunSummary(session)}</span>
                </>
              )}
              {archived && (
                <>
                  <span aria-hidden="true">·</span>
                  <span className="shrink-0">archived</span>
                </>
              )}
            </span>
          </button>
        )}
      </div>

      {!editing && (
        <div className="flex shrink-0 items-center gap-0.5">
          {isBusy && <Loader2 size={12} className="animate-spin text-text-muted" aria-hidden="true" />}
          <button
            type="button"
            aria-label={`Rename ${label}`}
            title="Rename"
            onClick={beginEdit}
            className="rounded p-1 text-text-muted opacity-0 transition-opacity hover:bg-surface hover:text-text-primary focus:outline-none focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-text-primary/60 group-hover:opacity-100"
          >
            <Pencil size={12} aria-hidden="true" />
          </button>
          {archived ? (
            <button
              type="button"
              aria-label={`Reopen ${label}`}
              title="Reopen"
              onClick={() => onReopen(session.session_id)}
              className="rounded p-1 text-text-muted opacity-0 transition-opacity hover:bg-surface hover:text-text-primary focus:outline-none focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-text-primary/60 group-hover:opacity-100"
            >
              <RotateCcw size={12} aria-hidden="true" />
            </button>
          ) : (
            <button
              type="button"
              aria-label={`Archive ${label}`}
              title="Archive"
              onClick={() => onArchive(session.session_id)}
              className="rounded p-1 text-text-muted opacity-0 transition-opacity hover:bg-surface hover:text-text-primary focus:outline-none focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-text-primary/60 group-hover:opacity-100"
            >
              <Archive size={12} aria-hidden="true" />
            </button>
          )}
        </div>
      )}
    </li>
  );
}
