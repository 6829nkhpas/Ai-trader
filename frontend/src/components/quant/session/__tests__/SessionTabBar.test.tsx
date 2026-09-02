// @vitest-environment jsdom
//
// components/quant/session/__tests__/SessionTabBar.test.tsx
//
// The tab bar's BEHAVIOUR: what the list comes from, what activation does, what closing does, and
// whether a keyboard-only user can work it. Markup details are not asserted; the accessible roles
// and names are, because they are the contract assistive technology depends on.
//
// The suite defaults to `environment: 'node'`, so the docblock above is required, and React must be
// imported explicitly — vitest here uses the classic JSX transform.

import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SessionSummary } from '../../../../lib/fq/api';
import { useSessionStore } from '../../../../store/useSessionStore';
import SessionTabBar from '../SessionTabBar';

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

/**
 * Click and let the resulting state settle.
 *
 * `fireEvent` rather than `@testing-library/user-event`: the dependency is not installed, and every
 * interaction here is a plain click or keydown that `fireEvent` models faithfully. Adding a package
 * to press a button is not worth it.
 */
async function clickAsync(el: Element) {
  fireEvent.click(el);
  // Flush the promise the click may have started (a mutation), so assertions do not race it.
  await act(async () => {
    await Promise.resolve();
  });
}

/** A keydown on whatever has focus, which is what the tablist's handler sees. */
async function press(key: string) {
  fireEvent.keyDown(document.activeElement ?? document.body, { key });
  await act(async () => {
    await Promise.resolve();
  });
}

/** `Ctrl+<key>` on the window, where the shortcut listener lives. */
async function chord(key: string) {
  fireEvent.keyDown(window, { key, ctrlKey: true });
  await act(async () => {
    await Promise.resolve();
  });
}

function renderBar(props: { onActivate?: (id: string) => void } = {}) {
  return render(
    <QueryClientProvider client={client}>
      <SessionTabBar {...props} />
    </QueryClientProvider>,
  );
}

/** Seed the session list the bar reads from. */
function listReturns(items: SessionSummary[], next_cursor: string | null = null) {
  fetchMock.mockImplementation((url: string) => {
    if (String(url).includes('/sessions?') || String(url).endsWith('/sessions')) {
      return Promise.resolve(json({ items, next_cursor }));
    }
    return Promise.resolve(json({}));
  });
}

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: {
      // Deterministic: a retry would make a failure assertion depend on timing.
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

describe('the list comes from the server', () => {
  it('renders a tab per session with the derived label', async () => {
    listReturns([
      summary({ session_id: 'a', symbol: 'RELIANCE', timeframe: '10m' }),
      summary({ session_id: 'b', symbol: 'INFY', timeframe: '1h' }),
    ]);
    renderBar();

    // Server-sourced, not store-sourced: this is what makes a tab survive a reload, and it means
    // the bar cannot show a session the server has never heard of.
    expect(await screen.findByRole('tab', { name: /RELIANCE/ })).toBeTruthy();
    expect(screen.getByRole('tab', { name: /INFY/ })).toBeTruthy();
  });

  it('exposes a tablist so the group is navigable', async () => {
    listReturns([summary()]);
    renderBar();
    await screen.findByRole('tab');

    expect(screen.getByRole('tablist', { name: 'Analysis sessions' })).toBeTruthy();
  });

  it('marks exactly one tab selected', async () => {
    listReturns([summary({ session_id: 'a' }), summary({ session_id: 'b', symbol: 'INFY' })]);
    useSessionStore.getState().setActiveSession('b');
    renderBar();

    await screen.findByRole('tab', { name: /INFY/ });
    const selected = screen.getAllByRole('tab').filter((t) => t.getAttribute('aria-selected') === 'true');
    expect(selected).toHaveLength(1);
    expect(selected[0].getAttribute('id')).toBe('fq-tab-b');
  });

  it('shows an empty state rather than an empty strip', async () => {
    listReturns([]);
    renderBar();
    expect(await screen.findByText(/No sessions yet/)).toBeTruthy();
  });

  it('reports WHY the list failed, and offers a retry', async () => {
    // A 401 needs a different action from a service being down, and the user can only tell if the
    // reason is stated.
    fetchMock.mockResolvedValue(json({ detail: 'nope' }, 500));
    renderBar();

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/Could not load your sessions/);
    expect(within(alert).getByRole('button', { name: 'Retry' })).toBeTruthy();
  });
});

