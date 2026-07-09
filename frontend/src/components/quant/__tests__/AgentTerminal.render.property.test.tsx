// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/no-explicit-any */

// Feature: deep-quant-runtime-hardening
//
// R1 verification / preservation property test for the AgentTerminal render
// seam. This is the POSITIVE-side (preservation) counterpart to the task-1
// bug-condition render test: for ANY committed HOLD / stand_aside decision
// driven through the store to a completed run, the terminal must present the
// non-actionable "Stand Aside — No Trade" state and NEVER an executable card:
//   * NO "Approve & Execute" control (R1.3),
//   * NO synthesized entry/stop/target level cells (R1.4, R1.8),
//   * NO fabricated "75% CONVICTION" badge (R1.7),
//   * NO "ACTIONABLE TRADE PLAN READY" chrome (R1.2).
//
//   Validates: Requirements 1.2, 1.3, 1.4, 1.7, 1.8

import React from 'react';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import fc from 'fast-check';

// The (unclicked) execute handler dynamically imports the Tauri core bridge —
// stub it so render never reaches the real IPC.
vi.mock('@tauri-apps/api/core', () => ({
  invoke: vi.fn(async () => 'ok'),
}));

import AgentTerminal from '../AgentTerminal';
import { useQuantStore, type StreamEventPayload, type ExecutionLevels } from '@/store/useQuantStore';
import { useTradeStore } from '@/store/useTradeStore';

// jsdom lacks scrollIntoView (AgentTerminal auto-scrolls in an effect).
if (!('scrollIntoView' in Element.prototype)) {
  (Element.prototype as any).scrollIntoView = vi.fn();
}

const RUNS = 100;
const SYMBOL = 'CUPID';
const VIEW_KEY = `${SYMBOL}::INTRADAY`;
const LAST_CLOSE = 500;

/** Drive the store as the real stream would for a committed non-actionable
 *  (HOLD / stand_aside) decision, then seed a last-close candle so any
 *  regression that RE-INTRODUCED last-close synthesis would be caught. */
