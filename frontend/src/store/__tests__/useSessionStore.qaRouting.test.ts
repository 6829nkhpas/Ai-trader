// store/__tests__/useSessionStore.qaRouting.test.ts
//
// A Q&A answer is a CHAT turn. Analysis reasoning is GLASS-BOX output. They arrive on the
// same thread, as the same `REASONING` event, and the only thing distinguishing them is the
// `turn` field the backend stamps in `_run_events`.
//
// Without that field the two are indistinguishable on the wire, so a Q&A reply would be
// appended to the reasoning transcript by `applyStreamEvent` — while rehydration, reading the
// stored `qa_answer` message rows, would show the same reply as a chat bubble. One
// conversation would look different live than it does after a reload. These tests pin the
// routing in both directions so that gap cannot silently reopen.

import { beforeEach, describe, expect, it } from 'vitest';

import { useSessionStore } from '../useSessionStore';

const S = 'sess_QQQQQQQQQQQQQQQQQQQQQQQQQQ';
const THREAD = 'thread_QQQQQQQQQQQQQQQQQQQQQQQQ';
const RUN = 'run_QQQQQQQQQQQQQQQQQQQQQQQQQQ';

/** A Q&A frame as the backend emits it: `thread_id`, `run_id` and `turn: 'qa'`. */
function qa(event: string, data: Record<string, unknown> = {}) {
  return { event, data: { thread_id: THREAD, run_id: RUN, turn: 'qa', ...data } };
}

/** An analysis frame: same thread, `turn: 'run'`. */
function run(event: string, data: Record<string, unknown> = {}) {
  return { event, data: { thread_id: THREAD, run_id: RUN, turn: 'run', ...data } };
}

// Read the session map directly rather than through the `selectCurrentSession` selectors:
// these assertions are about a NAMED session, not about whichever one happens to be active,
// and several cases below deliberately switch the active session mid-answer.
function qaMessages(sessionId: string = S) {
  return useSessionStore.getState().sessions[sessionId]?.qaMessages ?? [];
}

function reasoningSteps(sessionId: string = S) {
  return useSessionStore.getState().sessions[sessionId]?.reasoningSteps ?? [];
}

function answers() {
  return qaMessages().filter((m) => m.role === 'assistant');
}

beforeEach(() => {
  useSessionStore.getState().reset();
  useSessionStore.getState().upsertSession(S);
  useSessionStore.getState().bindThread(THREAD, S, RUN);
});

describe('turn routing', () => {
  it('sends a qa REASONING frame to the chat, not the glass box', () => {
    useSessionStore.getState().applyFrame(qa('REASONING', { content: 'Because IV is elevated.' }));

    expect(answers().map((m) => m.content)).toEqual(['Because IV is elevated.']);
    // The critical half of the assertion: it did NOT also land in the transcript.
    expect(reasoningSteps()).toEqual([]);
  });

  it('sends a run REASONING frame to the glass box, not the chat', () => {
    useSessionStore.getState().applyFrame(run('REASONING', { content: 'Scanning 50 symbols.' }));

    expect(reasoningSteps().length).toBeGreaterThan(0);
    expect(qaMessages()).toEqual([]);
  });

  it('keeps analysis and Q&A on one thread from bleeding into each other', () => {
    const s = useSessionStore.getState();
    s.applyFrame(run('REASONING', { content: 'analysis-one ' }));
    s.applyFrame(qa('REASONING', { content: 'answer-one ' }));
    s.applyFrame(run('REASONING', { content: 'analysis-two' }));
    s.applyFrame(qa('REASONING', { content: 'answer-two' }));

    expect(answers().map((m) => m.content)).toEqual(['answer-one answer-two']);
    const transcript = reasoningSteps()
      .filter((step) => step.type === 'message')
      .map((step) => step.content)
      .join('');
    expect(transcript).toBe('analysis-one analysis-two');
  });

  it('treats an unmarked frame as analysis, so pre-migration streams still work', () => {
    // Older backends emit no `turn`. Defaulting to the reasoning path preserves exactly the
    // behaviour those clients had, rather than silently rerouting their frames.
    useSessionStore.getState().applyFrame({ event: 'REASONING', data: { thread_id: THREAD, content: 'legacy' } });

    expect(reasoningSteps().length).toBeGreaterThan(0);
    expect(qaMessages()).toEqual([]);
  });
});

