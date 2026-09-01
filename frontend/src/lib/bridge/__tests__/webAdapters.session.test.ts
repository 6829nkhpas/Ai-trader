// lib/bridge/__tests__/webAdapters.session.test.ts
//
// The session-scoped call path. Three claims:
//
//   1. **The client no longer mints a thread id.** It sends `session_id`; the server mints
//      the thread and reports it on `RUN_STARTED`. The old `thread_${symbol}_${Date.now()}`
//      was guessable to the second, and `GET /stream/{thread_id}` had no ownership check.
//   2. **Reattach asks for the gap.** `?after_seq=` closes the window where frames published
//      while nobody was subscribed were lost — which is exactly when a paused run's client
//      is reconnecting.
//   3. **The legacy path is untouched.** Dispatch is on the presence of `session_id`, so a
//      client mid-deploy keeps working.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { bridgeInvoke, __resetBridgeBus } from '../index';
import { useSessionStore } from '../../../store/useSessionStore';

const SESSION = 'sess_01ABCDEFGHJKMNPQRSTVWXYZ00';
const THREAD = 'thread_01ABCDEFGHJKMNPQRSTVWXYZ00';

/** An SSE response body carrying the given frames. */
function sseResponse(frames: string[], ok = true): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(body, {
    status: ok ? 200 : 500,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function frame(event: string, data: Record<string, unknown>): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** Calls made to a given path prefix. */
function callsTo(fetchMock: ReturnType<typeof vi.fn>, prefix: string) {
  return fetchMock.mock.calls.filter(([url]) => String(url).startsWith(prefix));
}

function bodyOf(call: unknown[]): Record<string, unknown> {
  return JSON.parse(String((call[1] as RequestInit).body));
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  __resetBridgeBus();
  useSessionStore.getState().reset();
  fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  useSessionStore.getState().reset();
});

/** Let the fire-and-forget relay drain. */
async function settle(times = 6) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
  await new Promise((r) => setTimeout(r, 0));
}

describe('run_deep_quant_agent — session path', () => {
  it('sends session_id and NO thread_id, and returns the session id', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/api/tools/')) return new Response('{}', { status: 200 });
      return sseResponse([
        frame('RUN_STARTED', { thread_id: THREAD, session_id: SESSION, run_id: 'run_1' }),
        frame('RUN_FINISHED', { thread_id: THREAD, status: 'completed' }),
      ]);
    });

    const returned = await bridgeInvoke<string>('run_deep_quant_agent', {
      session_id: SESSION,
      symbol: 'RELIANCE',
      mode: 'FIND',
    });
    // The thread does not exist at call time, so the session id is what can be returned.
    expect(returned).toBe(SESSION);

    await settle();
    const runCall = callsTo(fetchMock, '/api/deepquant/run')[0];
    const body = bodyOf(runCall);
    expect(body.session_id).toBe(SESSION);
    expect(body.thread_id).toBeUndefined();
    expect('user_id' in body).toBe(false);
  });

  it('the graph still receives the symbol and timeframe it must analyse', async () => {
    // The server records the SESSION's context on the run row, but a VERIFY of specific
    // numbers must not change under the user, so the body still carries them.
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/api/tools/')) return new Response('{}', { status: 200 });
      return sseResponse([frame('RUN_FINISHED', { status: 'completed' })]);
    });

    await bridgeInvoke('run_deep_quant_agent', {
      session_id: SESSION,
      symbol: 'RELIANCE',
      timeframe: '10m',
      profile: 'INTRADAY',
      mode: 'FIND',
    });
    await settle();

    const body = bodyOf(callsTo(fetchMock, '/api/deepquant/run')[0]);
    expect(body.symbol).toBe('RELIANCE');
    expect(body.timeframe).toBe('10m');
    expect(body.profile).toBe('INTRADAY');
  });

  it('forwards a VERIFY manual trade and builds the verify prompt', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/api/tools/')) return new Response('{}', { status: 200 });
      return sseResponse([frame('RUN_FINISHED', { status: 'completed' })]);
    });

    await bridgeInvoke('run_deep_quant_agent', {
      session_id: SESSION,
      symbol: 'RELIANCE',
      mode: 'VERIFY',
      manual_trade: { side: 'BUY', entry: 2470, stop_loss: 2435, take_profit: 2550, user_analysis: 'breakout' },
    });
    await settle();

    const body = bodyOf(callsTo(fetchMock, '/api/deepquant/run')[0]);
    expect(body.mode).toBe('VERIFY');
    expect(String(body.message)).toContain('Verify the following proposed trade setup');
    expect(String(body.message)).toContain('2470');
    expect(body.manual_trade).toMatchObject({ side: 'BUY', entry: 2470 });
  });

  it('forwards client_msg_id so a retried press cannot duplicate the turn', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/api/tools/')) return new Response('{}', { status: 200 });
      return sseResponse([frame('RUN_FINISHED', { status: 'completed' })]);
    });

    await bridgeInvoke('run_deep_quant_agent', {
      session_id: SESSION,
      symbol: 'RELIANCE',
      client_msg_id: 'press-1',
    });
    await settle();
    expect(bodyOf(callsTo(fetchMock, '/api/deepquant/run')[0]).client_msg_id).toBe('press-1');
  });

  it('routes the streamed frames into the session, binding from RUN_STARTED', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/api/tools/')) return new Response('{}', { status: 200 });
      return sseResponse([
        frame('RUN_STARTED', { thread_id: THREAD, session_id: SESSION, run_id: 'run_1' }),
        frame('REASONING', { thread_id: THREAD, content: 'analysing' }),
        frame('RUN_FINISHED', { thread_id: THREAD, status: 'completed' }),
      ]);
    });

    // The panel subscribes to the bridge event and forwards to the store; do the same here.
    const { bridgeListen } = await import('../index');
    await bridgeListen('deep-quant-stream', (evt) => {
      useSessionStore.getState().applyFrame(evt.payload as never);
    });

    await bridgeInvoke('run_deep_quant_agent', { session_id: SESSION, symbol: 'RELIANCE' });
    await settle(20);

    const state = useSessionStore.getState();
    expect(state.threadToSession[THREAD]).toBe(SESSION);
    expect(state.streams[SESSION].runId).toBe('run_1');
    expect(state.sessions[SESSION].sessionStatus).toBe('complete');
    expect(state.unroutableFrames).toBe(0);
  });
});