function driveNonActionableRun(opts: {
  action: 'HOLD' | 'BUY' | 'SELL';
  tier?: string;
  conviction?: number;
  levels?: ExecutionLevels;
  prose: string;
}) {
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

  const store = useQuantStore.getState();
  const tid = 't-render-prop';
  const decisionData: Record<string, unknown> = {
    action: opts.action,
    rationale: opts.prose,
    execution_plan: opts.prose,
    thread_id: tid,
  };
  if (opts.tier !== undefined) decisionData.opportunity_tier = opts.tier;
  if (opts.conviction !== undefined) decisionData.conviction_score = opts.conviction;
  if (opts.levels !== undefined) decisionData.execution_levels = opts.levels;

  store.handleStreamEvent({ event: 'RUN_STARTED', data: { thread_id: tid } } as StreamEventPayload);
  store.handleStreamEvent({ event: 'DECISION', data: decisionData } as StreamEventPayload);
  store.handleStreamEvent({ event: 'RUN_FINISHED', data: { thread_id: tid, status: 'completed' } } as StreamEventPayload);

  useTradeStore.setState({
    selectedSymbol: SYMBOL,
    ohlcCandles: [
      {
        symbol: SYMBOL,
        start_timestamp_ms: Date.now(),
        open: LAST_CLOSE,
        high: LAST_CLOSE,
        low: LAST_CLOSE,
        close: LAST_CLOSE,
        volume: 1000,
      },
    ],
  } as any);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// Prose free of `{` so the RUN_FINISHED JSON extractor can't interfere; it
// deliberately contains the misleading "1.5x ATR" / "Target 1" tokens.
const arbProse = fc.stringMatching(/^[a-zA-Z0-9 .,:>=x-]{0,60}$/);
const arbConviction = fc.option(
  fc.double({ min: 0, max: 100, noNaN: true, noDefaultInfinity: true }),
  { nil: undefined },
);
// A validity-irrelevant levels object: even a *valid* directional level set must
// stay gated off when the decision is HOLD / stand_aside.
const arbMaybeLevels = fc.option(
  fc.record({
    entry: fc.double({ min: 1, max: 100000, noNaN: true, noDefaultInfinity: true }),
    stop_loss: fc.double({ min: 1, max: 100000, noNaN: true, noDefaultInfinity: true }),
    take_profit: fc.double({ min: 1, max: 100000, noNaN: true, noDefaultInfinity: true }),
  }),
  { nil: undefined },
);

describe('Property 8 (render): a committed HOLD / stand_aside never renders an executable trade card', () => {
  // Feature: deep-quant-runtime-hardening, Property 8: for any plan with action
  // HOLD or opportunity_tier stand_aside, isActionableTrade is false — the
  // terminal renders a Stand Aside panel with no control, no level cells, no
  // synthesized conviction. Validates: Requirements 1.2, 1.3, 1.4, 1.7, 1.8
  it('renders a Stand Aside panel and no Approve/levels/conviction chrome for any HOLD or stand_aside decision', () => {
    fc.assert(
      fc.property(
        // At least one arm of the bug condition holds: action HOLD OR tier stand_aside.
        fc.oneof(
          fc.record({ action: fc.constant<'HOLD'>('HOLD'), standAside: fc.boolean() }),
          fc.record({ action: fc.constantFrom<'BUY' | 'SELL'>('BUY', 'SELL'), standAside: fc.constant(true) }),
        ),
        arbConviction,
        arbMaybeLevels,
        arbProse,
        (base, conviction, levels, prose) => {
          const tier = base.standAside || base.action !== 'HOLD' ? 'stand_aside' : 'a_plus';
          driveNonActionableRun({
            action: base.action,
            tier,
            conviction,
            levels: levels ?? undefined,
            prose: `read ${prose}`,
          });

          render(React.createElement(AgentTerminal));

          expect(useQuantStore.getState().sessionStatus).toBe('complete');

          // R1.3 — no APPROVE & EXECUTE control.
          expect(screen.queryByRole('button', { name: /approve\s*&\s*execute/i })).toBeNull();
          // R1.2 — no actionable-plan chrome; a Stand Aside panel instead.
          expect(screen.queryByText(/actionable trade plan ready/i)).toBeNull();
          expect(screen.queryByText(/stand aside\s*—\s*no trade/i)).not.toBeNull();
          // R1.4/R1.8 — no last-close-synthesized level cells.
          expect(screen.queryByText('₹500.00')).toBeNull();
          expect(screen.queryByText('₹490.00')).toBeNull();
          expect(screen.queryByText('₹525.00')).toBeNull();
          // R1.7 — no fabricated 75% conviction badge.
          expect(screen.queryByText(/75%\s*conviction/i)).toBeNull();

          cleanup();
        },
      ),
      { numRuns: RUNS },
    );
  });

  // Feature: deep-quant-runtime-hardening, Property 9 (render): a directional
  // BUY/SELL decision with NO validated execution_levels stays non-actionable —
  // no Approve control, no synthesized levels. Validates: Requirements 1.5, 1.6, 1.8
  it('renders no executable card for a directional decision that carries no execution_levels', () => {
    fc.assert(
      fc.property(
        fc.constantFrom<'BUY' | 'SELL'>('BUY', 'SELL'),
        arbConviction,
        arbProse,
        (action, conviction, prose) => {
          driveNonActionableRun({
            action,
            tier: 'a_plus',
            conviction,
            levels: undefined, // no validated levels → must not synthesize
            prose: `entry stop target ${prose}`,
          });

          render(React.createElement(AgentTerminal));

          expect(useQuantStore.getState().sessionStatus).toBe('complete');
          expect(screen.queryByRole('button', { name: /approve\s*&\s*execute/i })).toBeNull();
          expect(screen.queryByText(/actionable trade plan ready/i)).toBeNull();
          expect(screen.queryByText('₹500.00')).toBeNull();
          expect(screen.queryByText('₹490.00')).toBeNull();
          expect(screen.queryByText('₹525.00')).toBeNull();

          cleanup();
        },
      ),
      { numRuns: RUNS },
    );
  });
});
