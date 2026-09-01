// @vitest-environment jsdom
//
// components/quant/session/__tests__/SessionHistory.test.tsx
//
// The constraint this component exists to honour: **the whole history is never in the browser.**
//
// A trader running several analyses a day accumulates thousands of sessions. A client-side list grows
// without bound, and a client-side search box looks like it searched everything while searching one
// page. So paging and filtering are asserted to be SERVER round trips with the right parameters, not
// merely to produce the right rows.

import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SessionSummary } from '../../../../lib/fq/api';
import { useSessionStore } from '../../../../store/useSessionStore';
import SessionHistory from '../SessionHistory';

const OPEN_IST = Date.UTC(2026, 2, 12, 3, 45, 0) / 1000;

function summary(over: Partial<SessionSummary> = {}): SessionSummary {
  return {
    session_id: 'sess_1',
    title: null,
    symbol: 'RELIANCE',
    timeframe: '10m',
    profile: 'INTRADAY',
    status: 'active',
    created_at: OPEN_IST,
    updated_at: OPEN_IST,
    archived_at: null,
    active_run_id: null,
    message_count: 0,
    last_run: null,
    ...over,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

let client: QueryClient;
let fetchMock: ReturnType<typeof vi.fn>;

async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

async function clickAsync(el: Element) {
  fireEvent.click(el);
  await settle();
}

function renderHistory(props: Partial<React.ComponentProps<typeof SessionHistory>> = {}) {
  const onOpen = props.onOpen ?? vi.fn();
  const result = render(
    <QueryClientProvider client={client}>
      <SessionHistory {...props} onOpen={onOpen} />
    </QueryClientProvider>,
  );
  return { ...result, onOpen };
}

/** Every `GET /sessions` request URL the component made. */
function listUrls(): string[] {
  return fetchMock.mock.calls
    .map(([url]) => String(url))
    .filter((url) => url.includes('/sessions?') || url.endsWith('/sessions'));
}

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: 0 },
    },
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

