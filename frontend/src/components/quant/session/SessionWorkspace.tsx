'use client';

// components/quant/session/SessionWorkspace.tsx
//
// The full-width session workspace behind `/find-trade/session/{sessionId}`.
//
// Composed from the SAME components the side panel uses — `AgentTerminal` for the glass box,
// `TradeQaPanel` for the composer. That is deliberate: the plan's requirement is that structured tool
// activity is not flattened to text here, and the surest way to honour it is to render the existing
// renderers rather than write a second set that will drift.
//
// Everything below the tab bar reads through the `useFq*` hooks, so this component never learns which
// state path is live.

import React from 'react';
import { notFound } from 'next/navigation';
import { AlertTriangle, History, Loader2, X } from 'lucide-react';

import { useSession } from '../../../lib/fq/queries';
import { useActivateSession } from '../../../lib/fq/useActivateSession';
import { useSessionStore } from '../../../store/useSessionStore';
import AgentTerminal from '../AgentTerminal';
import TradeQaPanel from '../TradeQaPanel';
import { useFqSessionStatus } from '../useFqSession';
import { useFqStreamListeners } from '../useFqStreamListeners';
import SessionHistory from './SessionHistory';
import SessionTabBar from './SessionTabBar';
import { sessionTabLabel } from './sessionLabel';

type OpenState = 'loading' | 'ready' | 'missing' | 'unauthenticated' | 'failed';

/** Human wording for the run state shown in the header. */
const STATUS_TEXT: Record<string, string> = {
  idle: 'Idle',
  running: 'Analysing…',
  watching: 'Watching for your price trigger',
  complete: 'Complete',
  error: 'Failed',
};

export default function SessionWorkspace({ sessionId }: { sessionId: string }) {
  // Subscribed here as well as in the side panel: this is a separate tree, and without it a run
  // streaming into this session would emit frames with nobody listening.
  useFqStreamListeners();

  const activate = useActivateSession();
  const [state, setState] = React.useState<OpenState>('loading');
  const [failure, setFailure] = React.useState('');
  const [historyOpen, setHistoryOpen] = React.useState(false);

  const activeSessionId = useSessionStore((s) => s.activeSessionId);
  const summaryQuery = useSession(activeSessionId);
  const sessionStatus = useFqSessionStatus();

  const open = React.useCallback(
    async (id: string) => {
      setState('loading');
      const result = await activate(id);
      if (result.ok) {
        setState('ready');
        setHistoryOpen(false);
        return;
      }
      if (result.error?.notFound) setState('missing');
      else if (result.error?.unauthenticated) setState('unauthenticated');
      else {
        setFailure(result.error?.message ?? 'Could not open this session.');
        setState('failed');
      }
    },
    [activate],
  );

  React.useEffect(() => {
    let cancelled = false;
    void (async () => {
      const result = await activate(sessionId);
      if (cancelled) return;
      if (result.ok) setState('ready');
      else if (result.error?.notFound) setState('missing');
      else if (result.error?.unauthenticated) setState('unauthenticated');
      else {
        setFailure(result.error?.message ?? 'Could not open this session.');
        setState('failed');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId, activate]);

  // Deliberately the same answer for "does not exist" and "is not yours".
  //
  // A 403 on someone else's session would confirm that the id is real, turning the route into an
  // enumeration oracle — which is why the API returns 404 for both (see the design record) and why the
  // UI must not undo that by distinguishing them.
  if (state === 'missing') notFound();

  if (state === 'unauthenticated') {
    return (
      <main className="flex h-dvh items-center justify-center bg-surface p-6">
        <div role="alert" className="max-w-sm text-center text-sm text-text-secondary">
          <AlertTriangle size={20} className="mx-auto mb-2 text-status-error" aria-hidden="true" />
          <p className="text-text-primary">Your session expired.</p>
          {/* A deep link is exactly when a cookie is most likely to have lapsed, so this is a normal
              path rather than an error state — and it must not read as "your work is gone". */}
          <p className="mt-1">Sign in again and this conversation will still be here.</p>
        </div>
      </main>
    );
  }

  const summary = summaryQuery.data;
  const title = summary ? sessionTabLabel(summary) : '';

  return (
    <main className="flex h-dvh min-h-0 flex-col bg-surface">
      <div className="relative shrink-0">
        <div className="flex items-stretch">
          <div className="min-w-0 flex-1">
            <SessionTabBar onActivate={(id) => void open(id)} />
          </div>
          <button
            type="button"
            onClick={() => setHistoryOpen((v) => !v)}
            aria-expanded={historyOpen}
            aria-label="Session history"
            className="flex w-9 shrink-0 items-center justify-center border-b border-border-default/40 bg-surface text-text-secondary hover:bg-elevated hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
          >
            {historyOpen ? <X size={14} aria-hidden="true" /> : <History size={14} aria-hidden="true" />}
          </button>
        </div>

        {historyOpen && (
          // Overlaid, NOT rendered in place of the conversation. Unmounting the transcript to show
          // history would discard the scroll position and — worse — the mounted subtree a live run is
          // streaming into, so reopening it would look like the run had restarted.
          <div className="absolute inset-x-0 top-full z-20 max-h-[60dvh] overflow-hidden border-b border-border-default/60 bg-surface shadow-lg">
            <SessionHistory onOpen={(id) => void open(id)} />
          </div>
        )}
      </div>

      <header className="flex shrink-0 items-center gap-2 border-b border-border-default/40 px-3 py-2">
        {summary ? (
          <>
            <h1 className="min-w-0 truncate text-sm font-medium text-text-primary">{title}</h1>
            <span className="shrink-0 text-[11px] uppercase tracking-wide text-text-muted">
              {summary.symbol} · {summary.timeframe} · {summary.profile}
            </span>
            <span className="ml-auto shrink-0 text-[11px] text-text-secondary" aria-live="polite">
              {sessionStatus === 'running' && (
                <Loader2 size={11} className="mr-1 inline animate-spin" aria-hidden="true" />
              )}
              {STATUS_TEXT[sessionStatus] ?? sessionStatus}
            </span>
          </>
        ) : (
          <span className="h-4 w-40 animate-pulse rounded bg-elevated" aria-hidden="true" />
        )}
      </header>

      {state === 'failed' && (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-1.5 border-b border-border-default/40 bg-elevated p-2 text-xs text-text-secondary"
        >
          <AlertTriangle size={12} className="mt-0.5 shrink-0 text-status-error" aria-hidden="true" />
          <span className="min-w-0 flex-1">{failure}</span>
          <button
            type="button"
            onClick={() => void open(sessionId)}
            className="shrink-0 rounded border border-border-default/60 px-2 py-0.5 hover:bg-surface focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
          >
            Retry
          </button>
        </div>
      )}

      {/* The conversation. `min-h-0` is load-bearing in a flex column: without it the scroll container
          grows to its content instead of scrolling, and the composer is pushed off screen. */}
      <div className="min-h-0 flex-1 overflow-hidden">
        {state === 'loading' ? (
          <div className="flex h-full items-center justify-center" role="status">
            <Loader2 size={18} className="animate-spin text-text-muted" aria-hidden="true" />
            <span className="sr-only">Opening session…</span>
          </div>
        ) : (
          <AgentTerminal />
        )}
      </div>

      {/* Pinned above the keyboard inset on mobile: `env(safe-area-inset-bottom)` keeps the composer
          reachable instead of hidden behind the on-screen keyboard. */}
      <div className="shrink-0" style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}>
        <TradeQaPanel />
      </div>
    </main>
  );
}
