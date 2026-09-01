// store/__tests__/useSessionStore.routing.test.ts
//
// The hard invariant: **a frame belonging to session A can never modify session B.**
//
// This is the test that has to pass before the tab UI can ship. The old routing resolved
// `runKey = _streamingKey || activeViewKey` in both the `RUN_STARTED` branch and the `else`
// branch, so a frame whose thread was unknown landed in whatever the user was looking at —
// and it looked like data, so nobody could see it happen. Everything here exists to make
// that failure loud instead.
//
// The reducer itself is NOT retested; it has four property suites of its own. What is
// tested is routing, isolation, and per-session state.

import { beforeEach, describe, expect, it } from 'vitest';

import { useSessionStore, blankUi } from '../useSessionStore';
import {
  selectCanAskQuestion,
  selectCurrentSession,
  selectCurrentThreadId,
  selectQaMessages,
  selectReasoningSteps,
  selectStreamingSessionIds,
} from '../sessionSelectors';

const A = 'sess_AAAAAAAAAAAAAAAAAAAAAAAAAA';
const B = 'sess_BBBBBBBBBBBBBBBBBBBBBBBBBB';
const THREAD_A = 'thread_AAAAAAAAAAAAAAAAAAAAAAAAAA';
const THREAD_B = 'thread_BBBBBBBBBBBBBBBBBBBBBBBBBB';

/** A frame as the backend actually emits it: `thread_id` on every payload. */
function frame(event: string, threadId: string, data: Record<string, unknown> = {}) {
  return { event, data: { thread_id: threadId, ...data } };
}

function reasoningText(sessionId: string): string {
  return (useSessionStore.getState().sessions[sessionId]?.reasoningSteps ?? [])
    .filter((s) => s.type === 'message')
    .map((s) => s.content)
    .join('');
}

beforeEach(() => {
  useSessionStore.getState().reset();
});

// ── Routing ───────────────────────────────────────────────────────────────────

