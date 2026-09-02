// lib/fq/queries.ts — the server-state cache for the Find Quant session surface.
//
// Why TanStack Query, and why only here
// -------------------------------------
// This project had no server-state cache: `hooks/useApi.ts` is a per-hook
// `useState` + `useEffect` fetch with no shared cache, no request dedup and no
// invalidation. That is adequate for three read-once endpoints (credit, profile,
// billing) and inadequate for this surface, which needs a session list that several
// components read at once, cursor pagination, optimistic rename with rollback, and
// invalidation after archive.
//
// Hand-rolling those four would be MORE code than the dependency and would be the
// "another uncontrolled custom caching system" the migration plan explicitly forbids.
//
// Scope discipline, deliberately narrow:
//   * one exact-pinned dependency;
//   * query keys only under `['fq', …]`, so nothing else in the app can collide;
//   * the existing `useApi` hooks are NOT migrated. Rewriting working code to adopt a
//     new library is churn, and it would put every unrelated screen in the blast radius
//     of this migration.

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from '@tanstack/react-query';

import {
  archiveSession,
  createSession,
  deleteSession,
  getSession,
  listMessages,
  listRunEvents,
  listRuns,
  listSessions,
  patchSession,
  reopenSession,
  type CreateSessionInput,
  type SessionSummary,
} from './api';

/**
 * Query keys, namespaced under `fq`.
 *
 * Functions rather than literals so a key is written once and every invalidation targets
 * the same array. A hand-built key that differs by one element silently fails to
 * invalidate, which presents as a stale list nobody can reproduce.
 */
export const fqKeys = {
  all: ['fq'] as const,
  sessions: () => ['fq', 'sessions'] as const,
  sessionList: (params: { status?: string; q?: string }) =>
    ['fq', 'sessions', 'list', params.status ?? 'active', params.q ?? ''] as const,
  session: (sessionId: string) => ['fq', 'sessions', sessionId] as const,
  messages: (sessionId: string) => ['fq', 'sessions', sessionId, 'messages'] as const,
  runs: (sessionId: string) => ['fq', 'sessions', sessionId, 'runs'] as const,
  runEvents: (runId: string) => ['fq', 'runs', runId, 'events'] as const,
  /**
   * The composite "rebuild this session's client state" fetch.
   *
   * Distinct from `session(id)` because it is a different shape and a different lifetime: the summary
   * is refetched routinely, while a rehydration is fetched once and then superseded by the store,
   * which the live stream keeps mutating.
   */
  rehydrate: (sessionId: string) => ['fq', 'sessions', sessionId, 'rehydrate'] as const,
};

/**
 * How long a session list is considered fresh.
 *
 * Short, because `updated_at` moves on every streamed flush and the list is ordered by it —
 * a long stale time would show a tab bar whose order lags what the user is watching. Not
 * zero, because several components read this list and refetching per mount would put the
 * request storm back that the cache exists to remove.
 */
const LIST_STALE_MS = 5_000;

/**
 * A finished run's transcript never changes, so it is cached indefinitely.
 *
 * This is what makes flipping back to an already-opened tab instant instead of a refetch of
 * potentially thousands of frames.
 */
const IMMUTABLE_STALE_MS = Infinity;

// ── Reads ─────────────────────────────────────────────────────────────────────

export function useSessions(params: { status?: 'active' | 'archived'; q?: string } = {}) {
  return useInfiniteQuery({
    queryKey: fqKeys.sessionList(params),
    queryFn: ({ pageParam }) =>
      listSessions({ status: params.status ?? 'active', q: params.q, cursor: pageParam, limit: 25 }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    staleTime: LIST_STALE_MS,
  });
}

export function useSession(sessionId: string | null) {
  return useQuery({
    queryKey: fqKeys.session(sessionId ?? ''),
    queryFn: () => getSession(sessionId as string),
    enabled: !!sessionId,
    staleTime: LIST_STALE_MS,
    // A 404 means the session is gone; retrying cannot change that and only delays the
    // not-found state the user needs to see.
    retry: (count, error) => !isTerminalError(error) && count < 2,
  });
}

export function useMessages(sessionId: string | null) {
  return useQuery({
    queryKey: fqKeys.messages(sessionId ?? ''),
    queryFn: () => listMessages(sessionId as string, { limit: 500 }),
    enabled: !!sessionId,
    retry: (count, error) => !isTerminalError(error) && count < 2,
  });
}

export function useRuns(sessionId: string | null) {
  return useQuery({
    queryKey: fqKeys.runs(sessionId ?? ''),
    queryFn: () => listRuns(sessionId as string),
    enabled: !!sessionId,
    retry: (count, error) => !isTerminalError(error) && count < 2,
  });
}

export function useRunEvents(runId: string | null, opts: { immutable?: boolean } = {}) {
  return useQuery({
    queryKey: fqKeys.runEvents(runId ?? ''),
    queryFn: () => listRunEvents(runId as string, { limit: 4000 }),
    enabled: !!runId,
    // A finished run's frames are immutable; a live run's are not.
    staleTime: opts.immutable ? IMMUTABLE_STALE_MS : 0,
    retry: (count, error) => !isTerminalError(error) && count < 2,
  });
}

/**
 * Whether an error is worth retrying.
 *
 * 401 and 404 are answers, not failures: retrying a 404 delays the not-found state, and
 * retrying a 401 delays the auth re-check. Anything else (including a transport failure,
 * which `FqApiError` reports as status 0) may be transient.
 */
function isTerminalError(error: unknown): boolean {
  const status = (error as { status?: number } | null)?.status;
  return status === 401 || status === 404;
}

// ── Mutations ─────────────────────────────────────────────────────────────────

export function useCreateSession() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateSessionInput) => createSession(input),
    onSuccess: (created) => {
      // Seeded so the workspace can render the new tab before its detail query resolves.
      client.setQueryData(fqKeys.session(created.session_id), created);
      void client.invalidateQueries({ queryKey: fqKeys.sessions() });
    },
  });
}

