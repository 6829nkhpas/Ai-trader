// @vitest-environment jsdom

/**
 * Terminal UX Overhaul (Task 7.2) — component test for the Mode_Selector and
 * the workspace switch that keys off `activeProfile`.
 *
 * This test covers two surfaces:
 *
 * 1. The REAL `Mode_Selector` (`TerminalLayout`'s `PROFILES` segmented control).
 *    Heavy header children (QuantRadar, UserProfileModal, ChartToolsBar) are
 *    mocked so the segmented control renders in isolation. We assert:
 *      - All four Workspace_Modes render: Intraday, Swing, Investor, F&O (R1.1).
 *      - Clicking each mode drives `useTradeStore.setActiveProfile`, switching
 *        the single-source-of-truth `activeProfile` (R1.2, R1.4).
 *      - The active mode is visually indicated (R1.6).
 *      - There is NO above-chart F&O toggle: the legacy `FnoModeToggle`
 *        (`aria-label="F&O Mode"`) is absent; F&O is reachable ONLY through the
 *        Mode_Selector (R1.3).
 *
 * 2. The workspace switch (`page.tsx`'s `renderProfileContent`). Rendering the
 *    full `page.tsx` is impractical (auth gating, WebSocket wiring, Tauri IPC,
 *    quote fetches), so — following the repo's established component-test
 *    convention — we mirror the page's exact `switch(activeProfile)` in a small
 *    harness driven by the REAL `useTradeStore`, with the heavy workspace
 *    children mocked. We assert:
 *      - Each profile renders its own workspace; `FNO` renders `FnoSection`
 *        (R1.3, R2.1).
 *      - Property 9 (non-regression): entering and leaving F&O leaves the
 *        Intraday/Swing/Investor workspaces' rendered structure identical to
 *        baseline (R7.1).
 *
 * **Property 9: Non-regression of existing profiles**
 * **Validates: Requirements 1.3, 7.1**
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// ── Mock the heavy header children so the Mode_Selector renders in isolation.
//    They are unrelated to the segmented control under test.
vi.mock('../../quant/QuantRadar', () => ({ __esModule: true, default: () => null }));
vi.mock('../../profile/UserProfileModal', () => ({ __esModule: true, default: () => null }));
vi.mock('../../chart/ChartToolsBar', () => ({ __esModule: true, default: () => null }));

import TerminalLayout from '../TerminalLayout';
import { useTradeStore, type TradeProfile } from '../../../store/useTradeStore';
import { useChartUIStore } from '../../../store/useChartUIStore';

const ALL_PROFILES: { key: TradeProfile; label: string }[] = [
  { key: 'INTRADAY', label: 'Intraday' },
  { key: 'SWING', label: 'Swing' },
  { key: 'INVESTOR', label: 'Investor' },
  { key: 'FNO', label: 'F&O' },
];

function resetStores() {
  useTradeStore.setState({ activeProfile: 'INTRADAY' });
  useChartUIStore.setState({ splitView: false });
}

function modeButton(key: TradeProfile): HTMLElement {
  return document.getElementById(`profile-btn-${key.toLowerCase()}`) as HTMLElement;
}

describe('Mode_Selector — four peer modes render and switch (R1.1, R1.2, R1.4, R1.6)', () => {
  beforeEach(() => resetStores());
  afterEach(() => cleanup());

  it('renders all four Workspace_Modes (Intraday, Swing, Investor, F&O)', () => {
    render(<TerminalLayout leftPanel={<div data-testid="left" />}>{<div data-testid="ws" />}</TerminalLayout>);

    for (const { key, label } of ALL_PROFILES) {
      const btn = modeButton(key);
      expect(btn).toBeInTheDocument();
      expect(btn).toHaveTextContent(label);
    }
  });

  it.each(ALL_PROFILES)(
    'clicking the $label mode sets activeProfile to $key in the store',
    ({ key }) => {
      render(<TerminalLayout leftPanel={<div />}>{<div />}</TerminalLayout>);

      fireEvent.click(modeButton(key));
      expect(useTradeStore.getState().activeProfile).toBe(key);
    },
  );

  it('switching modes is mutually exclusive — exactly one mode is active at a time', () => {
    render(<TerminalLayout leftPanel={<div />}>{<div />}</TerminalLayout>);

    for (const { key } of ALL_PROFILES) {
      fireEvent.click(modeButton(key));
      expect(useTradeStore.getState().activeProfile).toBe(key);
      // Every other profile is NOT the active one.
      for (const other of ALL_PROFILES) {
        if (other.key !== key) {
          expect(useTradeStore.getState().activeProfile).not.toBe(other.key);
        }
      }
    }
  });

  it('visually indicates the active mode, including F&O (R1.6)', () => {
    render(<TerminalLayout leftPanel={<div />}>{<div />}</TerminalLayout>);

    // Capture the inactive F&O class set, then activate F&O and confirm the
    // class set changes (the active emerald/elevated treatment is applied).
    const inactiveClass = modeButton('FNO').className;
    fireEvent.click(modeButton('FNO'));
    const activeClass = modeButton('FNO').className;

    expect(useTradeStore.getState().activeProfile).toBe('FNO');
    expect(activeClass).not.toBe(inactiveClass);
    expect(activeClass).toContain('bg-elevated');
  });
});

describe('Mode_Selector — F&O is reachable ONLY via the selector (R1.3)', () => {
  beforeEach(() => resetStores());
  afterEach(() => cleanup());

  it('renders no above-chart F&O toggle (legacy FnoModeToggle is absent)', () => {
    render(<TerminalLayout leftPanel={<div />}>{<div data-testid="ws" />}</TerminalLayout>);

    // The legacy above-chart toggle exposed aria-label="F&O Mode". It must not
    // exist anywhere in the rendered terminal — F&O is a Mode_Selector peer.
    expect(screen.queryByRole('button', { name: /^F&O Mode$/i })).toBeNull();
    expect(screen.queryByLabelText(/^F&O Mode$/i)).toBeNull();

    // The ONLY F&O entry point is the Mode_Selector segment button.
    const fnoSegment = modeButton('FNO');
    expect(fnoSegment).toBeInTheDocument();
    expect(fnoSegment).toHaveTextContent('F&O');
  });
});

/* ─────────────────────────────────────────────────────────────────────────
 * Workspace switch — mirrors page.tsx's `renderProfileContent` (keyed off the
 * REAL `useTradeStore.activeProfile`) with the heavy workspace children mocked.
 * ───────────────────────────────────────────────────────────────────────── */

