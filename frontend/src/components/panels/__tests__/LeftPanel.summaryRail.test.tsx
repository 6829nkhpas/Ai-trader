// @vitest-environment jsdom

/**
 * `LeftPanel` — the Market Watch layout after the analytics moved into strips.
 *
 * The panel used to stack the watchlist and three tall analytical blocks in one
 * scroll container, at ~224px wide. This suite pins the two properties that
 * rework was for:
 *
 * - All three strips are present as buttons in the panel regardless of how long
 *   the watchlist is. The rail is `shrink-0` precisely so it cannot be scrolled
 *   out of reach — and it is the only way into the detail sheet, so losing it
 *   would strand the analyses entirely.
 * - The fetches still fire on symbol change with the sheet closed. The strips
 *   need data before anything is opened, so the effects have to stay in the panel
 *   rather than moving into the sheet.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

vi.mock('../../../lib/bridge', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../lib/bridge')>()),
  bridgeInvoke: vi.fn(async (cmd: string) => {
    // A persisted watchlist, so hydrateWatchlist does not seed the NIFTY-50
    // defaults on top of what each test installs.
    if (cmd === 'load_workspace') return JSON.stringify([]);
    return undefined;
  }),
}));

import LeftPanel from '../LeftPanel';
import { useTradeStore } from '../../../store/useTradeStore';
import { useQuantStore } from '../../../store/useQuantStore';

/** A watchlist long enough to overflow the column many times over. */
function longWatchlist(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    symbol: `SYM${i}`,
    token: 1000 + i,
    name: `Company ${i}`,
    sector: 'EQ',
    lastPrice: 100 + i,
    change: 0.5,
  }));
}

const STRIP_NAMES = [/AI News Sentiment/, /Technical Consensus/, /Patterns/];

beforeEach(() => {
  useTradeStore.setState({ watchlist: [], selectedSymbol: 'RELIANCE' });
  global.fetch = vi.fn(async () => ({
    ok: true,
    json: async () => ({ quotes: [] }),
  })) as unknown as typeof fetch;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('LeftPanel summary rail', () => {
  it('renders one strip per analysis', () => {
    render(<LeftPanel />);

    for (const name of STRIP_NAMES) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument();
    }
  });

  it('keeps every strip present with a watchlist far longer than the column', () => {
    useTradeStore.setState({ watchlist: longWatchlist(60) });

    render(<LeftPanel />);

    // The rail is a `shrink-0` sibling of the scrolling list, not a child of it.
    for (const name of STRIP_NAMES) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument();
    }
  });

  it('advertises each strip as a dialog trigger', () => {
    render(<LeftPanel />);

    for (const name of STRIP_NAMES) {
      expect(screen.getByRole('button', { name })).toHaveAttribute('aria-haspopup', 'dialog');
    }
  });

  it('keeps the sheet closed until a strip is pressed', () => {
    render(<LeftPanel />);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('loads sentiment and consensus for the selected symbol with no sheet open', async () => {
    const loadSentimentForSymbol = vi.fn();
    const loadConsensusForSymbol = vi.fn();
    useQuantStore.setState({ loadSentimentForSymbol, loadConsensusForSymbol });

    render(<LeftPanel />);

    await waitFor(() => {
      expect(loadSentimentForSymbol).toHaveBeenCalledWith('RELIANCE');
      expect(loadConsensusForSymbol).toHaveBeenCalledWith('RELIANCE');
    });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('refetches when the charted symbol changes', async () => {
    const loadSentimentForSymbol = vi.fn();
    const loadConsensusForSymbol = vi.fn();
    useQuantStore.setState({ loadSentimentForSymbol, loadConsensusForSymbol });

    render(<LeftPanel />);
    await waitFor(() => expect(loadSentimentForSymbol).toHaveBeenCalledWith('RELIANCE'));

    useTradeStore.setState({ selectedSymbol: 'TCS' });

    await waitFor(() => {
      expect(loadSentimentForSymbol).toHaveBeenCalledWith('TCS');
      expect(loadConsensusForSymbol).toHaveBeenCalledWith('TCS');
    });
  });

  it('holds the pattern scan until the chart cache has enough candles', async () => {
    // The guard exists because the Rust engine otherwise sees 0–1 candles and
    // returns "Insufficient data". Firing the scan early produced a failure the
    // user then had to interpret, so the panel waits instead.
    const fetchMultiTfPatterns = vi.fn();
    useQuantStore.setState({ fetchMultiTfPatterns });
    useTradeStore.setState({ historicalCache: {} });

    render(<LeftPanel />);

    await waitFor(() => expect(screen.getByRole('button', { name: /Patterns/ })).toBeInTheDocument());
    expect(fetchMultiTfPatterns).not.toHaveBeenCalled();

    // 30 candles is the documented threshold in the panel's guard.
    useTradeStore.setState({
      historicalCache: {
        'RELIANCE::10m': Array.from({ length: 30 }, (_, i) => ({
          symbol: 'RELIANCE',
          start_timestamp_ms: (1_700_000_000 + i * 600) * 1000,
          time: 1_700_000_000 + i * 600,
          open: 1,
          high: 2,
          low: 0.5,
          close: 1.5,
          volume: 10,
        })),
      },
    });

    await waitFor(() => expect(fetchMultiTfPatterns).toHaveBeenCalledWith('RELIANCE'));
  });
});
