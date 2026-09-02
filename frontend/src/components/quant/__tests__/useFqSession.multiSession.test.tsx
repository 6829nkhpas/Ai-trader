// @vitest-environment jsdom
//
// components/quant/__tests__/useFqSession.multiSession.test.tsx
//
// The hook layer with `FQ_MULTI_SESSION` ON.
//
// `FQ_MULTI_SESSION` is a module constant read at import time, so the two paths cannot be
// exercised in one file — the flag is mocked here and left at its default (off) in
// `useFqSession.legacy.test.tsx`. Both files matter: the flag decides which one ships, and a
// layer that only works on the path nobody has enabled yet is not a migration.

import React from 'react';
import { act, render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../lib/env', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../lib/env')>()),
  FQ_MULTI_SESSION: true,
}));

import { useSessionStore } from '../../../store/useSessionStore';
import {
  useFqDraft,
  useFqMode,
  useFqQaMessages,
  useFqReasoningSteps,
  useFqSessionStatus,
  useFqThreadId,
} from '../useFqSession';

const A = 'sess_AAAAAAAAAAAAAAAAAAAAAAAAAA';
const B = 'sess_BBBBBBBBBBBBBBBBBBBBBBBBBB';
const THREAD_A = 'thread_AAAAAAAAAAAAAAAAAAAAAAAA';

/** Render a hook and expose its latest value, without pulling in a hook-testing dependency. */
function harness<T>(hook: () => T) {
  const seen: T[] = [];
  function Probe() {
    seen.push(hook());
    return null;
  }
  render(<Probe />);
  return {
    get current() {
      return seen[seen.length - 1];
    },
    get renders() {
      return seen.length;
    },
  };
}

beforeEach(() => {
  useSessionStore.getState().reset();
});

describe('reads resolve the ACTIVE session', () => {
  beforeEach(() => {
    const s = useSessionStore.getState();
    s.upsertSession(A);
    s.upsertSession(B);
    s.bindThread(THREAD_A, A);
    s.setActiveSession(A);
  });

  it('shows the active session, not a global', () => {
    const status = harness(useFqSessionStatus);
    const thread = harness(useFqThreadId);

    act(() => {
      useSessionStore.getState().applyFrame({ event: 'RUN_STARTED', data: { thread_id: THREAD_A } });
    });

    expect(status.current).toBe('running');
    expect(thread.current).toBe(THREAD_A);
  });

  it('switches what it shows when the active session changes', () => {
    const steps = harness(useFqReasoningSteps);
    act(() => {
      useSessionStore
        .getState()
        .applyFrame({ event: 'REASONING', data: { thread_id: THREAD_A, content: 'for A' } });
    });
    expect(steps.current.length).toBeGreaterThan(0);

    // B has its own state. The old flat mirror would have shown A's transcript here, because
    // there was only ever one.
    act(() => {
      useSessionStore.getState().setActiveSession(B);
    });
    expect(steps.current).toEqual([]);
  });

  it('does not re-render for a frame belonging to another session', () => {
    const THREAD_B = 'thread_BBBBBBBBBBBBBBBBBBBBBBBB';
    useSessionStore.getState().bindThread(THREAD_B, B);
    const steps = harness(useFqReasoningSteps);
    const before = steps.renders;

    act(() => {
      useSessionStore
        .getState()
        .applyFrame({ event: 'REASONING', data: { thread_id: THREAD_B, content: 'for B' } });
    });

    // The selected value is unchanged, so zustand must not re-render. This is the property that
    // makes eight concurrent sessions viable rather than merely correct.
    expect(steps.renders).toBe(before);
    expect(steps.current).toEqual([]);
  });

  it('routes a Q&A answer to the chat, through the same layer', () => {
    const qa = harness(useFqQaMessages);
    const steps = harness(useFqReasoningSteps);

    act(() => {
      useSessionStore.getState().applyFrame({
        event: 'REASONING',
        data: { thread_id: THREAD_A, run_id: 'run_1', turn: 'qa', content: 'because IV' },
      });
    });

    expect(qa.current.map((m) => m.content)).toEqual(['because IV']);
    expect(steps.current).toEqual([]);
  });
});

describe('per-session UI state', () => {
  beforeEach(() => {
    const s = useSessionStore.getState();
    s.upsertSession(A);
    s.upsertSession(B);
    s.setActiveSession(A);
  });

  it('keeps a half-typed question when the user switches away and back', () => {
    const draft = harness(useFqDraft);

    act(() => draft.current[1]('why is the stop so tight'));
    expect(draft.current[0]).toBe('why is the stop so tight');

    // A component-local `useState` was shared by every session, so this switch destroyed the
    // text with no way to recover it.
    act(() => useSessionStore.getState().setActiveSession(B));
    expect(draft.current[0]).toBe('');

    act(() => useSessionStore.getState().setActiveSession(A));
    expect(draft.current[0]).toBe('why is the stop so tight');
  });

  it('keeps each session on its own mode', () => {
    const mode = harness(useFqMode);

    act(() => mode.current[1]('VERIFY'));
    expect(mode.current[0]).toBe('VERIFY');

    act(() => useSessionStore.getState().setActiveSession(B));
    expect(mode.current[0]).toBe('FIND');

    act(() => useSessionStore.getState().setActiveSession(A));
    expect(mode.current[0]).toBe('VERIFY');
  });

  it('writes to the slot it reads from', () => {
    // Read and write must resolve the same key. Selecting via `activeSessionId` while writing to
    // a different key produces a control that appears inert.
    const draft = harness(useFqDraft);
    act(() => draft.current[1]('x'));
    expect(useSessionStore.getState().ui[A].draft).toBe('x');
  });
});

describe('with no session selected', () => {
  it('renders the empty state instead of crashing', () => {
    // The FIRST paint of the multi-session path, before any session is created. The selectors
    // must hand back stable empty values here or React rejects the uncached snapshot.
    const steps = harness(useFqReasoningSteps);
    const status = harness(useFqSessionStatus);
    const thread = harness(useFqThreadId);

    expect(steps.current).toEqual([]);
    expect(status.current).toBe('idle');
    expect(thread.current).toBeNull();
  });
});