/**
 * Rename a session, optimistically.
 *
 * A rename is a direct manipulation of a label the user is looking at, so waiting for a
 * round trip reads as lag. Rolled back on failure from the snapshot rather than by
 * refetching, because a refetch during a streaming run could arrive with a newer
 * `updated_at` and reorder the tab bar as a side effect of a failed rename.
 */
export function useRenameSession() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, title }: { sessionId: string; title: string | null }) =>
      patchSession(sessionId, { title }),
    onMutate: async ({ sessionId, title }) => {
      await client.cancelQueries({ queryKey: fqKeys.session(sessionId) });
      const previous = client.getQueryData<SessionSummary>(fqKeys.session(sessionId));
      if (previous) {
        client.setQueryData<SessionSummary>(fqKeys.session(sessionId), { ...previous, title });
      }
      return { previous };
    },
    onError: (_err, { sessionId }, context) => {
      if (context?.previous) {
        client.setQueryData(fqKeys.session(sessionId), context.previous);
      }
    },
    onSettled: (_data, _err, { sessionId }) => {
      void client.invalidateQueries({ queryKey: fqKeys.session(sessionId) });
      void client.invalidateQueries({ queryKey: fqKeys.sessions() });
    },
  });
}

export function useSetSessionTimeframe() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, timeframe }: { sessionId: string; timeframe: string }) =>
      patchSession(sessionId, { timeframe }),
    onSuccess: (updated) => {
      client.setQueryData(fqKeys.session(updated.session_id), updated);
      void client.invalidateQueries({ queryKey: fqKeys.sessions() });
    },
  });
}

/**
 * Archive a session (soft close).
 *
 * Not optimistic. Archiving removes the tab, and a removal that has to be undone is far
 * more jarring than a brief wait — an optimistically-vanished tab reappearing looks like the
 * app losing track of the user's work.
 */
export function useArchiveSession() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => archiveSession(sessionId),
    onSuccess: (updated) => {
      client.setQueryData(fqKeys.session(updated.session_id), updated);
      void client.invalidateQueries({ queryKey: fqKeys.sessions() });
    },
  });
}

export function useReopenSession() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) => reopenSession(sessionId),
    onSuccess: (updated) => {
      client.setQueryData(fqKeys.session(updated.session_id), updated);
      void client.invalidateQueries({ queryKey: fqKeys.sessions() });
    },
  });
}

export function useDeleteSession() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ sessionId, hard }: { sessionId: string; hard?: boolean }) =>
      deleteSession(sessionId, { hard }),
    onSuccess: (_result, { sessionId }) => {
      // Removed rather than invalidated: a deleted session's detail query would refetch and
      // 404, surfacing an error for something the user asked to be gone.
      client.removeQueries({ queryKey: fqKeys.session(sessionId) });
      client.removeQueries({ queryKey: fqKeys.messages(sessionId) });
      client.removeQueries({ queryKey: fqKeys.runs(sessionId) });
      void client.invalidateQueries({ queryKey: fqKeys.sessions() });
    },
  });
}

// ── Invalidation from outside React ───────────────────────────────────────────

/**
 * Mark a session's server state stale after a stream event.
 *
 * Called from the streaming layer, which is not a component and has no hooks. Deliberately
 * NOT called per frame — that would invalidate hundreds of times per run. The caller
 * invalidates on run start and on the terminal frame, which is when the stored rows the
 * cache holds have actually changed.
 */
export function invalidateSessionState(client: QueryClient, sessionId: string): void {
  void client.invalidateQueries({ queryKey: fqKeys.session(sessionId) });
  void client.invalidateQueries({ queryKey: fqKeys.messages(sessionId) });
  void client.invalidateQueries({ queryKey: fqKeys.runs(sessionId) });
  void client.invalidateQueries({ queryKey: fqKeys.sessions() });
}