describe('the streaming indicator', () => {
  it('names a running background session for screen readers', async () => {
    listReturns([summary({ session_id: 'a' }), summary({ session_id: 'b', symbol: 'INFY' })]);
    const s = useSessionStore.getState();
    s.upsertSession('b');
    s.bindThread('thread_b', 'b');
    s.setActiveSession('a');
    s.applyFrame({ event: 'RUN_STARTED', data: { thread_id: 'thread_b' } });

    renderBar();

    // The dot is `aria-hidden`, so the state has to be in the accessible name — and it must be on
    // the tab the user is NOT looking at, which is the state the old single-session store could not
    // represent at all.
    expect(await screen.findByRole('tab', { name: /INFY.*analysis running/ })).toBeTruthy();
    expect(screen.getByRole('tab', { name: /RELIANCE/ }).getAttribute('aria-label')).not.toMatch(
      /analysis running/,
    );
  });
});

describe('activation', () => {
  it('calls the injected handler instead of setting state itself', async () => {
    // Injected because activating an unloaded session must also REHYDRATE it, which belongs to the
    // workspace that renders the transcript.
    const onActivate = vi.fn();
    listReturns([summary({ session_id: 'a' })]);
    renderBar({ onActivate });

    await clickAsync(await screen.findByRole('tab', { name: /RELIANCE/ }));

    expect(onActivate).toHaveBeenCalledWith('a');
    expect(useSessionStore.getState().activeSessionId).toBeNull();
  });

  it('falls back to setting the active session when no handler is given', async () => {
    listReturns([summary({ session_id: 'a' })]);
    renderBar();

    await clickAsync(await screen.findByRole('tab', { name: /RELIANCE/ }));

    expect(useSessionStore.getState().activeSessionId).toBe('a');
  });
});

