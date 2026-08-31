// @vitest-environment jsdom
/**
 * The chart must SURVIVE a workspace-mode switch.
 *
 * Reported bug: moving between Intraday → Swing → Investor reloaded the same
 * instrument's candles every time. The cause was structural, not data-related.
 * `page.tsx` selected between three separate layout components with
 * `switch (activeProfile)`, so each switch changed the element TYPE at that
 * position — and React's reconciler must unmount and remount on a type change.
 * That tore down the entire TradingView subtree (`widget.remove()`, a fresh
 * widget construction, a new datafeed, another `getBars` round trip) even though
 * nothing about the chart differs across those three modes.
 *
 * The three layouts were byte-for-byte identical apart from a wrapper `id` that
 * nothing referenced, so they were replaced by one `TerminalChartPane`.
 *
 * WHAT THIS FILE NOW GUARDS, AND WHY IT CHANGED
 *
 * The previous version of this file rendered each layout component in ISOLATION
 * and asserted that an F&O round-trip in the store did not remount its chart.
 * That assertion was true and stayed true — the components never subscribed to
 * `activeProfile`. It simply could not observe the bug, because the remount was
 * caused by the PAGE's choice of component type, one level above anything the
 * test rendered. So the tests below drive the page-level mapping instead: they
 * mount a harness that mirrors `renderProfileContent` and count chart mounts
 * across real mode switches.
 *
 * The mount COUNT is the whole point. A test that only checked "a chart is
 * present after switching" passes just as happily when the chart was destroyed
 * and rebuilt, which is the exact bug.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// ── Mount registry (hoisted so the mock factory can reference it) ─────────────
const charts = vi.hoisted(() => ({
  mounts: 0,
  unmounts: 0,
  reset() {
    this.mounts = 0;
    this.unmounts = 0;
  },
}));

// Mock the heavy chart child: jsdom must never initialise a canvas/chart engine.
// It records mounts AND unmounts, which together prove the instance is reused.
vi.mock('../../MainTerminalChart', async () => {
  const ReactNs = await import('react');
  return {
    __esModule: true,
    default: function MockMainTerminalChart() {
      ReactNs.useEffect(() => {
        charts.mounts += 1;
        return () => {
          charts.unmounts += 1;
        };
      }, []);
      return ReactNs.createElement('div', { 'data-testid': 'main-chart' });
    },
  };
});

// Import AFTER the mock so the pane picks up the mocked chart child.
import TerminalChartPane from '../TerminalChartPane';
import { useTradeStore, type TradeProfile } from '../../../store/useTradeStore';

/** The three modes that share one chart workspace. */
const SHARED_CHART_MODES: Exclude<TradeProfile, 'FNO'>[] = ['INTRADAY', 'SWING', 'INVESTOR'];

/**
 * Mirrors `page.tsx`'s `renderProfileContent` for the non-split, non-F&O modes.
 *
 * Deliberately a mirror rather than an import of the page: `page.tsx` pulls in the
 * whole terminal (WebSockets, auth, the feature store), which jsdom cannot host.
 * The property under test is the MAPPING from mode to element type, which is what
 * this reproduces.
 */
function Harness({ profile }: { profile: TradeProfile }) {
  return <TerminalChartPane activeProfile={profile} />;
}

function resetStore() {
  useTradeStore.setState({ activeProfile: 'INTRADAY' });
  charts.reset();
}

