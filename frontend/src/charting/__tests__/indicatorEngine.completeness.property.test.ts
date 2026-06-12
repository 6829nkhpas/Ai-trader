// Feature: professional-charting-suite, Property 5
//
// Property-based test for Property 5: "Indicator plots contain every defined
// line, band, and reference level" (Validates Requirements 2.8, 3.5).
//
// For any sufficiently-long candle series, every registered indicator (overlay
// or oscillator) must, when given enough data, produce a plot that includes:
//   - every constituent line it defines, each non-empty and time-anchored,
//   - every filled band's upper edge, lower edge, and a fill colour,
//   - every reference level it defines (e.g. RSI 30/70, Stochastic 20/80).
//
// The expectation map below pins down the exact structure each indicator
// promises. If a new indicator is registered without a matching expectation,
// the test fails by design — keeping this completeness contract honest.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import { listIndicators } from '@/charting/engines';
import type { IndicatorDef, IndicatorParams } from '@/charting/engines';
import type { ChartCandle } from '@/charting/types';

const RUNS = 100;

/**
 * What each indicator promises to emit under its default parameters.
 *  - `lines`: every line id that must be present and non-empty
 *  - `bands`: number of filled bands that must be present
 *  - `referenceLevels`: the reference levels that must all be present
 *  - `projectedLines`: line ids whose points are projected past the last candle
 *    (Ichimoku senkou spans) and therefore are NOT required to land on an
 *    existing candle timestamp, only to be finite and strictly ascending.
 */
interface PlotExpectation {
  lines: string[];
  bands: number;
  referenceLevels: number[];
  projectedLines?: string[];
}

const EXPECTED: Record<string, PlotExpectation> = {
  // Overlays
  sma: { lines: ['sma'], bands: 0, referenceLevels: [] },
  ema: { lines: ['ema'], bands: 0, referenceLevels: [] },
  wma: { lines: ['wma'], bands: 0, referenceLevels: [] },
  bollinger: { lines: ['middle', 'upper', 'lower'], bands: 1, referenceLevels: [] },
  vwap: { lines: ['vwap'], bands: 0, referenceLevels: [] },
  ichimoku: {
    lines: ['tenkan', 'kijun', 'spanA', 'spanB', 'chikou'],
    bands: 1,
    referenceLevels: [],
    projectedLines: ['spanA', 'spanB'],
  },
  supertrend: { lines: ['supertrend'], bands: 0, referenceLevels: [] },
  psar: { lines: ['psar'], bands: 0, referenceLevels: [] },
  donchian: { lines: ['middle', 'upper', 'lower'], bands: 1, referenceLevels: [] },
  keltner: { lines: ['middle', 'upper', 'lower'], bands: 1, referenceLevels: [] },
  // Oscillators
  rsi: { lines: ['rsi'], bands: 0, referenceLevels: [30, 70] },
  macd: { lines: ['macd', 'signal', 'histogram'], bands: 0, referenceLevels: [0] },
  stochastic: { lines: ['k', 'd'], bands: 0, referenceLevels: [20, 80] },
  adx: { lines: ['plusDI', 'minusDI', 'adx'], bands: 0, referenceLevels: [25] },
  atr: { lines: ['atr'], bands: 0, referenceLevels: [] },
  obv: { lines: ['obv'], bands: 0, referenceLevels: [0] },
  cci: { lines: ['cci'], bands: 0, referenceLevels: [-100, 100] },
  mfi: { lines: ['mfi'], bands: 0, referenceLevels: [20, 80] },
  'williams-r': { lines: ['williams-r'], bands: 0, referenceLevels: [-80, -20] },
};

/**
 * The largest default lookback across all registered indicators. A series
 * comfortably longer than this guarantees every indicator has sufficient data.
 */
const MAX_LOOKBACK = Math.max(
  ...listIndicators().map((def) => def.minLookback(def.defaults)),
);

/**
 * A random-walk OHLC candle series of at least `minLen` candles, with strictly
 * ascending timestamps, valid high/low envelopes, and positive volume so that
 * volume-aware indicators (OBV, MFI, VWAP) get meaningful weights.
 */
