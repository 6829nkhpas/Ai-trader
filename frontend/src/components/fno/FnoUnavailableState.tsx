'use client';

/**
 * F&O Frontend Section (F4) — FnoUnavailableState (task 7.3).
 *
 * A presentational empty/error panel rendered whenever the F&O view-model
 * collapses to `kind: 'unavailable'` (an `Unavailable_Marker`, an empty chain,
 * a backend/transport error, or the market being closed with no snapshot).
 *
 * It carries the human-readable `reason` and, when a prior snapshot exists, the
 * formatted "last snapshot" timestamp — never a zero/placeholder presented as
 * if it were real option data.
 *
 * Scope: pure presentation. It computes no analytic and renders no fabricated
 * value. It matches the terminal's dark institutional theme, mirroring the
 * AgentTerminal "No reasoning was streamed" empty card.
 *
 * Requirements: 8.1 (explain unavailability), 8.2 (never show zero/placeholder
 * as real data), 8.4 (show the last snapshot timestamp when present), 6.4
 * (unavailable result → Unavailable_State), 6.5 (backend error → visible empty
 * state rather than a crash/freeze).
 */

import React from 'react';
import { CloudOff, Clock } from 'lucide-react';

export interface FnoUnavailableStateProps {
  /** Human-readable explanation of why F&O data is unavailable (R8.1, R6.4, R6.5). */
  reason: string;
  /**
   * Epoch-ms timestamp of the most recent available snapshot, when one exists
   * (R8.4). `null`/`undefined`/non-finite means no prior snapshot — nothing is
   * fabricated and no timestamp row is shown.
   */
  lastSnapshotTs?: number | null;
}

/**
 * Format an epoch-ms timestamp for the "last snapshot" label, or return `null`
 * when the value is absent/non-finite so the caller renders nothing rather than
 * a placeholder (R8.2, R8.4). Pure and total.
 */
function formatSnapshotTs(ts: number | null | undefined): string | null {
  if (typeof ts !== 'number' || !Number.isFinite(ts)) {
    return null;
  }

  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  // Locale-aware date + time so the trader can see how stale the snapshot is.
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/**
 * Explicit empty/unavailable panel for the F&O section. Renders the reason and,
 * when present, the formatted last-snapshot timestamp. Never displays a zero or
 * placeholder as though it were real option data (R8.2).
 */
export function FnoUnavailableState({ reason, lastSnapshotTs }: FnoUnavailableStateProps) {
  const formattedTs = formatSnapshotTs(lastSnapshotTs);
  const safeReason =
    typeof reason === 'string' && reason.trim().length > 0
      ? reason
      : 'F&O option data is currently unavailable.';

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex h-full w-full items-center justify-center p-6 select-text font-sans"
    >
      <div className="flex max-w-md items-start gap-3 p-3.5 bg-amber-500/5 border border-amber-500/25 rounded-none shadow-lg shadow-amber-955/20">
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-none bg-amber-500/20 text-amber-500 dark:text-amber-400 mt-0.5">
          <CloudOff size={13} />
        </div>

        <div className="flex flex-col">
          <span className="text-[11px] font-bold uppercase tracking-widest text-amber-500 dark:text-amber-400">
            F&amp;O Data Unavailable
          </span>

          <span className="text-[10px] text-amber-600 dark:text-amber-300/80 mt-1 leading-relaxed">
            No live option data is being shown. This usually means the market is
            closed, no chain snapshot exists for the selected underlying and
            expiry, or the backend returned an empty/error result. No values are
            fabricated in this state.
          </span>

          <span className="text-[9px] font-mono text-amber-500 dark:text-amber-400 bg-amber-500/5 rounded-none border border-amber-500/20 px-2 py-1 mt-2 leading-normal">
            {safeReason}
          </span>

          {formattedTs && (
            <span className="flex items-center gap-1.5 text-[9px] text-amber-600/90 dark:text-amber-300/70 mt-2 font-mono">
              <Clock size={10} className="text-amber-500 dark:text-amber-400" />
              <span className="uppercase tracking-wider font-semibold">Last snapshot</span>
              <span>{formattedTs}</span>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

export default FnoUnavailableState;
