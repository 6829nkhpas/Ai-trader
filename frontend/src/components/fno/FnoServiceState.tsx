'use client';

/**
 * F&O Frontend Section (F4) — FnoServiceState (bugfix: Defect A2 render).
 *
 * A presentational, ACTIONABLE error panel rendered whenever the F&O bridge
 * invoke (`get_fno_analytics`) is REJECTED with a transport `Err` — i.e. the
 * F&O service is unreachable or `DEEP_QUANT_URL` is misconfigured.
 *
 * NOTE on the env var name: this panel used to tell the user to check
 * `FNO_SERVICE_URL`, which does not exist anywhere in the codebase outside
 * comments. The F&O snapshot is served by the deep-quant service and the upstream
 * is resolved from `DEEP_QUANT_URL` (`app/api/_gateway.ts`), so following the old
 * instruction could not possibly fix the problem.
 *
 * This state is DISTINCT from `FnoUnavailableState` (a resolved no-data marker,
 * an honest empty market): here the cause is a fixable setup/configuration
 * problem, so the copy tells the user how to act — verify the F&O service is
 * running and check the `FNO_SERVICE_URL` environment variable — instead of
 * implying the market is simply empty (R2.3).
 *
 * Scope: pure presentation. It computes no analytic and fabricates no value.
 * It surfaces the verbatim transport detail from the bridge (which already
 * names the offending URL) so the user can diagnose the connection.
 */

import React from 'react';
import { PlugZap, Terminal } from 'lucide-react';

export interface FnoServiceStateProps {
  /**
   * The verbatim transport error string from the bridge, e.g.
   * "F&O service unreachable at http://localhost:8086/options/snapshot: ...".
   * Shown as a diagnostic detail line. When empty, a generic line is used.
   */
  detail?: string;
}

/**
 * Distinct, actionable service/configuration error panel for the F&O section.
 * Always surfaces the `DEEP_QUANT_URL` env var and a configuration framing so
 * the user can tell a setup problem apart from an empty market.
 */
export function FnoServiceState({ detail }: FnoServiceStateProps) {
  const safeDetail =
    typeof detail === 'string' && detail.trim().length > 0
      ? detail
      : 'The F&O service did not respond.';

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="flex h-full w-full items-center justify-center p-6 select-text font-sans"
    >
      <div className="flex max-w-md items-start gap-3 p-3.5 bg-rose-500/5 border border-rose-500/25 rounded-none shadow-lg shadow-rose-950/20">
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-none bg-rose-500/20 text-rose-500 dark:text-rose-400 mt-0.5">
          <PlugZap size={13} />
        </div>

        <div className="flex flex-col">
          <span className="text-[11px] font-bold uppercase tracking-widest text-rose-500 dark:text-rose-400">
            F&amp;O Service Unreachable
          </span>

          <span className="text-[10px] text-rose-600 dark:text-rose-300/80 mt-1 leading-relaxed">
            This is a service/configuration problem, not an empty market. The
            F&amp;O analytics service could not be reached. Verify the{' '}
            <code className="font-mono">deep-quant</code> service is running and that{' '}
            <code className="font-mono">DEEP_QUANT_URL</code> points at the correct
            host and port, then retry.
          </span>

          <span className="flex items-center gap-1.5 text-[9px] font-mono text-rose-500 dark:text-rose-400 bg-rose-500/5 rounded-none border border-rose-500/20 px-2 py-1 mt-2 leading-normal">
            <Terminal size={10} className="shrink-0 text-rose-500 dark:text-rose-400" />
            <span className="break-all">{safeDetail}</span>
          </span>
        </div>
      </div>
    </div>
  );
}

export default FnoServiceState;