describe('reattach', () => {
  it('asks for the gap with ?after_seq= when the run pauses', async () => {
    let served = 0;
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.includes('/api/tools/')) return new Response('{}', { status: 200 });
      if (u.startsWith('/api/deepquant/run')) {
        return sseResponse([
          frame('RUN_STARTED', { thread_id: THREAD, session_id: SESSION, run_id: 'run_1' }),
          frame('REASONING', { thread_id: THREAD, content: 'x', seq: 4 }),
          frame('RUN_FINISHED', { thread_id: THREAD, status: 'paused' }),
        ]);
      }
      // The hub. Complete on the first attach so the loop terminates.
      served += 1;
      return sseResponse([frame('RUN_FINISHED', { thread_id: THREAD, status: 'completed' })]);
    });

    const { bridgeListen } = await import('../index');
    await bridgeListen('deep-quant-stream', (evt) => {
      useSessionStore.getState().applyFrame(evt.payload as never);
    });

    await bridgeInvoke('run_deep_quant_agent', { session_id: SESSION, symbol: 'RELIANCE' });
    await settle(40);

    const hubCalls = callsTo(fetchMock, '/api/deepquant/stream/');
    expect(hubCalls.length).toBeGreaterThanOrEqual(1);
    // seq 4 was the high-water mark, so recovery must start after it — not from zero, which
    // would replay the whole transcript, and not from nothing, which loses the gap.
    expect(String(hubCalls[0][0])).toContain(`after_seq=4`);
    expect(String(hubCalls[0][0])).toContain(encodeURIComponent(THREAD));
    expect(served).toBeGreaterThanOrEqual(1);
  });

  it('omits after_seq when nothing has been seen yet', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      const u = String(url);
      if (u.includes('/api/tools/')) return new Response('{}', { status: 200 });
      if (u.startsWith('/api/deepquant/run')) {
        return sseResponse([
          frame('RUN_STARTED', { thread_id: THREAD, session_id: SESSION, run_id: 'run_1' }),
          frame('RUN_FINISHED', { thread_id: THREAD, status: 'paused' }),
        ]);
      }
      return sseResponse([frame('RUN_FINISHED', { thread_id: THREAD, status: 'completed' })]);
    });

    const { bridgeListen } = await import('../index');
    await bridgeListen('deep-quant-stream', (evt) => {
      useSessionStore.getState().applyFrame(evt.payload as never);
    });

    await bridgeInvoke('run_deep_quant_agent', { session_id: SESSION, symbol: 'RELIANCE' });
    await settle(40);

    const hubCalls = callsTo(fetchMock, '/api/deepquant/stream/');
    expect(hubCalls.length).toBeGreaterThanOrEqual(1);
    // Persistence may be off, in which case there are no seq values and a replay request
    // would be meaningless.
    expect(String(hubCalls[0][0])).not.toContain('after_seq');
  });
});

