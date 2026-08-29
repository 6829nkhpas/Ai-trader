'use client';

/**
 * Route-level error boundary.
 *
 * Before this existed, ANY render-time throw anywhere in the terminal tree
 * unmounted the whole app and Next.js showed its bare
 * "Application error: a client-side exception has occurred" screen — the exact
 * symptom reported for the Radar (a malformed scan payload dereferenced during
 * render) and for the order book (a stale/foreign-shaped cache entry read back
 * from localStorage). The throw is still a bug worth fixing at the source, but
 * one bad payload must not take the terminal down with it.
 *
 * `reset()` re-renders the segment, which is enough to recover once the
 * offending state has been cleared.
 */

import { useEffect } from 'react';
import { AlertTriangle, RefreshCw, RotateCcw } from 'lucide-react';

export default function TerminalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Keep the real stack in the console — the panel below only shows the
    // message, and a digest alone is not diagnosable.
    console.error('[Terminal] Unhandled render error:', error);
  }, [error]);

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="flex h-screen w-screen flex-col items-center justify-center gap-4 bg-background px-6 text-center"
    >
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-amber-500/15 text-amber-500">
        <AlertTriangle size={18} />
      </div>

      <div className="flex max-w-md flex-col gap-2">
        <h2 className="text-base font-black tracking-tight text-text-primary">
          Something in the terminal stopped responding
        </h2>
        <p className="text-xs leading-relaxed text-text-secondary">
          A panel hit an unexpected error. The rest of your session is intact —
          retrying reloads just this view.
        </p>
        {error.message && (
          <p className="mt-1 break-words rounded border border-border-default bg-card px-2 py-1.5 font-mono text-[10px] leading-normal text-text-muted">
            {error.message}
          </p>
        )}
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={reset}
          className="flex items-center justify-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-xs font-extrabold uppercase tracking-wider text-black transition-colors hover:bg-primary-hover"
        >
          <RotateCcw size={13} />
          Retry
        </button>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="flex items-center justify-center gap-2 rounded-lg border border-border-default bg-card px-5 py-2.5 text-xs font-extrabold uppercase tracking-wider text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
        >
          <RefreshCw size={13} />
          Reload
        </button>
      </div>
    </div>
  );
}
