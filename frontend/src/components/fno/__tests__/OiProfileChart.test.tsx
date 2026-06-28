// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/no-explicit-any */

/**
 * F&O Frontend Section (F4) — component test for `OiProfileChart` (task 7.5).
 *
 * Validates (Requirements 3.4, 7.2):
 * - The call/put histogram series are created with DISTINCT bull vs bear theme
 *   colors (R3.4).
 * - The chart + series are created exactly ONCE and the model is pushed through
 *   the series via `setData` on a model change, rather than recreating the chart
 *   per render — a re-render with a new model does NOT call `createChart` again
 *   (R7.2).
 *
 * `lightweight-charts` is mocked so the assertions inspect the chart/series API
 * calls without a real canvas; the bull/bear colors come from the component's
 * CSS-variable fallbacks (jsdom defines no `--color-bull`/`--color-bear`).
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import OiProfileChart from '../OiProfileChart';
import type { OiProfileModel } from '../viewModel';

// Shared registry the mocked `lightweight-charts` writes into so the test can
// inspect how the component drove the chart/series API.
const lc = vi.hoisted(() => ({
  createChartCalls: 0,
  addSeriesCalls: [] as Array<{ definition: unknown; options: any }>,
  series: [] as Array<{
    setData: ReturnType<typeof vi.fn>;
    update: ReturnType<typeof vi.fn>;
    createPriceLine: ReturnType<typeof vi.fn>;
    removePriceLine: ReturnType<typeof vi.fn>;
  }>,
  reset() {
    this.createChartCalls = 0;
    this.addSeriesCalls.length = 0;
    this.series.length = 0;
  },
}));

vi.mock('lightweight-charts', () => {
  const makeSeries = () => ({
    setData: vi.fn(),
    update: vi.fn(),
    createPriceLine: vi.fn((opts: any) => ({ opts })),
    removePriceLine: vi.fn(),
  });

  return {
    createChart: vi.fn(() => {
      lc.createChartCalls += 1;
      const timeScaleApi = { fitContent: vi.fn() };
      return {
        addSeries: vi.fn((definition: unknown, options: any) => {
          const series = makeSeries();
          lc.addSeriesCalls.push({ definition, options });
          lc.series.push(series);
          return series;
        }),
        timeScale: vi.fn(() => timeScaleApi),
        resize: vi.fn(),
        remove: vi.fn(),
      };
    }),
    ColorType: { Solid: 'solid' },
    LineStyle: { Dashed: 2 },
    HistogramSeries: 'HistogramSeries',
  };
});

// jsdom has no ResizeObserver; stub a no-op implementation.
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const modelA: OiProfileModel = {
  points: [
    { strike: 23800, callOi: 1_200_000, putOi: 2_100_000 },
    { strike: 24000, callOi: 1_820_000, putOi: null },
    { strike: 24200, callOi: null, putOi: 980_000 },
  ],
  maxPain: 24000,
  support: 23800,
  resistance: 24200,
};

const modelB: OiProfileModel = {
  points: [
    { strike: 23900, callOi: 900_000, putOi: 1_500_000 },
    { strike: 24100, callOi: 1_100_000, putOi: 1_300_000 },
  ],
  maxPain: null,
  support: null,
  resistance: null,
};

describe('OiProfileChart (component)', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', NoopResizeObserver);
    lc.reset();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('creates two histogram series with distinct bull vs bear colors (R3.4)', () => {
    render(<OiProfileChart model={modelA} />);

    // Exactly two series, both histograms (call OI, put OI).
    expect(lc.addSeriesCalls).toHaveLength(2);
    expect(lc.addSeriesCalls[0].definition).toBe('HistogramSeries');
    expect(lc.addSeriesCalls[1].definition).toBe('HistogramSeries');

    const callColor = lc.addSeriesCalls[0].options.color;
    const putColor = lc.addSeriesCalls[1].options.color;

    // Distinct bull (call) vs bear (put) colors — the theme fallbacks.
    expect(callColor).toBe('#10b981'); // --color-bull fallback (bull)
    expect(putColor).toBe('#ef4444'); // --color-bear fallback (bear)
    expect(callColor).not.toBe(putColor);
  });

  it('creates the chart once and pushes data via setData (R7.2)', () => {
    render(<OiProfileChart model={modelA} />);

    expect(lc.createChartCalls).toBe(1);
    expect(lc.series).toHaveLength(2);

    const [callSeries, putSeries] = lc.series;
    expect(callSeries.setData).toHaveBeenCalledTimes(1);
    expect(putSeries.setData).toHaveBeenCalledTimes(1);

    // Call series receives only the strikes with a non-null callOi (R8.2: a
    // null OI is dropped, never plotted as a fabricated 0).
    const callData = callSeries.setData.mock.calls[0][0];
    expect(callData.map((d: any) => d.time)).toEqual([23800, 24000]);
    const putData = putSeries.setData.mock.calls[0][0];
    expect(putData.map((d: any) => d.time)).toEqual([23800, 24200]);
  });

  it('updates via setData on model change WITHOUT recreating the chart (R7.2)', () => {
    const { rerender } = render(<OiProfileChart model={modelA} />);

    expect(lc.createChartCalls).toBe(1);
    const [callSeries, putSeries] = lc.series;
    expect(callSeries.setData).toHaveBeenCalledTimes(1);

    // Re-render with a brand-new model object.
    rerender(<OiProfileChart model={modelB} />);

    // The chart/series were NOT recreated — still one chart, same two series.
    expect(lc.createChartCalls).toBe(1);
    expect(lc.addSeriesCalls).toHaveLength(2);

    // Data flowed through the existing series via setData again (zero-latency
    // update path) rather than a fresh chart.
    expect(callSeries.setData).toHaveBeenCalledTimes(2);
    expect(putSeries.setData).toHaveBeenCalledTimes(2);

    const latestCallData = callSeries.setData.mock.calls[1][0];
    expect(latestCallData.map((d: any) => d.time)).toEqual([23900, 24100]);
  });

  it('draws marker price-lines only when the analytic level is non-null (R3.2, R3.3)', () => {
    const { rerender } = render(<OiProfileChart model={modelA} />);

    const callSeries = lc.series[0];
    // modelA has maxPain + support + resistance => three price-lines.
    expect(callSeries.createPriceLine).toHaveBeenCalledTimes(3);

    // modelB has all-null levels => no new price-lines created on the update.
    rerender(<OiProfileChart model={modelB} />);
    expect(callSeries.createPriceLine).toHaveBeenCalledTimes(3);
  });
});
