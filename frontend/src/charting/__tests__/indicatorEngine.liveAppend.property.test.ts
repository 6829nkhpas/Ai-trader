// Feature: professional-charting-suite, Property 6
//
// Property-based test for Property 6: "Live append equals full recompute"
// for the indicator engines (overlay indicators + oscillators).
// (Validates Requirements 2.7, 3.7.)
//
// Statement (design.md, Property 6): for any engine that supports live updates
// and any candle series, appending a new candle and incrementally updating
// yields the same result as recomputing over the full extended series.
//
// Formalization for indicators. The indicator `compute` is pure and
// deterministic, and every indicator value is causal: the value anchored to a
// candle depends only on that candle and the ones before it. Therefore the
// "incremental append" of one new candle can only introduce/refresh the tail
// of the plot — it must never retroactively change an already-settled value.
//
// We verify this directly. Let `full` be a sufficiently-long series and
// `prefix = full.slice(0, -1)` (the series before the newest candle arrives).
// For every registered indicator we compute the plot over both series and
// assert that, for every plotted point that exists in BOTH computations
// (matched by line/band id and timestamp), the value is identical. In other
// words, appending the newest candle leaves the prefix of the full recompute
// unchanged — the live "append" result matches the prior settled output plus a
// new tail. Reference levels (data-independent) and the warm-up count (anchored
// to the start of the series) must also be stable.
//
// Points that exist only in the full computation are the genuinely-new tail
// (e.g. the value for the appended candle, or forward-projected Ichimoku cloud
// points whose projection base moved) and are intentionally excluded — those
// are exactly the "append" delta the property allows.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { listIndicators } from '@/charting/engines';
import type { IndicatorBand, IndicatorPlot } from '@/charting/engines';
import type { ChartCandle, LinePoint } from '@/charting/types';

const RUNS = 100;

/** Relative tolerance for floating-point equality of indicator values. */
const EPS = 1e-9;

const approxEqual = (a: number, b: number): boolean =>
  Math.abs(a - b) <= EPS * Math.max(1, Math.abs(a), Math.abs(b));

/** A single finite price value generator. */
const price = () =>
  fc.double({ min: 0.0001, max: 100_000, noNaN: true, noDefaultInfinity: true });

/** Generate a well-formed OHLC candle at a fixed time. */
const candleAt = (time: number): fc.Arbitrary<ChartCandle> =>
  fc.record({ a: price(), b: price(), c: price(), d: price() }).map(({ a, b, c, d }) => ({
    time,
    open: a,
    high: Math.max(a, b, c, d),
    low: Math.min(a, b, c, d),
    close: c,
  }));

/**
 * Generate a candle series with strictly ascending unique timestamps. The
 * length is large enough (>= 60) that every registered indicator — including
 * Ichimoku (needs 52) and MACD (needs 35) — has sufficient data with its
 * default parameters for both the full series and the one-shorter prefix.
 */
const candleSeries = () =>
  fc
    .uniqueArray(fc.integer({ min: 0, max: 5_000_000 }), { minLength: 60, maxLength: 120 })
    .chain((times) => {
      const sorted = [...times].sort((x, y) => x - y);
      return fc.tuple(...sorted.map((t) => candleAt(t)));
    })
    .map((arr) => arr as ChartCandle[]);

/** Index a line's points by timestamp for intersection comparison. */
const byTime = (points: LinePoint[]): Map<number, number> => {
  const m = new Map<number, number>();
  for (const p of points) m.set(p.time, p.value);
  return m;
};

/**
 * Assert that every point shared (by timestamp) between two point arrays has an
 * identical value. Points present in only one array are the allowed append
 * delta and are skipped.
 */
const assertSharedPointsEqual = (
  label: string,
  fullPts: LinePoint[],
  prefixPts: LinePoint[],
): void => {
  const prefixMap = byTime(prefixPts);
  for (const p of fullPts) {
    const prior = prefixMap.get(p.time);
    if (prior === undefined) continue; // new tail point — allowed
    expect(
      approxEqual(p.value, prior),
      `${label} value at time ${p.time} changed retroactively: full=${p.value} prefix=${prior}`,
    ).toBe(true);
  }
};

const assertBandsEqual = (
  label: string,
  fullBands: IndicatorBand[] | undefined,
  prefixBands: IndicatorBand[] | undefined,
): void => {
  if (!fullBands || !prefixBands) {
    expect(Boolean(fullBands)).toBe(Boolean(prefixBands));
    return;
  }
  expect(fullBands.length).toBe(prefixBands.length);
  for (let i = 0; i < fullBands.length; i++) {
    assertSharedPointsEqual(`${label} band[${i}].upper`, fullBands[i].upper, prefixBands[i].upper);
    assertSharedPointsEqual(`${label} band[${i}].lower`, fullBands[i].lower, prefixBands[i].lower);
  }
};

const assertSettledOutputStable = (
  name: string,
  full: IndicatorPlot,
  prefix: IndicatorPlot,
): void => {
  // Match each full line to the prefix line of the same id.
  const prefixLines = new Map(prefix.lines.map((l) => [l.id, l]));
  for (const fLine of full.lines) {
    const pLine = prefixLines.get(fLine.id);
    if (!pLine) continue; // a line that only appears once data is long enough
    assertSharedPointsEqual(`${name}.${fLine.id}`, fLine.points, pLine.points);
  }

  assertBandsEqual(name, full.bands, prefix.bands);

  // Reference levels are data-independent and must be identical.
  expect(full.referenceLevels ?? []).toEqual(prefix.referenceLevels ?? []);

  // Warm-up is anchored to the start of the series, so appending a newest
  // candle must not change it.
  expect(full.warmupBars).toBe(prefix.warmupBars);
};

describe('Property 6: Live append equals full recompute (indicators)', () => {
  const indicators = listIndicators();

  it('has registered overlay and oscillator indicators to exercise', () => {
    expect(indicators.length).toBeGreaterThan(0);
    expect(indicators.some((d) => d.kind === 'overlay')).toBe(true);
    expect(indicators.some((d) => d.kind === 'oscillator')).toBe(true);
  });

  for (const def of indicators) {
    it(`${def.id}: appending a candle does not change settled values`, () => {
      fc.assert(
        fc.property(candleSeries(), (full) => {
          const prefix = full.slice(0, -1);

          const fullPlot = def.compute(full, {});
          const prefixPlot = def.compute(prefix, {});

          // With a >= 60 candle series and default params both computations are
          // sufficient; guard anyway so the property is never vacuously wrong.
          expect(fullPlot.insufficientData).not.toBe(true);
          expect(prefixPlot.insufficientData).not.toBe(true);

          assertSettledOutputStable(def.id, fullPlot, prefixPlot);
        }),
        { numRuns: RUNS },
      );
    });
  }
});
