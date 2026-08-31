// @vitest-environment jsdom
//
// Why F&O was blank until a refresh.
//
// `FnoOptionChainTable` returned early on an empty chain BEFORE calling four of
// its hooks (a useMemo for the spot row, a useState for the header expiry
// popover, its ref, and the click-outside useEffect). That is a rules-of-hooks
// violation, and the F&O panel hits it on every cold load:
//
//   render 1 — no snapshot yet, rows === []  -> early return, 3 hooks called
//   render 2 — snapshot arrives, rows filled -> runs to the end, 7 hooks called
//
// React compares the count with the previous render and throws "Rendered more
// hooks than during the previous render", which unmounts the F&O subtree — so the
// panel showed nothing. A refresh appeared to fix it because `useFnoSnapshotCache`
// restores a cached snapshot, making rows non-empty on the FIRST render so the
// count was 7 from the start and never changed.
//
// The empty->populated transition is the whole test. It is driven through a real
// re-render of the same mounted element, because that is the only way the hook
// counts are compared.
import React from 'react';
import { describe, expect, it, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

vi.mock('@/lib/bridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/bridge')>()),
  bridgeInvoke: vi.fn(async () => null),
}));

import FnoOptionChainTable from '../FnoOptionChainTable';
import type { FnoViewState } from '../viewModel';

type ReadyView = FnoViewState & { kind: 'ready' | 'partial' };

/**
 * A ready view-state carrying `strikeCount` strikes.
 *
 * The ladder reads its rows from `viewState.oi.points`, so an empty chain is
 * `oi.points === []` — that is the shape the cold load actually produces.
 */
function viewWith(strikeCount: number): ReadyView {
  const points = Array.from({ length: strikeCount }, (_, i) => ({
    strike: 24000 + i * 100,
    callOi: 1000 + i,
    putOi: 900 + i,
    callPrice: 120 - i,
    putPrice: 80 + i,
    iv: 18,
  }));
  return {
    kind: 'ready',
    snapshotTs: Date.now(),
    marketStatus: 'open',
    oi: { points },
    hud: {
      context: { underlying: 'NIFTY', expiry: '2026-09-24', spot: 24050 },
      maxPain: 24300,
      futuresBasis: null,
    },
  } as unknown as ReadyView;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('FnoOptionChainTable — hook order across the empty→populated transition', () => {
  it('survives rows going from empty to populated in the SAME mounted component', () => {
    // This is the cold-load sequence. Before the fix React threw here and the
    // whole F&O subtree came down.
    const { rerender, container } = render(
      <FnoOptionChainTable viewState={viewWith(0)} fnoExpiry="" expiries={[]} />,
    );
    expect(container.textContent).toContain('No Option Chain Strikes Available');

    expect(() =>
      rerender(
        <FnoOptionChainTable viewState={viewWith(5)} fnoExpiry="" expiries={[]} />,
      ),
    ).not.toThrow();

    // And it actually rendered the ladder, not just avoided throwing.
    expect(container.querySelectorAll('table').length).toBe(1);
  });

  it('survives the reverse transition too (chain drops back to empty)', () => {
    // Same violation in the other direction: fewer hooks than the previous render.
    const { rerender, container } = render(
      <FnoOptionChainTable viewState={viewWith(5)} fnoExpiry="" expiries={[]} />,
    );
    expect(() =>
      rerender(
        <FnoOptionChainTable viewState={viewWith(0)} fnoExpiry="" expiries={[]} />,
      ),
    ).not.toThrow();
    expect(container.textContent).toContain('No Option Chain Strikes Available');
  });

  it('survives repeated flapping between empty and populated', () => {
    // A live panel does this whenever a poll misses: any flap must be survivable,
    // not just the first transition.
    const { rerender } = render(
      <FnoOptionChainTable viewState={viewWith(0)} fnoExpiry="" expiries={[]} />,
    );
    expect(() => {
      for (const n of [3, 0, 7, 0, 2]) {
        rerender(
          <FnoOptionChainTable viewState={viewWith(n)} fnoExpiry="" expiries={[]} />,
        );
      }
    }).not.toThrow();
  });
});
