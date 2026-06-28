// @vitest-environment jsdom

/**
 * Terminal UX Overhaul — component test for the single/split control's
 * mode-gated visibility (task 6.2).
 *
 * Validates (Requirements 4.7, 5.3):
 * - The Split_Chart_View single/split control is available ONLY in the Intraday
 *   and F&O Workspace_Modes (R4.7). In Swing and Investor it is hidden — the
 *   `SplitViewToggle` renders nothing (R4.7, R5.3: hide controls that do not
 *   apply to the active mode).
 * - When visible (a gated mode), clicking the Split segment drives
 *   `useChartUIStore.setSplitView`, flipping `splitView` to true.
 *
 * The test drives the REAL `SplitViewToggle` against the REAL stores
 * (`useTradeStore` for the active profile, `useChartUIStore` for split state),
 * mirroring the FnoModeToggle component-test style already in the repo.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import SplitViewToggle from '../SplitViewToggle';
import { useTradeStore, type TradeProfile } from '../../../store/useTradeStore';
import { useChartUIStore } from '../../../store/useChartUIStore';

/** Profiles in which the single/split control is available (R4.7). */
const GATED_PROFILES: TradeProfile[] = ['INTRADAY', 'FNO'];
/** Profiles in which the control must be hidden (R4.7, R5.3). */
const HIDDEN_PROFILES: TradeProfile[] = ['SWING', 'INVESTOR'];

function resetStores() {
  useTradeStore.setState({ activeProfile: 'INTRADAY' });
  useChartUIStore.setState({ splitView: false });
}

describe('SplitViewToggle — control visibility (R4.7, R5.3)', () => {
  beforeEach(() => resetStores());
  afterEach(() => cleanup());

  it.each(GATED_PROFILES)(
    'renders the single/split control in the %s mode (R4.7)',
    (profile) => {
      useTradeStore.setState({ activeProfile: profile });
      render(<SplitViewToggle />);

      // Both segments of the gated control are present.
      expect(document.getElementById('split-view-single')).toBeInTheDocument();
      expect(document.getElementById('split-view-split')).toBeInTheDocument();
      expect(
        screen.getByRole('group', { name: /chart layout/i }),
      ).toBeInTheDocument();
    },
  );

  it.each(HIDDEN_PROFILES)(
    'hides the single/split control in the %s mode (R4.7, R5.3)',
    (profile) => {
      useTradeStore.setState({ activeProfile: profile });
      const { container } = render(<SplitViewToggle />);

      // Component returns null → nothing rendered, no control segments present.
      expect(container).toBeEmptyDOMElement();
      expect(document.getElementById('split-view-single')).toBeNull();
      expect(document.getElementById('split-view-split')).toBeNull();
      expect(
        screen.queryByRole('group', { name: /chart layout/i }),
      ).toBeNull();
    },
  );

  it.each(GATED_PROFILES)(
    'clicking the Split segment in the %s mode calls setSplitView (splitView → true)',
    (profile) => {
      useTradeStore.setState({ activeProfile: profile });
      render(<SplitViewToggle />);

      expect(useChartUIStore.getState().splitView).toBe(false);

      fireEvent.click(document.getElementById('split-view-split')!);
      expect(useChartUIStore.getState().splitView).toBe(true);

      // The Single segment routes back to single view.
      fireEvent.click(document.getElementById('split-view-single')!);
      expect(useChartUIStore.getState().splitView).toBe(false);
    },
  );
});
