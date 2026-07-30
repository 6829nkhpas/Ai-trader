// Regression: RUN_FINISHED must never downgrade a committed BUY/SELL.
//
// `graph.py` tells the model to restate a final JSON conviction block AFTER
// `declare_trade` succeeds. That block carries only conviction_score /
// setup_validation / execution_plan — no `action`, no `execution_levels`. The
// old RUN_FINISHED reducer assigned that scraped plan straight over
// finalTrade/aiPlan, stripping `action: 'BUY'` and the validated levels off an
// already-committed trade. `isActionableTrade` then failed and the UI rendered
// "Stand Aside — No Trade" for every run regardless of what the backend
// validated — the "declare_trade always gives HOLD" symptom.
//
// Property: for any committed directional DECISION followed by any closing
// monologue, the plan surviving RUN_FINISHED keeps the decision's action, tier
// and levels, and stays actionable.

import { describe, it, expect, beforeEach } from 'vitest';
import fc from 'fast-check';
import {
  useQuantStore,
  isActionableTrade,
  mergeFinalPlan,
  type StreamEventPayload,
  type AiExecutionPlan,
} from '../useQuantStore';

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
  });
}

const ev = (e: StreamEventPayload) => useQuantStore.getState().handleStreamEvent(e);

beforeEach(resetStore);

describe('RUN_FINISHED preserves a committed directional decision', () => {
  it('property: action + execution_levels survive any closing monologue', () => {
    fc.assert(
      fc.property(
        fc.constantFrom('BUY', 'SELL'),
        fc.integer({ min: 1, max: 100 }),
        fc.double({ min: 100, max: 50_000, noNaN: true, noDefaultInfinity: true }),
        fc.string({ minLength: 0, maxLength: 40 }),
        (action, conviction, entry, closingProse) => {
          resetStore();
          const levels = { entry, stop_loss: entry * 0.99, take_profit: entry * 1.02 };

          ev({ event: 'RUN_STARTED', data: { thread_id: 't-1' } });
          ev({
            event: 'DECISION',
            data: {
              thread_id: 't-1',
              action,
              conviction_score: conviction,
              opportunity_tier: 'b_continuation',
              rationale: 'Reclaim above VWAP with confluence.',
              execution_levels: levels,
            },
          });
          expect(isActionableTrade(useQuantStore.getState().finalTrade)).toBe(true);

          // The model's closing JSON restatement — action-less and levels-less.
          ev({
            event: 'TEXT_MESSAGE',
            data: {
              thread_id: 't-1',
              content: `${closingProse}{"conviction_score": ${conviction}, "setup_validation": "Restated.", "execution_plan": "Scale out at R1."}`,
            },
          });
          ev({ event: 'RUN_FINISHED', data: { thread_id: 't-1', status: 'completed' } });

          const after = useQuantStore.getState().finalTrade;
          expect(after?.action).toBe(action);
          expect(after?.execution_levels).toEqual(levels);
          expect(after?.opportunity_tier).toBe('b_continuation');
          expect(isActionableTrade(after)).toBe(true);
          // The scrape may still enrich prose the decision left empty.
          expect(after?.execution_plan).toBe('Scale out at R1.');
          expect(useQuantStore.getState().aiPlan?.action).toBe(action);
        },
      ),
      { numRuns: 60 },
    );
  });

  it('a committed HOLD stays non-actionable after RUN_FINISHED', () => {
    ev({ event: 'RUN_STARTED', data: { thread_id: 't-h' } });
    ev({
      event: 'DECISION',
      data: { thread_id: 't-h', action: 'HOLD', opportunity_tier: 'stand_aside', rationale: 'Chop, no edge.' },
    });
    ev({
      event: 'TEXT_MESSAGE',
      data: { thread_id: 't-h', content: '{"conviction_score": 90, "execution_plan": "Long here."}' },
    });
    ev({ event: 'RUN_FINISHED', data: { thread_id: 't-h', status: 'completed' } });

    const after = useQuantStore.getState().finalTrade;
    expect(after?.action).toBe('HOLD');
    expect(after?.execution_levels).toBeUndefined();
    expect(isActionableTrade(after)).toBe(false);
  });

  it('with no committed decision, the scraped plan is still used', () => {
    ev({ event: 'RUN_STARTED', data: { thread_id: 't-n' } });
    ev({
      event: 'TEXT_MESSAGE',
      data: { thread_id: 't-n', content: '{"conviction_score": 55, "setup_validation": "Weak.", "execution_plan": "Wait."}' },
    });
    ev({ event: 'RUN_FINISHED', data: { thread_id: 't-n', status: 'completed' } });

    const after = useQuantStore.getState().finalTrade;
    expect(after?.conviction_score).toBe(55);
    // Scraped text never confers directional identity.
    expect(after?.action).toBeUndefined();
    expect(isActionableTrade(after)).toBe(false);
  });
});

describe('mergeFinalPlan', () => {
  const committed: AiExecutionPlan = {
    conviction_score: 68,
    setup_validation: 'Committed thesis.',
    execution_plan: '',
    action: 'BUY',
    opportunity_tier: 'b_continuation',
    execution_levels: { entry: 100, stop_loss: 99, take_profit: 103 },
  };

  it('is a no-op passthrough when either side is null', () => {
    expect(mergeFinalPlan(null, null)).toBeNull();
    expect(mergeFinalPlan(committed, null)).toEqual(committed);
    const scraped: AiExecutionPlan = { conviction_score: 1, setup_validation: 'a', execution_plan: 'b' };
    expect(mergeFinalPlan(null, scraped)).toEqual(scraped);
  });

  it('never lets a scrape override directional identity', () => {
    const hostile: AiExecutionPlan = {
      conviction_score: 99,
      setup_validation: 'Scraped thesis.',
      execution_plan: 'Scraped plan.',
      action: 'SELL',
      opportunity_tier: 'a_plus',
      execution_levels: { entry: 1, stop_loss: 2, take_profit: 3 },
    };
    const merged = mergeFinalPlan(committed, hostile);
    expect(merged?.action).toBe('BUY');
    expect(merged?.opportunity_tier).toBe('b_continuation');
    expect(merged?.execution_levels).toEqual(committed.execution_levels);
    expect(merged?.conviction_score).toBe(68);
    expect(merged?.setup_validation).toBe('Committed thesis.');
    // Only the genuinely-empty field is filled from the scrape.
    expect(merged?.execution_plan).toBe('Scraped plan.');
  });
});
