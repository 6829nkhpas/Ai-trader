// @vitest-environment node
/* eslint-disable @typescript-eslint/no-explicit-any */

// Feature: deep-quant-runtime-hardening
//
// R1 verification / preservation property tests for the render-guard predicate
// `isActionableTrade` and the DECISION reducer of `useQuantStore`. These are the
// POSITIVE-side (preservation) properties: they assert the now-fixed code holds
// the invariants for ALL inputs, complementing the task-1 bug-condition tests.
//
//   Validates: Requirements 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';

import {
  useQuantStore,
  isActionableTrade,
  type AiExecutionPlan,
  type ExecutionLevels,
  type StreamEventPayload,
} from '@/store/useQuantStore';

const RUNS = 200;

// ── Shared arbitraries ───────────────────────────────────────────────────

/** A finite, strictly-positive price. */
const arbPositivePrice: fc.Arbitrary<number> = fc.double({
  min: Math.fround(0.01),
  max: 1_000_000,
  noNaN: true,
  noDefaultInfinity: true,
});

/** A "dirty" number that may be a valid positive price OR an invalid one
 *  (zero, negative, NaN, ±Infinity) — used to probe the level-validity gate. */
const arbDirtyNumber: fc.Arbitrary<number> = fc.oneof(
  arbPositivePrice,
  fc.constant(0),
  fc.double({ min: -1_000_000, max: Math.fround(-0.01), noNaN: true, noDefaultInfinity: true }),
  fc.constant(Number.NaN),
  fc.constant(Number.POSITIVE_INFINITY),
  fc.constant(Number.NEGATIVE_INFINITY),
);

/** A three-finite-positive-price execution_levels object. */
const arbValidLevels: fc.Arbitrary<ExecutionLevels> = fc.record({
  entry: arbPositivePrice,
  stop_loss: arbPositivePrice,
  take_profit: arbPositivePrice,
});

/** An execution_levels-shaped value that may be valid, partially populated,
 *  contain invalid numbers, or be entirely absent. Cast through `any` because
 *  the malformed variants intentionally break the `ExecutionLevels` contract. */
const arbLevelsVariant: fc.Arbitrary<any> = fc.oneof(
  arbValidLevels,
  fc.record({ entry: arbDirtyNumber, stop_loss: arbDirtyNumber, take_profit: arbDirtyNumber }),
  // Missing one or more fields.
  fc.record({ entry: arbPositivePrice, stop_loss: arbPositivePrice }, { requiredKeys: [] }),
  fc.constant(undefined),
  fc.constant(null),
);

/** Faithful mirror of the predicate's level check, for cross-verification. */
function hasThreeFinitePositive(l: any): boolean {
  return (
    !!l &&
    typeof l === 'object' &&
    [l.entry, l.stop_loss, l.take_profit].every(
      (n) => typeof n === 'number' && Number.isFinite(n) && n > 0,
    )
  );
}

const arbTierNonStandAside = fc.constantFrom<string | undefined>(
  undefined,
  'a_plus',
  'a',
  'b',
  'watch',
  'scout',
);

// ───────────────────────────────────────────────────────────────────────────
// Property 8 — HOLD / stand_aside is never actionable
// ───────────────────────────────────────────────────────────────────────────

describe('Property 8: HOLD or stand_aside plans are never actionable', () => {
  // Feature: deep-quant-runtime-hardening, Property 8: for any plan with action
  // HOLD or opportunity_tier stand_aside, isActionableTrade is false (no
  // control, no level cells, no synthesis). Validates: Requirements 1.2, 1.3, 1.4
  it('isActionableTrade is false whenever action is HOLD or tier is stand_aside, even with valid levels', () => {
    fc.assert(
      fc.property(
        // Force the bug condition: action HOLD, OR tier stand_aside (or both).
        fc.oneof(
          fc.constant<'HOLD'>('HOLD'),
          fc.constantFrom<'BUY' | 'SELL' | 'HOLD'>('BUY', 'SELL', 'HOLD'),
        ),
        fc.boolean(),
        arbLevelsVariant,
        fc.option(fc.double({ min: 0, max: 100, noNaN: true, noDefaultInfinity: true }), { nil: undefined }),
        (action, forceStandAside, levels, conviction) => {
          // Guarantee at least one arm of the bug condition holds.
          const isHold = action === 'HOLD';
          const tier = forceStandAside || !isHold ? 'stand_aside' : 'a_plus';

          const plan: AiExecutionPlan = {
            conviction_score: conviction,
            setup_validation: 'chop',
            execution_plan: 'stand aside',
            action,
            opportunity_tier: tier,
            execution_levels: levels,
          };

          // Precondition: this plan satisfies the R1 bug condition.
          fc.pre(action === 'HOLD' || tier === 'stand_aside');

          expect(isActionableTrade(plan)).toBe(false);
        },
      ),
      { numRuns: RUNS },
    );
  });
});

// ───────────────────────────────────────────────────────────────────────────
// Property 9 — directional plan is actionable iff it carries valid levels
// ───────────────────────────────────────────────────────────────────────────