describe('the list', () => {
  it('renders a row per session with the detail needed to choose one', async () => {
    fetchMock.mockResolvedValue(
      json({
        items: [summary({ session_id: 'a', symbol: 'RELIANCE', timeframe: '10m' })],
        next_cursor: null,
      }),
    );
    renderHistory();

    const row = await screen.findByRole('listitem');
    // Symbol and timeframe are repeated below the label on purpose: once renamed, the label no longer
    // carries them, and they are the one thing a custom title cannot convey.
    expect(row.textContent).toMatch(/RELIANCE/);
    expect(row.textContent).toMatch(/10m/);
  });

  it('shows what the last run concluded', async () => {
    fetchMock.mockResolvedValue(
      json({
        items: [
          summary({
            last_run: { run_id: 'r1', kind: 'find', status: 'complete', started_at: 1, ended_at: 2 },
          }),
        ],
        next_cursor: null,
      }),
    );
    renderHistory();

    expect((await screen.findByRole('listitem')).textContent).toMatch(/FIND · complete/i);
  });

  it('shows a skeleton while loading, not an empty list', async () => {
    // An empty list and a loading list look identical without this, and "no sessions" is a very
    // different message from "not loaded yet".
    fetchMock.mockImplementation(() => new Promise(() => {}));
    renderHistory();

    expect(await screen.findByText('Loading session history…')).toBeTruthy();
  });

  it('renders an empty state', async () => {
    fetchMock.mockResolvedValue(json({ items: [], next_cursor: null }));
    renderHistory();

    expect(await screen.findByText(/No sessions yet/)).toBeTruthy();
  });

  it('says the archived list is empty in its own words', async () => {
    fetchMock.mockResolvedValue(json({ items: [], next_cursor: null }));
    renderHistory({ status: 'archived' });

    expect(await screen.findByText(/Nothing archived yet/)).toBeTruthy();
  });

  it('reports WHY the list failed and offers a retry', async () => {
    fetchMock.mockResolvedValue(json({ detail: 'session expired' }, 401));
    renderHistory();

    const alert = await screen.findByRole('alert');
    // A 401 needs a re-login and a 500 needs a retry; a generic message hides which.
    expect(alert.textContent).toMatch(/Could not load your history/);
    expect(alert.textContent).toMatch(/session expired/);
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('asks the server for the requested status', async () => {
    fetchMock.mockResolvedValue(json({ items: [], next_cursor: null }));
    renderHistory({ status: 'archived' });
    await screen.findByText(/Nothing archived/);

    expect(listUrls()[0]).toMatch(/status=archived/);
  });
});

describe('pagination', () => {
  const page1 = {
    items: [summary({ session_id: 'a' }), summary({ session_id: 'b', symbol: 'INFY' })],
    next_cursor: 'cursor_1',
  };
  const page2 = {
    items: [summary({ session_id: 'c', symbol: 'TCS' })],
    next_cursor: null,
  };

  it('requests one page at a time, not one request per session', async () => {
    fetchMock.mockResolvedValue(json(page1));
    renderHistory();
    await screen.findAllByRole('listitem');

    // The whole point of cursor paging: two sessions cost ONE request.
    expect(listUrls()).toHaveLength(1);
    expect(listUrls()[0]).toMatch(/limit=25/);
  });

  it('passes the server cursor and does not duplicate rows', async () => {
    fetchMock
      .mockResolvedValueOnce(json(page1))
      .mockResolvedValueOnce(json(page2));
    renderHistory();
    await screen.findByRole('button', { name: 'Load more' });

    await clickAsync(screen.getByRole('button', { name: 'Load more' }));
    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(3));

    // The cursor is the SERVER's, echoed back verbatim — an offset computed client-side is what
    // produced the duplicate/missing rows this replaces.
    expect(listUrls()[1]).toMatch(/cursor=cursor_1/);
    const ids = screen.getAllByRole('listitem').map((li) => li.getAttribute('data-session-id'));
    expect(ids).toEqual(['a', 'b', 'c']);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('stops offering more when the server says there is no cursor', async () => {
    fetchMock.mockResolvedValue(json({ items: [summary()], next_cursor: null }));
    renderHistory();
    await screen.findByRole('listitem');

    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull();
  });

  it('keeps a Load-more control, so the list works without IntersectionObserver', async () => {
    // jsdom has no `IntersectionObserver`, and neither do some embedded webviews. If infinite scroll
    // were the only way to page, history would be permanently truncated there.
    expect(typeof IntersectionObserver).toBe('undefined');
    fetchMock.mockResolvedValue(json(page1));
    renderHistory();

    expect(await screen.findByRole('button', { name: 'Load more' })).toBeTruthy();
  });
});

describe('search', () => {
  const oneShortPage = { items: [summary()], next_cursor: null };
  const pagedList = {
    items: Array.from({ length: 25 }, (_, i) => summary({ session_id: `s${i}` })),
    next_cursor: 'cursor_1',
  };

  it('is hidden while the list fits on one page', async () => {
    // A search box over one page is noise, and it invites searching when scanning is faster.
    fetchMock.mockResolvedValue(json(oneShortPage));
    renderHistory();
    await screen.findByRole('listitem');

    expect(screen.queryByRole('searchbox', { name: 'Search sessions' })).toBeNull();
  });

  it('appears once there is more than one page', async () => {
    fetchMock.mockResolvedValue(json(pagedList));
    renderHistory();

    expect(await screen.findByRole('searchbox', { name: 'Search sessions' })).toBeTruthy();
  });

  // Real timers throughout. Fake timers were tried first and made every test AFTER this block hang:
  // `waitFor`/`findBy` and TanStack Query both schedule on timers, and swapping the clock underneath
  // a mounted tree and a live query client leaks pending work into the next test. The debounce is
  // 250ms and `waitFor` polls for 1000ms, so real timers assert exactly the same thing with none of
  // that coupling.

  it('filters on the SERVER via ?q=, debounced to one request', async () => {
    fetchMock.mockResolvedValue(json(pagedList));
    renderHistory();
    const box = await screen.findByRole('searchbox', { name: 'Search sessions' });
    const before = listUrls().length;

    // Eight keystrokes must not be eight requests.
    for (const ch of 'RELIANCE') {
      fireEvent.change(box, { target: { value: (box as HTMLInputElement).value + ch } });
    }

    await waitFor(() => expect(listUrls().length).toBe(before + 1));
    expect(listUrls().at(-1)).toMatch(/q=RELIANCE/);
  });

  it('stays visible when the filter narrows the list', async () => {
    fetchMock.mockResolvedValueOnce(json(pagedList));
    renderHistory();
    const box = await screen.findByRole('searchbox', { name: 'Search sessions' });

    // Removing the box because the filtered result is short would strand the user in a filtered view
    // with no way back.
    fetchMock.mockResolvedValue(json({ items: [summary()], next_cursor: null }));
    fireEvent.change(box, { target: { value: 'RELIANCE' } });

    await waitFor(() => expect(listUrls().at(-1)).toMatch(/q=RELIANCE/));
    expect(screen.getByRole('searchbox', { name: 'Search sessions' })).toBeTruthy();
  });

  it('says nothing matched rather than showing an empty list', async () => {
    fetchMock.mockResolvedValueOnce(json(pagedList));
    renderHistory();
    const box = await screen.findByRole('searchbox', { name: 'Search sessions' });

    fetchMock.mockResolvedValue(json({ items: [], next_cursor: null }));
    fireEvent.change(box, { target: { value: 'ZZZZ' } });

    expect(await screen.findByText(/No sessions match/)).toBeTruthy();
  });
});

describe('opening a session', () => {
  it('delegates to the caller, which is what rehydrates it', async () => {
    fetchMock.mockResolvedValue(json({ items: [summary({ session_id: 'a' })], next_cursor: null }));
    const onOpen = vi.fn();
    renderHistory({ onOpen });

    await clickAsync(await screen.findByRole('button', { name: /^Open RELIANCE/ }));

    expect(onOpen).toHaveBeenCalledWith('a');
  });
});

describe('rename', () => {
  const listOf = (items: SessionSummary[]) => json({ items, next_cursor: null });

  it('is optimistic — the new name shows before the server replies', async () => {
    let resolvePatch: (r: Response) => void = () => {};
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') return new Promise<Response>((res) => (resolvePatch = res));
      return Promise.resolve(listOf([summary({ session_id: 'a' })]));
    });
    // Seeded so the optimistic update in `useRenameSession` has a snapshot to patch.
    client.setQueryData(['fq', 'sessions', 'a'], summary({ session_id: 'a' }));
    renderHistory();

    await clickAsync(await screen.findByRole('button', { name: 'Rename RELIANCE · 10m · 9:15 AM' }));
    const input = screen.getByRole('textbox', { name: /^Rename/ });
    fireEvent.change(input, { target: { value: 'Gap-up thesis' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await settle();

    // A rename is direct manipulation of a label the user is looking at; a round trip reads as lag.
    expect(client.getQueryData<SessionSummary>(['fq', 'sessions', 'a'])?.title).toBe('Gap-up thesis');
    resolvePatch(json(summary({ session_id: 'a', title: 'Gap-up thesis' })));
    await settle();
  });

  it('rolls back to the previous name when the server rejects it', async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') return Promise.resolve(json({ detail: 'nope' }, 500));
      return Promise.resolve(listOf([summary({ session_id: 'a', title: 'Original' })]));
    });
    client.setQueryData(['fq', 'sessions', 'a'], summary({ session_id: 'a', title: 'Original' }));
    renderHistory();

    await clickAsync(await screen.findByRole('button', { name: 'Rename Original' }));
    const input = screen.getByRole('textbox', { name: /^Rename/ });
    fireEvent.change(input, { target: { value: 'Doomed' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    // Rolled back from the snapshot rather than by refetching: a refetch during a streaming run could
    // arrive with a newer `updated_at` and reorder the list as a side effect of a failed rename.
    await waitFor(() =>
      expect(client.getQueryData<SessionSummary>(['fq', 'sessions', 'a'])?.title).toBe('Original'),
    );
    expect((await screen.findByRole('alert')).textContent).toMatch(/Could not rename/);
  });

  it('abandons the edit on Escape', async () => {
    fetchMock.mockResolvedValue(listOf([summary({ session_id: 'a' })]));
    renderHistory();

    await clickAsync(await screen.findByRole('button', { name: /^Rename/ }));
    const input = screen.getByRole('textbox', { name: /^Rename/ });
    fireEvent.change(input, { target: { value: 'half typed' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    await settle();

    // Without this the only way out of a half-typed rename is to save it.
    expect(screen.queryByRole('textbox', { name: /^Rename/ })).toBeNull();
    expect(
      fetchMock.mock.calls.some(([, i]) => (i as RequestInit | undefined)?.method === 'PATCH'),
    ).toBe(false);
  });

  it('does not seed the input with the derived label', async () => {
    // Pre-filling it would turn "rename" into "accept this generated name", leaving the user with a
    // pinned title they never chose — and no way back to the derived label.
    fetchMock.mockResolvedValue(listOf([summary({ session_id: 'a' })]));
    renderHistory();

    await clickAsync(await screen.findByRole('button', { name: /^Rename/ }));

    expect((screen.getByRole('textbox', { name: /^Rename/ }) as HTMLInputElement).value).toBe('');
  });

  it('clears the title when the name is emptied, restoring the derived label', async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') return Promise.resolve(json(summary({ session_id: 'a' })));
      return Promise.resolve(listOf([summary({ session_id: 'a', title: 'Named' })]));
    });
    renderHistory();

    await clickAsync(await screen.findByRole('button', { name: 'Rename Named' }));
    const input = screen.getByRole('textbox', { name: /^Rename/ });
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      const patch = fetchMock.mock.calls.find(
        ([, i]) => (i as RequestInit | undefined)?.method === 'PATCH',
      );
      expect(JSON.parse(String((patch![1] as RequestInit).body))).toEqual({ title: null });
    });
  });

  it('sends nothing when the name is unchanged', async () => {
    fetchMock.mockResolvedValue(listOf([summary({ session_id: 'a', title: 'Same' })]));
    renderHistory();

    await clickAsync(await screen.findByRole('button', { name: 'Rename Same' }));
    fireEvent.keyDown(screen.getByRole('textbox', { name: /^Rename/ }), { key: 'Enter' });
    await settle();

    expect(
      fetchMock.mock.calls.some(([, i]) => (i as RequestInit | undefined)?.method === 'PATCH'),
    ).toBe(false);
  });
});

describe('archive and reopen', () => {
  it('archives a row and drops the client copy only after the server agrees', async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        return Promise.resolve(json(summary({ session_id: 'a', status: 'archived' })));
      }
      return Promise.resolve(json({ items: [summary({ session_id: 'a' })], next_cursor: null }));
    });
    useSessionStore.getState().setActiveSession('a');
    renderHistory();

    await clickAsync(await screen.findByRole('button', { name: /^Archive/ }));

    await waitFor(() => expect(useSessionStore.getState().activeSessionId).toBeNull());
    expect(useSessionStore.getState().sessions.a).toBeUndefined();
  });

  it('keeps the client copy when the archive fails', async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') return Promise.resolve(json({ detail: 'boom' }, 500));
      return Promise.resolve(json({ items: [summary({ session_id: 'a' })], next_cursor: null }));
    });
    useSessionStore.getState().setActiveSession('a');
    renderHistory();

    await clickAsync(await screen.findByRole('button', { name: /^Archive/ }));

    // Nothing was archived, so discarding the transcript would be losing work the server still has.
    expect((await screen.findByRole('alert')).textContent).toMatch(/Could not archive/);
    expect(useSessionStore.getState().activeSessionId).toBe('a');
  });

  it('offers reopen instead of archive for an archived session, and opens it', async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') return Promise.resolve(json(summary({ session_id: 'a' })));
      return Promise.resolve(
        json({ items: [summary({ session_id: 'a', status: 'archived' })], next_cursor: null }),
      );
    });
    const onOpen = vi.fn();
    renderHistory({ status: 'archived', onOpen });

    expect(screen.queryByRole('button', { name: /^Archive/ })).toBeNull();
    await clickAsync(await screen.findByRole('button', { name: /^Reopen/ }));

    // Reopening means "work on this now". A row that only flips a status badge appears to do nothing.
    await waitFor(() => expect(onOpen).toHaveBeenCalledWith('a'));
  });

  it('does not open the session when the reopen fails', async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') return Promise.resolve(json({ detail: 'gone' }, 404));
      return Promise.resolve(
        json({ items: [summary({ session_id: 'a', status: 'archived' })], next_cursor: null }),
      );
    });
    const onOpen = vi.fn();
    renderHistory({ status: 'archived', onOpen });

    await clickAsync(await screen.findByRole('button', { name: /^Reopen/ }));

    expect((await screen.findByRole('alert')).textContent).toMatch(/Could not reopen/);
    expect(onOpen).not.toHaveBeenCalled();
  });
});
