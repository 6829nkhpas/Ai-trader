'use client';

// lib/fq/useActivateSession.ts
//
// Switching to a session. The single place that decides whether a switch is free or costs a fetch.
//
// Two very different operations wear the same name in the UI:
//
//   * switching to a session this client is already holding — a pointer move, no network at all,
//     which is what lets a background run keep streaming while the user looks elsewhere;
//   * opening one it has never seen (a reload, a deep link, a reopen from history) — which has to
//     rebuild the transcript from stored frames before there is anything to show.
//
// Conflating them is how you get either a blank panel on first open or a refetch of thousands of
// frames every time someone flips a tab.

import { useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { fqKeys } from './queries';
import { rehydrateSession, type RehydratedSession } from './rehydrate';
import { useSessionStore } from '../../store/useSessionStore';

export interface ActivateResult {
  ok: boolean;
  /** Set when the session could not be opened. `notFound` covers gone-or-not-ours. */
  error?: { notFound: boolean; unauthenticated: boolean; message: string };
}

/**
 * Whether this client already holds a usable copy of a session.
 *
 * Keyed on the stream record rather than on `sessions[id]`, because `setActiveSession` and
 * `upsertSession` both create a BLANK entry as a side effect — so the presence of a session object
 * proves nothing about whether it was ever loaded. `hydratedAt` is set only by a completed
 * rehydration.
 */
function isHydrated(sessionId: string): boolean {
  return !!useSessionStore.getState().streams[sessionId]?.hydratedAt;
}

export function useActivateSession() {
  const client = useQueryClient();

  return useCallback(
    async (sessionId: string): Promise<ActivateResult> => {
      if (!sessionId) return { ok: false, error: { notFound: true, unauthenticated: false, message: 'No session id.' } };

      const store = useSessionStore.getState();

      // Switch FIRST, so the tab responds to the click even when a fetch follows. The alternative —
      // awaiting the rehydration before moving — makes every first open feel broken.
      store.setActiveSession(sessionId);

      if (isHydrated(sessionId)) return { ok: true };

      try {
        // Through the query cache, not a bare call: a double-click, or a tab bar and a history row
        // racing each other, would otherwise fire two full rehydrations. `fetchQuery` dedupes them
        // and serves the second from cache.
        const result = await client.fetchQuery<RehydratedSession>({
          queryKey: fqKeys.rehydrate(sessionId),
          queryFn: () => rehydrateSession(sessionId),
          // The STORE is the live copy once this lands — the stream keeps mutating it. Refetching
          // would overwrite frames that arrived after the snapshot.
          staleTime: Infinity,
        });

        // Re-read: an await happened, and the user may have switched away or a frame may have
        // arrived in the meantime.
        const after = useSessionStore.getState();
        if (after.streams[sessionId]?.hydratedAt) return { ok: true };

        after.replaceSession(sessionId, result.session);
        after.markHydrated(sessionId, result.lastSeq);
        if (result.activeRun?.thread_id) {
          // Binds routing for a run that is still live, so its frames land here rather than in the
          // unroutable counter.
          after.bindThread(result.activeRun.thread_id, sessionId, result.activeRun.run_id);
        }
        return { ok: true };
      } catch (err) {
        const status = (err as { status?: number } | null)?.status;
        // 404 and 401 are different answers and need different UI: one offers a new session, the
        // other re-checks auth. Collapsing them makes an expired login look like deleted work.
        return {
          ok: false,
          error: {
            notFound: status === 404,
            unauthenticated: status === 401,
            message: err instanceof Error && err.message ? err.message : 'Could not open this session.',
          },
        };
      }
    },
    [client],
  );
}
