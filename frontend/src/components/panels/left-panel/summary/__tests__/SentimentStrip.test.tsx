// @vitest-environment jsdom

/**
 * `SentimentStrip` — the one-line news verdict in the Market Watch rail.
 *
 * The strip is now the only sentiment surface visible without opening the sheet,
 * so the three ways it could lie are the three things pinned here:
 *
 * - a missing reading must not render as "Neutral" (absence is not a verdict);
 * - a failed refresh must not render the retained previous score as if current;
 * - a verdict computed on an option's UNDERLYING must say so, or it attributes
 *   one instrument's news to another.
 *
 * The gauge is also checked for being a single diverging measure of `score`,
 * because the design mock it replaces showed two independent bull/bear
 * percentages that the payload does not contain.
 */

import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import SentimentStrip, { scoreToTrackPercent } from '../SentimentStrip';
import type { SentimentPayload } from '../../../../../store/useQuantStore';

afterEach(cleanup);

function payload(overrides: Partial<SentimentPayload> = {}): SentimentPayload {
  return {
    symbol: 'RELIANCE',
    score: 42,
    label: 'Bullish',
    top_headline: 'Order book at record high',
    impact: 'positive',
    headlines: ['Order book at record high', 'Refinery margins expand'],
    ...overrides,
  };
}

describe('SentimentStrip', () => {
  it('renders the signed score and label when a reading exists', () => {
    render(
      <SentimentStrip
        symbol="RELIANCE"
        sentiment={payload()}
        isLoading={false}
        error={null}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('+42')).toBeInTheDocument();
    expect(screen.getByText('Bullish')).toBeInTheDocument();
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '42');
  });

  it('renders the empty state rather than a neutral verdict when there is no reading', () => {
    render(
      <SentimentStrip
        symbol="RELIANCE"
        sentiment={null}
        isLoading={false}
        error={null}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('No reading')).toBeInTheDocument();
    // "Neutral" is a measured verdict. Having no measurement is not it.
    expect(screen.queryByText(/neutral/i)).not.toBeInTheDocument();
    expect(screen.queryByRole('meter')).not.toBeInTheDocument();
  });

  it('shows the error state even while a previous payload is still in the store', () => {
    render(
      <SentimentStrip
        symbol="RELIANCE"
        sentiment={payload({ score: 42 })}
        isLoading={false}
        error="Sentiment service unreachable"
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('Sentiment service unreachable')).toBeInTheDocument();
    // The retained score must not be presented as the current read.
    expect(screen.queryByText('+42')).not.toBeInTheDocument();
    expect(screen.queryByRole('meter')).not.toBeInTheDocument();
  });

  it('names the underlying when the verdict is not about the charted symbol', () => {
    render(
      <SentimentStrip
        symbol="RELIANCE26AUG1290CE"
        sentiment={payload({ symbol: 'RELIANCE' })}
        isLoading={false}
        error={null}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('on RELIANCE')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /based on news about RELIANCE/ }),
    ).toBeInTheDocument();
  });

  it('omits the subject marker when the verdict is about the charted symbol', () => {
    render(
      <SentimentStrip
        symbol="reliance"
        sentiment={payload({ symbol: 'RELIANCE' })}
        isLoading={false}
        error={null}
        onClick={() => {}}
      />,
    );

    expect(screen.queryByText(/^on /)).not.toBeInTheDocument();
  });

  it('reports loading as a status while the fetch is in flight', () => {
    render(
      <SentimentStrip
        symbol="RELIANCE"
        sentiment={null}
        isLoading
        error={null}
        onClick={() => {}}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Reading news');
  });

  it('announces a bearish score with its sign spelled out', () => {
    render(
      <SentimentStrip
        symbol="TCS"
        sentiment={payload({ symbol: 'TCS', score: -63, label: 'Bearish', impact: 'negative' })}
        isLoading={false}
        error={null}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('-63')).toBeInTheDocument();
    // "-63" read aloud is ambiguous at best; the accessible name says "minus".
    expect(screen.getByRole('button', { name: /Bearish, score minus 63/ })).toBeInTheDocument();
  });
});

describe('scoreToTrackPercent', () => {
  it('maps the score range onto the track with neutral at the centre', () => {
    expect(scoreToTrackPercent(-100)).toBe(0);
    expect(scoreToTrackPercent(0)).toBe(50);
    expect(scoreToTrackPercent(100)).toBe(100);
  });

  it('clamps out-of-contract scores instead of overflowing the track', () => {
    expect(scoreToTrackPercent(-250)).toBe(0);
    expect(scoreToTrackPercent(250)).toBe(100);
  });
});