describe('assembling one answer', () => {
  it('coalesces chunks into a single turn instead of one bubble per chunk', () => {
    const s = useSessionStore.getState();
    s.applyFrame(qa('REASONING', { content: 'The ' }));
    s.applyFrame(qa('REASONING', { content: 'setup ' }));
    s.applyFrame(qa('TEXT_MESSAGE', { content: 'holds.' }));

    expect(answers()).toHaveLength(1);
    expect(answers()[0].content).toBe('The setup holds.');
  });

  it('creates the turn on demand when the optimistic insert was missed', () => {
    // A reattach, or a Q&A resumed in another tab: no client-side placeholder exists. The
    // answer must still render rather than being dropped on the floor.
    useSessionStore.getState().applyFrame(qa('REASONING', { content: 'recovered' }));

    expect(answers()[0].content).toBe('recovered');
    expect(answers()[0].id).toBe(`qa-${RUN}`);
  });

  it('derives the turn id from the run, so a session switch mid-answer keeps the rest', () => {
    const OTHER = 'sess_OOOOOOOOOOOOOOOOOOOOOOOOOO';
    const s = useSessionStore.getState();
    s.upsertSession(OTHER);
    s.applyFrame(qa('REASONING', { content: 'first-half ' }));

    // The user looks at another session. The old implementation matched chunks against an id
    // held in a React closure, so the remainder of the answer went nowhere.
    s.setActiveSession(OTHER);
    s.applyFrame(qa('REASONING', { content: 'second-half' }));

    expect(answers()).toHaveLength(1);
    expect(answers()[0].content).toBe('first-half second-half');
    expect(qaMessages(OTHER)).toEqual([]);
  });

  it('records tool activity against the answer', () => {
    const s = useSessionStore.getState();
    s.applyFrame(qa('TOOL_CALL_START', { tool: 'get_quote' }));
    s.applyFrame(qa('TOOL_CALL_END', { tool: 'get_quote' }));

    expect(answers()[0].activity).toEqual(['> get_quote…', 'get_quote']);
  });
});

describe('terminals', () => {
  it('unlocks the composer when the answer finishes', () => {
    const s = useSessionStore.getState();
    s.applyFrame(qa('REASONING', { content: 'done' }));
    expect(useSessionStore.getState().sessions[S].qaStatus).toBe('streaming');

    s.applyFrame(qa('RUN_FINISHED'));

    expect(answers()[0].streaming).toBe(false);
    expect(useSessionStore.getState().sessions[S].qaStatus).toBe('idle');
  });

  it('stays finished when RUN_FINISHED is replayed', () => {
    const s = useSessionStore.getState();
    s.applyFrame(qa('REASONING', { content: 'done' }));
    s.applyFrame(qa('RUN_FINISHED'));
    // A reattach replays the tail of the stream. Flipping `streaming` back on would lock the
    // composer with nothing left to arrive to unlock it.
    s.applyFrame(qa('RUN_FINISHED'));

    expect(answers()[0].streaming).toBe(false);
    expect(useSessionStore.getState().sessions[S].qaStatus).toBe('idle');
  });

  it('keeps the partial answer when the run errors', () => {
    const s = useSessionStore.getState();
    s.applyFrame(qa('REASONING', { content: 'Half an ans' }));
    s.applyFrame(qa('ERROR', { error: 'upstream timeout' }));

    // A partial answer plus a visible failure beats an empty bubble.
    expect(answers()[0].content).toBe('Half an ans');
    expect(answers()[0].error).toBe(true);
    expect(answers()[0].streaming).toBe(false);
    expect(useSessionStore.getState().sessions[S].qaStatus).toBe('idle');
  });

  it('shows the reason when the run errors before any text arrived', () => {
    useSessionStore.getState().applyFrame(qa('ERROR', { error: 'model refused' }));

    expect(answers()[0].content).toBe('model refused');
    expect(answers()[0].error).toBe(true);
  });
});

describe('isolation', () => {
  it('never writes a Q&A answer into an unrouted session', () => {
    const OTHER = 'sess_ZZZZZZZZZZZZZZZZZZZZZZZZZZ';
    useSessionStore.getState().upsertSession(OTHER);

    const routed = useSessionStore
      .getState()
      .applyFrame({ event: 'REASONING', data: { thread_id: 'thread_unknown', turn: 'qa', content: 'x' } });

    expect(routed).toBeNull();
    expect(useSessionStore.getState().unroutableFrames).toBe(1);
    expect(qaMessages(OTHER)).toEqual([]);
    expect(qaMessages()).toEqual([]);
  });
});
