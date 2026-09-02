'use client';

// components/quant/session/SessionTabBarConnected.tsx
//
// The tab bar plus history, wired to the real activation path.
//
// This exists because `useActivateSession` needs the query client, so it cannot be called from
// `DeepQuantPanel` — that component renders the provider, and a hook cannot consume a context its own
// component supplies. Keeping the wiring here also keeps `SessionTabBar` and `SessionHistory`
// dependency-free and testable in isolation.

import React from 'react';
import { AlertTriangle, History } from 'lucide-react';

import { useActivateSession } from '../../../lib/fq/useActivateSession';
import SessionHistory from './SessionHistory';
import SessionTabBar from './SessionTabBar';

export default function SessionTabBarConnected() {
  const activate = useActivateSession();
  const [historyOpen, setHistoryOpen] = React.useState(false);
  const [openError, setOpenError] = React.useState<string | null>(null);

  const open = React.useCallback(
    async (sessionId: string) => {
      setOpenError(null);
      const result = await activate(sessionId);
      if (result.ok) {
        // Closed only on success, so a failed open leaves the list up for the user to pick something
        // else rather than dropping them on an empty workspace.
        setHistoryOpen(false);
        return;
      }
      // 404 and 401 are different answers needing different actions: one offers a new session, the
      // other means the login expired. Collapsing them makes an expired session look like deleted work.
      setOpenError(
        result.error?.notFound
          ? 'That session no longer exists. It may have been deleted.'
          : result.error?.unauthenticated
            ? 'Your session expired. Sign in again to open it.'
            : (result.error?.message ?? 'Could not open this session.'),
      );
    },
    [activate],
  );

  const onOpen = React.useCallback((sessionId: string) => void open(sessionId), [open]);

  return (
    <div className="relative shrink-0">
      <div className="flex items-stretch">
        <div className="min-w-0 flex-1">
          <SessionTabBar onActivate={onOpen} />
        </div>
        <button
          type="button"
          onClick={() => setHistoryOpen((v) => !v)}
          aria-expanded={historyOpen}
          aria-label="Session history"
          title="Session history"
          className="flex w-8 shrink-0 items-center justify-center border-b border-border-default/40 bg-surface text-text-secondary hover:bg-elevated hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
        >
          <History size={14} aria-hidden="true" />
        </button>
      </div>

      {openError && (
        <div
          role="alert"
          className="flex items-start gap-1.5 border-b border-border-default/40 bg-elevated p-2 text-xs text-text-secondary"
        >
          <AlertTriangle size={12} className="mt-0.5 shrink-0 text-status-error" aria-hidden="true" />
          <span className="min-w-0 flex-1">{openError}</span>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => setOpenError(null)}
            className="shrink-0 text-text-muted hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
          >
            ×
          </button>
        </div>
      )}

      {historyOpen && (
        // A panel rather than a modal: history is a navigation aid, and a modal would block the
        // transcript the user is comparing against. Height-capped so a long list scrolls internally
        // instead of pushing the workspace off screen.
        <div className="absolute inset-x-0 top-full z-20 max-h-80 overflow-hidden rounded-b-md border border-border-default/60 bg-surface shadow-lg">
          <SessionHistory onOpen={onOpen} />
        </div>
      )}
    </div>
  );
}