const candleSeries = (minLen: number): fc.Arbitrary<ChartCandle[]> =>
  fc
    .array(
      fc.record({
        move: fc.double({ min: -5, max: 5, noNaN: true, noDefaultInfinity: true }),
        spread: fc.double({ min: 0.1, max: 5, noNaN: true, noDefaultInfinity: true }),
        vol: fc.double({ min: 1, max: 10_000, noNaN: true, noDefaultInfinity: true }),
      }),
      { minLength: minLen, maxLength: minLen + 40 },
    )
    .map((moves) => {
      const out: ChartCandle[] = [];
      let price = 100;
      for (let i = 0; i < moves.length; i += 1) {
        const open = price;
        const close = Math.max(0.01, open + moves[i].move);
        const high = Math.max(open, close) + moves[i].spread;
        const low = Math.max(0.001, Math.min(open, close) - moves[i].spread);
        out.push({
          time: 1_000 + i * 60,
          open,
          high,
          low,
          close,
          volume: moves[i].vol,
        } as ChartCandle);
        price = close;
      }
      return out;
    });

/** Assert a line's points are finite and strictly ascending in time. */
function assertWellFormedLine(points: { time: number; value: number }[], label: string): void {
  expect(points.length, `${label} should be non-empty`).toBeGreaterThan(0);
  for (let i = 0; i < points.length; i += 1) {
    expect(Number.isFinite(points[i].value), `${label} value finite`).toBe(true);
    expect(Number.isFinite(points[i].time), `${label} time finite`).toBe(true);
    if (i > 0) {
      expect(points[i].time, `${label} time strictly ascending`).toBeGreaterThan(
        points[i - 1].time,
      );
    }
  }
}

describe('Property 5: indicator plots contain every defined line, band, and reference level', () => {
  it('every registered indicator with sufficient data emits all promised lines, bands, and reference levels', () => {
    fc.assert(
      fc.property(candleSeries(MAX_LOOKBACK + 10), (candles) => {
        const candleTimes = new Set(candles.map((c) => c.time));
        const defs: IndicatorDef[] = listIndicators();

        for (const def of defs) {
          const expectation = EXPECTED[def.id];
          // Completeness guard: a newly-registered indicator must declare its
          // expected plot structure here, otherwise this test must be updated.
          expect(expectation, `no plot expectation declared for "${def.id}"`).toBeDefined();

          const params: IndicatorParams = def.defaults;
          // Sanity: our series is long enough for this indicator.
          expect(candles.length).toBeGreaterThanOrEqual(def.minLookback(params));

          const plot = def.compute(candles, params);

          // Sufficient data -> not flagged insufficient.
          expect(plot.insufficientData ?? false, `${def.id} should have sufficient data`).toBe(
            false,
          );

          // Every defined line is present, non-empty, and time-anchored.
          const projected = new Set(expectation.projectedLines ?? []);
          for (const lineId of expectation.lines) {
            const found = plot.lines.find((l) => l.id === lineId);
            expect(found, `${def.id} missing line "${lineId}"`).toBeDefined();
            const points = found!.points;
            assertWellFormedLine(points, `${def.id}.${lineId}`);
            // Non-projected lines must be anchored to actual candle timestamps.
            if (!projected.has(lineId)) {
              for (const pt of points) {
                expect(
                  candleTimes.has(pt.time),
                  `${def.id}.${lineId} point time ${pt.time} not anchored to a candle`,
                ).toBe(true);
              }
            } else {
              // Projected lines anchor at or after the first candle time.
              for (const pt of points) {
                expect(pt.time).toBeGreaterThanOrEqual(candles[0].time);
              }
            }
          }

          // Every promised band carries non-empty upper/lower edges and a fill.
          if (expectation.bands > 0) {
            expect(plot.bands, `${def.id} should define bands`).toBeDefined();
            expect(plot.bands!.length).toBeGreaterThanOrEqual(expectation.bands);
            for (let b = 0; b < expectation.bands; b += 1) {
              const band = plot.bands![b];
              expect(band.upper.length, `${def.id} band ${b} upper non-empty`).toBeGreaterThan(0);
              expect(band.lower.length, `${def.id} band ${b} lower non-empty`).toBeGreaterThan(0);
              expect(typeof band.fill, `${def.id} band ${b} fill colour`).toBe('string');
              expect(band.fill.length).toBeGreaterThan(0);
            }
          }

          // Every defined reference level is present in the plot.
          if (expectation.referenceLevels.length > 0) {
            expect(
              plot.referenceLevels,
              `${def.id} should expose reference levels`,
            ).toBeDefined();
            for (const level of expectation.referenceLevels) {
              expect(
                plot.referenceLevels!.includes(level),
                `${def.id} missing reference level ${level}`,
              ).toBe(true);
            }
          }
        }
      }),
      { numRuns: RUNS },
    );
  });
});