describe('routing', () => {
  it('routes a frame to the session its thread is bound to', () => {
    const store = useSessionStore.getState();
    store.bindThread(THREAD_A, A);
    expect(store.applyFrame(frame('RUN_STARTED', THREAD_A))).toBe(A);
    expect(useSessionStore.getState().sessions[A].sessionStatus).toBe('running');
  });

  it('drops an unroutable frame and counts it, mutating nothing', () => {
    // Under the old fallback this frame was written into the active session.
    const store = useSessionStore.getState();
    store.setActiveSession(A);
    expect(store.applyFrame(frame('REASONING', 'thread_UNBOUND', { content: 'stray' }))).toBeNull();

    const after = useSessionStore.getState();
    expect(after.unroutableFrames).toBe(1);
    expect(reasoningText(A)).toBe('');
    expect(after.sessions[A].sessionStatus).toBe('idle');
  });

  it('drops a frame with no thread_id at all', () => {
    const store = useSessionStore.getState();
    store.setActiveSession(A);
    expect(store.applyFrame({ event: 'REASONING', data: { content: 'no thread' } })).toBeNull();
    expect(useSessionStore.getState().unroutableFrames).toBe(1);
    expect(reasoningText(A)).toBe('');
  });

  it('never falls back to the active session, even when exactly one session exists', () => {
    // The tempting special case. One session and one unbound frame looks unambiguous, and
    // routing it would be right most of the time — which is exactly what makes the
    // occasional wrong case impossible to notice.
    const store = useSessionStore.getState();
    store.setActiveSession(A);
    store.applyFrame(frame('DECISION', 'thread_SOMEONE_ELSE', { action: 'BUY' }));
    expect(useSessionStore.getState().sessions[A].finalTrade).toBeNull();
  });

  it.each([null, undefined, {}, { event: '' }])('ignores a malformed payload (%p)', (payload) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect(useSessionStore.getState().applyFrame(payload as any)).toBeNull();
  });

  it('binds its own thread from RUN_STARTED when the POST response was missed', () => {
    // A client that reconnected, or one whose POST response was lost, would otherwise drop
    // the entire run rather than one frame.
    const store = useSessionStore.getState();
    const routed = store.applyFrame(
      frame('RUN_STARTED', THREAD_A, { session_id: A, run_id: 'run_1' }),
    );
    expect(routed).toBe(A);
    const after = useSessionStore.getState();
    expect(after.threadToSession[THREAD_A]).toBe(A);
    expect(after.streams[A].runId).toBe('run_1');
    expect(after.unroutableFrames).toBe(0);
  });

  it('never lets a frame retarget a thread that is already bound', () => {
    // THE invariant self-binding must not break. Once a thread belongs to a session, no
    // payload may move it: a frame claiming a different `session_id` mid-stream would split
    // one run's transcript across two sessions, and the half that moved would look like it
    // belonged to a conversation the user never had.
    const store = useSessionStore.getState();
    store.bindThread(THREAD_A, A);

    const routed = store.applyFrame(frame('REASONING', THREAD_A, { session_id: B, content: 'x' }));

    expect(routed).toBe(A);
    expect(useSessionStore.getState().threadToSession[THREAD_A]).toBe(A);
    expect(reasoningText(A)).toBe('x');
    expect(reasoningText(B)).toBe('');
  });

  it('lets an UNBOUND thread self-bind from a frame that names its session', () => {
    // Widened from "RUN_STARTED only", deliberately.
    //
    // The earlier rule was written to stop a mid-stream frame retargeting a thread — which the
    // test above now pins directly, and which this cannot do because it only fires when the
    // thread has no binding at all. Restricting it to `RUN_STARTED` had a cost: the bridge
    // synthesizes a terminal frame when a stream ends without `RUN_FINISHED`, and builds error
    // frames from a caught exception with no thread in scope. Those were unroutable, so a Q&A
    // whose stream died left the composer locked with nothing able to unlock it.
    const store = useSessionStore.getState();

    const routed = store.applyFrame(frame('REASONING', THREAD_A, { session_id: A, content: 'x' }));

    expect(routed).toBe(A);
    expect(useSessionStore.getState().threadToSession[THREAD_A]).toBe(A);
    expect(reasoningText(A)).toBe('x');
  });

  it('routes a threadless frame that names its session, without poisoning the table', () => {
    // A locally synthesized terminal has no thread id at all. It must still reach its session,
    // and it must not write an empty key into the routing table — a `''` entry would swallow
    // every later frame whose thread id was missing.
    const store = useSessionStore.getState();

    const routed = store.applyFrame({ event: 'RUN_FINISHED', data: { session_id: A, status: 'completed' } });

    expect(routed).toBe(A);
    expect(useSessionStore.getState().threadToSession['']).toBeUndefined();
    expect(useSessionStore.getState().unroutableFrames).toBe(0);
  });

  it('still drops a frame that names neither a known thread nor a session', () => {
    const store = useSessionStore.getState();
    expect(store.applyFrame(frame('DECISION', THREAD_A))).toBeNull();
    expect(useSessionStore.getState().threadToSession[THREAD_A]).toBeUndefined();
    expect(useSessionStore.getState().unroutableFrames).toBe(1);
  });

  it('binding is idempotent and does not disturb accumulated state', () => {
    const store = useSessionStore.getState();
    store.bindThread(THREAD_A, A);
    store.applyFrame(frame('RUN_STARTED', THREAD_A));
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'hello' }));
    store.bindThread(THREAD_A, A);
    expect(reasoningText(A)).toBe('hello');
  });

  it('ignores a bind with a missing id', () => {
    const store = useSessionStore.getState();
    store.bindThread('', A);
    store.bindThread(THREAD_A, '');
    expect(useSessionStore.getState().threadToSession).toEqual({});
  });

  it('tracks the high-water seq for gap recovery', () => {
    const store = useSessionStore.getState();
    store.bindThread(THREAD_A, A);
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'a', seq: 3 }));
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'b', seq: 7 }));
    // An out-of-order or replayed frame must not lower the mark, or a reattach would ask
    // for frames it already has.
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'c', seq: 2 }));
    expect(useSessionStore.getState().streams[A].lastSeq).toBe(7);
  });

  it('tolerates frames with no seq (persistence disabled)', () => {
    const store = useSessionStore.getState();
    store.bindThread(THREAD_A, A);
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'a' }));
    expect(useSessionStore.getState().streams[A].lastSeq).toBe(0);
    expect(reasoningText(A)).toBe('a');
  });
});

