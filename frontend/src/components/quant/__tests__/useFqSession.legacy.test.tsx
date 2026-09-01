// @vitest-environment jsdom
//
// components/quant/__tests__/useFqSession.legacy.test.tsx
//
// The hook layer with `FQ_MULTI_SESSION` OFF — which is what ships today.
//
// The migration's whole premise is that routing every component read through `useFq*` changes
// nothing until the flag is flipped. That is only true if the off path reads the legacy flat
// fields, so this file exists to keep the claim honest. The flag is left at its default rather
// than mocked; the on path is covered in `useFqSession.multiSession.test.tsx`.

import React from 'react';
import { act, render } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { useQuantStore } from '../../../store/useQuantStore';
import { useSessionStore } from '../../../store/useSessionStore';
import {
  useFqActiveSessionId,
  useFqAnalysisError,
  useFqCanAskQuestion,
  useFqDraft,
  useFqQaMessages,
  useFqQaStatus,
  useFqReasoningSteps,
  useFqRunId,
  useFqSessionStatus,
  useFqThreadId,
} from '../useFqSession';

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
  };
}

const THREAD = 'thread_LLLLLLLLLLLLLLLLLLLLLLLL';

beforeEach(() => {
  useSessionStore.getState().reset();
  useQuantStore.setState({
    sessionStatus: 'idle',
    reasoningSteps: [],
    qaMessages: [],
    qaStatus: 'idle',
    currentThreadId: null,
    analysisError: null,
  });
});

describe('reads come from the flat store', () => {
  it('reflects the legacy fields, not the session store', () => {
    const status = harness(useFqSessionStatus);
    const thread = harness(useFqThreadId);
    const error = harness(useFqAnalysisError);

    act(() => {
      useQuantStore.setState({
        sessionStatus: 'complete',
        currentThreadId: THREAD,
        analysisError: 'boom',
      });
    });

    expect(status.current).toBe('complete');
    expect(thread.current).toBe(THREAD);
    expect(error.current).toBe('boom');
  });

  it('ignores session-store state entirely while the flag is off', () => {
    const steps = harness(useFqReasoningSteps);
    const qa = harness(useFqQaMessages);

    // Writing into the new store must have no visible effect: until the flag flips, the session
    // store is not the source of truth and a leak either way would be a silent behaviour change.
    act(() => {
      const s = useSessionStore.getState();
      s.upsertSession('sess_X', {
        reasoningSteps: [
          { id: 'step-1', type: 'message', content: 'from the new store', timestamp: 0 },
        ],
        qaMessages: [{ id: 'q1', role: 'user', content: 'new store question' }],
      });
      s.setActiveSession('sess_X');
    });

    expect(steps.current).toEqual([]);
    expect(qa.current).toEqual([]);
  });

  it('reports no session id and no run id, because the legacy path has neither', () => {
    expect(harness(useFqActiveSessionId).current).toBeNull();
    expect(harness(useFqRunId).current).toBeNull();
  });
});

describe('the composer gate matches the old inline expression', () => {
  it.each([
    ['idle', THREAD, 'idle', false],
    ['running', THREAD, 'idle', false],
    ['watching', THREAD, 'idle', true],
    ['complete', THREAD, 'idle', true],
    // No thread means the backend cannot ground the answer, so the control stays locked.
    ['complete', null, 'idle', false],
    // Already answering: a second send would interleave two answers.
    ['complete', THREAD, 'streaming', false],
  ] as const)(
    'status=%s thread=%s qa=%s -> %s',
    (sessionStatus, currentThreadId, qaStatus, expected) => {
      useQuantStore.setState({ sessionStatus, currentThreadId, qaStatus });
      expect(harness(useFqCanAskQuestion).current).toBe(expected);
    },
  );
});

describe('the draft', () => {
  it('round-trips even though the legacy path has no session id', () => {
    // Parked under a fixed key. The read and the write must agree on it, or the textarea would
    // appear to ignore typing.
    const draft = harness(useFqDraft);
    act(() => draft.current[1]('legacy question'));
    expect(draft.current[0]).toBe('legacy question');
  });
});

describe('Q&A status', () => {
  it('follows the legacy field', () => {
    const status = harness(useFqQaStatus);
    act(() => useQuantStore.setState({ qaStatus: 'streaming' }));
    expect(status.current).toBe('streaming');
  });
});
