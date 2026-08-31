/**
 * `patternsSummary` — the counting rules shared by the summary strip and the
 * 7-timeframe scanner.
 *
 * These were duplicated inside `MultiTfPatternsView` before the strip existed.
 * Once two surfaces report the same scan, a divergence between them is a bug the
 * user sees as "the strip says 4 patterns but the tabs only add up to 3", so the
 * arithmetic is pinned here and imported by both.
 */

import { describe, it, expect } from 'vitest';

import {
  DEFAULT_PATTERN_TIMEFRAME,
  PATTERN_TIMEFRAMES,
  bestPatternTimeframe,
  formingPatternCount,
  patternCountFor,
  totalPatternCount,
} from '../patternsSummary';
import type { ChartPattern, MultiTfChartPatterns } from '../../../../store/useQuantStore';

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

describe('patternsSummary', () => {
  it('covers the timeframes the scanner offers', () => {
    expect([...PATTERN_TIMEFRAMES]).toEqual(['1m', '5m', '10m', '15m', '1h', '4h', '1d']);
  });

  it('totals patterns across every timeframe', () => {
    expect(totalPatternCount([tf('5m', 2), tf('1h', 3), tf('1d', 0)])).toBe(5);
  });

  it('reports zero rather than throwing when the scan has not run', () => {
    expect(totalPatternCount(null)).toBe(0);
    expect(totalPatternCount(undefined)).toBe(0);
    expect(totalPatternCount([])).toBe(0);
  });

  it('counts a single timeframe, and reports zero for one it has no entry for', () => {
    const data = [tf('5m', 2), tf('1h', 3)];
    expect(patternCountFor(data, '1h')).toBe(3);
    expect(patternCountFor(data, '4h')).toBe(0);
    expect(patternCountFor(null, '1h')).toBe(0);
  });

  it('picks the timeframe carrying the most patterns', () => {
    expect(bestPatternTimeframe([tf('5m', 2), tf('1h', 4), tf('1d', 1)])).toBe('1h');
  });

  it('falls back to the default timeframe when nothing was found', () => {
    // Never point the user at an arbitrary empty tab.
    expect(bestPatternTimeframe([tf('5m', 0), tf('1h', 0)])).toBe(DEFAULT_PATTERN_TIMEFRAME);
    expect(bestPatternTimeframe(null)).toBe(DEFAULT_PATTERN_TIMEFRAME);
    expect(bestPatternTimeframe([])).toBe(DEFAULT_PATTERN_TIMEFRAME);
  });

  it('keeps the first timeframe on a tie, matching the scanner tab order', () => {
    expect(bestPatternTimeframe([tf('5m', 3), tf('1h', 3)])).toBe('5m');
  });

  it('counts forming patterns separately from complete ones', () => {
    // A forming pattern is not yet a pattern that has happened, so the strip
    // reports it as its own figure rather than folding it into the total silently.
    expect(formingPatternCount([tf('5m', 3, 2), tf('1h', 2, 0)])).toBe(2);
    expect(formingPatternCount(null)).toBe(0);
  });
});
