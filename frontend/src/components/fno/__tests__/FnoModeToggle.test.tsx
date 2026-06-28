// @vitest-environment jsdom

/**
 * F&O Frontend Section (F4) — component tests for the command-bar toggle and
 * the workspace branch (task 6.3).
 *
 * Validates (Requirements 1.1, 1.5, 2.4):
 * - A distinct `FnoModeToggle` button exists in the command bar and clicking it
 *   flips the single-source-of-truth `fnoMode` in `useTradeStore` (R1.1).
 * - The toggle applies the active emerald styling when `fnoMode` is true and the
 *   inactive styling when false, so the trader sees F&O mode is engaged (R1.1,
 *   R1.5).
 * - `fnoMode` drives the workspace branch `{fnoMode ? <FnoSection/> :
 *   renderProfileContent()}`: `FnoSection` mounts only while active, and the
 *   profile content renders only while inactive (R2.4).
 *
 * Testing the full `page.tsx` is heavy (many providers, WebSocket wiring, Tauri
 * IPC). Per the task's guidance we take the focused approach: drive the REAL
 * `FnoModeToggle` against the REAL store, and render a small harness that
 * mirrors the page's branch with a MOCKED `FnoSection` and a stand-in profile
 * component. This exercises the exact toggle→store→branch path that page.tsx
 * uses without brittle full-page rendering.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import FnoModeToggle from '../FnoModeToggle';
import { useTradeStore } from '../../../store/useTradeStore';

// Mock FnoSection with a lightweight stand-in so the branch test never pulls in
// the heavy section (task 8.1 will add Tauri IPC / charts). The branch only
// needs to observe whether the section mounts.
vi.mock('../FnoSection', () => ({
  __esModule: true,
  default: () => <div data-testid="fno-section">F&O SECTION</div>,
}));

import FnoSection from '../FnoSection';

/** Stand-in for `renderProfileContent()` — the non-F&O workspace. */
function ProfileStandIn() {
  return <div data-testid="profile-content">PROFILE WORKSPACE</div>;
}

/**
 * Harness mirroring the page.tsx command-bar + workspace branch:
 *   {fnoMode ? <FnoSection /> : renderProfileContent()}
 * driven by the real store via the real FnoModeToggle.
 */
function WorkspaceHarness() {
  const fnoMode = useTradeStore((s) => s.fnoMode);
  return (
    <div>
      <FnoModeToggle />
      <div data-testid="workspace">
        {fnoMode ? <FnoSection /> : <ProfileStandIn />}
      </div>
    </div>
  );
}

const ACTIVE_CLASSES = ['bg-emerald-500/10', 'text-emerald-400'];
const INACTIVE_CLASSES = ['bg-surface', 'text-text-secondary'];

function resetStore() {
  useTradeStore.setState({
    fnoMode: false,
    activeProfile: 'INTRADAY',
    activeTimeframe: '10m',
    chartMode: 'STANDARD',
    fnoUnderlying: 'NIFTY 50',
    fnoExpiry: '',
  });
}

describe('FnoModeToggle (component)', () => {
  beforeEach(() => resetStore());
  afterEach(() => cleanup());

  it('renders a distinct command-bar button labelled F&O (R1.1)', () => {
    render(<FnoModeToggle />);

    const toggle = screen.getByRole('button', { name: /F&O Mode/i });
    expect(toggle).toBeInTheDocument();
    // Distinct, self-describing control (not a profile/timeframe control).
    expect(toggle).toHaveTextContent('F&O');
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
  });

  it('clicking the toggle flips fnoMode in the store (R1.1)', () => {
    render(<FnoModeToggle />);
    const toggle = screen.getByRole('button', { name: /F&O Mode/i });

    expect(useTradeStore.getState().fnoMode).toBe(false);

    fireEvent.click(toggle);
    expect(useTradeStore.getState().fnoMode).toBe(true);

    fireEvent.click(toggle);
    expect(useTradeStore.getState().fnoMode).toBe(false);
  });

  it('applies the active emerald styling when fnoMode is true (R1.1, R1.5)', () => {
    useTradeStore.setState({ fnoMode: true });
    render(<FnoModeToggle />);

    const toggle = screen.getByRole('button', { name: /F&O Mode/i });
    for (const cls of ACTIVE_CLASSES) expect(toggle).toHaveClass(cls);
    for (const cls of INACTIVE_CLASSES) expect(toggle).not.toHaveClass(cls);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
  });

  it('applies the inactive styling when fnoMode is false (R1.5)', () => {
    useTradeStore.setState({ fnoMode: false });
    render(<FnoModeToggle />);

    const toggle = screen.getByRole('button', { name: /F&O Mode/i });
    for (const cls of INACTIVE_CLASSES) expect(toggle).toHaveClass(cls);
    for (const cls of ACTIVE_CLASSES) expect(toggle).not.toHaveClass(cls);
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
  });

  it('active and inactive styling differ (R1.5)', () => {
    // Inactive render.
    useTradeStore.setState({ fnoMode: false });
    const { unmount } = render(<FnoModeToggle />);
    const inactiveClass = screen.getByRole('button', { name: /F&O Mode/i }).className;
    unmount();

    // Active render.
    useTradeStore.setState({ fnoMode: true });
    render(<FnoModeToggle />);
    const activeClass = screen.getByRole('button', { name: /F&O Mode/i }).className;

    expect(activeClass).not.toEqual(inactiveClass);
  });
});

describe('Workspace branch driven by fnoMode (R2.4)', () => {
  beforeEach(() => resetStore());
  afterEach(() => cleanup());

  it('renders the profile workspace and NOT FnoSection while fnoMode is false (R2.4)', () => {
    render(<WorkspaceHarness />);

    expect(screen.getByTestId('profile-content')).toBeInTheDocument();
    expect(screen.queryByTestId('fno-section')).toBeNull();
  });

  it('mounts FnoSection and NOT the profile workspace while fnoMode is true (R2.4)', () => {
    useTradeStore.setState({ fnoMode: true });
    render(<WorkspaceHarness />);

    expect(screen.getByTestId('fno-section')).toBeInTheDocument();
    expect(screen.queryByTestId('profile-content')).toBeNull();
  });

  it('toggling via the button swaps the mounted workspace (R1.1, R2.4)', () => {
    render(<WorkspaceHarness />);
    const toggle = screen.getByRole('button', { name: /F&O Mode/i });

    // Starts on the profile workspace.
    expect(screen.getByTestId('profile-content')).toBeInTheDocument();
    expect(screen.queryByTestId('fno-section')).toBeNull();

    // Activate F&O mode → FnoSection mounts, profile unmounts.
    fireEvent.click(toggle);
    expect(screen.getByTestId('fno-section')).toBeInTheDocument();
    expect(screen.queryByTestId('profile-content')).toBeNull();

    // Deactivate → back to the profile workspace.
    fireEvent.click(toggle);
    expect(screen.getByTestId('profile-content')).toBeInTheDocument();
    expect(screen.queryByTestId('fno-section')).toBeNull();
  });
});
