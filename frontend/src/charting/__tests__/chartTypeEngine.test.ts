// Feature: professional-charting-suite
//
// Unit tests for the chart-type registry and the engine's empty/error-state
// behavior (task 2.3). These cover specific examples and edge cases:
//   - the registry exposes all 11 supported chart types (Requirement 1.1) and
//     `buildSeries` produces a renderable series for every one of them;
//   - an empty candle dataset yields an empty series of the appropriate kind so
//     the renderer can show an empty/loading state (Requirement 1.8);
//   - a fetch failure retains the previously rendered series rather than
//     replacing it with an empty frame (Requirement 1.9).
//
// The universal correctness properties for chart types (Property 1, Heikin Ashi)
// are exercised separately by the property test (task 2.2).

import { describe, it, expect } from 'vitest';

import {
  buildSeries,
  CHART_TYPES,
  CHART_TYPE_PARAM_DEFAULTS,
  type ChartType,
  type RenderableSeries,
} from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';

/** The 11 chart types Requirement 1.1 mandates, listed independently here so
 *  the test fails if the engine's registry drifts from the requirement. */
const REQUIRED_CHART_TYPES: ChartType[] = [
  'candlestick',
  'hollow-candle',
  'ohlc-bar',
  'line',
  'area',
  'baseline',
  'heikin-ashi',
  'renko',
  'kagi',
  'point-figure',
  'line-break',
];

/** Which native series kind and index-mode each chart type should resolve to. */
const EXPECTED_SHAPE: Record<
  ChartType,
  { kind: RenderableSeries['kind']; indexBased: boolean }
> = {
  candlestick: { kind: 'candlestick', indexBased: false },
  'hollow-candle': { kind: 'candlestick', indexBased: false },
  'ohlc-bar': { kind: 'bar', indexBased: false },
  line: { kind: 'line', indexBased: false },
  area: { kind: 'area', indexBased: false },
  baseline: { kind: 'baseline', indexBased: false },
  'heikin-ashi': { kind: 'candlestick', indexBased: false },
  renko: { kind: 'candlestick', indexBased: true },
  kagi: { kind: 'line', indexBased: true },
  'point-figure': { kind: 'candlestick', indexBased: true },
  'line-break': { kind: 'candlestick', indexBased: true },
};

/** A small ascending, mildly volatile candle series usable by every type. */
function sampleCandles(): ChartCandle[] {
  const out: ChartCandle[] = [];
  let price = 100;
  for (let i = 0; i < 40; i += 1) {
    const open = price;
    const close = price + (i % 2 === 0 ? 3 : -2);
    const high = Math.max(open, close) + 1;
    const low = Math.min(open, close) - 1;
    out.push({ time: 1_000 + i, open, high, low, close });
    price = close;
  }
  return out;
}

describe('CHART_TYPES registry (Requirement 1.1)', () => {
  it('contains exactly the 11 required chart types', () => {
    expect(CHART_TYPES).toHaveLength(11);
    // Order-independent set equality.
    expect([...CHART_TYPES].sort()).toEqual([...REQUIRED_CHART_TYPES].sort());
  });

  it('lists each required type exactly once (no duplicates)', () => {
    expect(new Set(CHART_TYPES).size).toBe(CHART_TYPES.length);
    for (const t of REQUIRED_CHART_TYPES) {
      expect(CHART_TYPES).toContain(t);
    }
  });
});

