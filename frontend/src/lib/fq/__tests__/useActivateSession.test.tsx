// @vitest-environment jsdom
//
// lib/fq/__tests__/useActivateSession.test.tsx
//
// Switching to a session is two different operations wearing one name:
//
//   * a session this client already holds — a pointer move, no network, which is what lets a
//     background run keep streaming while the user looks elsewhere;
//   * one it has never seen — a reload, a deep link, a reopen from history — which must rebuild the
//     transcript from stored frames before there is anything to show.
//
// Conflating them gives you either a blank panel on first open, or a refetch of thousands of frames
// every time someone flips a tab. These tests pin the distinction.

import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useActivateSession } from '../useActivateSession';
import { useSessionStore } from '../../../store/useSessionStore';

const SESSION = 'sess_AAAAAAAAAAAAAAAAAAAAAAAAAA';
const THREAD = 'thread_AAAAAAAAAAAAAAAAAAAAAAAA';
const RUN = 'run_AAAAAAAAAAAAAAAAAAAAAAAAAA';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const summary = {
  session_id: SESSION,
  title: null,
  symbol: 'RELIANCE',
  timeframe: '10m',
  profile: 'INTRADAY',
  status: 'active' as const,
  created_at: 1,
  updated_at: 2,
  archived_at: null,
  active_run_id: RUN,
  message_count: 2,
  last_run: null,
};

const run = {
  run_id: RUN,
  session_id: SESSION,
  thread_id: THREAD,
  kind: 'find' as const,
  status: 'watching',
  symbol: 'RELIANCE',
  timeframe: '10m',
  profile: 'INTRADAY',
  started_at: 1,
  ended_at: null,
  last_seq: 7,
};

/**
 * Answer the four requests `rehydrateSession` makes.
 *
 * Dispatched on the URL, deliberately: asserting on call ORDER would pin an implementation detail
 * (the summary is fetched first and alone; runs and messages go in parallel) rather than the contract.
 */
function serveRehydration(over: { events?: unknown[]; last_seq?: number } = {}) {
  fetchMock.mockImplementation((url: string) => {
    const u = String(url);
    if (u.includes('/events')) {
      return Promise.resolve(
        json({ items: over.events ?? [], last_seq: over.last_seq ?? run.last_seq }),
      );
    }
    if (u.includes('/runs')) return Promise.resolve(json({ items: [run] }));
    if (u.includes('/messages')) return Promise.resolve(json({ items: [], last_seq: 0 }));
    return Promise.resolve(json(summary));
  });
}

let client: QueryClient;
let fetchMock: ReturnType<typeof vi.fn>;

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/** How many times the composite rehydration ran, counted by its first request. */
function summaryFetches(): number {
  return fetchMock.mock.calls.filter(([url]) => {
    const u = String(url);
    return u.includes(`/sessions/${SESSION}`) && !u.includes('/runs') && !u.includes('/messages');
  }).length;
}

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } },
  });
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  useSessionStore.getState().reset();
});