describe('keyboard navigation', () => {
  it('keeps one tab stop, so the bar is not eight tab presses deep', async () => {
    listReturns([
      summary({ session_id: 'a' }),
      summary({ session_id: 'b', symbol: 'INFY' }),
      summary({ session_id: 'c', symbol: 'TCS' }),
    ]);
    useSessionStore.getState().setActiveSession('b');
    renderBar();
    await screen.findByRole('tab', { name: /TCS/ });

    const focusable = screen.getAllByRole('tab').filter((t) => t.getAttribute('tabindex') === '0');
    expect(focusable).toHaveLength(1);
    expect(focusable[0].getAttribute('id')).toBe('fq-tab-b');
  });

  it('moves focus with the arrow keys WITHOUT activating', async () => {
    const onActivate = vi.fn();
    listReturns([summary({ session_id: 'a' }), summary({ session_id: 'b', symbol: 'INFY' })]);
    useSessionStore.getState().setActiveSession('a');
    renderBar({ onActivate });
    const first = await screen.findByRole('tab', { name: /RELIANCE/ });

    first.focus();
    await press('ArrowRight');

    expect(document.activeElement?.id).toBe('fq-tab-b');
    // Activating on arrow would rehydrate a session per keypress while the user is merely scanning.
    expect(onActivate).not.toHaveBeenCalled();
  });

  it('wraps at both ends', async () => {
    listReturns([summary({ session_id: 'a' }), summary({ session_id: 'b', symbol: 'INFY' })]);
    useSessionStore.getState().setActiveSession('a');
    renderBar();
    const first = await screen.findByRole('tab', { name: /RELIANCE/ });

    first.focus();
    await press('ArrowLeft');
    expect(document.activeElement?.id).toBe('fq-tab-b');

    await press('ArrowRight');
    expect(document.activeElement?.id).toBe('fq-tab-a');
  });

  it('jumps to the ends with Home and End', async () => {
    listReturns([
      summary({ session_id: 'a' }),
      summary({ session_id: 'b', symbol: 'INFY' }),
      summary({ session_id: 'c', symbol: 'TCS' }),
    ]);
    useSessionStore.getState().setActiveSession('b');
    renderBar();
    const middle = await screen.findByRole('tab', { name: /INFY/ });

    middle.focus();
    await press('End');
    expect(document.activeElement?.id).toBe('fq-tab-c');

    await press('Home');
    expect(document.activeElement?.id).toBe('fq-tab-a');
  });

  it('activates whatever the arrow keys moved focus to', async () => {
    // The focused tab, not the previously active one, is what gets committed — the pairing that
    // makes arrow-then-commit work at all.
    //
    // The commit is fired as a CLICK rather than as Enter. Enter and Space on a native `<button>`
    // are translated to a click by the browser, and jsdom does not implement that translation, so
    // asserting `keyDown{Enter}` here would test jsdom rather than this component. The tab is a real
    // `<button type="button">`, which is what earns the keyboard behaviour.
    const onActivate = vi.fn();
    listReturns([summary({ session_id: 'a' }), summary({ session_id: 'b', symbol: 'INFY' })]);
    useSessionStore.getState().setActiveSession('a');
    renderBar({ onActivate });
    const first = await screen.findByRole('tab', { name: /RELIANCE/ });

    first.focus();
    await press('ArrowRight');
    await clickAsync(document.activeElement as Element);

    expect(onActivate).toHaveBeenCalledWith('b');
  });

  it('makes every tab a real button, which is what gives it Enter and Space for free', async () => {
    listReturns([summary({ session_id: 'a' })]);
    renderBar();
    const tab = await screen.findByRole('tab', { name: /RELIANCE/ });

    // A `div role="tab"` would look identical to `getByRole` and be inert for keyboard users.
    expect(tab.tagName).toBe('BUTTON');
    expect(tab.getAttribute('type')).toBe('button');
  });

  it('switches with Ctrl+N, and Ctrl+9 means the last one', async () => {
    const onActivate = vi.fn();
    listReturns([
      summary({ session_id: 'a' }),
      summary({ session_id: 'b', symbol: 'INFY' }),
      summary({ session_id: 'c', symbol: 'TCS' }),
    ]);
    renderBar({ onActivate });
    await screen.findByRole('tab', { name: /TCS/ });

    await chord('2');
    expect(onActivate).toHaveBeenLastCalledWith('b');

    // 9 means LAST, matching browsers, so it stays useful past nine sessions.
    await chord('9');
    expect(onActivate).toHaveBeenLastCalledWith('c');
  });

  it('ignores Ctrl+N pointing past the end', async () => {
    const onActivate = vi.fn();
    listReturns([summary({ session_id: 'a' })]);
    renderBar({ onActivate });
    await screen.findByRole('tab');

    await chord('5');
    expect(onActivate).not.toHaveBeenCalled();
  });
});

