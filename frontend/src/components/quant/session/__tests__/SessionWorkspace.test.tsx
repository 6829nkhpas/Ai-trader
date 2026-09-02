// @vitest-environment jsdom
//
// components/quant/session/__tests__/SessionWorkspace.test.tsx
//
// The standalone workspace at `/find-trade/session/{id}`.
//
// Two things matter here beyond "it renders". First, a deep link is the case where NOTHING is in memory
// — no tab was clicked, no run was started — so the whole transcript has to come back from stored
// frames. Second, the workspace must reuse the panel's renderers rather than grow a second set: the
// requirement is that structured tool activity is not flattened into text, and the only durable way to
// honour that is to render the same components.
//
// A 360px viewport assertion is deliberately NOT here. jsdom has no layout engine, so anything it
// claimed about what is visible at a given width would be fiction. That belongs to the Playwright job
// in T10.2.

import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The workspace only makes sense on the multi-session path, and `FQ_MULTI_SESSION` is read at import
// time, so it has to be mocked before the module graph loads.
vi.mock('../../../../lib/env', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../../lib/env')>()),
  FQ_MULTI_SESSION: true,
}));

// `vi.hoisted` is required, not stylistic: `vi.mock` factories are lifted to the top of the file, so a
// plain `const` declared above one is still uninitialised when the factory runs
// ("Cannot access 'notFoundMock' before initialization").
const { notFoundMock, bridgeInvokeMock } = vi.hoisted(() => ({
  // Next's real `notFound()` throws to unwind to the nearest boundary. Reproduced so the component's
  // control flow is exercised rather than short-circuited.
  notFoundMock: vi.fn(() => {
    throw new Error('NEXT_NOT_FOUND');
  }),
  // Typed as the bridge's real call shape so the argument assertions below are checked, not `any`.
  bridgeInvokeMock: vi.fn(
    async (_command: string, _args?: Record<string, unknown>): Promise<undefined> => undefined,
  ),
}));

vi.mock('next/navigation', () => ({ notFound: notFoundMock }));

vi.mock('../../../../lib/bridge', () => ({
  bridgeInvoke: (command: string, args?: Record<string, unknown>) => bridgeInvokeMock(command, args),
  // Resolves to a disposer so the listener effects complete instead of hanging.
  bridgeListen: async () => () => {},
}));

import { useSessionStore } from '../../../../store/useSessionStore';
import { useQuantStore } from '../../../../store/useQuantStore';
import SessionWorkspace from '../SessionWorkspace';

const SESSION = 'sess_WWWWWWWWWWWWWWWWWWWWWWWWWW';
const THREAD = 'thread_WWWWWWWWWWWWWWWWWWWWWWWW';
const RUN = 'run_WWWWWWWWWWWWWWWWWWWWWWWWWW';

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
  created_at: Date.UTC(2026, 2, 12, 3, 45, 0) / 1000,
  updated_at: Date.UTC(2026, 2, 12, 3, 45, 0) / 1000,
  archived_at: null,
  active_run_id: RUN,
  message_count: 2,
  last_run: null,
};

const storedRun = {
  run_id: RUN,
  session_id: SESSION,
  thread_id: THREAD,
  kind: 'find' as const,
  status: 'complete',
  symbol: 'RELIANCE',
  timeframe: '10m',
  profile: 'INTRADAY',
  started_at: 1,
  ended_at: 2,
  last_seq: 4,
};

/** Frames as the backend stored them, including a structured tool pair. */
const STORED_EVENTS = [
  { seq: 1, event: 'RUN_STARTED', data: { thread_id: THREAD } },
  { seq: 2, event: 'REASONING', data: { thread_id: THREAD, content: 'Momentum is intact.' } },
  { seq: 3, event: 'TOOL_CALL_START', data: { thread_id: THREAD, tool: 'get_ohlc' } },
  { seq: 4, event: 'TOOL_CALL_END', data: { thread_id: THREAD, tool: 'get_ohlc' } },
];

function serve(over: { events?: unknown[]; summaryStatus?: number } = {}) {
  fetchMock.mockImplementation((url: string) => {
    const u = String(url);
    if (u.includes('/events')) {
      return Promise.resolve(json({ items: over.events ?? STORED_EVENTS, last_seq: 4 }));
    }
    if (u.includes('/runs')) return Promise.resolve(json({ items: [storedRun] }));
    if (u.includes('/messages')) return Promise.resolve(json({ items: [], last_seq: 0 }));
    // The list the tab bar reads.
    if (u.includes('/sessions?')) return Promise.resolve(json({ items: [summary], next_cursor: null }));
    if (over.summaryStatus) return Promise.resolve(json({ detail: 'nope' }, over.summaryStatus));
    return Promise.resolve(json(summary));
  });
}

