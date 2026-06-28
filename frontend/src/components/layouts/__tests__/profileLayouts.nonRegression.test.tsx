// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/no-explicit-any */

/**
 * Terminal UX Overhaul (Task 11.2) — non-regression component tests for the
 * EXISTING profile layout components themselves (`IntradayLayout`, `SwingLayout`,
 * `InvestorLayout`).
 *
 * Where Task 7.2's `TerminalLayout.modeSelector.test.tsx` covers Property 9 at
 * the HARNESS level (mirroring page.tsx's `switch(activeProfile)` and asserting
 * the workspace MAPPING is unchanged across an F&O round-trip), this file adds
 * focused, complementary coverage of the actual rendered layout COMPONENTS:
 *
 *  - Each existing-profile layout renders its expected workspace structure
 *    (its profile-scoped wrapper `#…-hud` + a single mounted chart instance).
 *  - Rendering the same layout BEFORE vs AFTER an F&O round-trip (set
 *    `activeProfile` to `FNO` and back via the REAL `useTradeStore`) produces a
 *    byte-for-byte identical rendered structure — the mode-model migration does
 *    not perturb these components (Property 9 / R7.1).
 *  - The split control / split container does NOT appear inside the
 *    Swing/Investor layouts — split is mode-gated to Intraday/F&O (R4.7), and
 *    these single-chart workspaces never host a pane-split surface.
 *
 * The heavy chart child (`MainTerminalChart`) is MOCKED with a lightweight
 * stand-in so jsdom never initializes a canvas/chart engine; it echoes its
 * `activeProfile`/`timeframe` props and counts mounts so we can prove exactly
 * one chart instance is mounted per single-chart layout.
 *
 * **Property 9: Non-regression of existing profiles**
 * **Validates: Requirements 7.1**
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// ── Mount registry (hoisted so the mock factory can reference it) ─────────────
// Counts MainTerminalChart mounts so each single-chart layout proves it mounts
// exactly one independent chart instance.
const charts = vi.hoisted(() => ({
  mounts: 0,
  reset() {
    this.mounts = 0;
  },
}));

// Mock the heavy chart child. It renders its profile/timeframe props so the
// layout's wiring is observable, and counts mounts. Importing the layout modules
// pulls MainTerminalChart in transitively, so this mock keeps the test fast and
// canvas-free.
vi.mock('../../MainTerminalChart', async () => {
  const ReactNs = await import('react');
  return {
    __esModule: true,
    default: ({ activeProfile, timeframe }: any) => {
      ReactNs.useEffect(() => {
        charts.mounts += 1;
      }, []);
      return ReactNs.createElement('div', {
        'data-testid': 'main-chart',
        'data-active-profile': String(activeProfile ?? ''),
        'data-timeframe': String(timeframe ?? ''),
      });
    },
  };
});

// Import AFTER the mock so the layouts pick up the mocked chart child.
import IntradayLayout from '../IntradayLayout';
import SwingLayout from '../SwingLayout';
import InvestorLayout from '../InvestorLayout';
import { useTradeStore, type TradeProfile } from '../../../store/useTradeStore';

type LayoutCase = {
  profile: Exclude<TradeProfile, 'FNO'>;
  Component: React.ComponentType<any>;
  hudId: string;
  timeframe: string;
};

const LAYOUTS: LayoutCase[] = [
  { profile: 'INTRADAY', Component: IntradayLayout, hudId: 'intraday-hud', timeframe: '1m' },
  { profile: 'SWING', Component: SwingLayout, hudId: 'swing-hud', timeframe: '1h' },
  { profile: 'INVESTOR', Component: InvestorLayout, hudId: 'investor-hud', timeframe: '1D' },
];

function resetStore() {
  useTradeStore.setState({ activeProfile: 'INTRADAY' });
  charts.reset();
}

describe('Existing-profile layouts render their expected workspace structure (R7.1)', () => {
  beforeEach(() => resetStore());
  afterEach(() => cleanup());

  it.each(LAYOUTS)(
    '$profile layout renders its #$hudId wrapper with exactly one mounted chart',
    ({ profile, Component, hudId, timeframe }) => {
      const { container } = render(
        <Component activeProfile={profile} timeframe={timeframe} isExpanded={false} />,
      );

      // Profile-scoped workspace wrapper present.
      const hud = container.querySelector(`#${hudId}`);
      expect(hud).not.toBeNull();

      // Exactly one independent chart instance mounted for the single-chart layout.
      expect(screen.getAllByTestId('main-chart')).toHaveLength(1);
      expect(charts.mounts).toBe(1);

      // The chart is driven by this profile's props (wiring unchanged).
      const chart = screen.getByTestId('main-chart');
      expect(chart).toHaveAttribute('data-active-profile', profile);
      expect(chart).toHaveAttribute('data-timeframe', timeframe);
    },
  );
});

describe('Property 9 — layout structure is identical across an F&O round-trip (R7.1)', () => {
  beforeEach(() => resetStore());
  afterEach(() => cleanup());

  it.each(LAYOUTS)(
    '$profile layout renders byte-for-byte identically before vs after entering/leaving F&O',
    ({ profile, Component, timeframe }) => {
      // Baseline render of the profile layout while that profile is active.
      act(() => useTradeStore.getState().setActiveProfile(profile));
      const { container, rerender } = render(
        <Component activeProfile={profile} timeframe={timeframe} isExpanded={false} />,
      );
      const baseline = container.innerHTML;

      // Enter F&O, then leave back to the original profile via the REAL store —
      // exercising the unified Workspace_Mode migration.
      act(() => useTradeStore.getState().setActiveProfile('FNO'));
      expect(useTradeStore.getState().activeProfile).toBe('FNO');
      act(() => useTradeStore.getState().setActiveProfile(profile));
      expect(useTradeStore.getState().activeProfile).toBe(profile);

      // Re-render the same layout with the same props after the round-trip.
      rerender(<Component activeProfile={profile} timeframe={timeframe} isExpanded={false} />);

      // The rendered structure is unchanged — the mode-model migration leaves the
      // existing profile workspaces identical to baseline.
      expect(container.innerHTML).toBe(baseline);
    },
  );

  it('the existing-profile layout components do not subscribe to activeProfile (no re-mount on F&O round-trip)', () => {
    // Render all three single-chart layouts; 3 chart instances mount.
    render(
      <>
        <IntradayLayout activeProfile="INTRADAY" timeframe="1m" />
        <SwingLayout activeProfile="SWING" timeframe="1h" />
        <InvestorLayout activeProfile="INVESTOR" timeframe="1D" />
      </>,
    );
    expect(charts.mounts).toBe(3);

    // An F&O round-trip in the store must not cause any layout to re-mount its
    // chart — these components are prop-driven, not store-mode-driven.
    act(() => useTradeStore.getState().setActiveProfile('FNO'));
    act(() => useTradeStore.getState().setActiveProfile('INTRADAY'));
    expect(charts.mounts).toBe(3);
  });
});

describe('Split is mode-gated — no split surface inside Swing/Investor layouts (R4.7)', () => {
  beforeEach(() => resetStore());
  afterEach(() => cleanup());

  it.each(LAYOUTS.filter((l) => l.profile === 'SWING' || l.profile === 'INVESTOR'))(
    '$profile layout renders a single chart and no split container/control',
    ({ profile, Component, timeframe }) => {
      render(<Component activeProfile={profile} timeframe={timeframe} />);

      // Single-chart confluence/macro workspace: exactly one chart, never two.
      expect(screen.getAllByTestId('main-chart')).toHaveLength(1);

      // No split-pane surface or single/split control is hosted by these layouts.
      expect(document.querySelector('[data-pane-id]')).toBeNull();
      expect(document.querySelector('[data-testid="split-chart-container"]')).toBeNull();
      expect(screen.queryByRole('button', { name: /split/i })).toBeNull();
    },
  );
});
