// lib/fq/__tests__/rehydrate.test.ts
//
// Two claims, and the second is the one that is easy to get wrong.
//
//   1. **Anti-drift.** A session rebuilt from stored frames is byte-for-byte the session a
//      live stream of the same frames produces. Asserted by building both and comparing,
//      not by eyeballing a few fields — that is what makes it a guarantee rather than a
//      hope, and it is why the backend stores payloads instead of renderings.
//
//   2. **Honesty.** Frame replay ALONE leaves a run that died mid-stream looking `running`
//      with `isAnalyzing: true` — a dead run rendering as a live one, indefinitely. The
//      reconciliation step against the run's stored status is what prevents that, and every
//      status has a test.

import { describe, expect, it } from 'vitest';

import {
  isRunLive,
  pickActiveRun,
  reconcileWithRun,
  replayEvents,
  toQaMessages,
} from '../rehydrate';
import type { SessionSummary, StoredEvent, StoredMessage, StoredRun } from '../api';
import { applyStreamEvent, blankSession, type QuantSession } from '../../../store/useQuantStore';

const THREAD = 'thread_01ABC';

function run(over: Partial<StoredRun> = {}): StoredRun {
  return {
    run_id: 'run_1',
    session_id: 'sess_1',
    thread_id: THREAD,
    kind: 'find',
    symbol: 'RELIANCE',
    timeframe: '10m',
    profile: 'INTRADAY',
    model: null,
    manual_trade: null,
    status: 'complete',
    terminal_status: 'complete',
    started_at: 1,
    ended_at: 2,
    last_seq: 0,
    ...over,
  };
}

function summary(over: Partial<SessionSummary> = {}): SessionSummary {
  return {
    session_id: 'sess_1',
    title: null,
    symbol: 'RELIANCE',
    timeframe: '10m',
    profile: 'INTRADAY',
    status: 'active',
    created_at: 1,
    updated_at: 2,
    archived_at: null,
    active_run_id: null,
    message_count: 0,
    last_run: null,
    ...over,
  };
}

function message(over: Partial<StoredMessage> = {}): StoredMessage {
  return {
    message_id: 'msg_1',
    session_id: 'sess_1',
    run_id: 'run_1',
    seq: 1,
    role: 'user',
    kind: 'qa_question',
    content: 'why that stop?',
    status: 'complete',
    error_detail: null,
    activity: null,
    client_msg_id: null,
    created_at: 1,
    updated_at: 1,
    ...over,
  };
}

/** A realistic FIND transcript. */
const TRANSCRIPT: StoredEvent[] = [
  { seq: 1, event: 'RUN_STARTED', data: { thread_id: THREAD } },
  { seq: 2, event: 'REASONING', data: { thread_id: THREAD, content: 'Checking the ' } },
  { seq: 3, event: 'REASONING', data: { thread_id: THREAD, content: 'multi-TF trend.' } },
  { seq: 4, event: 'TOOL_CALL_START', data: { thread_id: THREAD, tool: 'get_candles', args: { symbol: 'RELIANCE' } } },
  { seq: 5, event: 'TOOL_CALL_END', data: { thread_id: THREAD, tool: 'get_candles', status: 'success' } },
  {
    seq: 6,
    event: 'DECISION',
    data: {
      thread_id: THREAD,
      action: 'BUY',
      conviction_score: 78,
      rationale: 'Golden cross with rising OBV.',
      execution_levels: { entry: 2470, stop_loss: 2435, take_profit: 2550 },
    },
  },
  { seq: 7, event: 'RUN_FINISHED', data: { thread_id: THREAD, status: 'completed' } },
];

/** Drive the same frames through the reducer the way a LIVE stream does. */
function liveSession(events: StoredEvent[]): QuantSession {
  let session = blankSession();
  for (const e of events) session = applyStreamEvent(session, { event: e.event, data: e.data });
  return session;
}