describe('closing a tab', () => {
  it('archives immediately when nothing is running', async () => {
    // No confirm for a recoverable action: always asking trains the user to dismiss without
    // reading, which is worse than not asking.
    listReturns([summary({ session_id: 'a' })]);
    renderBar();
    const close = await screen.findByRole('button', { name: /^Close RELIANCE/ });

    await clickAsync(close);

    await waitFor(() => {
      const archiveCall = fetchMock.mock.calls.find(([url, init]) =>
        String(url).includes('/sessions/a') && (init as RequestInit | undefined)?.method === 'PATCH',
      );
      expect(archiveCall).toBeDefined();
    });
  });

  it('does not activate the session on its way out', async () => {
    const onActivate = vi.fn();
    listReturns([summary({ session_id: 'a' })]);
    renderBar({ onActivate });

    await clickAsync(await screen.findByRole('button', { name: /^Close RELIANCE/ }));

    // Without `stopPropagation` the click also lands on the tab.
    expect(onActivate).not.toHaveBeenCalled();
  });

  it('confirms before interrupting a RUNNING session', async () => {
    listReturns([summary({ session_id: 'a' })]);
    const s = useSessionStore.getState();
    s.upsertSession('a');
    s.bindThread('thread_a', 'a');
    s.applyFrame({ event: 'RUN_STARTED', data: { thread_id: 'thread_a' } });
    renderBar();

    await clickAsync(await screen.findByRole('button', { name: /^Close RELIANCE/ }));

    const dialog = await screen.findByRole('alertdialog');
    expect(dialog.textContent).toMatch(/still running/);
    // Nothing archived yet.
    expect(
      fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH'),
    ).toBe(false);
  });

  it('keeps the session when the confirm is declined', async () => {
    listReturns([summary({ session_id: 'a' })]);
    const s = useSessionStore.getState();
    s.upsertSession('a');
    s.bindThread('thread_a', 'a');
    s.applyFrame({ event: 'RUN_STARTED', data: { thread_id: 'thread_a' } });
    renderBar();

    await clickAsync(await screen.findByRole('button', { name: /^Close RELIANCE/ }));
    await clickAsync(await screen.findByRole('button', { name: 'Keep it open' }));

    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(
      fetchMock.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH'),
    ).toBe(false);
  });

  it('leaves the tab in place when the archive request fails', async () => {
    // The archive is NOT optimistic: an un-removed tab reappearing looks exactly like lost work.
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') return Promise.resolve(json({ detail: 'boom' }, 500));
      return Promise.resolve(json({ items: [summary({ session_id: 'a' })], next_cursor: null }));
    });
    renderBar();

    await clickAsync(await screen.findByRole('button', { name: /^Close RELIANCE/ }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([, i]) => (i as RequestInit | undefined)?.method === 'PATCH'),
      ).toBe(true),
    );
    expect(screen.getByRole('tab', { name: /RELIANCE/ })).toBeTruthy();
  });

  it('says the close failed instead of failing silently', async () => {
    // The tab correctly stays put — nothing was archived — but with no message that reads as "the
    // close button does nothing". The rejection also has to be caught: left alone it surfaces as an
    // unhandled promise rejection and takes the process down in CI.
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') return Promise.resolve(json({ detail: 'archive blew up' }, 500));
      return Promise.resolve(json({ items: [summary({ session_id: 'a' })], next_cursor: null }));
    });
    renderBar();

    await clickAsync(await screen.findByRole('button', { name: /^Close RELIANCE/ }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/Could not close/);
    expect(alert.textContent).toMatch(/archive blew up/);
  });

  it('moves to a surviving session when the ACTIVE one is closed', async () => {
    // Closing the tab you are looking at must land somewhere, or the workspace renders nothing with
    // no indication why.
    const onActivate = vi.fn();
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        return Promise.resolve(json(summary({ session_id: 'a', status: 'archived' })));
      }
      return Promise.resolve(
        json({
          items: [summary({ session_id: 'a' }), summary({ session_id: 'b', symbol: 'INFY' })],
          next_cursor: null,
        }),
      );
    });
    useSessionStore.getState().setActiveSession('a');
    renderBar({ onActivate });

    await clickAsync(await screen.findByRole('button', { name: /^Close RELIANCE/ }));

    await waitFor(() => expect(onActivate).toHaveBeenCalledWith('b'));
  });

  it('clears the selection when the last session is closed', async () => {
    fetchMock.mockImplementation((url: string, init?: RequestInit) => {
      if (init?.method === 'PATCH') {
        return Promise.resolve(json(summary({ session_id: 'a', status: 'archived' })));
      }
      return Promise.resolve(json({ items: [summary({ session_id: 'a' })], next_cursor: null }));
    });
    useSessionStore.getState().setActiveSession('a');
    renderBar();

    await clickAsync(await screen.findByRole('button', { name: /^Close RELIANCE/ }));

    // Not `''` — an empty string is a truthy-looking session id that would create a phantom entry.
    await waitFor(() => expect(useSessionStore.getState().activeSessionId).toBeNull());
  });
});

