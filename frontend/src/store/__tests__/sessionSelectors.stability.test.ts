// store/__tests__/sessionSelectors.stability.test.ts
//
// Selectors that return an object or an array MUST return a stable reference when nothing
// they depend on changed.
//
// zustand's `useStore` runs on `useSyncExternalStore`, which calls the selector on every
// render and compares the result with `Object.is`. A selector that constructs a fresh value
// each call never compares equal, so React raises "The result of getSnapshot should be cached
// to avoid an infinite loop" — and for these selectors that is a real render loop, not a
// warning.
//
// The empty case matters most: "no active session" is the INITIAL state of the multi-session
// path, so an unstable fallback fails on the first paint of every component reading it, not in
// some rare corner. Asserted at the selector level because that is where the bug lives; a
// component test would only show the symptom.

import { beforeEach, describe, expect, it } from 'vitest';

import { useSessionStore } from '../useSessionStore';
import {
  selectCurrentSession,
  selectCurrentStream,
  selectCurrentUi,
  selectQaMessages,
  selectReasoningSteps,
  selectSession,
  selectStreamingSessionIds,
  selectVerification,
} from '../sessionSelectors';

const A = 'sess_SSSSSSSSSSSSSSSSSSSSSSSSSS';
const B = 'sess_TTTTTTTTTTTTTTTTTTTTTTTTTT';
const THREAD_A = 'thread_SSSSSSSSSSSSSSSSSSSSSSSS';

const state = () => useSessionStore.getState();

beforeEach(() => {
  useSessionStore.getState().reset();
});

describe('the empty case is reference-stable', () => {
  // Every object/array-valued selector, with no active session — the first-paint state.
  const cases: Array<[string, (s: ReturnType<typeof state>) => unknown]> = [
    ['selectCurrentSession', selectCurrentSession],
    ['selectCurrentStream', selectCurrentStream],
    ['selectCurrentUi', selectCurrentUi],
    ['selectReasoningSteps', selectReasoningSteps],
    ['selectQaMessages', selectQaMessages],
    ['selectVerification', selectVerification],
    ['selectStreamingSessionIds', selectStreamingSessionIds],
  ];

  for (const [name, select] of cases) {
    it(`${name} returns the same reference twice`, () => {
      expect(select(state())).toBe(select(state()));
    });
  }

  it('selectSession returns the same reference for an unknown id', () => {
    const select = selectSession('sess_NOPE');
    expect(select(state())).toBe(select(state()));
  });

  it('selectSession returns the same reference for a null id', () => {
    const select = selectSession(null);
    expect(select(state())).toBe(select(state()));
  });
});

describe('the empty case cannot be corrupted by a consumer', () => {
  it('is frozen, so a stray mutation fails loudly instead of poisoning every reader', () => {
    // The empty value is SHARED. Were it mutable, one component pushing onto it would change
    // what every other component sees as "empty", with nothing pointing at the culprit.
    const empty = selectCurrentSession(state());
    expect(Object.isFrozen(empty)).toBe(true);
  });
});

describe('a real session is still tracked', () => {
  it('changes reference when the session changes, and not otherwise', () => {
    const s = state();
    s.upsertSession(A);
    s.setActiveSession(A);
    s.bindThread(THREAD_A, A);

    const before = selectReasoningSteps(state());
    // A frame for this session must produce a NEW reference, or the UI would not repaint.
    s.applyFrame({ event: 'REASONING', data: { thread_id: THREAD_A, content: 'hello' } });
    const after = selectReasoningSteps(state());

    expect(after).not.toBe(before);
    expect(after.length).toBeGreaterThan(0);
    // A second read with no intervening write must be identical again.
    expect(selectReasoningSteps(state())).toBe(after);
  });

  it('does not re-render the tab bar when an unrelated field changes', () => {
    const s = state();
    s.upsertSession(A);
    s.upsertSession(B);
    s.setActiveSession(A);
    s.bindThread(THREAD_A, A);
    s.applyFrame({ event: 'RUN_STARTED', data: { thread_id: THREAD_A } });

    const before = selectStreamingSessionIds(state());
    expect(before).toContain(A);

    // Reasoning text is not part of the streaming-id set, so the set must keep its identity
    // even though the sessions map was replaced.
    s.applyFrame({ event: 'REASONING', data: { thread_id: THREAD_A, content: 'more' } });

    expect(selectStreamingSessionIds(state())).toBe(before);
  });

  it('changes reference when a session starts or stops streaming', () => {
    const s = state();
    s.upsertSession(A);
    s.setActiveSession(A);
    s.bindThread(THREAD_A, A);

    const idle = selectStreamingSessionIds(state());
    s.applyFrame({ event: 'RUN_STARTED', data: { thread_id: THREAD_A } });
    const streaming = selectStreamingSessionIds(state());

    expect(streaming).not.toBe(idle);
    expect(streaming).toEqual([A]);
  });

  it('survives a reset without handing back a stale cached array', () => {
    const s = state();
    s.upsertSession(A);
    s.setActiveSession(A);
    s.bindThread(THREAD_A, A);
    s.applyFrame({ event: 'RUN_STARTED', data: { thread_id: THREAD_A } });
    expect(selectStreamingSessionIds(state())).toEqual([A]);

    useSessionStore.getState().reset();

    expect(selectStreamingSessionIds(state())).toEqual([]);
  });
});