describe('ask_trade_question — session path', () => {
  it('sends session_id and context_run_id instead of a thread id', async () => {
    fetchMock.mockResolvedValue(sseResponse([frame('RUN_FINISHED', { status: 'completed' })]));

    await bridgeInvoke('ask_trade_question', {
      session_id: SESSION,
      context_run_id: 'run_1',
      question: 'why that stop?',
    });
    await settle();

    const body = bodyOf(callsTo(fetchMock, '/api/deepquant/qa')[0]);
    expect(body.session_id).toBe(SESSION);
    expect(body.context_run_id).toBe('run_1');
    expect(body.question).toBe('why that stop?');
    // The old client read its thread id from a flat "current" field, so switching tabs
    // mid-question asked about the wrong analysis.
    expect(body.thread_id).toBeUndefined();
    expect('user_id' in body).toBe(false);
  });

  it('a null context_run_id grounds in the session default', async () => {
    fetchMock.mockResolvedValue(sseResponse([frame('RUN_FINISHED', { status: 'completed' })]));
    await bridgeInvoke('ask_trade_question', { session_id: SESSION, question: 'why?' });
    await settle();
    expect(bodyOf(callsTo(fetchMock, '/api/deepquant/qa')[0]).context_run_id).toBeNull();
  });

  it('still synthesises a terminal frame when the stream ends without one', async () => {
    // Pre-existing behaviour: the UI must always leave its streaming state.
    fetchMock.mockResolvedValue(sseResponse([frame('REASONING', { content: 'partial' })]));
    const seen: string[] = [];
    const { bridgeListen } = await import('../index');
    await bridgeListen('deep-quant-qa-stream', (evt) => {
      seen.push((evt.payload as { event: string }).event);
    });

    await bridgeInvoke('ask_trade_question', { session_id: SESSION, question: 'why?' });
    await settle(20);
    expect(seen).toContain('RUN_FINISHED');
  });

  it('never puts the session id in the thread_id field of a synthetic frame', async () => {
    // A session id is not a thread id. This was `sessionId ?? payload.thread_id`, so the
    // synthetic terminal carried the SESSION id under `thread_id` — and since the store routes
    // strictly `thread_id → session_id`, that frame could never be routed. The composer stayed
    // locked forever, with no later frame able to unlock it.
    fetchMock.mockResolvedValue(sseResponse([frame('REASONING', { content: 'partial' })]));
    const frames: Array<{ event: string; data: Record<string, unknown> }> = [];
    const { bridgeListen } = await import('../index');
    await bridgeListen('deep-quant-qa-stream', (evt) => {
      frames.push(evt.payload as { event: string; data: Record<string, unknown> });
    });

    await bridgeInvoke('ask_trade_question', { session_id: SESSION, question: 'why?' });
    await settle(20);

    const terminal = frames.find((f) => f.event === 'RUN_FINISHED');
    expect(terminal).toBeDefined();
    expect(terminal!.data.thread_id).not.toBe(SESSION);
    // It names the session directly instead, which is what makes it routable.
    expect(terminal!.data.session_id).toBe(SESSION);
  });

  it('marks synthetic frames as a Q&A turn so they reach the chat, not the glass box', async () => {
    // The server stamps `turn` on everything it sends. A locally built frame must carry it too,
    // or the reducer treats it as analysis reasoning and appends it to the transcript.
    fetchMock.mockResolvedValue(sseResponse([frame('REASONING', { content: 'partial' })]));
    const frames: Array<{ event: string; data: Record<string, unknown> }> = [];
    const { bridgeListen } = await import('../index');
    await bridgeListen('deep-quant-qa-stream', (evt) => {
      frames.push(evt.payload as { event: string; data: Record<string, unknown> });
    });

    await bridgeInvoke('ask_trade_question', { session_id: SESSION, question: 'why?' });
    await settle(20);

    expect(frames.find((f) => f.event === 'RUN_FINISHED')!.data.turn).toBe('qa');
  });

  it('makes a failed request routable so the composer unlocks', async () => {
    // Nothing streamed, so no thread id was ever observed. Without `session_id` on the error
    // frame the user is left with a spinner and no explanation.
    fetchMock.mockResolvedValue(new Response('nope', { status: 500 }));
    const frames: Array<{ event: string; data: Record<string, unknown> }> = [];
    const { bridgeListen } = await import('../index');
    await bridgeListen('deep-quant-qa-stream', (evt) => {
      frames.push(evt.payload as { event: string; data: Record<string, unknown> });
    });

    await bridgeInvoke('ask_trade_question', { session_id: SESSION, question: 'why?' });
    await settle(20);

    const error = frames.find((f) => f.event === 'ERROR');
    expect(error).toBeDefined();
    expect(error!.data.session_id).toBe(SESSION);
    expect(error!.data.turn).toBe('qa');
  });

  it('uses the thread the server actually minted once it is known', async () => {
    // On the session path the thread does not exist at call time — the server mints it and
    // reports it on RUN_STARTED. A synthetic terminal should route the same way the server's
    // own frames did, so it carries the LEARNED thread id.
    fetchMock.mockResolvedValue(
      sseResponse([frame('RUN_STARTED', { thread_id: 'thread_server_minted' })]),
    );
    const frames: Array<{ event: string; data: Record<string, unknown> }> = [];
    const { bridgeListen } = await import('../index');
    await bridgeListen('deep-quant-qa-stream', (evt) => {
      frames.push(evt.payload as { event: string; data: Record<string, unknown> });
    });

    await bridgeInvoke('ask_trade_question', { session_id: SESSION, question: 'why?' });
    await settle(20);

    expect(frames.find((f) => f.event === 'RUN_FINISHED')!.data.thread_id).toBe(
      'thread_server_minted',
    );
  });

  it('leaves the legacy path sending its own thread id', async () => {
    fetchMock.mockResolvedValue(sseResponse([frame('REASONING', { content: 'partial' })]));
    const frames: Array<{ event: string; data: Record<string, unknown> }> = [];
    const { bridgeListen } = await import('../index');
    await bridgeListen('deep-quant-qa-stream', (evt) => {
      frames.push(evt.payload as { event: string; data: Record<string, unknown> });
    });

    await bridgeInvoke('ask_trade_question', { thread_id: THREAD, question: 'why?' });
    await settle(20);

    const terminal = frames.find((f) => f.event === 'RUN_FINISHED')!;
    expect(terminal.data.thread_id).toBe(THREAD);
    // No session on this path, so nothing to name.
    expect(terminal.data.session_id).toBeUndefined();
  });
});

