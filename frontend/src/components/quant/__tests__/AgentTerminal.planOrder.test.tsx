// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/no-explicit-any */

// The trade plan must sit where the run finished, not at the bottom of the log.
//
// `AgentTerminal` rendered `<ActionableTradePlan />` AFTER `<QaMessages />`, so
// every follow-up question and answer was inserted ABOVE the plan and the plan
// slid to the end of the log each time. It read as a footer pinned under
// whatever was said last, detached from the run that produced it — and the
// stand-aside branch a few lines earlier already rendered inline correctly,
// so the two branches disagreed.
//
// Nothing was `position: sticky`; it was purely JSX order, which is exactly what
// a DOM-order assertion can hold in place. These tests compare document
// positions rather than snapshotting markup, so they survive restyling of either
// block and fail only if the sequence changes.
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

vi.mock('@/lib/bridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/bridge')>()),
  bridgeInvoke: vi.fn(async () => 'ok'),
}));

import AgentTerminal from '../AgentTerminal';
import { useQuantStore, type StreamEventPayload, type QaChatMessage } from '@/store/useQuantStore';
import { useTradeStore } from '@/store/useTradeStore';

// jsdom has no scrollIntoView and AgentTerminal auto-scrolls in an effect.
if (!('scrollIntoView' in Element.prototype)) {
  (Element.prototype as any).scrollIntoView = vi.fn();
}

const SYMBOL = 'RELIANCE';
const VIEW_KEY = `${SYMBOL}::INTRADAY`;

// The store slices this file writes, so afterEach can put them back.
//
// Zustand stores are module singletons shared by every test file running in the
// same vitest worker, and `cleanup()` only unmounts React — it does not touch
// them. Leaving a finished session and a selected symbol behind leaked into
// later files in the worker and broke an unrelated LeftPanel F&O test. Snapshot
// and restore rather than reset-to-a-guess, so this file cannot decide what
// another file's starting state should be.
const QUANT_KEYS = [
  'sessionsByKey', '_threadToKey', '_streamingKey', 'activeViewKey',
  'finalTrade', 'aiPlan', 'sessionStatus', 'reasoningSteps', 'qaMessages',
] as const;
const TRADE_KEYS = ['selectedSymbol'] as const;

function snapshot(store: any, keys: readonly string[]) {
  const s = store.getState();
  return Object.fromEntries(keys.map((k) => [k, s[k]]));
}

let quantSnapshot: Record<string, unknown>;
let tradeSnapshot: Record<string, unknown>;

/** The SELL setup from the reported session, driven through the real reducer. */
function driveActionableRunToComplete() {
  useQuantStore.setState({
    sessionsByKey: {},
    _threadToKey: {},
    _streamingKey: VIEW_KEY,
    activeViewKey: VIEW_KEY,
    finalTrade: null,
    aiPlan: null,
    sessionStatus: 'idle',
    reasoningSteps: [],
    qaMessages: [],
  } as any);

  const store = useQuantStore.getState();
  store.handleStreamEvent({ event: 'RUN_STARTED', data: { thread_id: 't-plan' } } as StreamEventPayload);
  store.handleStreamEvent({
    event: 'DECISION',
    data: {
      action: 'SELL',
      conviction: 62,
      rationale: 'Price accepted below VAL; trend and forecast align short.',
      execution_plan: 'Sell into the retest, stop above the neckline.',
      execution_levels: { entry: 1278.4, stop_loss: 1284.6, take_profit: 1266.0 },
      thread_id: 't-plan',
    },
  } as StreamEventPayload);
  store.handleStreamEvent({
    event: 'RUN_FINISHED',
    data: { thread_id: 't-plan', status: 'completed' },
  } as StreamEventPayload);

  useTradeStore.setState({ selectedSymbol: SYMBOL } as any);
}

/** Two follow-up turns, as asking a question after the run produces. */
function seedQaTurns() {
  const qaMessages: QaChatMessage[] = [
    { id: 'q1', role: 'user', content: 'WHICH_TOOLS_WERE_UNRELIABLE' },
    { id: 'a1', role: 'assistant', content: 'EVENT_RISK_WAS_UNAVAILABLE' },
  ];
  useQuantStore.setState({ qaMessages } as any);
}

/** Document position of the plan card, located via its ENTRY cell label. */
function planNode(): HTMLElement {
  const label = screen.getByText(/ENTRY/i);
  return label.closest('div.w-full.rounded') ?? label;
}

beforeEach(() => {
  quantSnapshot = snapshot(useQuantStore, QUANT_KEYS);
  tradeSnapshot = snapshot(useTradeStore, TRADE_KEYS);
  driveActionableRunToComplete();
});

afterEach(() => {
  cleanup();
  // Put both stores back before the next file in this worker sees them.
  useQuantStore.setState(quantSnapshot as any);
  useTradeStore.setState(tradeSnapshot as any);
  vi.clearAllMocks();
});

describe('AgentTerminal — the trade plan stays in the chat flow', () => {
  it('renders the plan when the run commits an actionable trade', () => {
    render(<AgentTerminal />);
    // Guard for the tests below: if the plan never rendered, an order assertion
    // would pass vacuously.
    expect(screen.getByText(/ENTRY/i)).toBeInTheDocument();
    expect(screen.getByText(/1,?278\.40|1278\.4/)).toBeInTheDocument();
  });

  it('places the plan BEFORE the follow-up Q&A turns in the DOM', () => {
    seedQaTurns();
    render(<AgentTerminal />);

    const plan = planNode();
    const firstQuestion = screen.getByText('WHICH_TOOLS_WERE_UNRELIABLE');

    // DOCUMENT_POSITION_FOLLOWING === 4: the question comes after the plan.
    // This is the regression: with the old order the plan followed the Q&A.
    expect(plan.compareDocumentPosition(firstQuestion) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it('keeps the plan above EVERY subsequent turn, not just the first', () => {
    seedQaTurns();
    render(<AgentTerminal />);

    const plan = planNode();
    for (const text of ['WHICH_TOOLS_WERE_UNRELIABLE', 'EVENT_RISK_WAS_UNAVAILABLE']) {
      const turn = screen.getByText(text);
      expect(plan.compareDocumentPosition(turn) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
        Node.DOCUMENT_POSITION_FOLLOWING,
      );
    }
  });

  it('does not pin the plan with sticky or fixed positioning', () => {
    render(<AgentTerminal />);
    // Ordering is the fix. If someone later reaches for `sticky bottom-0` to
    // achieve the same thing, the plan would detach from the flow again — which
    // is the behaviour being removed here.
    let node: HTMLElement | null = planNode();
    while (node) {
      expect(node.className).not.toMatch(/\bsticky\b/);
      expect(node.className).not.toMatch(/\bfixed\b/);
      node = node.parentElement;
    }
  });

  it('still renders the plan when there are no Q&A turns at all', () => {
    render(<AgentTerminal />);
    expect(screen.getByText(/ENTRY/i)).toBeInTheDocument();
    expect(screen.queryByText('WHICH_TOOLS_WERE_UNRELIABLE')).not.toBeInTheDocument();
  });
});
