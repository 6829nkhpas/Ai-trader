// @vitest-environment jsdom
//
// The suite defaults to `environment: 'node'` (vitest.config.ts), so any file that renders —
// even a hook, via `renderHook` — needs this docblock. Without it: `ReferenceError: document
// is not defined`. Same convention as the `AgentTerminal` tests.

// lib/fq/__tests__/queries.test.tsx
//
// The four behaviours that justified taking the dependency at all — a shared cache, cursor
// pagination, optimistic rename with rollback, and invalidation after a mutation. If these
// were not needed, `hooks/useApi.ts` would have been enough.
//
// Plus the two judgement calls that are easy to get wrong and expensive in production:
// a 404 must not be retried (it delays the not-found state the user needs), and a deleted
// session's queries must be REMOVED rather than invalidated (an invalidated one refetches
// and 404s, surfacing an error for something the user asked to be gone).

// Explicit React import: this suite has no JSX runtime configured in vitest, so the
// classic transform is in play and `React` must be in scope. Same as the AgentTerminal tests.
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FqApiError, type SessionSummary } from '../api';
import {
  fqKeys,
  invalidateSessionState,
  useArchiveSession,
  useCreateSession,
  useDeleteSession,
  useRenameSession,
  useSession,
  useSessions,
} from '../queries';

function summary(over: Partial<SessionSummary> = {}): SessionSummary {
  return {
    session_id: 'sess_1',
    title: null,
    symbol: 'RELIANCE',
    timeframe: '10m',
    profile: 'INTRADAY',
    status: 'active',
    created_at: 1,
    updated_at: 2,
    archived_at: null,
    active_run_id: null,
    message_count: 0,
    last_run: null,
    ...over,
  };
}

let client: QueryClient;
let fetchMock: ReturnType<typeof vi.fn>;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: {
      // Deterministic: retries would make a failure assertion depend on timing.
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: 0 },
    },
  });
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  client.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ── Keys ──────────────────────────────────────────────────────────────────────

describe('query keys', () => {
  it('are all namespaced under fq, so nothing else can collide', () => {
    for (const key of [
      fqKeys.sessions(),
      fqKeys.sessionList({}),
      fqKeys.session('s'),
      fqKeys.messages('s'),
      fqKeys.runs('s'),
      fqKeys.runEvents('r'),
    ]) {
      expect(key[0]).toBe('fq');
    }
  });

  it('separate lists by status and search, so one does not overwrite another', () => {
    expect(fqKeys.sessionList({ status: 'active' })).not.toEqual(
      fqKeys.sessionList({ status: 'archived' }),
    );
    expect(fqKeys.sessionList({ q: 'reliance' })).not.toEqual(fqKeys.sessionList({ q: 'tcs' }));
  });

  it('nest a session key under the sessions key so one invalidation reaches both', () => {
    // `invalidateQueries({queryKey: fqKeys.sessions()})` must also mark a detail stale.
    expect(fqKeys.session('sess_1').slice(0, 2)).toEqual([...fqKeys.sessions()]);
  });
});

// ── Reads ─────────────────────────────────────────────────────────────────────