afterEach(() => {
  client.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('opening a session the client has never seen', () => {
  it('rebuilds its state from the server', async () => {
    serveRehydration({
      events: [
        { seq: 1, event: 'RUN_STARTED', data: { thread_id: THREAD } },
        { seq: 2, event: 'REASONING', data: { thread_id: THREAD, content: 'stored reasoning' } },
      ],
    });
    const { result } = renderHook(() => useActivateSession(), { wrapper });

    await result.current(SESSION);

    const session = useSessionStore.getState().sessions[SESSION];
    // Rebuilt by replaying stored frames through the SAME reducer the live stream uses, which is what
    // makes a restored transcript identical to the one that was streamed.
    expect(
      session.reasoningSteps.filter((s) => s.type === 'message').map((s) => s.content).join(''),
    ).toBe('stored reasoning');
  });

  it('switches the workspace immediately, before the fetch resolves', async () => {
    // Only the FIRST request is held. `rehydrateSession` makes four (summary, runs, messages, events),
    // so hanging all of them would just deadlock the test rather than testing anything.
    serveRehydration();
    let release: (r: Response) => void = () => {};
    fetchMock.mockImplementationOnce(() => new Promise<Response>((res) => (release = res)));
    const { result } = renderHook(() => useActivateSession(), { wrapper });

    const pending = result.current(SESSION);

    // Awaiting the rehydration before moving makes every first open feel broken — the tab does not
    // respond to the click until the network does.
    expect(useSessionStore.getState().activeSessionId).toBe(SESSION);
    // Not hydrated yet, so the workspace can show a loading state rather than an empty transcript that
    // reads as a finished conversation with nothing in it.
    expect(useSessionStore.getState().streams[SESSION]?.hydratedAt ?? null).toBeNull();

    release(json(summary));
    expect((await pending).ok).toBe(true);
    expect(useSessionStore.getState().streams[SESSION].hydratedAt).toBeTruthy();
  });

  it('binds the live run so its frames are routable', async () => {
    serveRehydration();
    const { result } = renderHook(() => useActivateSession(), { wrapper });

    await result.current(SESSION);

    // Without this, frames from a run that is still watching arrive with a thread nobody has claimed
    // and go straight to the unroutable counter.
    expect(useSessionStore.getState().threadToSession[THREAD]).toBe(SESSION);
    expect(useSessionStore.getState().streams[SESSION].runId).toBe(RUN);
  });

  it("records the run's high-water mark, not the page's", async () => {
    // A transcript longer than the replay cap must still reattach at the true `last_seq`, or the gap
    // request would re-deliver frames that were merely not loaded.
    serveRehydration({
      events: [{ seq: 3, event: 'REASONING', data: { thread_id: THREAD, content: 'x' } }],
      last_seq: 90,
    });
    const { result } = renderHook(() => useActivateSession(), { wrapper });

    await result.current(SESSION);

    expect(useSessionStore.getState().streams[SESSION].lastSeq).toBe(90);
  });

  it('marks the session hydrated', async () => {
    serveRehydration();
    const { result } = renderHook(() => useActivateSession(), { wrapper });

    await result.current(SESSION);

    expect(useSessionStore.getState().streams[SESSION].hydratedAt).toBeTruthy();
  });
});

describe('switching to a session already held', () => {
  it('costs no network at all', async () => {
    serveRehydration();
    const { result } = renderHook(() => useActivateSession(), { wrapper });
    await result.current(SESSION);
    const after = summaryFetches();

    useSessionStore.getState().setActiveSession(null);
    await result.current(SESSION);

    // The store is the LIVE copy once hydrated — the stream keeps mutating it. Refetching would
    // both waste the round trip and overwrite frames that arrived after the snapshot.
    expect(summaryFetches()).toBe(after);
    expect(useSessionStore.getState().activeSessionId).toBe(SESSION);
  });

  it('does not discard frames that arrived after the snapshot', async () => {
    serveRehydration();
    const { result } = renderHook(() => useActivateSession(), { wrapper });
    await result.current(SESSION);

    // A frame lands while the user is on another session.
    useSessionStore
      .getState()
      .applyFrame({ event: 'REASONING', data: { thread_id: THREAD, content: ' live-tail' } });
    await result.current(SESSION);

    expect(
      useSessionStore
        .getState()
        .sessions[SESSION].reasoningSteps.filter((s) => s.type === 'message')
        .map((s) => s.content)
        .join(''),
    ).toMatch(/live-tail/);
  });

  it('never lowers the high-water mark', async () => {
    // `markHydrated` takes `lastSeq` as a FLOOR. Lowering it would make the next reattach ask for a
    // gap it already has, re-delivering frames and duplicating the transcript.
    serveRehydration({ last_seq: 5 });
    const { result } = renderHook(() => useActivateSession(), { wrapper });
    await result.current(SESSION);

    useSessionStore
      .getState()
      .applyFrame({ event: 'REASONING', data: { thread_id: THREAD, content: 'x', seq: 42 } });
    useSessionStore.getState().markHydrated(SESSION, 5);

    expect(useSessionStore.getState().streams[SESSION].lastSeq).toBe(42);
  });

  it('collapses a double-click into one rehydration', async () => {
    serveRehydration();
    const { result } = renderHook(() => useActivateSession(), { wrapper });

    // Two callers racing — a tab and a history row, or an impatient double-click. `fetchQuery` dedupes
    // by key, so the second is served from the first's in-flight promise.
    await Promise.all([result.current(SESSION), result.current(SESSION)]);

    expect(summaryFetches()).toBe(1);
  });
});

describe('when it cannot be opened', () => {
  it('distinguishes gone from unauthenticated', async () => {
    fetchMock.mockResolvedValue(json({ detail: 'no such session' }, 404));
    const { result } = renderHook(() => useActivateSession(), { wrapper });

    const gone = await result.current(SESSION);

    expect(gone.ok).toBe(false);
    expect(gone.error?.notFound).toBe(true);
    expect(gone.error?.unauthenticated).toBe(false);
  });

  it('reports an expired login as such', async () => {
    fetchMock.mockResolvedValue(json({ detail: 'expired' }, 401));
    const { result } = renderHook(() => useActivateSession(), { wrapper });

    const denied = await result.current(SESSION);

    // Collapsing this into "not found" makes an expired login look like the user's work was deleted.
    expect(denied.error?.unauthenticated).toBe(true);
    expect(denied.error?.notFound).toBe(false);
  });

  it('leaves the session unhydrated so a retry actually retries', async () => {
    fetchMock.mockResolvedValue(json({ detail: 'boom' }, 500));
    const { result } = renderHook(() => useActivateSession(), { wrapper });

    await result.current(SESSION);

    expect(useSessionStore.getState().streams[SESSION]?.hydratedAt ?? null).toBeNull();
  });

  it('recovers on a retry after a transient failure', async () => {
    fetchMock.mockResolvedValueOnce(json({ detail: 'boom' }, 500));
    const { result } = renderHook(() => useActivateSession(), { wrapper });
    expect((await result.current(SESSION)).ok).toBe(false);

    // The failed rehydration must not be cached as a success, or the session would stay permanently
    // blank with no way to reload it.
    serveRehydration();
    await waitFor(async () => expect((await result.current(SESSION)).ok).toBe(true));
    expect(useSessionStore.getState().streams[SESSION].hydratedAt).toBeTruthy();
  });

  it('refuses an empty id instead of creating a phantom session', async () => {
    const { result } = renderHook(() => useActivateSession(), { wrapper });

    const bad = await result.current('');

    expect(bad.ok).toBe(false);
    expect(Object.keys(useSessionStore.getState().sessions)).toHaveLength(0);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
