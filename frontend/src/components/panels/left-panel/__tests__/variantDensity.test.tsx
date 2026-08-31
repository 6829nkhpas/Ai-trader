// @vitest-environment jsdom

/**
 * Density variants for the three detail views.
 *
 * `SentimentBlock`, `LiveAssetHUD` and `MultiTfPatternsView` each render in two
 * places now: the 224px Market Watch column (`variant="panel"`, the default) and
 * the ~420px detail sheet (`variant="sheet"`). Two things are worth pinning:
 *
 * - The `panel` output is a REGRESSION GUARD. Those call sites shipped at their
 *   current sizes, and the sheet work must not quietly restyle the sidebar — the
 *   default was made explicit precisely so existing callers were untouched.
 * - The `sheet` output must actually be bigger and must not repeat the section
 *   title the dialog header already shows, which was the whole point of moving
 *   the detail out of the column.
 *
 * Class assertions are unusual, but the variant IS a styling contract; there is
 * no behaviour to observe instead, and jsdom does not do layout.
 */

import React from 'react';
import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import SentimentBlock from '../SentimentBlock';
import LiveAssetHUD from '../LiveAssetHUD';
import MultiTfPatternsView from '../../../quant/deep-quant/MultiTfPatternsView';
import {
  useQuantStore,
  type ConsensusReport,
  type SentimentPayload,
} from '../../../../store/useQuantStore';

afterEach(cleanup);

const SENTIMENT: SentimentPayload = {
  symbol: 'RELIANCE',
  score: 42,
  label: 'Bullish',
  top_headline: 'Order book at record high',
  impact: 'positive',
  headlines: ['Order book at record high', 'Refinery margins expand'],
};

const CONSENSUS: ConsensusReport = {
  symbol: 'RELIANCE',
  trend_score: 61,
  momentum_state: 'OVERBOUGHT',
  volatility_state: 'EXPANDING',
  volume_flow_state: 'ACCUMULATION',
  active_patterns: ['Bullish Engulfing'],
  active_strategies: ['Golden Cross'],
};

describe('SentimentBlock variants', () => {
  it('keeps the sidebar layout on the default variant', () => {
    render(
      <SentimentBlock symbol="RELIANCE" sentiment={SENTIMENT} isLoading={false} error={null} />,
    );

    // Its own section title, because nothing else names it in the column.
    expect(screen.getByRole('heading', { name: 'AI News Sentiment' })).toBeInTheDocument();
    expect(screen.getByText('+42')).toHaveClass('text-xl');
    // Headlines stay behind the toggle: the column has no room for the list.
    expect(screen.getByRole('button', { name: /Headlines \(2\)/ })).toBeInTheDocument();
    expect(screen.queryByText('Refinery margins expand')).not.toBeInTheDocument();
  });

  it('scales up and drops the duplicate title in the sheet', () => {
    render(
      <SentimentBlock
        symbol="RELIANCE"
        sentiment={SENTIMENT}
        isLoading={false}
        error={null}
        variant="sheet"
      />,
    );

    expect(screen.queryByRole('heading', { name: 'AI News Sentiment' })).not.toBeInTheDocument();
    expect(screen.getByText('+42')).toHaveClass('text-3xl');
    // The list is open on arrival — the click-to-expand only existed for width.
    expect(screen.getByText('Refinery margins expand')).toBeInTheDocument();
  });

  it('wraps a failure message in the sheet instead of truncating it', () => {
    const longError = 'Sentiment service returned 503 after 3 retries against the gateway';

    const { unmount } = render(
      <SentimentBlock symbol="RELIANCE" sentiment={null} isLoading={false} error={longError} />,
    );
    expect(screen.getByText(longError)).toHaveClass('truncate');
    unmount();

    render(
      <SentimentBlock
        symbol="RELIANCE"
        sentiment={null}
        isLoading={false}
        error={longError}
        variant="sheet"
      />,
    );
    // The sheet is where the failure is explained in full, so it must not clip.
    expect(screen.getByText(longError)).toHaveClass('break-words');
    expect(screen.getByText(longError)).not.toHaveClass('truncate');
  });
});

describe('LiveAssetHUD variants', () => {
  it('keeps the sidebar layout on the default variant', () => {
    render(<LiveAssetHUD data={CONSENSUS} computedAt={Date.now()} />);

    expect(screen.getByRole('heading', { name: 'Technical Consensus' })).toBeInTheDocument();
    expect(screen.getByText('+61')).toHaveClass('text-2xl');
    // The column has no header of its own, so the HUD names its subject.
    expect(screen.getByText('RELIANCE')).toBeInTheDocument();
  });

  it('scales up and defers the title and symbol to the sheet header', () => {
    render(<LiveAssetHUD data={CONSENSUS} computedAt={Date.now()} variant="sheet" />);

    expect(screen.queryByRole('heading', { name: 'Technical Consensus' })).not.toBeInTheDocument();
    expect(screen.getByText('+61')).toHaveClass('text-4xl');
    // The dialog header already carries the symbol badge.
    expect(screen.queryByText('RELIANCE')).not.toBeInTheDocument();
    // Patterns and strategies still fold into this view, at any density.
    expect(screen.getByText('Bullish Engulfing')).toBeInTheDocument();
    expect(screen.getByText('Golden Cross')).toBeInTheDocument();
  });
});

describe('MultiTfPatternsView variants', () => {
  beforeEach(() => {
    useQuantStore.setState({
      multiTfPatterns: [{ timeframe: '10m', patterns: [] }],
      isFetchingPatterns: false,
      patternsError: null,
    });
  });

  it('keeps its own title and the height cap in the column', () => {
    const { container } = render(<MultiTfPatternsView />);

    expect(screen.getByRole('heading', { name: 'Dynamic Pattern Scanner' })).toBeInTheDocument();
    // The cap stops the 7-timeframe list swallowing the sidebar.
    expect(container.querySelector('.max-h-47\\.5')).not.toBeNull();
  });

  it('drops the title and the height cap in the sheet', () => {
    const { container } = render(<MultiTfPatternsView variant="sheet" />);

    expect(
      screen.queryByRole('heading', { name: 'Dynamic Pattern Scanner' }),
    ).not.toBeInTheDocument();
    // The dialog owns the scrolling; a nested cap would mean two scrollbars.
    expect(container.querySelector('.max-h-47\\.5')).toBeNull();
  });
});