/** Ids and timestamps are wall-clock derived, so normalise them out of comparisons. */
function shape(session: QuantSession) {
  return {
    sessionStatus: session.sessionStatus,
    isAnalyzing: session.isAnalyzing,
    analysisError: session.analysisError,
    finalTrade: session.finalTrade,
    aiPlan: session.aiPlan,
    pendingToolCalls: session._pendingToolCalls,
    runFinishedProcessed: session._runFinishedProcessed,
    steps: session.reasoningSteps.map((s) => ({
      type: s.type,
      content: s.content,
      toolName: s.toolName ?? null,
    })),
  };
}

// ── Anti-drift ────────────────────────────────────────────────────────────────

describe('replay produces the same session a live stream would', () => {
  it('is identical for a full FIND transcript', () => {
    // The guarantee that makes storing payloads worthwhile: one reducer, one result.
    expect(shape(replayEvents(TRANSCRIPT))).toEqual(shape(liveSession(TRANSCRIPT)));
  });

  it('rebuilds the committed trade plan exactly', () => {
    const restored = replayEvents(TRANSCRIPT);
    expect(restored.finalTrade).toEqual({
      conviction_score: 78,
      setup_validation: 'Golden cross with rising OBV.',
      execution_plan: '',
      action: 'BUY',
      opportunity_tier: undefined,
      execution_levels: { entry: 2470, stop_loss: 2435, take_profit: 2550 },
    });
  });

  it('preserves structured tool activity rather than flattening it', () => {
    const restored = replayEvents(TRANSCRIPT);
    const toolStart = restored.reasoningSteps.find((s) => s.type === 'tool_start');
    expect(toolStart?.toolName).toBe('get_candles');
    expect(toolStart?.args).toEqual({ symbol: 'RELIANCE' });
  });

  it('coalesces consecutive REASONING exactly as the live path does', () => {
    // The reducer merges into the trailing message step, so a different replay order would
    // produce a different — plausible-looking — transcript.
    const restored = replayEvents(TRANSCRIPT);
    const text = restored.reasoningSteps
      .filter((s) => s.type === 'message')
      .map((s) => s.content)
      .join('');
    expect(text).toContain('Checking the multi-TF trend.');
  });

  it('sorts by seq before replaying, so a reordered response cannot corrupt it', () => {
    const shuffled = [TRANSCRIPT[6], TRANSCRIPT[2], TRANSCRIPT[0], TRANSCRIPT[5], TRANSCRIPT[1], TRANSCRIPT[3], TRANSCRIPT[4]];
    expect(shape(replayEvents(shuffled))).toEqual(shape(replayEvents(TRANSCRIPT)));
  });

  it('an empty transcript yields a blank session', () => {
    expect(shape(replayEvents([]))).toEqual(shape(blankSession()));
  });
});

// ── Honesty: reconciliation against the run's stored status ───────────────────

