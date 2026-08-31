// @vitest-environment jsdom
//
// Showing and hiding the Market Watch column from the nav rail.
//
// The controls used to live in a `h-8` "MARKET WATCH" header strip above the
// column: a search button and a collapse button, on a row whose only other job
// was to name the column it sat on top of. Collapsing it meant the collapse
// button went with it, so the ONLY way back was a floating, drag-to-reposition
// chevron hovering over the left edge of the chart.
//
// Both controls are in `NavRail` now and the chevron is gone, which makes one
// behaviour worth pinning: the rail's toggle has to work in BOTH directions. A
// one-way collapse here would strand the user with no way to bring the column
// back at all — strictly worse than what it replaced.
import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// Heavy rail/header children, unrelated to the toggle under test.
vi.mock('../../quant/QuantRadar', () => ({ __esModule: true, default: () => null }));
vi.mock('../../profile/UserProfileModal', () => ({ __esModule: true, default: () => null }));
vi.mock('../../chart/ChartToolsBar', () => ({ __esModule: true, default: () => null }));

import TerminalLayout from '../TerminalLayout';
import { useTradeStore } from '../../../store/useTradeStore';

/** The column's own element — found via the panel content it wraps. */
function marketWatchColumn(): HTMLElement {
  const panel = screen.getByTestId('left-panel-content');
  const column = panel.closest('aside');
  expect(column, 'the Market Watch column should be an <aside>').not.toBeNull();
  return column as HTMLElement;
}

function toggle(): HTMLElement {
  return screen.getByTitle(/Market Watch$/);
}

beforeEach(() => {
  useTradeStore.setState({ activeProfile: 'INTRADAY' });
  render(
    <TerminalLayout leftPanel={<div data-testid="left-panel-content">watchlist</div>}>
      <div>chart</div>
    </TerminalLayout>,
  );
});
afterEach(() => cleanup());

describe('the Market Watch toggle in the nav rail', () => {
  it('starts expanded, and says what pressing it will do', () => {
    expect(toggle()).toHaveAttribute('title', 'Hide Market Watch');
    expect(toggle()).toHaveAttribute('aria-pressed', 'true');
    expect(marketWatchColumn()).toHaveStyle({ width: '224px' });
  });

  it('collapses the column to zero width', () => {
    fireEvent.click(toggle());
    expect(marketWatchColumn()).toHaveStyle({ width: '0px' });
    expect(marketWatchColumn().className).toContain('pointer-events-none');
  });

  it('brings the column BACK — the toggle is not one-way', () => {
    // The whole point. Nothing else can reopen it now that the floating chevron
    // is gone, so a regression here loses the watchlist until a page reload.
    fireEvent.click(toggle());
    expect(toggle()).toHaveAttribute('title', 'Show Market Watch');
    expect(toggle()).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(toggle());
    expect(marketWatchColumn()).toHaveStyle({ width: '224px' });
    expect(toggle()).toHaveAttribute('title', 'Hide Market Watch');
  });

  it('keeps the toggle reachable while the column is hidden', () => {
    fireEvent.click(toggle());
    // In the rail, not inside the column it hides.
    expect(marketWatchColumn().contains(toggle())).toBe(false);
    expect(toggle().closest('nav[aria-label="Primary navigation"]')).not.toBeNull();
  });

  it('renders no "MARKET WATCH" header row', () => {
    expect(screen.queryByText(/market watch/i)).toBeNull();
  });

  it('puts search at the top of the rail, above the workspace modes', () => {
    const rail = document.querySelector('nav[aria-label="Primary navigation"]') as HTMLElement;
    const order = [...rail.querySelectorAll('button')];
    const search = order.findIndex((b) => b.getAttribute('title')?.startsWith('Search symbol'));
    const firstMode = order.findIndex((b) => b.id === 'profile-btn-intraday');
    expect(search, 'the search button should be in the rail').toBeGreaterThan(-1);
    expect(search).toBeLessThan(firstMode);
  });
});