// ── Isolation ─────────────────────────────────────────────────────────────────

describe('isolation between concurrent sessions', () => {
  beforeEach(() => {
    const store = useSessionStore.getState();
    store.bindThread(THREAD_A, A);
    store.bindThread(THREAD_B, B);
  });

  it('two concurrent runs accumulate independently', () => {
    const store = useSessionStore.getState();
    store.applyFrame(frame('RUN_STARTED', THREAD_A));
    store.applyFrame(frame('RUN_STARTED', THREAD_B));
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'A1' }));
    store.applyFrame(frame('REASONING', THREAD_B, { content: 'B1' }));
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'A2' }));

    expect(reasoningText(A)).toBe('A1A2');
    expect(reasoningText(B)).toBe('B1');
  });

  it('A keeps receiving while B is on screen', () => {
    // The scenario the whole store exists for: switch tab, and the background run must keep
    // filling its own session rather than the visible one.
    const store = useSessionStore.getState();
    store.setActiveSession(B);
    store.applyFrame(frame('RUN_STARTED', THREAD_A));
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'background' }));

    expect(reasoningText(A)).toBe('background');
    expect(reasoningText(B)).toBe('');
    expect(useSessionStore.getState().activeSessionId).toBe(B);
  });

  it('switching away and back shows A unchanged, with no snapshot step', () => {
    const store = useSessionStore.getState();
    store.setActiveSession(A);
    store.applyFrame(frame('RUN_STARTED', THREAD_A));
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'A content' }));

    store.setActiveSession(B);
    store.setActiveSession(A);

    // `activateSymbolSession` had to copy the flat mirror back into the archive on every
    // switch, and three actions bypassed that copy. There is nothing to copy here.
    expect(selectReasoningSteps(useSessionStore.getState()).map((s) => s.content).join('')).toBe(
      'A content',
    );
  });

  it("A's terminal event does not finish B", () => {
    const store = useSessionStore.getState();
    store.applyFrame(frame('RUN_STARTED', THREAD_A));
    store.applyFrame(frame('RUN_STARTED', THREAD_B));
    store.applyFrame(frame('RUN_FINISHED', THREAD_A, { status: 'completed' }));

    const after = useSessionStore.getState();
    expect(after.sessions[A].sessionStatus).toBe('complete');
    expect(after.sessions[B].sessionStatus).toBe('running');
    expect(after.sessions[B].isAnalyzing).toBe(true);
  });

  it("A's error does not error B", () => {
    const store = useSessionStore.getState();
    store.applyFrame(frame('RUN_STARTED', THREAD_A));
    store.applyFrame(frame('RUN_STARTED', THREAD_B));
    store.applyFrame(frame('ERROR', THREAD_A, { error: 'A failed' }));

    const after = useSessionStore.getState();
    expect(after.sessions[A].sessionStatus).toBe('error');
    expect(after.sessions[A].analysisError).toBe('A failed');
    expect(after.sessions[B].sessionStatus).toBe('running');
    expect(after.sessions[B].analysisError).toBeNull();
  });

  it("A's decision does not appear in B", () => {
    const store = useSessionStore.getState();
    store.applyFrame(frame('DECISION', THREAD_A, {
      action: 'BUY',
      conviction_score: 78,
      execution_levels: { entry: 2470, stop_loss: 2435, take_profit: 2550 },
    }));

    const after = useSessionStore.getState();
    expect(after.sessions[A].finalTrade?.action).toBe('BUY');
    expect(after.sessions[B]?.finalTrade ?? null).toBeNull();
  });

  it('the same symbol in two sessions stays independent', () => {
    // The product fix. `${SYMBOL}::${PROFILE}` collided, so RELIANCE at 10m and RELIANCE at
    // 5m could not coexist — the second FIND overwrote the first.
    const store = useSessionStore.getState();
    store.applyFrame(frame('RUN_STARTED', THREAD_A));
    store.applyFrame(frame('REASONING', THREAD_A, { content: '10m read' }));
    store.applyFrame(frame('RUN_STARTED', THREAD_B));
    store.applyFrame(frame('REASONING', THREAD_B, { content: '5m read' }));

    expect(reasoningText(A)).toBe('10m read');
    expect(reasoningText(B)).toBe('5m read');
  });

  it('reports which sessions are streaming, stably', () => {
    const store = useSessionStore.getState();
    store.applyFrame(frame('RUN_STARTED', THREAD_A));
    store.applyFrame(frame('RUN_STARTED', THREAD_B));
    expect(selectStreamingSessionIds(useSessionStore.getState())).toEqual([A, B].sort());

    store.applyFrame(frame('RUN_FINISHED', THREAD_A, { status: 'completed' }));
    expect(selectStreamingSessionIds(useSessionStore.getState())).toEqual([B]);
  });
});