let client: QueryClient;
let fetchMock: ReturnType<typeof vi.fn>;

function renderWorkspace(sessionId = SESSION) {
  return render(
    <QueryClientProvider client={client}>
      <SessionWorkspace sessionId={sessionId} />
    </QueryClientProvider>,
  );
}

/**
 * Wait until the session has been rebuilt from the server.
 *
 * Used as the barrier before interacting, rather than waiting for some reasoning text to appear.
 * A restored FINISHED run renders its reasoning inside a collapsed "Thinking" group
 * (`ThinkingGroupRenderer` opens only while a run is live), so the words are legitimately absent from
 * the DOM until it is expanded — waiting on them would hang forever and read as a data failure.
 */
async function awaitHydrated() {
  await waitFor(() => expect(useSessionStore.getState().streams[SESSION]?.hydratedAt).toBeTruthy());
}

/**
 * Text anywhere in the rendered transcript.
 *
 * `getByText` cannot be used: the glass-box renderers pass reasoning through a markdown renderer and a
 * number highlighter, which split a sentence across several `<span>`s, so a regex matcher finds nothing
 * even though the words are on screen.
 */
function bodyText(): string {
  return document.body.textContent ?? '';
}

beforeEach(() => {
  // jsdom has no layout engine and therefore no `scrollIntoView`. `AgentTerminal` auto-scrolls on
  // every transcript change, so without this stub the first frame throws and the transcript never
  // renders — a failure that looks like a data problem and is not.
  Element.prototype.scrollIntoView = vi.fn();

  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: 0 } },
  });
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  useSessionStore.getState().reset();
  useQuantStore.setState({ selectedModel: undefined });
  notFoundMock.mockClear();
  bridgeInvokeMock.mockClear();
});

afterEach(() => {
  client.clear();
  vi.unstubAllGlobals();
});

describe('a deep link with nothing in memory', () => {
  it('rebuilds the transcript from stored frames', async () => {
    serve();
    renderWorkspace();

    // Nothing was clicked and no run was started in this tab — every word here came back from the
    // server and through the same reducer the live stream uses.
    await awaitHydrated();
    expect(
      (useSessionStore.getState().sessions[SESSION]?.reasoningSteps ?? [])
        .filter((s) => s.type === 'message')
        .map((s) => s.content)
        .join(''),
    ).toMatch(/Momentum is intact/);

    // Rendered through the SAME collapsible renderer the live panel uses, not dumped as raw text: a
    // finished run shows a closed "Thinking" group, and the reasoning is behind it.
    const toggle = await screen.findByRole('button', { name: /Thinking/ });
    expect(bodyText()).not.toMatch(/Momentum is intact/);

    fireEvent.click(toggle);
    await waitFor(() => expect(bodyText()).toMatch(/Momentum is intact/));
  });

  it('keeps tool activity STRUCTURED rather than flattening it into the text', async () => {
    serve();
    renderWorkspace();
    await awaitHydrated();

    // The tool appears as its own rendered step, not as a sentence inside the reasoning.
    const toolSteps = useSessionStore
      .getState()
      .sessions[SESSION].reasoningSteps.filter((s) => s.type !== 'message');
    expect(toolSteps.length).toBeGreaterThan(0);
    expect(toolSteps.some((s) => s.toolName === 'get_ohlc' || s.content.includes('get_ohlc'))).toBe(true);

    // And the message text is unpolluted — flattening would have appended the tool name to it.
    const messageText = useSessionStore
      .getState()
      .sessions[SESSION].reasoningSteps.filter((s) => s.type === 'message')
      .map((s) => s.content)
      .join('');
    expect(messageText).not.toMatch(/get_ohlc/);
  });

  it('shows the symbol, timeframe and profile in the header', async () => {
    serve();
    renderWorkspace();

    const header = await screen.findByRole('heading', { level: 1 });
    expect(header.textContent).toMatch(/RELIANCE/);
    // The profile is not in the tab label, so the header is the only place it appears.
    expect(document.body.textContent).toMatch(/INTRADAY/);
  });

  it('shows a loading state before the transcript arrives', async () => {
    let release: (r: Response) => void = () => {};
    serve();
    fetchMock.mockImplementationOnce(() => new Promise<Response>((res) => (release = res)));
    renderWorkspace();

    // An empty transcript would read as a finished conversation with nothing in it.
    expect(await screen.findByText('Opening session…')).toBeTruthy();
    release(json(summary));
    await waitFor(() => expect(screen.queryByText('Opening session…')).toBeNull());
  });

  it('marks the session hydrated and active', async () => {
    serve();
    renderWorkspace();
    await awaitHydrated();

    expect(useSessionStore.getState().activeSessionId).toBe(SESSION);
    expect(useSessionStore.getState().streams[SESSION].hydratedAt).toBeTruthy();
  });
});

