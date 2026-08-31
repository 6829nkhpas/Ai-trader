// @vitest-environment jsdom

/**
 * `TechnicalStrip` — the one-line technical consensus in the Market Watch rail.
 *
 * Two failure modes matter more than anything cosmetic here, because the strip is
 * a single number the user will act on:
 *
 * - The store retains the last computed report regardless of what is charted now.
 *   A report for RELIANCE must never be rendered while TCS is selected; absence
 *   is the honest answer, with the remedy named.
 * - The consensus is only recomputed on an explicit FIND/VERIFY press, so a
 *   retained reading is legitimate — but a reading from a previous session must
 *   not look live. Past `CONSENSUS_STALE_AFTER_MS` the strip has to say so.
 */

import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import TechnicalStrip from '../TechnicalStrip';
import { CONSENSUS_STALE_AFTER_MS } from '../../consensusView';
import type { ConsensusReport } from '../../../../../store/useQuantStore';

afterEach(cleanup);

function report(overrides: Partial<ConsensusReport> = {}): ConsensusReport {
  return {
    symbol: 'RELIANCE',
    trend_score: 61,
    momentum_state: 'NEUTRAL',
    volatility_state: 'EXPANDING',
    volume_flow_state: 'ACCUMULATION',
    active_patterns: ['Bullish Engulfing'],
    active_strategies: ['Golden Cross'],
    ...overrides,
  };
}

const NOW = 1_700_000_000_000;

describe('TechnicalStrip', () => {
  it('renders the signed trend score and its verdict', () => {
    render(
      <TechnicalStrip
        symbol="RELIANCE"
        consensus={report({ trend_score: 61 })}
        computedAt={NOW - 30_000}
        now={NOW}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('+61')).toBeInTheDocument();
    expect(screen.getByText('STRONG BULL')).toBeInTheDocument();
  });

  it('renders the Deep Quant call to action when nothing has been computed', () => {
    render(
      <TechnicalStrip
        symbol="RELIANCE"
        consensus={null}
        computedAt={null}
        now={NOW}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('Run Deep Quant')).toBeInTheDocument();
    expect(screen.queryByText('NEUTRAL')).not.toBeInTheDocument();
  });

  it('never renders another symbol\u2019s reading under the selected symbol', () => {
    render(
      <TechnicalStrip
        symbol="TCS"
        consensus={report({ symbol: 'RELIANCE', trend_score: 61 })}
        computedAt={NOW - 30_000}
        now={NOW}
        onClick={() => {}}
      />,
    );

    expect(screen.queryByText('+61')).not.toBeInTheDocument();
    expect(screen.queryByText('STRONG BULL')).not.toBeInTheDocument();
    expect(screen.getByText('Run Deep Quant')).toBeInTheDocument();
  });

  it('treats a matching symbol case-insensitively', () => {
    render(
      <TechnicalStrip
        symbol="reliance"
        consensus={report({ trend_score: -20 })}
        computedAt={NOW - 30_000}
        now={NOW}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('-20')).toBeInTheDocument();
    expect(screen.getByText('BEARISH')).toBeInTheDocument();
  });

  it('does not flag a reading inside the freshness window', () => {
    render(
      <TechnicalStrip
        symbol="RELIANCE"
        consensus={report()}
        computedAt={NOW - (CONSENSUS_STALE_AFTER_MS - 1_000)}
        now={NOW}
        onClick={() => {}}
      />,
    );

    expect(
      screen.getByRole('button', { name: /measured 4m ago/ }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/previous reading/)).not.toBeInTheDocument();
  });

  it('flags a reading older than the freshness window as a previous reading', () => {
    render(
      <TechnicalStrip
        symbol="RELIANCE"
        consensus={report()}
        computedAt={NOW - 3 * 60 * 60 * 1000}
        now={NOW}
        onClick={() => {}}
      />,
    );

    // Visible age badge, so the staleness is not conveyed by the accessible name
    // alone — the number next to it is what the user would otherwise trust.
    expect(screen.getByText('3h ago')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /previous reading from 3h ago/ }),
    ).toBeInTheDocument();
  });

  it('renders a reading with no known computation time without claiming an age', () => {
    render(
      <TechnicalStrip
        symbol="RELIANCE"
        consensus={report({ trend_score: 0 })}
        computedAt={null}
        now={NOW}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText('NEUTRAL')).toBeInTheDocument();
    expect(screen.queryByText(/ago/)).not.toBeInTheDocument();
  });
});