describe('reconciliation with the run status', () => {
  /** A transcript that stops mid-run: what a crashed process leaves behind. */
  const INTERRUPTED: StoredEvent[] = TRANSCRIPT.slice(0, 4);

  it('frame replay alone would leave a dead run looking alive', () => {
    // The defect the reconciliation step exists to fix, asserted so the fix cannot be
    // removed without this failing.
    const replayedOnly = replayEvents(INTERRUPTED);
    expect(replayedOnly.sessionStatus).toBe('running');
    expect(replayedOnly.isAnalyzing).toBe(true);
  });

  it('a truncated run becomes a visible error, not a spinner', () => {
    const reconciled = reconcileWithRun(replayEvents(INTERRUPTED), run({ status: 'truncated' }));
    expect(reconciled.sessionStatus).toBe('error');
    expect(reconciled.isAnalyzing).toBe(false);
    expect(reconciled.analysisError).toContain('interrupted before it finished');
  });

  it('a truncated run keeps its reasoning but drops any plan', () => {
    // A truncated run may have emitted a DECISION before dying. Rendering an executable
    // trade card for an analysis that never completed its own verification is the worst
    // available outcome here.
    const withDecision = replayEvents(TRANSCRIPT.slice(0, 6));
    expect(withDecision.finalTrade).not.toBeNull();

    const reconciled = reconcileWithRun(withDecision, run({ status: 'truncated' }));
    expect(reconciled.finalTrade).toBeNull();
    expect(reconciled.aiPlan).toBeNull();
    expect(reconciled.reasoningSteps.length).toBeGreaterThan(0);
  });

  it('a complete run stays complete with its plan intact', () => {
    const reconciled = reconcileWithRun(replayEvents(TRANSCRIPT), run({ status: 'complete' }));
    expect(reconciled.sessionStatus).toBe('complete');
    expect(reconciled.isAnalyzing).toBe(false);
    expect(reconciled.finalTrade?.action).toBe('BUY');
  });

  it('a watching run is restored as watching, with the composer usable', () => {
    // The watcher will wake it, so this is a live-but-idle state rather than a finished one.
    const reconciled = reconcileWithRun(replayEvents(INTERRUPTED), run({ status: 'watching' }));
    expect(reconciled.sessionStatus).toBe('watching');
    expect(reconciled.isAnalyzing).toBe(false);
  });

  it('a still-running run is restored as running', () => {
    const reconciled = reconcileWithRun(replayEvents(INTERRUPTED), run({ status: 'running' }));
    expect(reconciled.sessionStatus).toBe('running');
    expect(reconciled.isAnalyzing).toBe(true);
  });

  it('an errored run surfaces an error', () => {
    const reconciled = reconcileWithRun(replayEvents(INTERRUPTED), run({ status: 'error' }));
    expect(reconciled.sessionStatus).toBe('error');
    expect(reconciled.analysisError).toBeTruthy();
  });

  it('an errored run keeps the error the transcript already carried', () => {
    // The ERROR frame's own message is more specific than the generic fallback.
    const withError = replayEvents([
      ...INTERRUPTED,
      { seq: 5, event: 'ERROR', data: { error: 'AI analysis unavailable: provider timeout' } },
    ]);
    const reconciled = reconcileWithRun(withError, run({ status: 'error' }));
    expect(reconciled.analysisError).toContain('provider timeout');
  });

  it('a cancelled run returns to idle without an error', () => {
    // The user asked it to stop; presenting that as a failure would be wrong.
    const reconciled = reconcileWithRun(replayEvents(INTERRUPTED), run({ status: 'cancelled' }));
    expect(reconciled.sessionStatus).toBe('idle');
    expect(reconciled.analysisError).toBeNull();
  });

  it('no run leaves the session untouched', () => {
    const base = replayEvents(TRANSCRIPT);
    expect(reconcileWithRun(base, null)).toBe(base);
  });

  it.each(['complete', 'cancelled', 'error', 'truncated'] as const)(
    'never leaves isAnalyzing true for a finished run (%s)',
    (status) => {
      // A spinner that never stops is the single most visible way this could lie.
      const reconciled = reconcileWithRun(replayEvents(INTERRUPTED), run({ status }));
      expect(reconciled.isAnalyzing).toBe(false);
    },
  );
});

// ── Which run to show ─────────────────────────────────────────────────────────

describe('pickActiveRun', () => {
  it('prefers the session active_run_id', () => {
    const first = run({ run_id: 'run_1', started_at: 1 });
    const second = run({ run_id: 'run_2', started_at: 2 });
    const picked = pickActiveRun(summary({ active_run_id: 'run_1' }), [first, second]);
    expect(picked?.run_id).toBe('run_1');
  });

  it('falls back to the newest run', () => {
    const first = run({ run_id: 'run_1', started_at: 1 });
    const second = run({ run_id: 'run_2', started_at: 2 });
    expect(pickActiveRun(summary(), [first, second])?.run_id).toBe('run_2');
  });

  it('falls back to the newest when active_run_id names a run that is gone', () => {
    // A hard-deleted run leaves the pointer dangling; showing the newest beats showing none.
    const only = run({ run_id: 'run_2' });
    expect(pickActiveRun(summary({ active_run_id: 'run_missing' }), [only])?.run_id).toBe('run_2');
  });

  it('returns null for a session with no runs', () => {
    expect(pickActiveRun(summary(), [])).toBeNull();
  });
});

