// store/__tests__/useSessionStore.isolation.property.test.ts
//
// The isolation invariant, as a property rather than a list of scenarios I happened to
// think of:
//
//   **For ANY interleaving of frames across N sessions, each session's state is exactly
//   what it would have been had its own frames arrived alone.**
//
// That formulation is the strong one. A weaker test ("A's content is not in B") passes even
// if interleaving perturbs A's own state — a dropped chunk, a coalescing boundary landing
// differently, a status transition arriving out of turn. This compares against a
// single-session control run, so any perturbation at all fails.
//
// It is the assertion the tab UI depends on. The previous routing resolved
// `runKey = _streamingKey || activeViewKey` with no thread match, so under interleaving a
// frame could land in whichever session was on screen — and because the result looked like
// ordinary data, the corruption was undetectable in practice.

import { describe, expect, it } from 'vitest';
import fc from 'fast-check';

import { useSessionStore } from '../useSessionStore';
import { blankSession, type QuantSession } from '../useQuantStore';

const SESSION_IDS = ['sess_A', 'sess_B', 'sess_C'] as const;
const threadFor = (sessionId: string) => `thread_${sessionId}`;

/** The frame shapes a run actually emits, in the payload shape the backend sends. */
const frameArb = fc.oneof(
  fc.record({ event: fc.constant('RUN_STARTED'), data: fc.constant({}) }),
  fc.record({
    event: fc.constant('REASONING'),
    data: fc.record({ content: fc.string({ minLength: 1, maxLength: 8 }) }),
  }),
  fc.record({
    event: fc.constant('TOOL_CALL_START'),
    data: fc.record({ tool: fc.constantFrom('get_candles', 'get_consensus', 'watch_price_condition') }),
  }),
  fc.record({
    event: fc.constant('TOOL_CALL_END'),
    data: fc.record({ tool: fc.constantFrom('get_candles', 'get_consensus') }),
  }),
  fc.record({
    event: fc.constant('DECISION'),
    data: fc.record({
      action: fc.constantFrom('BUY', 'SELL', 'HOLD'),
      conviction_score: fc.integer({ min: 1, max: 100 }),
    }),
  }),
  fc.record({
    event: fc.constant('RUN_FINISHED'),
    data: fc.record({ status: fc.constantFrom('completed', 'paused') }),
  }),
  fc.record({ event: fc.constant('ERROR'), data: fc.record({ error: fc.string({ maxLength: 8 }) }) }),
);

/** A frame tagged with the session it belongs to. */
const addressedFrameArb = fc.record({
  session: fc.constantFrom(...SESSION_IDS),
  frame: frameArb,
});

type Addressed = { session: string; frame: { event: string; data: Record<string, unknown> } };

function send(store: ReturnType<typeof useSessionStore.getState>, item: Addressed) {
  store.applyFrame({
    event: item.frame.event,
    data: { ...item.frame.data, thread_id: threadFor(item.session) },
  });
}

/**
 * Run one session's frames in a store of its own — the control.
 *
 * `timestamp` and generated step ids are wall-clock derived, so the comparison below
 * normalises them away rather than pretending they are reproducible.
 */
function controlState(sessionId: string, items: Addressed[]): QuantSession {
  useSessionStore.getState().reset();
  const store = useSessionStore.getState();
  store.bindThread(threadFor(sessionId), sessionId);
  for (const item of items.filter((i) => i.session === sessionId)) send(store, item);
  return useSessionStore.getState().sessions[sessionId] ?? blankSession();
}

/**
 * Strip the non-deterministic parts: generated ids and timestamps.
 *
 * What remains is everything that is actually meaningful — statuses, the ordered content of
 * the transcript, the committed plan, the pending-tool counter. If interleaving perturbed
 * any of that, this comparison catches it.
 */
function normalise(session: QuantSession) {
  return {
    sessionStatus: session.sessionStatus,
    isAnalyzing: session.isAnalyzing,
    analysisError: session.analysisError,
    pendingToolCalls: session._pendingToolCalls,
    runFinishedProcessed: session._runFinishedProcessed,
    finalTrade: session.finalTrade,
    aiPlan: session.aiPlan,
    steps: session.reasoningSteps.map((s) => ({
      type: s.type,
      content: s.content,
      toolName: s.toolName ?? null,
    })),
    qaMessages: session.qaMessages.map((m) => ({ role: m.role, content: m.content })),
    qaStatus: session.qaStatus,
  };
}