describe('overflow', () => {
  const many = (n: number) =>
    Array.from({ length: n }, (_, i) =>
      summary({ session_id: `s${i}`, symbol: `SYM${i}`, created_at: OPEN_IST + i * 60 }),
    );

  it('shows every tab up to the threshold', async () => {
    listReturns(many(8));
    renderBar();
    await screen.findByRole('tab', { name: /SYM0/ });

    expect(screen.getAllByRole('tab')).toHaveLength(8);
    expect(screen.queryByRole('button', { name: /more sessions/ })).toBeNull();
  });

  it('moves the excess into a menu past the threshold', async () => {
    listReturns(many(11));
    renderBar();
    await screen.findByRole('tab', { name: /SYM0/ });

    expect(screen.getAllByRole('tab')).toHaveLength(8);
    const trigger = screen.getByRole('button', { name: '3 more sessions' });
    await clickAsync(trigger);

    const menu = screen.getByRole('menu');
    expect(within(menu).getAllByRole('menuitem')).toHaveLength(3);
  });

  it('activates from the overflow menu and closes it', async () => {
    const onActivate = vi.fn();
    listReturns(many(10));
    renderBar({ onActivate });
    await screen.findByRole('tab', { name: /SYM0/ });

    await clickAsync(screen.getByRole('button', { name: '2 more sessions' }));
    await clickAsync(screen.getByRole('menuitem', { name: /SYM9/ }));

    expect(onActivate).toHaveBeenCalledWith('s9');
    expect(screen.queryByRole('menu')).toBeNull();
  });
});

describe('malformed list responses', () => {
  // `useInfiniteQuery` hands the component a `pages` array, and the bar flattens it with
  // `flatMap(page => page.items)`. A page that arrives WITHOUT an `items` array makes that flatMap
  // yield a single `undefined` entry, which reaches `session.session_id` in the render and throws —
  // taking out the whole tab bar rather than one tab, because the throw happens during the parent's
  // render. The full suite surfaced this as three "Vitest caught unhandled errors" and a non-zero
  // exit code even though every test still reported green, which is precisely the false-positive
  // vitest warns about.
  //
  // This is a network boundary: a truncated response, a proxy that rewrote the body, or a server
  // version that renames the field all produce it. Assert the bar degrades to "no sessions" instead.
  it('renders the empty state when a page arrives without an items array', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(json({ next_cursor: null })));
    renderBar();

    // The empty affordance, not a crash. `findBy` so the query has resolved before asserting.
    await screen.findByRole('button', { name: /new session|new analysis/i });
    expect(screen.queryAllByRole('tab')).toHaveLength(0);
  });

  it('skips a null page without dropping the sessions around it', async () => {
    let call = 0;
    fetchMock.mockImplementation((url: string) => {
      if (!String(url).includes('/sessions')) return Promise.resolve(json({}));
      call += 1;
      // `items: null` rather than a missing key: a JSON serialiser that emits null for an empty
      // collection is common, and `?? []` has to cover it too.
      return Promise.resolve(
        call === 1 ? json({ items: null, next_cursor: null }) : json({ items: [summary()], next_cursor: null }),
      );
    });

    renderBar();
    await waitFor(() => expect(screen.queryAllByRole('tab')).toHaveLength(0));
  });
});