describe('useSession', () => {
  it('fetches and caches', async () => {
    fetchMock.mockResolvedValue(json(summary()));
    const { result } = renderHook(() => useSession('sess_1'), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.session_id).toBe('sess_1');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does not fetch for a null id', () => {
    renderHook(() => useSession(null), { wrapper });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('two consumers share one request', async () => {
    // The reason for a shared cache: the tab bar, the header and the workspace all read the
    // same session.
    fetchMock.mockResolvedValue(json(summary()));
    const { result } = renderHook(
      () => ({ a: useSession('sess_1'), b: useSession('sess_1') }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.a.isSuccess).toBe(true));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does not retry a 404', async () => {
    // Retrying cannot change the answer and only delays the not-found state.
    client = new QueryClient({ defaultOptions: { queries: { staleTime: 0 } } });
    fetchMock.mockResolvedValue(json({ detail: 'session not found' }, 404));
    const { result } = renderHook(() => useSession('sess_gone'), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect((result.current.error as FqApiError).isNotFound).toBe(true);
  });

  it('surfaces a 401 distinctly from a 404', async () => {
    // The workspace shows not-found for one and re-checks auth for the other; collapsing
    // them would make an expired login look like a deleted conversation.
    fetchMock.mockResolvedValue(json({ detail: 'authentication required' }, 401));
    const { result } = renderHook(() => useSession('sess_1'), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    const error = result.current.error as FqApiError;
    expect(error.isUnauthenticated).toBe(true);
    expect(error.isNotFound).toBe(false);
  });

  it('reports a transport failure as status 0, not as a 4xx', async () => {
    // "Session not found" for an offline client would send the user to recreate it.
    //
    // The generous timeout is not padding: a transport failure is NOT terminal, so it IS
    // retried with backoff — which is the correct policy and the reason this needs longer
    // than a 404 does. The retry behaviour itself is asserted below.
    fetchMock.mockRejectedValue(new Error('ECONNREFUSED'));
    const { result } = renderHook(() => useSession('sess_1'), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true), { timeout: 5000 });
    const error = result.current.error as FqApiError;
    expect(error.status).toBe(0);
    expect(error.isNotFound).toBe(false);
  });

  it('retries a transport failure but not a 404', async () => {
    // The distinction that makes `isTerminalError` worth having: a 404 is an answer and
    // retrying it only delays the not-found state, while a dropped connection may well
    // succeed on the next attempt.
    fetchMock.mockRejectedValue(new Error('ECONNREFUSED'));
    const transient = renderHook(() => useSession('sess_flaky'), { wrapper });
    await waitFor(() => expect(transient.result.current.isError).toBe(true), { timeout: 5000 });
    const transportAttempts = fetchMock.mock.calls.length;
    expect(transportAttempts).toBeGreaterThan(1);

    fetchMock.mockClear();
    fetchMock.mockResolvedValue(json({ detail: 'session not found' }, 404));
    const gone = renderHook(() => useSession('sess_gone'), { wrapper });
    await waitFor(() => expect(gone.result.current.isError).toBe(true));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('useSessions pagination', () => {
  it('follows the cursor and accumulates pages without duplicates', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const hasCursor = String(url).includes('cursor=');
      return hasCursor
        ? json({ items: [summary({ session_id: 'sess_2' })], next_cursor: null })
        : json({ items: [summary({ session_id: 'sess_1' })], next_cursor: 'c1' });
    });

    const { result } = renderHook(() => useSessions(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.hasNextPage).toBe(true);

    await result.current.fetchNextPage();
    await waitFor(() => expect(result.current.data?.pages).toHaveLength(2));

    const ids = result.current.data!.pages.flatMap((p) => p.items.map((i) => i.session_id));
    expect(ids).toEqual(['sess_1', 'sess_2']);
    expect(new Set(ids).size).toBe(ids.length);
    expect(result.current.hasNextPage).toBe(false);
  });

  it('requests the status filter', async () => {
    fetchMock.mockResolvedValue(json({ items: [], next_cursor: null }));
    const { result } = renderHook(() => useSessions({ status: 'archived' }), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(String(fetchMock.mock.calls[0][0])).toContain('status=archived');
  });
});

// ── Mutations ─────────────────────────────────────────────────────────────────

describe('useCreateSession', () => {
  it('seeds the detail cache so the tab can render immediately', async () => {
    const created = summary({ session_id: 'sess_new' });
    fetchMock.mockResolvedValue(json(created, 201));
    const { result } = renderHook(() => useCreateSession(), { wrapper });

    await result.current.mutateAsync({ symbol: 'RELIANCE', profile: 'INTRADAY', timeframe: '10m' });

    // Without the seed the new tab would show a loading state for a session whose data the
    // client already has.
    expect(client.getQueryData(fqKeys.session('sess_new'))).toEqual(created);
  });

  it('sends no user_id — the caller cannot choose the owner', async () => {
    fetchMock.mockResolvedValue(json(summary(), 201));
    const { result } = renderHook(() => useCreateSession(), { wrapper });
    await result.current.mutateAsync({ symbol: 'RELIANCE', profile: 'INTRADAY', timeframe: '10m' });
    const body = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body));
    expect('user_id' in body).toBe(false);
  });

  it('does not retry a failed create', async () => {
    // A retried create is a duplicate session.
    fetchMock.mockResolvedValue(json({ detail: 'boom' }, 500));
    const { result } = renderHook(() => useCreateSession(), { wrapper });
    await expect(
      result.current.mutateAsync({ symbol: 'X', profile: 'INTRADAY', timeframe: '10m' }),
    ).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('useRenameSession', () => {
  it('applies the new title optimistically', async () => {
    client.setQueryData(fqKeys.session('sess_1'), summary({ title: 'Old' }));
    let release: (v: Response) => void = () => {};
    fetchMock.mockImplementation(() => new Promise<Response>((r) => { release = r; }));

    const { result } = renderHook(() => useRenameSession(), { wrapper });
    void result.current.mutate({ sessionId: 'sess_1', title: 'New' });

    // A rename is direct manipulation of a label the user is looking at, so a round trip
    // reads as lag.
    await waitFor(() =>
      expect(client.getQueryData<SessionSummary>(fqKeys.session('sess_1'))?.title).toBe('New'),
    );
    release(json(summary({ title: 'New' })));
  });

  it('rolls back from the snapshot on failure', async () => {
    // Restored from the snapshot rather than by refetching: a refetch during a streaming run
    // could arrive with a newer updated_at and reorder the tab bar as a side effect of a
    // FAILED rename.
    client.setQueryData(fqKeys.session('sess_1'), summary({ title: 'Old' }));
    fetchMock.mockResolvedValue(json({ detail: 'nope' }, 500));

    const { result } = renderHook(() => useRenameSession(), { wrapper });
    await expect(
      result.current.mutateAsync({ sessionId: 'sess_1', title: 'New' }),
    ).rejects.toThrow();

    await waitFor(() =>
      expect(client.getQueryData<SessionSummary>(fqKeys.session('sess_1'))?.title).toBe('Old'),
    );
  });

  it('can clear a title back to the derived label', async () => {
    client.setQueryData(fqKeys.session('sess_1'), summary({ title: 'Named' }));
    fetchMock.mockResolvedValue(json(summary({ title: null })));
    const { result } = renderHook(() => useRenameSession(), { wrapper });
    await result.current.mutateAsync({ sessionId: 'sess_1', title: null });
    const body = JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body));
    expect(body.title).toBeNull();
  });
});

describe('useArchiveSession', () => {
  it('is NOT optimistic', async () => {
    // Archiving removes a tab. A removal that has to be undone looks like the app losing
    // track of the user's work, which is worse than a brief wait.
    client.setQueryData(fqKeys.session('sess_1'), summary({ status: 'active' }));
    let release: (v: Response) => void = () => {};
    fetchMock.mockImplementation(() => new Promise<Response>((r) => { release = r; }));

    const { result } = renderHook(() => useArchiveSession(), { wrapper });
    void result.current.mutate('sess_1');
    await Promise.resolve();

    expect(client.getQueryData<SessionSummary>(fqKeys.session('sess_1'))?.status).toBe('active');
    release(json(summary({ status: 'archived', archived_at: 9 })));
    await waitFor(() =>
      expect(client.getQueryData<SessionSummary>(fqKeys.session('sess_1'))?.status).toBe('archived'),
    );
  });
});

describe('useDeleteSession', () => {
  it('REMOVES the cached queries rather than invalidating them', async () => {
    // An invalidated detail query refetches and 404s, surfacing an error for something the
    // user asked to be gone.
    client.setQueryData(fqKeys.session('sess_1'), summary());
    client.setQueryData(fqKeys.messages('sess_1'), { items: [], last_seq: 0 });
    client.setQueryData(fqKeys.runs('sess_1'), { items: [] });
    fetchMock.mockResolvedValue(json({ session_id: 'sess_1', status: 'deleted', hard: false }));

    const { result } = renderHook(() => useDeleteSession(), { wrapper });
    await result.current.mutateAsync({ sessionId: 'sess_1' });

    expect(client.getQueryData(fqKeys.session('sess_1'))).toBeUndefined();
    expect(client.getQueryData(fqKeys.messages('sess_1'))).toBeUndefined();
    expect(client.getQueryData(fqKeys.runs('sess_1'))).toBeUndefined();
  });

  it('passes hard=true through', async () => {
    fetchMock.mockResolvedValue(json({ session_id: 'sess_1', status: 'deleted', hard: true }));
    const { result } = renderHook(() => useDeleteSession(), { wrapper });
    await result.current.mutateAsync({ sessionId: 'sess_1', hard: true });
    expect(String(fetchMock.mock.calls[0][0])).toContain('hard=true');
  });
});

// ── Invalidation from outside React ───────────────────────────────────────────

describe('invalidateSessionState', () => {
  it('marks the session, its messages and its runs stale', () => {
    // Called from the streaming layer, which has no hooks. Deliberately NOT per frame —
    // that would invalidate hundreds of times per run.
    client.setQueryData(fqKeys.session('sess_1'), summary());
    client.setQueryData(fqKeys.messages('sess_1'), { items: [], last_seq: 0 });
    client.setQueryData(fqKeys.runs('sess_1'), { items: [] });

    invalidateSessionState(client, 'sess_1');

    for (const key of [fqKeys.session('sess_1'), fqKeys.messages('sess_1'), fqKeys.runs('sess_1')]) {
      expect(client.getQueryState(key)?.isInvalidated).toBe(true);
    }
  });
});
