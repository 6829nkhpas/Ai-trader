// Feature: deep-quant-runtime-hardening (bugfix)
//
// Property 8 (Bug Condition), frontend store DECISION reducer seam —
// "HOLD / stand-aside must never render as an executable trade":
//
//   For any committed DECISION whose action is HOLD (or whose
//   opportunity_tier is `stand_aside`) reaching the store reducer, the built
//   AiExecutionPlan should:
//     * carry the decision `action` (and `opportunity_tier`) through so the UI
//       can gate on it (R1.1),
//     * NEVER synthesize/fabricate a conviction of 75 when the payload omits
//       conviction — it should stay undefined so the UI can render "—" (R1.7),
//     * carry no synthesized `execution_levels` for a HOLD (R1.4).
//
//   Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.7
//
// *** EXPLORATION TEST — EXPECTED TO FAIL ON UNFIXED CODE ***
//
// The unfixed DECISION reducer (`applyStreamEvent` `case 'DECISION'` in
// useQuantStore.ts) builds a plan of only
// `{ conviction_score: conviction ?? 75, setup_validation, execution_plan }`.
// It DROPS `action` / `opportunity_tier`, and it DEFAULTS conviction to a
// fabricated `75` when the model emitted none. So a committed HOLD reaches the
// UI stripped of the very fields it needs to gate the APPROVE & EXECUTE card,
// and its conviction reads as a confident 75. The failures below are the
// informative, expected outcome (the R1 capital-safety counterexamples). DO
// NOT fix the code here; tasks 2.2/2.3 carry action/tier/levels and drop the
// `?? 75`, and task 2.6 re-runs THIS SAME test to confirm the fix.

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';
import { useQuantStore, type StreamEventPayload } from '../useQuantStore';

const VIEW_KEY = 'CUPID::INTRADAY';

/** A loose read of the built plan so we can inspect fields the (unfixed)
 *  AiExecutionPlan type does not yet declare (action / opportunity_tier /
 *  execution_levels) without a compile error. */
type LoosePlan = {
  conviction_score?: unknown;
  action?: unknown;
  opportunity_tier?: unknown;
  execution_levels?: unknown;
  setup_validation?: unknown;
  execution_plan?: unknown;
} | null;

/** Bind an active/streaming session key so `handleStreamEvent` routes the
 *  DECISION into a session and mirrors it to the flat top-level fields. */
function resetStore() {
  useQuantStore.setState({
    sessionsByKey: {},
    _threadToKey: {},
    _streamingKey: VIEW_KEY,
    activeViewKey: VIEW_KEY,
    finalTrade: null,
    aiPlan: null,
  });
}

/** Feed a HOLD DECISION payload with NO conviction through the reducer and
 *  return the built plan the UI would consume. */
function dispatchHoldDecision(tier: string, rationale: string): LoosePlan {
  const payload: StreamEventPayload = {
    event: 'DECISION',
    data: {
      action: 'HOLD',
      opportunity_tier: tier,
      // conviction deliberately ABSENT — the model committed a HOLD without one.
      rationale,
      // Prose that the unfixed AgentTerminal would scrape/synthesize from.
      execution_plan: 'Standing aside — chop, no edge. Rule: stop >= 1.5x ATR. Target 1: reassess.',
      thread_id: 't-hold-1',
    },
  };
  useQuantStore.getState().handleStreamEvent(payload);
  return useQuantStore.getState().finalTrade as LoosePlan;
}

beforeEach(() => {
  resetStore();
});

describe('R1 DECISION reducer: HOLD must not fabricate conviction or drop action — EXPECTED FAIL on unfixed code', () => {
  it('property: a HOLD DECISION with no conviction never yields a fabricated 75 and carries its action', () => {
    fc.assert(
      fc.property(
        fc.constantFrom('stand_aside', 'watch', ''),
        fc.string({ minLength: 0, maxLength: 60 }),
        (tier, rationale) => {
          resetStore();
          const plan = dispatchHoldDecision(tier, rationale);
          expect(plan).not.toBeNull();

          // R1.7 — conviction was ABSENT in the payload, so it must NOT be a
          // fabricated 75. EXPECTED FAIL on unfixed code: `conviction ?? 75`.
          expect(plan?.conviction_score).not.toBe(75);

          // R1.1 — the committed action must be threaded so the UI can gate the
          // APPROVE & EXECUTE card. EXPECTED FAIL on unfixed code: dropped.
          expect(plan?.action).toBe('HOLD');

          // R1.4 — a HOLD carries no synthesized execution levels.
          expect(plan?.execution_levels).toBeUndefined();
        },
      ),
      { numRuns: 100 },
    );
  });

  it('concrete counterexample: committed HOLD (conviction absent) reads as a confident 75 with no action', () => {
    const plan = dispatchHoldDecision('stand_aside', 'No edge — waiting for a clean break.');
    expect(plan).not.toBeNull();

    // Counterexample the unfixed reducer produces:
    //   conviction_score === 75  (fabricated — the model emitted none)
    //   action === undefined     (dropped — UI cannot tell it is a HOLD)
    // The safe assertions below therefore FAIL on unfixed code.
    expect(plan?.conviction_score).not.toBe(75); // unfixed: 75
    expect(plan?.action).toBe('HOLD'); // unfixed: undefined
    expect(plan?.opportunity_tier).toBe('stand_aside'); // unfixed: undefined
  });
});
