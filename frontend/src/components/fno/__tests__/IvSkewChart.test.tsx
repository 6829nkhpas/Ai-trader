// @vitest-environment jsdom
/* eslint-disable @typescript-eslint/no-explicit-any */

/**
 * F&O Frontend Section (F4) — component test for `IvSkewChart` (task 7.5).
 *
 * Validates (Requirements 4.x, 7.2):
 * - The chart + line series are created exactly ONCE and the model is pushed
 *   through the series via `setData` on a model change, rather than recreating
 *   the chart per render (R7.2).
 * - The ATM strike is marked via `createPriceLine` only when `atmStrike` is
 *   non-null (R4.3).
 * - An empty point set renders the explicit Unavailable_State overlay (R4.4).
 *
 * `lightweight-charts` is mocked so the assertions inspect the chart/series API
 * calls without a real canvas.
 */

import React from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

import IvSkewChart from '../IvSkewChart';
import type { IvSkewModel } from '../viewModel';

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
    CrosshairMode: { Normal: 0 },
    LineSeries: 'LineSeries',
  };
});

class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

const populated: IvSkewModel = {
  points: [
    { strike: 23800, iv: 0.142 },
    { strike: 24000, iv: 0.131 },
    { strike: 24200, iv: 0.138 },
  ],
  atmStrike: 24000,
};

const populatedNoAtm: IvSkewModel = {
  points: [
    { strike: 23900, iv: 0.15 },
    { strike: 24100, iv: 0.149 },
  ],
  atmStrike: null,
};

const empty: IvSkewModel = { points: [], atmStrike: null };

describe('IvSkewChart (component)', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', NoopResizeObserver);
    lc.reset();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('creates one line series once and pushes IV points via setData (R4.1, R7.2)', () => {
    render(<IvSkewChart model={populated} />);

    expect(lc.createChartCalls).toBe(1);
    expect(lc.addSeriesCalls).toHaveLength(1);
    expect(lc.addSeriesCalls[0].definition).toBe('LineSeries');

    const series = lc.series[0];
    expect(series.setData).toHaveBeenCalledTimes(1);
    const data = series.setData.mock.calls[0][0];
    expect(data.map((d: any) => d.time)).toEqual([23800, 24000, 24200]);
    expect(data.map((d: any) => d.value)).toEqual([0.142, 0.131, 0.138]);
  });

  it('marks the ATM strike via a price-line only when atmStrike is non-null (R4.3)', () => {
    const withAtm = render(<IvSkewChart model={populated} />);
    expect(lc.series[0].createPriceLine).toHaveBeenCalledTimes(1);
    withAtm.unmount();

    lc.reset();
    render(<IvSkewChart model={populatedNoAtm} />);
    expect(lc.series[0].createPriceLine).not.toHaveBeenCalled();
  });

  it('updates via setData on model change WITHOUT recreating the chart (R7.2)', () => {
    const { rerender } = render(<IvSkewChart model={populated} />);
    expect(lc.createChartCalls).toBe(1);
    const series = lc.series[0];
    expect(series.setData).toHaveBeenCalledTimes(1);

    rerender(<IvSkewChart model={populatedNoAtm} />);

    expect(lc.createChartCalls).toBe(1);
    expect(lc.addSeriesCalls).toHaveLength(1);
    expect(series.setData).toHaveBeenCalledTimes(2);
    const latest = series.setData.mock.calls[1][0];
    expect(latest.map((d: any) => d.time)).toEqual([23900, 24100]);
  });

  it('renders the Unavailable_State overlay when there are no points (R4.4)', () => {
    render(<IvSkewChart model={empty} />);
    expect(screen.getByText('IV skew unavailable')).toBeInTheDocument();
  });
});
