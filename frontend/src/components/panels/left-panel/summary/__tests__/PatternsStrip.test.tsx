// @vitest-environment jsdom

/**
 * `PatternsStrip` — the one-line pattern scan summary in the Market Watch rail.
 *
 * The state precedence is the substance of these tests. `fetchMultiTfPatterns`
 * historically caught every failure and fell back to an empty list, so an
 * unreachable tool-server, a proxy timeout and a genuinely quiet market all
 * rendered identically as "No patterns forming". The store was fixed to report
 * `patternsError`; these tests make sure the strip honours it instead of
 * re-flattening a failed scan into a calm-looking zero.
 */

import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import PatternsStrip from '../PatternsStrip';
import type { ChartPattern, MultiTfChartPatterns } from '../../../../../store/useQuantStore';

afterEach(cleanup);

function pattern(overrides: Partial<ChartPattern> = {}): ChartPattern {
  return {
    pattern_type: 'Bullish Engulfing',
    sentiment: 'bullish',
    confidence: 0.8,
    start_idx: 10,
    end_idx: 12,
    description: 'A bullish engulfing candle pair',
    structural_bias: 'UPTREND',
    geometric_strictness: 0.7,
    volume_validation: 'CONFIRMED',
    breakout_status: 'PENDING',
    ...overrides,
  };
}

function tf(timeframe: string, count: number, formingCount = 0): MultiTfChartPatterns {
  return {
    timeframe,
    patterns: Array.from({ length: count }, (_, i) => pattern({ is_forming: i < formingCount })),
  };
}

describe('PatternsStrip', () => {
  it('reports the total count and the strongest timeframe', () => {
    render(
      <PatternsStrip
        multiTfPatterns={[tf('5m', 2), tf('1h', 4)]}
        isLoading={false}
        error={null}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('6')).toBeInTheDocument();
    expect(screen.getByText('patterns')).toBeInTheDocument();
    expect(screen.getByText('1h')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /6 patterns, strongest on the 1h timeframe/ }),
    ).toBeInTheDocument();
  });

  it('shows the forming count as its own figure', () => {
    render(
      <PatternsStrip
        multiTfPatterns={[tf('5m', 3, 2)]}
        isLoading={false}
        error={null}
        onClick={() => {}}
      />,
    );

    expect(screen.getByRole('button', { name: /3 patterns, 2 still forming/ })).toBeInTheDocument();
  });

  it('uses the singular when exactly one pattern was found', () => {
    render(
      <PatternsStrip
        multiTfPatterns={[tf('5m', 1)]}
        isLoading={false}
        error={null}
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('pattern')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /1 pattern,/ })).toBeInTheDocument();
  });

  it('reports a failed scan as an error, not as a quiet market', () => {
    render(
      <PatternsStrip
        multiTfPatterns={null}
        isLoading={false}
        error="Tool server unreachable"
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('Tool server unreachable')).toBeInTheDocument();
    expect(screen.queryByText(/none forming/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/not scanned/i)).not.toBeInTheDocument();
  });

  it('lets the error outrank a stale pattern list left on screen', () => {
    // The store deliberately keeps cached patterns visible across a failure. The
    // strip must not present that retained list as the result of a fresh scan.
    render(
      <PatternsStrip
        multiTfPatterns={[tf('5m', 4)]}
        isLoading={false}
        error="Scan timed out after 30s"
        onClick={() => {}}
      />,
    );

    expect(screen.getByText('Scan timed out after 30s')).toBeInTheDocument();
    expect(screen.queryByText('4')).not.toBeInTheDocument();
  });

  it('distinguishes a completed scan that found nothing from one that never ran', () => {
    const { unmount } = render(
      <PatternsStrip
        multiTfPatterns={[tf('5m', 0), tf('1h', 0)]}
        isLoading={false}
        error={null}
        onClick={() => {}}
      />,
    );
    expect(screen.getByText('None forming')).toBeInTheDocument();
    unmount();

    render(
      <PatternsStrip multiTfPatterns={null} isLoading={false} error={null} onClick={() => {}} />,
    );
    expect(screen.getByText('Not scanned')).toBeInTheDocument();
  });

  it('reports an in-flight scan as a status', () => {
    render(
      <PatternsStrip multiTfPatterns={null} isLoading error={null} onClick={() => {}} />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Scanning');
  });
});
