// @vitest-environment jsdom
//
// components/quant/session/__tests__/NewSessionButton.test.tsx
//
// The one rule this component exists to enforce: **no session exists until the server minted it.**
//
// A client-created session has no `session_id` to own runs, no row to persist messages against and
// no way to survive a reload. It would look like a session right up to the moment the user asked it
// to do something, and then fail in a way that looks like the analysis broke rather than the session
// never having existed.

import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { SessionSummary } from '../../../../lib/fq/api';
import { useSessionStore } from '../../../../store/useSessionStore';
import { useTradeStore } from '../../../../store/useTradeStore';
import NewSessionButton from '../NewSessionButton';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

const created: SessionSummary = {
  session_id: 'sess_new',
  title: null,
  symbol: 'INFY',
  timeframe: '1h',
  profile: 'SWING',
  status: 'active',
  created_at: 1,
  updated_at: 1,
  archived_at: null,
  active_run_id: null,
  message_count: 0,
  last_run: null,
};

let client: QueryClient;
let fetchMock: ReturnType<typeof vi.fn>;

async function clickNew() {
  fireEvent.click(screen.getByRole('button', { name: 'New analysis session' }));
  await act(async () => {
    await Promise.resolve();
  });
}

function renderButton(onCreated?: (id: string) => void) {
  return render(
    <QueryClientProvider client={client}>
      <NewSessionButton onCreated={onCreated} />
    </QueryClientProvider>,
  );
}

/** The body of the `POST /sessions` call, or `null` if none was made. */
function postedBody(): Record<string, unknown> | null {
  const call = fetchMock.mock.calls.find(
    ([, init]) => (init as RequestInit | undefined)?.method === 'POST',
  );
  if (!call) return null;
  return JSON.parse(String((call[1] as RequestInit).body));
}

beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } },
  });
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  useSessionStore.getState().reset();
  useTradeStore.setState({ selectedSymbol: 'INFY', activeTimeframe: '1h', activeProfile: 'SWING' });
});

afterEach(() => {
  client.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the seed is the current trading context', () => {
  it('posts the symbol, timeframe and profile the user is looking at', async () => {
    // A new session must analyse what is on screen. Defaulting to something else would silently
    // analyse the wrong instrument.
    fetchMock.mockResolvedValue(json(created, 201));
    renderButton();

    await clickNew();

    expect(postedBody()).toEqual({ symbol: 'INFY', timeframe: '1h', profile: 'SWING' });
  });

  it('reads the context at click time, not at mount', async () => {
    fetchMock.mockResolvedValue(json(created, 201));
    renderButton();

    // The user changes symbol after the bar rendered. Subscribing at mount would post the stale one.
    act(() => {
      useTradeStore.setState({ selectedSymbol: 'TCS', activeTimeframe: '10m' });
    });
    await clickNew();

    expect(postedBody()).toMatchObject({ symbol: 'TCS', timeframe: '10m' });
  });

  it('refuses without a symbol, in language a trader can act on', async () => {
    // The server would answer 422 "unprocessable entity", which is not something to show anyone.
    useTradeStore.setState({ selectedSymbol: '' });
    renderButton();

    await clickNew();

    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toMatch(/Pick a symbol first/);
    expect(postedBody()).toBeNull();
  });
});

describe('success', () => {
  it('hands back the SERVER-minted id', async () => {
    fetchMock.mockResolvedValue(json(created, 201));
    const onCreated = vi.fn();
    renderButton(onCreated);

    await clickNew();

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('sess_new'));
  });

  it('invalidates the session list so the new tab appears', async () => {
    fetchMock.mockResolvedValue(json(created, 201));
    const spy = vi.spyOn(client, 'invalidateQueries');
    renderButton();

    await clickNew();

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(expect.objectContaining({ queryKey: ['fq', 'sessions'] })),
    );
  });
});

describe('failure', () => {
  it('creates nothing at all', async () => {
    fetchMock.mockResolvedValue(json({ detail: 'quota exceeded' }, 402));
    const onCreated = vi.fn();
    renderButton(onCreated);

    await clickNew();

    await screen.findByRole('alert');
    // No id was minted, so there is nothing to activate and nothing to put in the store.
    expect(onCreated).not.toHaveBeenCalled();
    expect(Object.keys(useSessionStore.getState().sessions)).toHaveLength(0);
    expect(useSessionStore.getState().activeSessionId).toBeNull();
  });

  it('surfaces the reason the server gave', async () => {
    // "Could not start a session" hides the difference between being out of credit and the service
    // being down — one the user can fix, one they cannot.
    fetchMock.mockResolvedValue(json({ detail: 'quota exceeded' }, 402));
    renderButton();

    await clickNew();

    expect((await screen.findByRole('alert')).textContent).toMatch(/quota exceeded/);
  });

  it('survives a transport failure without an unhandled rejection', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));
    renderButton();

    await clickNew();

    expect(await screen.findByRole('alert')).toBeTruthy();
  });

  it('can be dismissed and retried', async () => {
    fetchMock.mockResolvedValueOnce(json({ detail: 'transient' }, 500));
    const onCreated = vi.fn();
    renderButton(onCreated);
    await clickNew();
    await screen.findByRole('alert');

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('alert')).toBeNull();

    fetchMock.mockResolvedValue(json(created, 201));
    await clickNew();

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith('sess_new'));
  });
});