// ── Q&A: per session, not process-wide ────────────────────────────────────────

describe('Q&A isolation', () => {
  beforeEach(() => {
    const store = useSessionStore.getState();
    store.bindThread(THREAD_A, A);
    store.bindThread(THREAD_B, B);
    // Both complete, so both composers are unlocked.
    for (const t of [THREAD_A, THREAD_B]) {
      store.applyFrame(frame('RUN_STARTED', t));
      store.applyFrame(frame('RUN_FINISHED', t, { status: 'completed' }));
    }
  });

  it('two sessions can both be asking at once', () => {
    // `qaStatus` was a flat field, so a Q&A on one session blocked a Q&A on every other —
    // process-wide, not per session.
    const store = useSessionStore.getState();
    store.upsertSession(A, { qaStatus: 'streaming' });
    store.upsertSession(B, { qaStatus: 'streaming' });

    const after = useSessionStore.getState();
    expect(after.sessions[A].qaStatus).toBe('streaming');
    expect(after.sessions[B].qaStatus).toBe('streaming');
  });

  it("a Q&A answer for A does not append to B", () => {
    const store = useSessionStore.getState();
    store.upsertSession(A, {
      qaMessages: [{ id: 'q1', role: 'user', content: 'why that stop?' }],
    });
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'because ATR' }));

    const after = useSessionStore.getState();
    expect(after.sessions[A].qaMessages).toHaveLength(1);
    expect(reasoningText(A)).toContain('because ATR');
    expect(reasoningText(B)).toBe('');
    expect(after.sessions[B].qaMessages).toEqual([]);
  });

  it("A's streaming Q&A does not block B's composer", () => {
    const store = useSessionStore.getState();
    store.upsertSession(A, { qaStatus: 'streaming' });
    store.setActiveSession(B);
    expect(selectCanAskQuestion(useSessionStore.getState())).toBe(true);

    store.setActiveSession(A);
    expect(selectCanAskQuestion(useSessionStore.getState())).toBe(false);
  });

  it('the composer stays locked until the session has a thread', () => {
    const store = useSessionStore.getState();
    store.setActiveSession('sess_NOTHREAD');
    store.upsertSession('sess_NOTHREAD', { sessionStatus: 'complete' });
    // The backend needs the thread to ground the answer, so offering the control earlier
    // would produce a failure the user cannot act on.
    expect(selectCanAskQuestion(useSessionStore.getState())).toBe(false);
  });

  it('the composer unlocks at watching, not only at complete', () => {
    // A watching run is the case the Q&A feature was built for: chat while the agent waits
    // for the price trigger.
    const store = useSessionStore.getState();
    store.setActiveSession(A);
    store.upsertSession(A, { sessionStatus: 'watching' });
    expect(selectCanAskQuestion(useSessionStore.getState())).toBe(true);
  });
});

