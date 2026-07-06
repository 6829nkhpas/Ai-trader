// Feature: professional-charting-suite, Property 32
//
// Property-based test for Property 32: "Out-of-range or warm-up positions
// yield a no-value placeholder" (Validates Requirements 10.3, 10.8).
//
// For any crosshair time that is outside the loaded candle range, or that
// falls within an indicator's warm-up region (or for an indicator whose plot
// is flagged insufficientData), the readout is the NO_VALUE placeholder rather
// than a numeric value borrowed from an adjacent candle / point.
//
// Conversely, for a time that does have a loaded candle and a defined indicator
// point, the readout carries real (non-placeholder) numeric strings. This
// "presence" direction guards against an over-eager implementation that always
// emits placeholders.

import { describe, it, expect } from 'vitest';
import fc from 'fast-check';

import {
  buildCrosshairReadout,
  NO_VALUE,
  type IndicatorReadoutInput,
} from '@/charting/crosshair';
import type { ChartCandle, LinePoint, LineStyleSpec } from '@/charting/types';

const RUNS = 100;

const STYLE: LineStyleSpec = { color: '#fff', lineWidth: 1, lineStyle: 'solid' };

/** A finite, well-scaled numeric value generator (never NaN / ±Infinity). */
const finite = fc.double({ noNaN: true, noDefaultInfinity: true, min: -1e6, max: 1e6 });

/** Build a candle at `time` with finite OHLC values. */
function candleAt(time: number, o: number, h: number, l: number, c: number): ChartCandle {
  return { time, open: o, high: h, low: l, close: c };
}

/** A set of candles at strictly-ascending, unique integer times in [0, 10000]. */
const candlesArb = fc
  .uniqueArray(fc.integer({ min: 0, max: 10000 }), { minLength: 1, maxLength: 25 })
  .chain((times) => {
    const sorted = [...times].sort((a, b) => a - b);
    return fc
      .array(
        fc.tuple(finite, finite, finite, finite),
        { minLength: sorted.length, maxLength: sorted.length },
      )
      .map((vals) =>
        sorted.map((t, i) => {
          const [o, h, l, c] = vals[i];
          return candleAt(t, o, h, l, c);
        }),
      );
  });

describe('Property 32: out-of-range / warm-up positions yield a no-value placeholder', () => {
  it('OHLC is all-placeholder and hasCandle=false when no candle exists at time (Req 10.8)', () => {
    fc.assert(
      fc.property(candlesArb, fc.integer({ min: -10000, max: 20000 }), fc.integer({ min: 0, max: 8 }), (candles, time, precision) => {
        const times = new Set(candles.map((k) => k.time));
        // Only exercise the out-of-range case.
        fc.pre(!times.has(time));

        const readout = buildCrosshairReadout({ time, candles, indicators: [], precision });

        expect(readout.hasCandle).toBe(false);
        expect(readout.ohlc.open).toBe(NO_VALUE);
        expect(readout.ohlc.high).toBe(NO_VALUE);
        expect(readout.ohlc.low).toBe(NO_VALUE);
        expect(readout.ohlc.close).toBe(NO_VALUE);
      }),
      { numRuns: RUNS },
    );
  });

  it('an indicator line with no point at the crosshair time (warm-up) reads NO_VALUE (Req 10.3)', () => {
    fc.assert(
      fc.property(
        candlesArb.filter((c) => c.length >= 2),
        fc.integer({ min: 0, max: 10000 }),
        fc.integer({ min: 0, max: 8 }),
        (candles, warmupBars, precision) => {
          // Warm-up region: leading candles have no plotted point.
          const k = warmupBars % candles.length; // 0..len-1 leading bars omitted
          const definedTimes = candles.slice(k).map((c) => c.time);
          fc.pre(definedTimes.length >= 1);

          const points: LinePoint[] = candles
            .slice(k)
            .map((c, i) => ({ time: c.time, value: i + 1 })); // finite, non-placeholder

          const indicator: IndicatorReadoutInput = {
            instanceId: 'i1',
            indicatorId: 'sma',
            label: 'SMA',
            plot: { lines: [{ id: 'line', points, style: STYLE }], warmupBars: k, insufficientData: false },
          };

          // Crosshair on a warm-up bar (one without a plotted point).
          if (k > 0) {
            const warmTime = candles[0].time;
            const r = buildCrosshairReadout({ time: warmTime, candles, indicators: [indicator], precision });
            expect(r.indicators[0].lines[0].value).toBe(NO_VALUE);
          }
          // Crosshair on a defined bar yields a real numeric string.
          const definedTime = candles[k].time;
          const r2 = buildCrosshairReadout({ time: definedTime, candles, indicators: [indicator], precision });
          expect(r2.indicators[0].lines[0].value).not.toBe(NO_VALUE);
        },
      ),
      { numRuns: RUNS },
    );
  });

  it('insufficientData forces every indicator line to NO_VALUE regardless of points (Req 10.3)', () => {
    fc.assert(
      fc.property(candlesArb, fc.integer({ min: 0, max: 8 }), (candles, precision) => {
        // Points exist at every candle time, but the plot is flagged insufficient.
        const points: LinePoint[] = candles.map((c, i) => ({ time: c.time, value: i + 1 }));
        const indicator: IndicatorReadoutInput = {
          instanceId: 'i1',
          indicatorId: 'sma',
          label: 'SMA',
          plot: { lines: [{ id: 'line', points, style: STYLE }], warmupBars: 0, insufficientData: true },
        };

        const target = candles[0].time;
        const r = buildCrosshairReadout({ time: target, candles, indicators: [indicator], precision });
        for (const line of r.indicators[0].lines) {
          expect(line.value).toBe(NO_VALUE);
        }
      }),
      { numRuns: RUNS },
    );
  });

  it('a time with a candle and a defined indicator point yields non-placeholder values (presence)', () => {
    fc.assert(
      fc.property(candlesArb, fc.integer({ min: 0, max: 8 }), (candles, precision) => {
        const idx = candles.length - 1;
        const target = candles[idx].time;
        const points: LinePoint[] = [{ time: target, value: 42.5 }];
        const indicator: IndicatorReadoutInput = {
          instanceId: 'i1',
          indicatorId: 'sma',
          label: 'SMA',
          plot: { lines: [{ id: 'line', points, style: STYLE }], warmupBars: 0, insufficientData: false },
        };

        const r = buildCrosshairReadout({ time: target, candles, indicators: [indicator], precision });

        expect(r.hasCandle).toBe(true);
        expect(r.ohlc.open).not.toBe(NO_VALUE);
        expect(r.ohlc.high).not.toBe(NO_VALUE);
        expect(r.ohlc.low).not.toBe(NO_VALUE);
        expect(r.ohlc.close).not.toBe(NO_VALUE);
        expect(r.indicators[0].lines[0].value).not.toBe(NO_VALUE);
      }),
      { numRuns: RUNS },
    );
  });
});