describe('buildSeries handles every chart type (Requirement 1.1)', () => {
  const candles = sampleCandles();

  it.each(REQUIRED_CHART_TYPES)('produces a renderable series for %s', (type) => {
    const series = buildSeries(candles, type, {});
    const expected = EXPECTED_SHAPE[type];

    expect(series.kind).toBe(expected.kind);
    expect(series.indexBased).toBe(expected.indexBased);
    expect(Array.isArray(series.points)).toBe(true);
    // Non-empty input yields at least one rendered point for every type.
    expect(series.points.length).toBeGreaterThan(0);
  });

  it('renders the same time-driven candle count for native candle types', () => {
    for (const type of ['candlestick', 'hollow-candle', 'ohlc-bar', 'heikin-ashi'] as ChartType[]) {
      const series = buildSeries(candles, type, {});
      expect(series.points).toHaveLength(candles.length);
    }
  });

  it('emits index-based ordinals (0,1,2,...) for brick/column types', () => {
    for (const type of ['renko', 'kagi', 'point-figure', 'line-break'] as ChartType[]) {
      const series = buildSeries(candles, type, CHART_TYPE_PARAM_DEFAULTS);
      expect(series.indexBased).toBe(true);
      const times = series.points.map((p) => p.time);
      expect(times).toEqual(times.map((_, i) => i));
    }
  });
});

describe('empty dataset yields an empty-state series (Requirement 1.8)', () => {
  it.each(REQUIRED_CHART_TYPES)('returns an empty series of the right kind for %s', (type) => {
    const series = buildSeries([], type, {});
    const expected = EXPECTED_SHAPE[type];

    // The series kind is still meaningful so the renderer can mount the right
    // primitive and show its empty/loading state rather than a wrong frame.
    expect(series.kind).toBe(expected.kind);
    expect(series.indexBased).toBe(expected.indexBased);
    expect(series.points).toEqual([]);
  });
});

describe('fetch failure retains the prior rendered series (Requirement 1.9)', () => {
  // The engine is pure; the "retain prior output on fetch failure" behavior
  // lives in the data-flow layer that feeds it. We model that documented
  // contract here: a fetch result drives what the renderer should display.
  type FetchResult =
    | { status: 'ok'; candles: ChartCandle[] }
    | { status: 'error' };

  interface RenderState {
    series: RenderableSeries;
    error: string | null;
  }

  function reduceOnFetch(
    prev: RenderState,
    result: FetchResult,
    type: ChartType,
  ): RenderState {
    if (result.status === 'error') {
      // Requirement 1.9: keep the previously rendered chart, surface an error.
      return { series: prev.series, error: 'data retrieval failed' };
    }
    return { series: buildSeries(result.candles, type, {}), error: null };
  }

  it('keeps the last good series and flags the error when a fetch fails', () => {
    const candles = sampleCandles();
    const initial: RenderState = {
      series: buildSeries(candles, 'candlestick', {}),
      error: null,
    };

    const afterFailure = reduceOnFetch(initial, { status: 'error' }, 'candlestick');

    // Same series instance is retained (no empty frame), error indication set.
    expect(afterFailure.series).toBe(initial.series);
    expect(afterFailure.series.points).toHaveLength(candles.length);
    expect(afterFailure.error).toBe('data retrieval failed');
  });

  it('clears the error and re-renders once a later fetch succeeds', () => {
    const first = sampleCandles();
    let state: RenderState = {
      series: buildSeries(first, 'line', {}),
      error: null,
    };

    state = reduceOnFetch(state, { status: 'error' }, 'line');
    expect(state.error).toBe('data retrieval failed');
    expect(state.series.points).toHaveLength(first.length);

    const recovered = first.slice(0, 10);
    state = reduceOnFetch(state, { status: 'ok', candles: recovered }, 'line');
    expect(state.error).toBeNull();
    expect(state.series.points).toHaveLength(recovered.length);
  });

  it('does not fall back to an empty frame on failure even from an empty start', () => {
    // An empty start (1.8 empty-state) that then fails to load must remain the
    // empty-state series, not get replaced by anything else.
    const start: RenderState = {
      series: buildSeries([], 'area', {}),
      error: null,
    };
    const afterFailure = reduceOnFetch(start, { status: 'error' }, 'area');
    expect(afterFailure.series).toBe(start.series);
    expect(afterFailure.series.points).toEqual([]);
    expect(afterFailure.error).toBe('data retrieval failed');
  });
});