// ── Per-session UI state ──────────────────────────────────────────────────────

describe('per-session UI state', () => {
  it('mode, draft and the verification form survive a tab switch', () => {
    // All three were global before: `activeMode` was React state in `DeepQuantPanel`, the
    // draft was `useState` in `TradeQaPanel`, and the verification form reset on SYMBOL
    // change only — so switching session kept another session's numbers on screen.
    const store = useSessionStore.getState();
    store.setActiveSession(A);
    store.setUi(A, { mode: 'VERIFY', draft: 'why that stop?' });
    store.setVerification(A, { entry: '2470', stopLoss: '2435' });

    store.setActiveSession(B);
    expect(useSessionStore.getState().ui[B]).toEqual(blankUi());

    store.setActiveSession(A);
    const ui = useSessionStore.getState().ui[A];
    expect(ui.mode).toBe('VERIFY');
    expect(ui.draft).toBe('why that stop?');
    expect(ui.verification.entry).toBe('2470');
    expect(ui.verification.stopLoss).toBe('2435');
  });

  it('a new session starts with a clean form', () => {
    const store = useSessionStore.getState();
    store.setActiveSession(A);
    store.setVerification(A, { entry: '9999', hasManuallySetEntry: true });
    store.setActiveSession(B);
    expect(useSessionStore.getState().ui[B].verification.entry).toBe('');
    expect(useSessionStore.getState().ui[B].verification.hasManuallySetEntry).toBe(false);
  });

  it('a verification patch does not clobber the rest of the form', () => {
    const store = useSessionStore.getState();
    store.setVerification(A, { entry: '2470' });
    store.setVerification(A, { stopLoss: '2435' });
    const v = useSessionStore.getState().ui[A].verification;
    expect(v.entry).toBe('2470');
    expect(v.stopLoss).toBe('2435');
    expect(v.side).toBe('BUY');
  });

  it('ignores a UI write with no session id', () => {
    const store = useSessionStore.getState();
    store.setUi('', { draft: 'nowhere' });
    store.setVerification('', { entry: '1' });
    expect(useSessionStore.getState().ui).toEqual({});
  });
});

// ── Lifecycle ─────────────────────────────────────────────────────────────────

