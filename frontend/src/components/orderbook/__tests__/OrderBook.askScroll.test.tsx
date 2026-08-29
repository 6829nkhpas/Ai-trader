// @vitest-environment jsdom
/**
 * OrderBook — the ask ladder must be a working scroll container.
 *
 * The reported bug: the green (bid) ladder scrolled, the red (ask) ladder did
 * not, and its topmost rows were clipped with no way to reach them.
 *
 * The cause was CSS, not data. The ask container was
 * `flex flex-col justify-end ... overflow-y-auto`. When a flex container uses
 * `justify-content: flex-end` and its content overflows, the overflow happens
 * past the BLOCK-START (top) edge — and `scrollTop` cannot go below 0, so those
 * rows are unreachable in every major browser. The bid container never had
 * `justify-end`, which is precisely why one side worked and the other didn't.
 * Bottom alignment now comes from an auto margin on an inner wrapper, which
 * resolves to 0 once the content overflows and so leaves scrolling intact.
 *
 * jsdom performs no layout, so it cannot observe the actual clipping. What it CAN
 * pin is the class contract that caused it — that the ask ladder never again
 * combines `overflow-y-auto` with `justify-end`, and that both ladders scroll
 * the same way. That is the regression this file exists to catch.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// ── Transport stubs ───────────────────────────────────────────────────────
// The component polls Kite for depth and subscribes to a bridge event. Neither
// is under test here; both are stubbed so the render is deterministic.
// Never settles, so the depth poll cannot land a state update mid-assertion
// (which would be an un-acted React update and pure noise here).
vi.mock('../../../lib/kiteFetch', () => ({
  kiteFetch: vi.fn(() => new Promise(() => {})),
}));
vi.mock('../../../lib/bridge', () => ({
  bridgeListen: vi.fn(async () => () => {}),
}));
// framer-motion's AnimatePresence/motion are irrelevant to the class contract.
vi.mock('framer-motion', () => ({
  AnimatePresence: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  motion: { div: (p: Record<string, unknown>) => <div {...p} /> },
}));

import OrderBook from '../../OrderBook';
import { useTradeStore } from '../../../store/useTradeStore';
import { buildBookFromKiteDepth, BOOK_CACHE_VERSION } from '../orderBookHelpers';

/** A book deep enough on both sides to overflow a real pane. */
function seedBook() {
  const buy = Array.from({ length: 5 }, (_, i) => ({ price: 1308.6 - i * 0.1, quantity: 100 + i }));
  const sell = Array.from({ length: 5 }, (_, i) => ({ price: 1308.7 + i * 0.1, quantity: 100 + i }));
  return buildBookFromKiteDepth({ buy, sell })!;
}

/** The two ladder scroll containers, in DOM order: [asks, bids]. */
function ladders(container: HTMLElement): HTMLElement[] {
  return Array.from(
    container.querySelectorAll<HTMLElement>('div.overflow-y-auto'),
  ).filter((el) => el.className.includes('flex-col'));
}

describe('OrderBook — ask ladder scrollability', () => {
  beforeEach(() => {
    useTradeStore.setState({ selectedSymbol: 'RELIANCE' });
    // Seed a populated book straight into localStorage: the component restores
    // it synchronously on mount, so no polling is needed to get rows rendered.
    // Key must match the component's versioned key, hence the shared constant
    // rather than a hardcoded string that would silently stop matching.
    localStorage.setItem(
      `ai-trader-orderbook-${BOOK_CACHE_VERSION}-RELIANCE`,
      JSON.stringify(seedBook()),
    );
  });

  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.clearAllMocks();
  });

  it('renders both ladders as scroll containers', () => {
    const { container } = render(<OrderBook />);
    expect(ladders(container)).toHaveLength(2);
  });

  it('never combines justify-end with overflow-y-auto on the ask ladder', () => {
    const { container } = render(<OrderBook />);
    const [asks] = ladders(container);

    expect(asks.className).toContain('overflow-y-auto');
    // The regression. `justify-end` here makes upward overflow unreachable.
    expect(asks.className).not.toContain('justify-end');
    expect(asks.className).not.toContain('justify-center');
  });

  it('keeps the bid ladder scrollable too, with the same scroll setup', () => {
    const { container } = render(<OrderBook />);
    const [asks, bids] = ladders(container);

    // Whatever alignment trick the ask side uses, the two ladders must agree on
    // how they scroll — the original bug was exactly a divergence here.
    for (const cls of ['overflow-y-auto', 'min-h-0', 'flex-1']) {
      expect(asks.className).toContain(cls);
      expect(bids.className).toContain(cls);
    }
    expect(bids.className).not.toContain('justify-end');
  });

  it('bottom-aligns the asks via an auto margin rather than justify-content', () => {
    const { container } = render(<OrderBook />);
    const [asks] = ladders(container);

    // The rows live in a wrapper carrying `mt-auto`: same visual result when
    // there is spare room, but it resolves to 0 on overflow so scroll works.
    const wrapper = asks.querySelector<HTMLElement>(':scope > div.mt-auto');
    expect(wrapper).not.toBeNull();
    expect(wrapper!.childElementCount).toBeGreaterThan(0);
  });

  it('anchors the ask ladder to its bottom so the best ask stays visible', () => {
    // The builder reverses the asks, so the best ask (nearest the mid) is LAST.
    // At scrollTop 0 a scroll container shows the FARTHEST asks, which would hide
    // the level that actually matters — hence the explicit bottom anchor.
    //
    // jsdom does no layout, so scrollHeight is natively 0 and asserting
    // `scrollTop === scrollHeight` would be 0 === 0 — true even with the anchor
    // removed. Give scrollHeight a non-zero value so the assertion can only pass
    // if the component really wrote to scrollTop.
    const SCROLL_HEIGHT = 420;
    const protoSpy = vi
      .spyOn(HTMLElement.prototype, 'scrollHeight', 'get')
      .mockReturnValue(SCROLL_HEIGHT);
    const rafSpy = vi
      .spyOn(window, 'requestAnimationFrame')
      .mockImplementation((cb: FrameRequestCallback) => {
        cb(0);
        return 1;
      });

    const { container } = render(<OrderBook />);
    const [asks] = ladders(container);

    expect(rafSpy).toHaveBeenCalled();
    expect(asks.scrollTop).toBe(SCROLL_HEIGHT);

    rafSpy.mockRestore();
    protoSpy.mockRestore();
  });
});