describe('a session that is not available', () => {
  it('renders not-found for an unknown id', async () => {
    serve({ summaryStatus: 404 });
    // The component throws out of render via `notFound()`, exactly as Next's boundary expects.
    expect(() => renderWorkspace()).not.toThrow();

    await waitFor(() => expect(notFoundMock).toHaveBeenCalled());
  });

  it('gives the same answer for a session that belongs to someone else', async () => {
    // The API answers 404 rather than 403 so the route is not an enumeration oracle. The UI must not
    // undo that by being more specific.
    serve({ summaryStatus: 404 });
    renderWorkspace();

    await waitFor(() => expect(notFoundMock).toHaveBeenCalled());
    expect(document.body.textContent).not.toMatch(/permission|not yours|forbidden/i);
  });

  it('says the login expired instead of claiming the work is gone', async () => {
    serve({ summaryStatus: 401 });
    renderWorkspace();

    // A deep link is exactly when a cookie is most likely to have lapsed. Collapsing this into
    // not-found would tell the user their conversation was deleted.
    expect((await screen.findByRole('alert')).textContent).toMatch(/expired/i);
    expect(notFoundMock).not.toHaveBeenCalled();
  });

  it('offers a retry for a transient failure', async () => {
    serve({ summaryStatus: 500 });
    renderWorkspace();

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/nope|Could not open/);
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy();
    expect(notFoundMock).not.toHaveBeenCalled();
  });
});

describe('the composer', () => {
  it('grounds a question in the session and the run that produced the analysis', async () => {
    serve();
    renderWorkspace();
    await awaitHydrated();

    // The stored run finished, so the composer unlocks.
    const box = await screen.findByRole('textbox');
    fireEvent.change(box, { target: { value: 'why is the stop there?' } });
    await act(async () => {
      fireEvent.keyDown(box, { key: 'Enter' });
      await Promise.resolve();
    });

    await waitFor(() => expect(bridgeInvokeMock).toHaveBeenCalled());
    const [command, args = {}] = bridgeInvokeMock.mock.calls.at(-1)!;
    expect(command).toBe('ask_trade_question');
    // Named, not inferred. The old client read a flat "current thread" field, so switching sessions
    // mid-question asked the backend about the wrong analysis.
    expect(args.session_id).toBe(SESSION);
    expect(args.context_run_id).toBe(RUN);
    expect(args.question).toBe('why is the stop there?');
    // A thread id has no place on this path; the server resolves the thread from the run.
    expect(args.thread_id).toBeUndefined();
  });

  it('shows the question immediately, before any frame arrives', async () => {
    serve();
    renderWorkspace();
    await awaitHydrated();

    const box = await screen.findByRole('textbox');
    fireEvent.change(box, { target: { value: 'and the target?' } });
    await act(async () => {
      fireEvent.keyDown(box, { key: 'Enter' });
      await Promise.resolve();
    });

    // Optimistic, so pressing send has visible effect while the model thinks.
    expect(await screen.findByText('and the target?')).toBeTruthy();
    expect(useSessionStore.getState().sessions[SESSION].qaStatus).toBe('streaming');
  });
});

describe('history', () => {
  it('overlays rather than replacing the conversation', async () => {
    serve();
    renderWorkspace();
    await awaitHydrated();

    fireEvent.click(screen.getByRole('button', { name: 'Session history' }));
    await act(async () => {
      await Promise.resolve();
    });

    // Unmounting the transcript to show history would discard the subtree a live run is streaming
    // into, so reopening it would look like the run had restarted.
    expect(screen.getByRole('button', { name: /Thinking/ })).toBeTruthy();
  });
});