describe('lifecycle', () => {
  it('activating an unknown session creates blank state rather than crashing', () => {
    useSessionStore.getState().setActiveSession(A);
    const state = useSessionStore.getState();
    expect(state.sessions[A].sessionStatus).toBe('idle');
    expect(state.ui[A]).toEqual(blankUi());
    expect(state.streams[A].threadId).toBeNull();
  });

  it('clearing the active session leaves the others intact', () => {
    const store = useSessionStore.getState();
    store.bindThread(THREAD_A, A);
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'kept' }));
    store.setActiveSession(A);
    store.setActiveSession(null);

    expect(useSessionStore.getState().activeSessionId).toBeNull();
    expect(reasoningText(A)).toBe('kept');
    // A blank session renders as the empty state, which is honest when nothing is selected.
    expect(selectCurrentSession(useSessionStore.getState()).sessionStatus).toBe('idle');
    expect(selectCurrentThreadId(useSessionStore.getState())).toBeNull();
  });

  it('dropping a session removes its state and its thread bindings', () => {
    // The bindings must go too: a late frame from a run still finishing server-side would
    // otherwise resurrect the entry as a bare blank session — which reads as a corrupted
    // session rather than an absent one.
    const store = useSessionStore.getState();
    store.bindThread(THREAD_A, A);
    store.bindThread(THREAD_B, B);
    store.setActiveSession(A);
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'doomed' }));

    store.dropSession(A);

    const after = useSessionStore.getState();
    expect(after.sessions[A]).toBeUndefined();
    expect(after.ui[A]).toBeUndefined();
    expect(after.streams[A]).toBeUndefined();
    expect(after.threadToSession[THREAD_A]).toBeUndefined();
    expect(after.threadToSession[THREAD_B]).toBe(B);
    expect(after.activeSessionId).toBeNull();
  });

  it('a late frame for a dropped session is counted, not resurrected', () => {
    const store = useSessionStore.getState();
    store.bindThread(THREAD_A, A);
    store.dropSession(A);
    expect(store.applyFrame(frame('REASONING', THREAD_A, { content: 'late' }))).toBeNull();
    const after = useSessionStore.getState();
    expect(after.sessions[A]).toBeUndefined();
    expect(after.unroutableFrames).toBe(1);
  });

  it('dropping a non-active session leaves the active one alone', () => {
    const store = useSessionStore.getState();
    store.setActiveSession(A);
    store.upsertSession(B);
    store.dropSession(B);
    expect(useSessionStore.getState().activeSessionId).toBe(A);
  });

  it('replaceSession swaps a session wholesale, for rehydration', () => {
    const store = useSessionStore.getState();
    store.setActiveSession(A);
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'ignored' }));
    store.replaceSession(A, {
      ...selectCurrentSession(useSessionStore.getState()),
      sessionStatus: 'complete',
      reasoningSteps: [{ id: 's1', type: 'message', content: 'restored', timestamp: 1 }],
    });
    expect(reasoningText(A)).toBe('restored');
    expect(useSessionStore.getState().sessions[A].sessionStatus).toBe('complete');
  });

  it('reset clears everything, as a logout must', () => {
    const store = useSessionStore.getState();
    store.bindThread(THREAD_A, A);
    store.setActiveSession(A);
    store.setUi(A, { draft: 'private' });
    store.applyFrame(frame('REASONING', 'thread_UNBOUND', { content: 'x' }));

    store.reset();

    const after = useSessionStore.getState();
    // A new user must not inherit the previous one's tabs, drafts, or transcripts.
    expect(after.sessions).toEqual({});
    expect(after.ui).toEqual({});
    expect(after.streams).toEqual({});
    expect(after.threadToSession).toEqual({});
    expect(after.activeSessionId).toBeNull();
    expect(after.unroutableFrames).toBe(0);
  });
});

// ── Selector behaviour ────────────────────────────────────────────────────────

describe('selectors', () => {
  it('read through to the active session rather than a mirrored copy', () => {
    const store = useSessionStore.getState();
    store.bindThread(THREAD_A, A);
    store.setActiveSession(A);
    store.applyFrame(frame('RUN_STARTED', THREAD_A));
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'live' }));

    const state = useSessionStore.getState();
    expect(selectReasoningSteps(state).map((s) => s.content).join('')).toBe('live');
    expect(selectQaMessages(state)).toEqual([]);
    expect(selectCurrentThreadId(state)).toBe(THREAD_A);
  });

  it('field selectors are reference-stable when an unrelated session changes', () => {
    // Why field selectors exist: a tab must re-render when ITS session changes, not on
    // every frame of every session. Eight streaming tabs otherwise re-render 8x per frame.
    const store = useSessionStore.getState();
    store.bindThread(THREAD_A, A);
    store.bindThread(THREAD_B, B);
    store.setActiveSession(A);
    store.applyFrame(frame('REASONING', THREAD_A, { content: 'A' }));

    const before = selectReasoningSteps(useSessionStore.getState());
    store.applyFrame(frame('REASONING', THREAD_B, { content: 'B' }));
    const after = selectReasoningSteps(useSessionStore.getState());

    expect(after).toBe(before);
  });
});
