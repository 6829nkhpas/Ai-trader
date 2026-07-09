// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/no-explicit-any */

// Feature: deep-quant-runtime-hardening (bugfix)
//
// Property 8 (Bug Condition), frontend AgentTerminal render seam —
// "HOLD / stand-aside must never render as an executable trade":
//
//   Feeding a concrete committed HOLD DECISION (action: "HOLD", no
//   execution_levels, prose containing "stop >= 1.5x ATR" / "Target 1",
//   conviction absent) through the store reducer and then rendering
//   AgentTerminal after RUN_FINISHED, the terminal should present a
//   non-actionable "no trade" state:
//     * NO "Approve & Execute" control (R1.3),
//     * NO synthesized entry/stop/target cells derived from the last close
//       (R1.4, R1.8),
//     * NO fabricated "75% CONVICTION" badge (R1.7).
//
//   Validates: Requirements 1.2, 1.3, 1.4, 1.7
//
// *** EXPLORATION TEST — EXPECTED TO FAIL ON UNFIXED CODE ***
//
// The unfixed AgentTerminal renders the Trade_Plan_Card whenever
// `sessionStatus === 'complete' && finalTrade && parsedPlan`, with NO gate on
// action/tier. `parsePlanDetails()` finds no numeric prose, so it SYNTHESIZES
// entry = lastClose, stopLoss = entry * 0.98, takeProfit = entry * 1.05 and a
// default BUY side, and the reducer defaults conviction to 75. So a committed
// HOLD renders as an executable APPROVE & EXECUTE card with fabricated levels.
// The failures below are the informative, expected outcome (the R1
// capital-safety counterexamples). DO NOT fix the code here; tasks 2.2–2.4 gate
// the card behind `isActionableTrade`, and task 2.6 re-runs THIS SAME test.

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// Tauri IPC is only touched by the (unclicked) execute handler; stub it so the
// dynamic import never reaches the real bridge during render.
vi.mock('@tauri-apps/api/core', () => ({
  invoke: vi.fn(async () => 'ok'),
}));

import AgentTerminal from '../AgentTerminal';
import { useQuantStore, type StreamEventPayload } from '@/store/useQuantStore';
import { useTradeStore } from '@/store/useTradeStore';

// jsdom does not implement scrollIntoView (AgentTerminal auto-scrolls in an
// effect). Stub it so the render completes and the tests fail for the RIGHT
// reason — the trade card actually rendering — rather than an unrelated error.
if (!('scrollIntoView' in Element.prototype)) {
  (Element.prototype as any).scrollIntoView = vi.fn();
}

const SYMBOL = 'CUPID';
const VIEW_KEY = `${SYMBOL}::INTRADAY`;
const LAST_CLOSE = 500;

/** Drive the store as the real stream would: RUN_STARTED → HOLD DECISION →
 *  RUN_FINISHED(completed), then seed the last-close candle the unfixed
 *  synthesis reads from. */
function driveHoldRunToComplete() {
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

  const store = useQuantStore.getState();
  const started: StreamEventPayload = { event: 'RUN_STARTED', data: { thread_id: 't-hold' } };
  const decision: StreamEventPayload = {
    event: 'DECISION',
    data: {
      action: 'HOLD',
      opportunity_tier: 'stand_aside',
      // conviction ABSENT.
      rationale: 'Standing aside — chop, no edge.',
      execution_plan: 'No trade. Rule: stop >= 1.5x ATR. Target 1: reassess on a clean break.',
      thread_id: 't-hold',
    },
  };
  const finished: StreamEventPayload = { event: 'RUN_FINISHED', data: { thread_id: 't-hold', status: 'completed' } };

  store.handleStreamEvent(started);
  store.handleStreamEvent(decision);
  store.handleStreamEvent(finished);

  // Seed the last-close candle the unfixed parser falls back to for synthesis.
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

beforeEach(() => {
  driveHoldRunToComplete();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('R1 AgentTerminal: a committed HOLD must not render an executable trade card — EXPECTED FAIL on unfixed code', () => {
  it('does not render an APPROVE & EXECUTE control for a HOLD decision', () => {
    render(React.createElement(AgentTerminal));

    // Sanity: the run reached a terminal complete state.
    expect(useQuantStore.getState().sessionStatus).toBe('complete');

    // R1.3 — EXPECTED FAIL on unfixed code: the card synthesizes a plan and
    // renders the "Approve & Execute (Virtual)" button for a HOLD.
    const approve = screen.queryByRole('button', { name: /approve\s*&\s*execute/i });
    expect(approve).toBeNull();
  });

  it('does not synthesize entry/stop/target levels from the last close for a HOLD', () => {
    render(React.createElement(AgentTerminal));

    // R1.4/R1.8 — EXPECTED FAIL on unfixed code: parsePlanDetails synthesizes
    // entry ≈ lastClose (₹500.00), SL ≈ entry*0.98 (₹490.00), TP ≈ entry*1.05
    // (₹525.00) and renders them in the trade card.
    expect(screen.queryByText('₹500.00')).toBeNull(); // synthesized entry
    expect(screen.queryByText('₹490.00')).toBeNull(); // synthesized stop-loss
    expect(screen.queryByText('₹525.00')).toBeNull(); // synthesized take-profit

    // The actionable-plan chrome must not appear for a HOLD.
    expect(screen.queryByText(/actionable trade plan ready/i)).toBeNull();
  });

  it('does not present a fabricated 75% conviction badge when conviction was absent', () => {
    render(React.createElement(AgentTerminal));

    // R1.7 — EXPECTED FAIL on unfixed code: conviction defaults to 75 and the
    // card renders a "75% CONVICTION" badge for a HOLD that emitted none.
    expect(screen.queryByText(/75%\s*conviction/i)).toBeNull();
  });
});