describe('session isolation under arbitrary interleaving', () => {
  it('each session ends in exactly the state its own frames alone would produce', () => {
    fc.assert(
      fc.property(fc.array(addressedFrameArb, { minLength: 0, maxLength: 60 }), (items) => {
        // Controls first, so the interleaved run below cannot influence them.
        const expected = new Map<string, ReturnType<typeof normalise>>();
        for (const sessionId of SESSION_IDS) {
          expected.set(sessionId, normalise(controlState(sessionId, items)));
        }

        // Now the same frames, interleaved.
        useSessionStore.getState().reset();
        const store = useSessionStore.getState();
        for (const sessionId of SESSION_IDS) store.bindThread(threadFor(sessionId), sessionId);
        for (const item of items) send(store, item);

        const actual = useSessionStore.getState().sessions;
        for (const sessionId of SESSION_IDS) {
          expect(normalise(actual[sessionId] ?? blankSession())).toEqual(expected.get(sessionId));
        }
      }),
      { numRuns: 200 },
    );
  });

  it('the active session is irrelevant to where frames land', () => {
    // The specific failure of the old routing: it consulted `activeViewKey`. Switching the
    // active session between every frame must change nothing at all.
    fc.assert(
      fc.property(
        fc.array(addressedFrameArb, { minLength: 0, maxLength: 40 }),
        fc.array(fc.constantFrom(...SESSION_IDS), { minLength: 0, maxLength: 40 }),
        (items, switches) => {
          const withoutSwitching = (() => {
            useSessionStore.getState().reset();
            const store = useSessionStore.getState();
            for (const s of SESSION_IDS) store.bindThread(threadFor(s), s);
            for (const item of items) send(store, item);
            return SESSION_IDS.map((s) =>
              normalise(useSessionStore.getState().sessions[s] ?? blankSession()),
            );
          })();

          const withSwitching = (() => {
            useSessionStore.getState().reset();
            const store = useSessionStore.getState();
            for (const s of SESSION_IDS) store.bindThread(threadFor(s), s);
            items.forEach((item, i) => {
              const target = switches[i % Math.max(1, switches.length)];
              if (target) store.setActiveSession(target);
              send(store, item);
            });
            return SESSION_IDS.map((s) =>
              normalise(useSessionStore.getState().sessions[s] ?? blankSession()),
            );
          })();

          expect(withSwitching).toEqual(withoutSwitching);
        },
      ),
      { numRuns: 150 },
    );
  });

  it('unbound frames never touch any session, however many arrive', () => {
    fc.assert(
      fc.property(
        fc.array(frameArb, { minLength: 1, maxLength: 30 }),
        fc.constantFrom(...SESSION_IDS),
        (frames, active) => {
          useSessionStore.getState().reset();
          const store = useSessionStore.getState();
          for (const s of SESSION_IDS) store.bindThread(threadFor(s), s);
          store.setActiveSession(active);

          const before = SESSION_IDS.map((s) =>
            normalise(useSessionStore.getState().sessions[s] ?? blankSession()),
          );

          for (const frame of frames) {
            store.applyFrame({ event: frame.event, data: { ...frame.data, thread_id: 'thread_ORPHAN' } });
          }

          const after = SESSION_IDS.map((s) =>
            normalise(useSessionStore.getState().sessions[s] ?? blankSession()),
          );
          expect(after).toEqual(before);
          // Dropped AND counted: silently swallowing them would hide a real bug, and
          // routing them is what the old fallback did.
          expect(useSessionStore.getState().unroutableFrames).toBe(frames.length);
        },
      ),
      { numRuns: 100 },
    );
  });

  it('per-session UI state is never cross-contaminated', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.record({
            session: fc.constantFrom(...SESSION_IDS),
            draft: fc.string({ maxLength: 12 }),
            mode: fc.constantFrom('FIND' as const, 'VERIFY' as const),
            entry: fc.string({ maxLength: 8 }),
          }),
          { minLength: 1, maxLength: 30 },
        ),
        (writes) => {
          useSessionStore.getState().reset();
          const store = useSessionStore.getState();
          for (const w of writes) {
            store.setUi(w.session, { draft: w.draft, mode: w.mode });
            store.setVerification(w.session, { entry: w.entry });
          }

          // Each session must hold its OWN last write — not the last write overall, which
          // is what a shared draft field produced.
          const state = useSessionStore.getState();
          for (const sessionId of SESSION_IDS) {
            const mine = writes.filter((w) => w.session === sessionId);
            if (mine.length === 0) {
              expect(state.ui[sessionId]).toBeUndefined();
              continue;
            }
            const last = mine[mine.length - 1];
            expect(state.ui[sessionId].draft).toBe(last.draft);
            expect(state.ui[sessionId].mode).toBe(last.mode);
            expect(state.ui[sessionId].verification.entry).toBe(last.entry);
          }
        },
      ),
      { numRuns: 150 },
    );
  });
});