describe('Mode switching reuses the chart instead of rebuilding it', () => {
  beforeEach(() => resetStore());
  afterEach(() => cleanup());

  it('mounts the chart exactly once across Intraday → Swing → Investor', () => {
    const { rerender } = render(<Harness profile="INTRADAY" />);
    expect(charts.mounts).toBe(1);

    // The reported journey.
    act(() => useTradeStore.getState().setActiveProfile('SWING'));
    rerender(<Harness profile="SWING" />);

    act(() => useTradeStore.getState().setActiveProfile('INVESTOR'));
    rerender(<Harness profile="INVESTOR" />);

    // THE ASSERTION. Still one mount and zero unmounts means the widget was never
    // torn down, so no re-initialisation and no refetch of the same candles.
    expect(charts.mounts).toBe(1);
    expect(charts.unmounts).toBe(0);
    expect(screen.getAllByTestId('main-chart')).toHaveLength(1);
  });

  it('survives switching back and forth repeatedly', () => {
    const { rerender } = render(<Harness profile="INTRADAY" />);
    for (let i = 0; i < 3; i++) {
      for (const profile of SHARED_CHART_MODES) {
        act(() => useTradeStore.getState().setActiveProfile(profile));
        rerender(<Harness profile={profile} />);
      }
    }
    expect(charts.mounts).toBe(1);
    expect(charts.unmounts).toBe(0);
  });

  it('keeps the same DOM node for the chart across a switch', () => {
    // Belt-and-braces on the mount counter: an identical node proves React
    // reconciled rather than replaced the subtree.
    const { rerender } = render(<Harness profile="INTRADAY" />);
    const before = screen.getByTestId('main-chart');

    rerender(<Harness profile="INVESTOR" />);
    const after = screen.getByTestId('main-chart');

    expect(after).toBe(before);
  });

  it('updates the workspace wrapper id per mode without remounting', () => {
    // The `id` is the one thing that legitimately differs between these modes, so
    // it must still track the active mode — as an attribute patch, not a rebuild.
    const { container, rerender } = render(<Harness profile="INTRADAY" />);
    expect(container.querySelector('#intraday-hud')).not.toBeNull();

    rerender(<Harness profile="SWING" />);
    expect(container.querySelector('#swing-hud')).not.toBeNull();
    expect(container.querySelector('#intraday-hud')).toBeNull();

    rerender(<Harness profile="INVESTOR" />);
    expect(container.querySelector('#investor-hud')).not.toBeNull();

    expect(charts.mounts).toBe(1);
  });
});

describe('The shared workspace renders one chart and no split surface (R4.7)', () => {
  beforeEach(() => resetStore());
  afterEach(() => cleanup());

  it.each(SHARED_CHART_MODES)('%s renders exactly one chart', (profile) => {
    render(<Harness profile={profile} />);
    expect(screen.getAllByTestId('main-chart')).toHaveLength(1);
    expect(charts.mounts).toBe(1);
  });

  it.each(['SWING', 'INVESTOR'] as const)(
    '%s hosts no split-pane surface or control — split is mode-gated to Intraday/F&O',
    (profile) => {
      render(<Harness profile={profile} />);
      expect(document.querySelector('[data-pane-id]')).toBeNull();
      expect(document.querySelector('[data-testid="split-chart-container"]')).toBeNull();
      expect(screen.queryByRole('button', { name: /split/i })).toBeNull();
    },
  );
});

describe('Property 9 — an F&O round-trip leaves the workspace unchanged (R7.1)', () => {
  beforeEach(() => resetStore());
  afterEach(() => cleanup());

  it.each(SHARED_CHART_MODES)(
    '%s renders byte-for-byte identically before vs after entering/leaving F&O',
    (profile) => {
      act(() => useTradeStore.getState().setActiveProfile(profile));
      const { container, rerender } = render(<Harness profile={profile} />);
      const baseline = container.innerHTML;

      act(() => useTradeStore.getState().setActiveProfile('FNO'));
      expect(useTradeStore.getState().activeProfile).toBe('FNO');
      act(() => useTradeStore.getState().setActiveProfile(profile));
      expect(useTradeStore.getState().activeProfile).toBe(profile);

      rerender(<Harness profile={profile} />);
      expect(container.innerHTML).toBe(baseline);
    },
  );

  it('does not subscribe to activeProfile, so a store-only mode change is inert', () => {
    render(<Harness profile="INTRADAY" />);
    expect(charts.mounts).toBe(1);

    act(() => useTradeStore.getState().setActiveProfile('FNO'));
    act(() => useTradeStore.getState().setActiveProfile('INTRADAY'));

    expect(charts.mounts).toBe(1);
    expect(charts.unmounts).toBe(0);
  });
});