describe('cancel_deep_quant_agent', () => {
  it('sends run_id when one is known', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));
    await bridgeInvoke('cancel_deep_quant_agent', { run_id: 'run_1', session_id: SESSION });
    const body = bodyOf(callsTo(fetchMock, '/api/deepquant/cancel')[0]);
    // run_id is what the server minted, so it can check ownership on it directly.
    expect(body.run_id).toBe('run_1');
    expect(body.thread_id).toBeUndefined();
  });

  it('falls back to thread_id for a legacy run', async () => {
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));
    await bridgeInvoke('cancel_deep_quant_agent', { thread_id: THREAD });
    expect(bodyOf(callsTo(fetchMock, '/api/deepquant/cancel')[0]).thread_id).toBe(THREAD);
  });

  it('does not POST when there is nothing to name yet', async () => {
    // A cancel pressed before RUN_STARTED has been routed. The local abort is the whole
    // stop; posting an identifier we do not have would be a guess.
    fetchMock.mockResolvedValue(new Response('{}', { status: 200 }));
    await bridgeInvoke('cancel_deep_quant_agent', { session_id: SESSION });
    expect(callsTo(fetchMock, '/api/deepquant/cancel')).toHaveLength(0);
  });

  it('surfaces a failed cancel rather than swallowing it', async () => {
    // A silently failed cancel leaves the run burning LLM credits while the UI shows it
    // stopped.
    fetchMock.mockResolvedValue(new Response('{"error":"nope"}', { status: 500 }));
    await expect(
      bridgeInvoke('cancel_deep_quant_agent', { run_id: 'run_1' }),
    ).rejects.toThrow();
  });
});

describe('backward compatibility', () => {
  it('the legacy run path still mints a thread id and works', async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).includes('/api/tools/')) return new Response('{}', { status: 200 });
      return sseResponse([frame('RUN_FINISHED', { status: 'completed' })]);
    });

    const threadId = await bridgeInvoke<string>('run_deep_quant_agent', {
      symbol: 'RELIANCE',
      mode: 'FIND',
    });
    expect(threadId).toMatch(/^thread_RELIANCE_\d+$/);
    await settle();
    const body = bodyOf(callsTo(fetchMock, '/api/deepquant/run')[0]);
    expect(body.thread_id).toBe(threadId);
    expect(body.session_id).toBeUndefined();
  });

  it('the legacy qa path still sends thread_id', async () => {
    fetchMock.mockResolvedValue(sseResponse([frame('RUN_FINISHED', { status: 'completed' })]));
    await bridgeInvoke('ask_trade_question', { thread_id: THREAD, question: 'why?' });
    await settle();
    const body = bodyOf(callsTo(fetchMock, '/api/deepquant/qa')[0]);
    expect(body.thread_id).toBe(THREAD);
    expect(body.session_id).toBeUndefined();
  });
});
