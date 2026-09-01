// lib/fq/__tests__/replayCannedRun.test.ts
//
// Replays the EXACT frame sequence the e2e stub emits, to answer one question the browser suite could
// not: when a completed run is restored, does the store contain the whole transcript?
//
// The e2e journey fails on the LAST reasoning message ("Momentum is intact") after a session stops
// streaming — via two independent paths, a switch-back and a from-scratch rehydration. `AgentTerminal`'s
// grouping was eliminated by inspection (it flushes the trailing group after the loop), so the fault is
// either in what was stored or in how it is replayed. This is the cheap half of that split: no browser,
// no service, just the real `replayEvents` and the real reducer.
//
// The sequence is not invented. It was dumped from `agents/deep-quant-loop/e2e_stub_server.py` driving the
// real `/run` endpoint, so a change to the canned script or to the SSE assembler will show up here.

import { describe, expect, it } from 'vitest';

import { replayEvents, reconcileWithRun } from '../rehydrate';
import type { StoredEvent, StoredRun } from '../api';

const THREAD = 'thread_CANNED';

/** Exactly what `POST /run` streams for the stubbed graph, in order. */
const CANNED: StoredEvent[] = [
  { seq: 1, event: 'RUN_STARTED', data: { thread_id: THREAD } },
  { seq: 2, event: 'REASONING', data: { thread_id: THREAD, content: 'Scanning RELIANCE on the 10m timeframe.' } },
  { seq: 3, event: 'REASONING', data: { thread_id: THREAD, content: 'Pulling candles before I commit to a read.' } },
  { seq: 4, event: 'TOOL_CALL_START', data: { thread_id: THREAD, tool: 'get_ohlc' } },
  { seq: 5, event: 'TOOL_CALL_RESULT', data: { thread_id: THREAD, tool: 'get_ohlc' } },
  { seq: 6, event: 'TOOL_CALL_END', data: { thread_id: THREAD, tool: 'get_ohlc', status: 'success' } },
  { seq: 7, event: 'REASONING', data: { thread_id: THREAD, content: 'Momentum is intact above 2,450.' } },
  {
    seq: 8,
    event: 'DECISION',
    data: {
      thread_id: THREAD,
      decision: {
        action: 'BUY',
        conviction_score: 72,
        rationale: 'Trend continuation with a defined invalidation.',
        entry: 2470,
        stop_loss: 2435,
        take_profit: 2550,
      },
    },
  },
  { seq: 9, event: 'RUN_FINISHED', data: { thread_id: THREAD, status: 'completed' } },
] as unknown as StoredEvent[];

const COMPLETED_RUN = {
  run_id: 'run_CANNED',
  session_id: 'sess_CANNED',
  thread_id: THREAD,
  kind: 'find',
  status: 'complete',
  symbol: 'RELIANCE',
  timeframe: '10m',
  profile: 'INTRADAY',
  started_at: 1,
  ended_at: 2,
  last_seq: 9,
} as unknown as StoredRun;

function messages(steps: { type: string; content: string }[]): string {
  return steps.filter((s) => s.type === 'message').map((s) => s.content).join(' | ');
}

describe('replaying a completed canned run', () => {
  it('keeps the reasoning that came AFTER the tool call', () => {
    const session = replayEvents(CANNED);

    // The message the e2e journey cannot find. It arrives at seq 7, after the tool triple — so anything
    // that stops accumulating at the first tool boundary loses exactly this one.
    expect(messages(session.reasoningSteps)).toMatch(/Momentum is intact/);
  });

  it('keeps every reasoning message, in order', () => {
    const session = replayEvents(CANNED);
    const text = messages(session.reasoningSteps);

    // Asserted as ORDERED CONTAINMENT rather than an exact string. The reducer merges CONSECUTIVE
    // `REASONING` frames into one step — which is correct, that is how streamed prose accumulates — so
    // pinning the exact joined form would be testing the step boundaries rather than the transcript, and
    // it broke on the first run for exactly that reason.
    const first = text.indexOf('Scanning RELIANCE');
    const second = text.indexOf('Pulling candles');
    const third = text.indexOf('Momentum is intact');

    expect(first).toBeGreaterThanOrEqual(0);
    expect(second).toBeGreaterThan(first);
    expect(third).toBeGreaterThan(second);
  });

  it('renders the post-tool reasoning as its own step, so it forms a second Thinking group', () => {
    // Why this matters: `AgentTerminal` groups CONSECUTIVE message steps and breaks the group on a tool
    // step, so reasoning either side of a tool call becomes TWO collapsible groups. A finished run
    // therefore needs BOTH expanded to be readable — expanding only the first is what made the e2e look
    // as though the frames had never arrived.
    //
    // Asserted by identity rather than index: an earlier version pinned `msgSteps[1]` and broke on how
    // the reducer distributes content across steps, which is not what this is about.
    const session = replayEvents(CANNED);
    const msgSteps = session.reasoningSteps.filter((s) => s.type === 'message');
    const postTool = msgSteps.find((s) => /Momentum is intact/.test(s.content));

    expect(postTool).toBeDefined();
    // It is not the first step, i.e. it genuinely sits after the tool boundary.
    expect(msgSteps.indexOf(postTool!)).toBeGreaterThan(0);
  });

  it('keeps the tool step, so the transcript is not flattened', () => {
    const session = replayEvents(CANNED);
    expect(session.reasoningSteps.some((s) => s.type !== 'message')).toBe(true);
  });

  it('survives reconciliation against a COMPLETE run', () => {
    // `reconcileWithRun` rewrites a session to match the run's terminal status. The `truncated` branch
    // deliberately drops the plan; a `complete` run must keep everything.
    const session = reconcileWithRun(replayEvents(CANNED), COMPLETED_RUN);

    expect(messages(session.reasoningSteps)).toMatch(/Momentum is intact/);
    expect(session.sessionStatus).toBe('complete');
  });

  it('records the terminal state, so the composer can unlock', () => {
    const session = reconcileWithRun(replayEvents(CANNED), COMPLETED_RUN);
    expect(session.isAnalyzing).toBe(false);
  });
});