describe('Property 9: directional plans are actionable iff execution_levels holds three finite positive prices', () => {
  // Feature: deep-quant-runtime-hardening, Property 9: for any directional plan,
  // isActionableTrade is true iff execution_levels has three finite positive
  // prices; missing levels -> non-actionable, no prose/last-close derivation.
  // Validates: Requirements 1.5, 1.6, 1.8
  it('actionability equals the presence of three finite positive prices for BUY/SELL plans', () => {
    fc.assert(
      fc.property(
        fc.constantFrom<'BUY' | 'SELL'>('BUY', 'SELL'),
        arbTierNonStandAside,
        arbLevelsVariant,
        fc.option(fc.double({ min: 0, max: 100, noNaN: true, noDefaultInfinity: true }), { nil: undefined }),
        (action, tier, levels, conviction) => {
          const plan: AiExecutionPlan = {
            conviction_score: conviction,
            setup_validation: 'valid setup',
            execution_plan: 'entry/stop/target from args',
            action,
            opportunity_tier: tier,
            execution_levels: levels,
          };

          // The predicate must agree exactly with the finite-positive check —
          // never derived from prose or a last-close fallback.
          expect(isActionableTrade(plan)).toBe(hasThreeFinitePositive(levels));
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('a directional plan with NO execution_levels is non-actionable (no synthesis)', () => {
    fc.assert(
      fc.property(
        fc.constantFrom<'BUY' | 'SELL'>('BUY', 'SELL'),
        arbTierNonStandAside,
        (action, tier) => {
          const plan: AiExecutionPlan = {
            conviction_score: 80,
            setup_validation: 'setup',
            execution_plan: 'Entry 500 SL 490 TP 525', // prose that MUST NOT be scraped
            action,
            opportunity_tier: tier,
            // execution_levels intentionally omitted
          };
          expect(isActionableTrade(plan)).toBe(false);
        },
      ),
      { numRuns: RUNS },
    );
  });
});

// ───────────────────────────────────────────────────────────────────────────
// Property 10 — DECISION reducer never defaults conviction to 75
// ───────────────────────────────────────────────────────────────────────────

const VIEW_KEY = 'CUPID::INTRADAY';

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

describe('Property 10: the DECISION reducer carries conviction verbatim and never defaults to 75', () => {
  // Feature: deep-quant-runtime-hardening, Property 10: for any DECISION
  // payload, the built plan conviction_score equals the payload conviction when
  // present and is undefined when absent — never 75. Validates: Requirement 1.7
  it('built plan conviction equals the payload value when present, undefined when absent (never 75)', () => {
    fc.assert(
      fc.property(
        // conviction present (any finite number, incl. values != 75) or absent.
        fc.option(
          fc.double({ min: -50, max: 200, noNaN: true, noDefaultInfinity: true }),
          { nil: undefined },
        ),
        // Which key the payload uses to carry conviction.
        fc.constantFrom<'conviction_score' | 'conviction'>('conviction_score', 'conviction'),
        fc.constantFrom<'BUY' | 'SELL' | 'HOLD'>('BUY', 'SELL', 'HOLD'),
        // Prose free of `{` so RUN_FINISHED JSON extraction can't interfere.
        fc.stringMatching(/^[a-zA-Z0-9 .,:>=x-]{0,40}$/),
        (conviction, key, action, prose) => {
          resetStore();
          const store = useQuantStore.getState();

          const started: StreamEventPayload = { event: 'RUN_STARTED', data: { thread_id: 't-prop10' } };
          const decisionData: Record<string, unknown> = {
            action,
            rationale: `read ${prose}`,
            execution_plan: `plan ${prose}`,
            thread_id: 't-prop10',
          };
          if (conviction !== undefined) decisionData[key] = conviction;

          store.handleStreamEvent(started);
          store.handleStreamEvent({ event: 'DECISION', data: decisionData });

          const built = useQuantStore.getState().finalTrade;
          expect(built).not.toBeNull();

          if (conviction !== undefined) {
            expect(built!.conviction_score).toBe(conviction);
          } else {
            expect(built!.conviction_score).toBeUndefined();
            expect(built!.conviction_score).not.toBe(75);
          }
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('conviction survives a subsequent RUN_FINISHED without acquiring a 75 default', () => {
    fc.assert(
      fc.property(
        fc.option(
          fc.double({ min: -50, max: 200, noNaN: true, noDefaultInfinity: true }),
          { nil: undefined },
        ),
        fc.constantFrom<'BUY' | 'SELL' | 'HOLD'>('BUY', 'SELL', 'HOLD'),
        (conviction, action) => {
          resetStore();
          const store = useQuantStore.getState();

          const decisionData: Record<string, unknown> = {
            action,
            rationale: 'no braces here just prose',
            execution_plan: 'stop >= 1.5x ATR Target 1',
            thread_id: 't-prop10b',
          };
          if (conviction !== undefined) decisionData.conviction_score = conviction;

          store.handleStreamEvent({ event: 'RUN_STARTED', data: { thread_id: 't-prop10b' } });
          store.handleStreamEvent({ event: 'DECISION', data: decisionData });
          store.handleStreamEvent({ event: 'RUN_FINISHED', data: { thread_id: 't-prop10b', status: 'completed' } });

          const built = useQuantStore.getState().finalTrade;
          expect(built).not.toBeNull();
          if (conviction !== undefined) {
            expect(built!.conviction_score).toBe(conviction);
          } else {
            expect(built!.conviction_score).toBeUndefined();
          }
        },
      ),
      { numRuns: RUNS },
    );
  });
});
