// @vitest-environment node
/* eslint-disable @typescript-eslint/no-explicit-any */

// Feature: deep-quant-runtime-hardening
//
// Property 17 (Preservation) — frontend ¬C directional render path.
//
// For inputs that trigger NONE of the six bug conditions — here, a normal
// directional BUY/SELL DECISION payload carrying three finite positive
// `execution_levels`, a present conviction, and a non-`stand_aside` tier — the
// fixed DECISION reducer + `isActionableTrade` render path behave EXACTLY as a
// pre-fix directional run did: the built plan preserves the committed
// action, the execution levels (byte-for-byte), the conviction, and the tier,
// with NO synthesis and NO default. Driving the plan on to RUN_FINISHED must not
// disturb any of those values (text-extraction never overrides a real decision).
//
// This is the composed, end-to-end preservation guarantee for the ¬C directional
// path. The isolated predicate/conviction properties (Property 8/9/10) live in
// `useQuantStore.decision.property.test.ts`; this module does not duplicate them
// — it asserts the whole ¬C directional pipeline is value-preserving.
//
//   Validates: Requirements 7.6, 7.8

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';

import {
  useQuantStore,
  isActionableTrade,
  type AiExecutionPlan,
  type ExecutionLevels,
} from '@/store/useQuantStore';

const RUNS = 200;
const VIEW_KEY = 'CUPID::INTRADAY';

/** A finite, strictly-positive price. */
const arbPositivePrice: fc.Arbitrary<number> = fc.double({
  min: Math.fround(0.01),
  max: 1_000_000,
  noNaN: true,
  noDefaultInfinity: true,
});

/** Three finite positive prices — the validated directional Execution_Levels. */
const arbValidLevels: fc.Arbitrary<ExecutionLevels> = fc.record({
  entry: arbPositivePrice,
  stop_loss: arbPositivePrice,
  take_profit: arbPositivePrice,
});

/** A present conviction (¬C: the directional run emitted a real conviction). */
const arbConviction: fc.Arbitrary<number> = fc.double({
  min: 1,
  max: 100,
  noNaN: true,
  noDefaultInfinity: true,
});

/** A non-`stand_aside` opportunity tier carried by a real directional plan. */
const arbDirectionalTier = fc.constantFrom<string | undefined>(
  undefined,
  'a_plus',
  'a',
  'b',
);

/** Brace-free prose so a subsequent RUN_FINISHED JSON text-extraction cannot
 *  interfere with the committed decision. */
const arbProse = fc.stringMatching(/^[a-zA-Z0-9 .,:>=x/-]{0,40}$/);

/** Faithful mirror of `AgentTerminal.parsePlanDetails` (a component-local
 *  closure): the render path maps the trade card DIRECTLY from the committed
 *  `execution_levels` / `action`, with no prose-scraping or synthesis. */
function renderPlanDetails(plan: AiExecutionPlan | null) {
  if (!isActionableTrade(plan)) return null;
  const { execution_levels, action } = plan;
  return {
    side: action === 'SELL' ? 'SELL' : 'BUY',
    entryPrice: execution_levels.entry,
    stopLoss: execution_levels.stop_loss,
    takeProfit: execution_levels.take_profit,
  };
}

function resetStore() {
  useQuantStore.setState({
    sessionsByKey: {},
    _threadToKey: {},
    _streamingKey: VIEW_KEY,
    activeViewKey: VIEW_KEY,
    finalTrade: null,
    aiPlan: null,
    sessionStatus: 'idle',
    reasoningSteps: [],
  } as any);
}

beforeEach(() => {
  resetStore();
});

describe('Property 17: the ¬C directional render path is byte-for-byte preserved', () => {
  // Feature: deep-quant-runtime-hardening, Property 17: for any directional
  // BUY/SELL DECISION payload with three finite positive execution_levels and a
  // present conviction, the built plan + isActionableTrade render path preserves
  // the exact levels/action/conviction/tier — no synthesis, no default.
  it('preserves action, execution_levels, conviction, and tier verbatim through the reducer + render path', () => {
    fc.assert(
      fc.property(
        fc.constantFrom<'BUY' | 'SELL'>('BUY', 'SELL'),
        arbValidLevels,
        arbConviction,
        arbDirectionalTier,
        arbProse,
        (action, levels, conviction, tier, prose) => {
          resetStore();
          const store = useQuantStore.getState();

          const thread = 't-prop17';
          const decisionData: Record<string, unknown> = {
            action,
            conviction_score: conviction,
            opportunity_tier: tier,
            execution_levels: { ...levels },
            rationale: `read ${prose}`,
            execution_plan: `plan ${prose}`,
            thread_id: thread,
          };

          store.handleStreamEvent({ event: 'RUN_STARTED', data: { thread_id: thread } });
          store.handleStreamEvent({ event: 'DECISION', data: decisionData });

          const built = useQuantStore.getState().finalTrade;
          expect(built).not.toBeNull();

          // The ¬C directional plan is actionable — the trade card renders.
          expect(isActionableTrade(built)).toBe(true);

          // Action / conviction / tier preserved verbatim (no default 75).
          expect(built!.action).toBe(action);
          expect(built!.conviction_score).toBe(conviction);
          if (tier === undefined) {
            expect(built!.opportunity_tier).toBeUndefined();
          } else {
            expect(built!.opportunity_tier).toBe(tier);
          }

          // Execution levels preserved byte-for-byte — no synthesis.
          expect(built!.execution_levels).toEqual(levels);

          // The render path maps the card DIRECTLY from those same values.
          const rendered = renderPlanDetails(built);
          expect(rendered).toEqual({
            side: action,
            entryPrice: levels.entry,
            stopLoss: levels.stop_loss,
            takeProfit: levels.take_profit,
          });

          // Driving on to RUN_FINISHED must not disturb the committed decision.
          store.handleStreamEvent({ event: 'RUN_FINISHED', data: { thread_id: thread, status: 'completed' } });
          const finished = useQuantStore.getState().finalTrade;
          expect(isActionableTrade(finished)).toBe(true);
          expect(finished!.action).toBe(action);
          expect(finished!.conviction_score).toBe(conviction);
          expect(finished!.execution_levels).toEqual(levels);
        },
      ),
      { numRuns: RUNS },
    );
  });
});
