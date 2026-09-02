'use client';

// components/quant/session/NewSessionButton.tsx
//
// Creates a session on the SERVER and activates it.
//
// There is deliberately no local-first path. A client-minted session would have no `session_id` to
// own runs, no row to persist messages against, and no way to survive a reload — it would look like
// a session until the first thing the user did with it failed. So a failed `POST /sessions` creates
// nothing at all and says why.

import React from 'react';
import { AlertTriangle, Loader2, Plus } from 'lucide-react';

import { useCreateSession } from '../../../lib/fq/queries';
import { useTradeStore } from '../../../store/useTradeStore';

export interface NewSessionButtonProps {
  /** Called with the server-minted id once the session exists. */
  onCreated?: (sessionId: string) => void;
}

export default function NewSessionButton({ onCreated }: NewSessionButtonProps) {
  const create = useCreateSession();
  // The seed is the CURRENT trading context, so the new session analyses what the user is looking
  // at. Read at click time rather than subscribed, so changing symbol does not re-render the bar.
  const [failure, setFailure] = React.useState<string | null>(null);

  const handleClick = async () => {
    setFailure(null);
    const { selectedSymbol, activeTimeframe, activeProfile } = useTradeStore.getState();
    const symbol = (selectedSymbol || '').trim();
    if (!symbol) {
      // Validated before the request: the server would reject an empty symbol with a 422, and
      // "unprocessable entity" is not something to show a trader.
      setFailure('Pick a symbol first.');
      return;
    }
    try {
      const created = await create.mutateAsync({
        symbol,
        timeframe: activeTimeframe,
        profile: activeProfile,
      });
      onCreated?.(created.session_id);
    } catch (err) {
      setFailure(err instanceof Error && err.message ? err.message : 'Could not start a session.');
    }
  };

  return (
    <div className="relative flex items-center">
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={create.isPending}
        aria-label="New analysis session"
        title="New analysis session"
        className="flex h-7 w-7 items-center justify-center rounded text-text-secondary hover:bg-elevated hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60 disabled:opacity-50"
      >
        {create.isPending ? (
          <Loader2 size={14} className="animate-spin" aria-hidden="true" />
        ) : (
          <Plus size={14} aria-hidden="true" />
        )}
      </button>

      {failure && (
        <div
          role="alert"
          className="absolute right-0 top-full z-30 mt-1 flex w-64 items-start gap-1.5 rounded-md border border-border-default/60 bg-elevated p-2 text-xs text-text-secondary shadow-lg"
        >
          <AlertTriangle size={12} className="mt-0.5 shrink-0 text-status-error" aria-hidden="true" />
          <span className="min-w-0 flex-1">{failure}</span>
          <button
            type="button"
            onClick={() => setFailure(null)}
            aria-label="Dismiss"
            className="shrink-0 text-text-muted hover:text-text-primary focus:outline-none focus-visible:ring-2 focus-visible:ring-text-primary/60"
          >
            ×
          </button>
        </div>
      )}
    </div>
  );
}