describe('isRunLive', () => {
  it.each([
    ['running', true],
    ['watching', true],
    ['complete', false],
    ['cancelled', false],
    ['error', false],
    ['truncated', false],
  ] as const)('%s -> %s', (status, expected) => {
    expect(isRunLive(run({ status }))).toBe(expected);
  });

  it('is false for no run', () => {
    expect(isRunLive(null)).toBe(false);
  });
});

// ── Q&A transcript ────────────────────────────────────────────────────────────

describe('toQaMessages', () => {
  it('restores a question and answer pair', () => {
    const restored = toQaMessages([
      message({ message_id: 'm1', seq: 1, role: 'user', kind: 'qa_question', content: 'why?' }),
      message({
        message_id: 'm2',
        seq: 2,
        role: 'assistant',
        kind: 'qa_answer',
        content: 'because ATR',
        activity: ['> get_candles'],
      }),
    ]);
    expect(restored).toEqual([
      { id: 'm1', role: 'user', content: 'why?', activity: undefined, streaming: false, error: undefined },
      {
        id: 'm2',
        role: 'assistant',
        content: 'because ATR',
        activity: ['> get_candles'],
        streaming: false,
        error: undefined,
      },
    ]);
  });

  it('excludes analysis messages, which belong to the glass box', () => {
    const restored = toQaMessages([
      message({ kind: 'analysis_request', content: 'find a setup' }),
      message({ message_id: 'm2', kind: 'analysis_answer', role: 'assistant', content: 'analysis' }),
      message({ message_id: 'm3', kind: 'qa_question', content: 'why?' }),
    ]);
    expect(restored.map((m) => m.id)).toEqual(['m3']);
  });

  it.each(['truncated', 'error', 'cancelled'] as const)(
    'marks a %s answer as an error so it cannot read as a good one',
    (status) => {
      const [restored] = toQaMessages([
        message({ role: 'assistant', kind: 'qa_answer', content: 'half an ans', status }),
      ]);
      expect(restored.error).toBe(true);
      expect(restored.streaming).toBe(false);
    },
  );

  it('keeps the partial text of a truncated answer', () => {
    const [restored] = toQaMessages([
      message({ role: 'assistant', kind: 'qa_answer', content: 'The stop sits at', status: 'truncated' }),
    ]);
    expect(restored.content).toBe('The stop sits at');
  });

  it('explains an empty failed answer rather than showing a blank bubble', () => {
    // A blank bubble tells the user something went wrong but not what, and is
    // indistinguishable from a still-loading turn.
    const [restored] = toQaMessages([
      message({ role: 'assistant', kind: 'qa_answer', content: '', status: 'truncated' }),
    ]);
    expect(restored.content).toContain('interrupted before it finished');
  });

  it('prefers the stored error detail when there is one', () => {
    const [restored] = toQaMessages([
      message({
        role: 'assistant',
        kind: 'qa_answer',
        content: '',
        status: 'error',
        error_detail: 'LLM key unavailable',
      }),
    ]);
    expect(restored.content).toBe('LLM key unavailable');
  });

  it('keeps a genuinely streaming answer streaming', () => {
    // On a freshly-loaded session this means the run really is producing; the live stream
    // will finish it.
    const [restored] = toQaMessages([
      message({ role: 'assistant', kind: 'qa_answer', content: 'partial', status: 'streaming' }),
    ]);
    expect(restored.streaming).toBe(true);
    expect(restored.error).toBeUndefined();
  });

  it('is empty for a session with no Q&A', () => {
    expect(toQaMessages([])).toEqual([]);
  });
});