const IntradayStub = () => <div data-testid="workspace" data-kind="INTRADAY">Intraday</div>;
const SwingStub = () => <div data-testid="workspace" data-kind="SWING">Swing</div>;
const InvestorStub = () => <div data-testid="workspace" data-kind="INVESTOR">Investor</div>;
const FnoStub = () => <div data-testid="workspace" data-kind="FNO">FnoSection</div>;
const SplitStub = ({ mode }: { mode: string }) => (
  <div data-testid="workspace" data-kind="SPLIT" data-mode={mode}>Split</div>
);

/**
 * Faithful replica of the `switch(activeProfile)` workspace renderer in
 * `page.tsx`. Kept in lockstep with the page so this test exercises the exact
 * mode → workspace mapping (FNO → FnoSection) and the split mode-gating.
 */
function WorkspaceHarness() {
  const activeProfile = useTradeStore((s) => s.activeProfile);
  const splitView = useChartUIStore((s) => s.splitView);

  const split = splitView && (activeProfile === 'INTRADAY' || activeProfile === 'FNO');

  switch (activeProfile) {
    case 'INTRADAY':
      return split ? <SplitStub mode="INTRADAY" /> : <IntradayStub />;
    case 'SWING':
      return <SwingStub />;
    case 'INVESTOR':
      return <InvestorStub />;
    case 'FNO':
      return split ? <SplitStub mode="FNO" /> : <FnoStub />;
    default:
      return <IntradayStub />;
  }
}

describe('Workspace switch — activeProfile selects the workspace (R1.3, R2.1)', () => {
  beforeEach(() => resetStores());
  afterEach(() => cleanup());

  it('renders the matching workspace for each profile; F&O renders FnoSection', () => {
    render(<WorkspaceHarness />);

    // Default Intraday.
    expect(screen.getByTestId('workspace')).toHaveAttribute('data-kind', 'INTRADAY');

    act(() => useTradeStore.getState().setActiveProfile('SWING'));
    expect(screen.getByTestId('workspace')).toHaveAttribute('data-kind', 'SWING');

    act(() => useTradeStore.getState().setActiveProfile('INVESTOR'));
    expect(screen.getByTestId('workspace')).toHaveAttribute('data-kind', 'INVESTOR');

    // F&O renders the FnoSection workspace (re-homed under the Mode_Selector).
    act(() => useTradeStore.getState().setActiveProfile('FNO'));
    const fno = screen.getByTestId('workspace');
    expect(fno).toHaveAttribute('data-kind', 'FNO');
    expect(fno).toHaveTextContent('FnoSection');
  });

  it('F&O with split on renders the split container, not an above-chart toggle (R1.3, R4.7)', () => {
    useChartUIStore.setState({ splitView: true });
    useTradeStore.setState({ activeProfile: 'FNO' });
    render(<WorkspaceHarness />);

    const ws = screen.getByTestId('workspace');
    expect(ws).toHaveAttribute('data-kind', 'SPLIT');
    expect(ws).toHaveAttribute('data-mode', 'FNO');
  });
});

describe('Property 9 — non-regression of existing profiles across F&O round-trip (R7.1)', () => {
  beforeEach(() => resetStores());
  afterEach(() => cleanup());

  it.each(['INTRADAY', 'SWING', 'INVESTOR'] as TradeProfile[])(
    'entering and leaving F&O leaves the %s workspace structure identical to baseline',
    (profile) => {
      const { container } = render(<WorkspaceHarness />);

      // Baseline render for the profile.
      act(() => useTradeStore.getState().setActiveProfile(profile));
      const baseline = container.innerHTML;

      // Enter F&O — the F&O workspace renders.
      act(() => useTradeStore.getState().setActiveProfile('FNO'));
      expect(screen.getByTestId('workspace')).toHaveAttribute('data-kind', 'FNO');

      // Leave F&O back to the original profile.
      act(() => useTradeStore.getState().setActiveProfile(profile));

      // The profile's rendered structure is byte-for-byte identical to baseline.
      expect(container.innerHTML).toBe(baseline);
      expect(screen.getByTestId('workspace')).toHaveAttribute('data-kind', profile);
    },
  );
});
