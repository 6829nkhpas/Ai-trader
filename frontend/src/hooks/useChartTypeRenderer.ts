// Feature: professional-charting-suite
//
// useChartTypeRenderer — drives the price-pane series from `ChartTypeEngine`
// output so the renderer can present any of the 11 supported chart types from
// one canonical candle series (Requirement 1.1, and the price-series substrate
// the overlay indicators of Requirements 2.2/2.4 align to).
//
// The base chart pipeline (`useChartInit` + `useChartDataSync`) owns the
// default candlestick presentation. This hook layers chart-type generalization
// on top:
//   - For the default `candlestick` type it is a no-op, leaving the base
//     pipeline (with volume, EMAs and the predictive ghost line) untouched so
//     existing behavior is preserved exactly.
//   - For candlestick-kind transforms (Heikin Ashi, hollow candle, Renko,
//     Point & Figure, Line Break) it overrides the candle series data with the
//     engine's transformed candles.
//   - For line/area/baseline/bar kinds it renders a managed alternate series of
//     the right type and hides the candlestick series.
//
// Price-driven (`indexBased`) transforms use a synthetic ordinal x-axis, so the
// time-domain volume / EMA / ghost overlays are cleared while they are active.

import { useEffect, useRef } from 'react';
import {
  AreaSeries,
  BarSeries,
  BaselineSeries,
  CandlestickSeries,
  LineSeries,
  type ISeriesApi,
  type SeriesType,
  type Time,
} from 'lightweight-charts';

import type { ChartRefs, ChartCandle } from '../utils/chartTypes';
import { COLORS } from '../utils/chartTypes';
import {
  buildSeries,
  type ChartType,
  type ChartTypeParams,
  type RenderableSeries,
} from '../charting/engines';
import type { LinePoint } from '../charting/types';

/** lightweight-charts native series constructor for a renderable kind. */
const SERIES_DEFINITION = {
  candlestick: CandlestickSeries,
  bar: BarSeries,
  line: LineSeries,
  area: AreaSeries,
  baseline: BaselineSeries,
} as const;

function toCandleData(points: ChartCandle[]): Array<{
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
}> {
  return points.map((c) => ({
    time: c.time as Time,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
  }));
}

function toLineData(points: LinePoint[]): Array<{ time: Time; value: number }> {
  return points.map((p) => ({ time: p.time as Time, value: p.value }));
}

/** Default options per managed alternate-series kind. */
function altSeriesOptions(kind: RenderableSeries['kind']): Record<string, unknown> {
  switch (kind) {
    case 'bar':
      return { upColor: COLORS.up, downColor: COLORS.down, thinBars: false };
    case 'line':
      return { color: COLORS.ema9, lineWidth: 2, lastValueVisible: true };
    case 'area':
      return {
        lineColor: COLORS.ema9,
        topColor: 'rgba(56,189,248,0.30)',
        bottomColor: 'rgba(56,189,248,0.02)',
        lineWidth: 2,
      };
    case 'baseline':
      return {
        topLineColor: COLORS.up,
        bottomLineColor: COLORS.down,
        lineWidth: 2,
      };
    default:
      return {};
  }
}

/**
 * Render the price pane using the selected chart type.
 *
 * @param refs    the shared chart refs.
 * @param candles the canonical candle series fed to the engine.
 * @param type    the selected chart type (defaults to candlestick).
 * @param params  configuration parameters for the parametric chart types.
 */
export function useChartTypeRenderer(
  refs: ChartRefs,
  candles: ChartCandle[],
  type: ChartType = 'candlestick',
  params: ChartTypeParams = {},
): void {
  const { chartRef, candleSeriesRef, volumeSeriesRef, ema9SeriesRef, ema21SeriesRef, ghostLineRef } = refs;
  const altSeriesRef = useRef<ISeriesApi<SeriesType> | null>(null);
  const altKindRef = useRef<RenderableSeries['kind'] | null>(null);

  const paramsKey = JSON.stringify(params);

  useEffect(() => {
    const chart = chartRef.current;
    const candleSeries = candleSeriesRef.current;
    if (!chart || !candleSeries) return;

    const removeAlt = () => {
      if (altSeriesRef.current) {
        try {
          chart.removeSeries(altSeriesRef.current);
        } catch {
          /* already detached */
        }
        altSeriesRef.current = null;
        altKindRef.current = null;
      }
    };

    // Default candlestick: hand the price pane back to the base pipeline.
    if (type === 'candlestick') {
      removeAlt();
      try {
        candleSeries.applyOptions({ visible: true });
      } catch {
        /* detached */
      }
      return;
    }

    const renderable = buildSeries(candles, type, params);

    // Time-domain overlays only make sense on a wall-clock x-axis; clear them
    // for the price-driven (index-based) transforms.
    if (renderable.indexBased) {
      try {
        volumeSeriesRef.current?.setData([]);
        ema9SeriesRef.current?.setData([]);
        ema21SeriesRef.current?.setData([]);
        ghostLineRef.current?.setData([]);
      } catch {
        /* detached */
      }
    }

    if (renderable.kind === 'candlestick') {
      // Hollow candle: transparent up-body. All other candlestick-kind types
      // keep the standard fill.
      removeAlt();
      try {
        candleSeries.applyOptions({
          visible: true,
          upColor: type === 'hollow-candle' ? 'rgba(0,0,0,0)' : COLORS.up,
          downColor: COLORS.down,
          wickUpColor: COLORS.up,
          wickDownColor: COLORS.down,
          borderUpColor: COLORS.up,
          borderDownColor: COLORS.down,
          borderVisible: type === 'hollow-candle',
        });
        candleSeries.setData(toCandleData(renderable.points as ChartCandle[]));
      } catch {
        /* detached */
      }
      return;
    }

    // Non-candlestick kinds: hide the candle series and render a managed series.
    try {
      candleSeries.applyOptions({ visible: false });
    } catch {
      /* detached */
    }

    if (altKindRef.current !== renderable.kind) {
      removeAlt();
      try {
        const def = SERIES_DEFINITION[renderable.kind];
        altSeriesRef.current = chart.addSeries(def, altSeriesOptions(renderable.kind));
        altKindRef.current = renderable.kind;
      } catch {
        return;
      }
    }

    const series = altSeriesRef.current;
    if (!series) return;
    try {
      if (renderable.kind === 'bar') {
        series.setData(toCandleData(renderable.points as ChartCandle[]));
      } else {
        series.setData(toLineData(renderable.points as LinePoint[]));
      }
    } catch {
      /* detached */
    }
  }, [type, paramsKey, candles, chartRef, candleSeriesRef, volumeSeriesRef, ema9SeriesRef, ema21SeriesRef, ghostLineRef, params]);

  // Remove the managed alternate series on unmount.
  useEffect(() => {
    return () => {
      const chart = chartRef.current;
      if (altSeriesRef.current) {
        try {
          chart?.removeSeries(altSeriesRef.current);
        } catch {
          /* detached */
        }
        altSeriesRef.current = null;
        altKindRef.current = null;
      }
    };
  }, [chartRef]);
}
