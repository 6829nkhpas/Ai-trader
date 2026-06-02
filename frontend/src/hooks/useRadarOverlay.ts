// hooks/useRadarOverlay.ts — On-chart visualization for Quant Radar (FEAT-037).
//
// Renders the user-selected radar detection on the master chart:
//   • Pattern  → a candle marker (arrow/flag) at the bar where it formed,
//                plus trendlines and bounding rectangle drawings added directly
//                to useChartUIStore so they are drawn using native drawing tools.
//   • Strategy → a candle marker at the trigger bar, plus a horizontal
//                price line at the strategy's key level (ORB/VWAP/SMA).
//
// The visualization target lives in useRadarStore; this hook subscribes to
// it imperatively so toggling/clicking a radar detection redraws instantly
// without forcing the heavy chart component to re-render.

import { useEffect } from 'react';
import {
  createSeriesMarkers,
  type Time,
  type ISeriesMarkersPluginApi,
  type IPriceLine,
  type SeriesMarker,
} from 'lightweight-charts';
import { TIMEFRAME_MS, type ChartRefs, type ChartCandle } from '../utils/chartTypes';
import { useRadarStore, type RadarVizTarget } from '../store/useRadarStore';
import { useTradeStore } from '../store/useTradeStore';
import { useChartUIStore, type Drawing } from '../store/useChartUIStore';
import { BIAS_COLORS } from '../utils/radarData';

export function useRadarOverlay(refs: ChartRefs, chartData: ChartCandle[]) {
  const { chartRef, candleSeriesRef, fibOverlayRef } = refs;

  useEffect(() => {
    const chart = chartRef.current;
    const series = candleSeriesRef.current;
    if (!chart || !series) return;

    // Dedicated markers plugin instance for radar (separate from any other
    // marker usage so we never clobber unrelated markers).
    const markers: ISeriesMarkersPluginApi<Time> = createSeriesMarkers(series, []);
    let priceLine: IPriceLine | null = null;
    let retryTimeout: ReturnType<typeof setTimeout> | null = null;
    let retryCount = 0;
    const MAX_RETRIES = 20;

    const clearLevel = () => {
      if (priceLine) {
        try { series.removePriceLine(priceLine); } catch { /* detached */ }
        priceLine = null;
      }
    };

    const clearRadarDrawings = () => {
      const drawings = useChartUIStore.getState().drawings;
      const cleanDrawings = drawings.filter(d => !d.id.startsWith('radar-'));
      if (drawings.length !== cleanDrawings.length) {
        useChartUIStore.setState({ drawings: cleanDrawings });
      }
    };

    const render = (isRetry = false) => {
      if (isRetry) {
        retryCount++;
        if (retryCount > MAX_RETRIES) {
          // Stop retrying to prevent CPU hogging
          return;
        }
      } else {
        retryCount = 0;
      }

      if (retryTimeout) {
        clearTimeout(retryTimeout);
        retryTimeout = null;
      }

      const { vizTarget, vizEnabled } = useRadarStore.getState();
      markers.setMarkers([]);
      clearLevel();

      if (!vizEnabled || !vizTarget) {
        clearRadarDrawings();
        return;
      }

      // Only draw when the chart's active symbol + timeframe match the detection.
      const { activeTimeframe, selectedSymbol } = useTradeStore.getState();
      if (
        vizTarget.timeframe !== activeTimeframe ||
        vizTarget.symbol.toUpperCase() !== selectedSymbol.toUpperCase()
      ) {
        clearRadarDrawings();
        return;
      }

      if (vizTarget.kind === 'pattern' && vizTarget.pattern) {
        drawPattern(vizTarget);
      } else if (vizTarget.kind === 'strategy' && vizTarget.strategy) {
        clearRadarDrawings(); // Clear pattern drawings when a strategy is selected
        drawStrategy(vizTarget);
      }
    };

    const drawPattern = (target: RadarVizTarget) => {
      const p = target.pattern!;
      const color = BIAS_COLORS[p.bias] ?? BIAS_COLORS.NEUTRAL;
      const bullish = p.bias === 'BULLISH';

      const intervalMs = TIMEFRAME_MS[target.timeframe] ?? 600_000;
      const intervalSec = intervalMs / 1000;
      const alignTime = (t: number) => Math.floor(t / intervalSec) * intervalSec;

      const alignedEndTime = alignTime(p.time);

      const marker: SeriesMarker<Time> = {
        time: alignedEndTime as Time,
        position: bullish ? 'belowBar' : 'aboveBar',
        color,
        shape: bullish ? 'arrowUp' : 'arrowDown',
        text: p.name,
        id: `radar-pattern-${alignedEndTime}`,
      };
      markers.setMarkers([marker]);

      if (!p.start_time) {
        clearRadarDrawings();
        return;
      }

      const alignedStartTime = alignTime(p.start_time);
      const rangeCandles = chartData.filter(c => c.time >= alignedStartTime && c.time <= alignedEndTime);

      // If candles are not yet loaded in chartData, retry
      if (rangeCandles.length < 3) {
        if (retryTimeout) clearTimeout(retryTimeout);
        retryTimeout = setTimeout(() => render(true), 50);
        return;
      }

      // Detect local swings (peaks and troughs)
      const swings: { time: number; price: number; isPeak: boolean }[] = [];
      const n = rangeCandles.length;
      const window = n > 35 ? 4 : n > 15 ? 2 : 1;

      for (let i = 0; i < n; i++) {
        let isPeak = true;
        let isTrough = true;

        const startIdx = Math.max(0, i - window);
        const endIdx = Math.min(n - 1, i + window);

        for (let j = startIdx; j <= endIdx; j++) {
          if (j === i) continue;
          if (rangeCandles[j].high >= rangeCandles[i].high) isPeak = false;
          if (rangeCandles[j].low <= rangeCandles[i].low) isTrough = false;
        }

        if (isPeak) {
          swings.push({ time: rangeCandles[i].time, price: rangeCandles[i].high, isPeak: true });
        } else if (isTrough) {
          swings.push({ time: rangeCandles[i].time, price: rangeCandles[i].low, isPeak: false });
        }
      }

      // Alternating swings (Peak -> Trough -> Peak -> Trough)
      const alternatingSwings: { time: number; price: number; isPeak: boolean }[] = [];
      for (const s of swings) {
        if (alternatingSwings.length === 0) {
          alternatingSwings.push(s);
        } else {
          const last = alternatingSwings[alternatingSwings.length - 1];
          if (last.isPeak !== s.isPeak) {
            alternatingSwings.push(s);
          } else {
            // Keep the more extreme one
            if (s.isPeak) {
              if (s.price > last.price) {
                alternatingSwings[alternatingSwings.length - 1] = s;
              }
            } else {
              if (s.price < last.price) {
                alternatingSwings[alternatingSwings.length - 1] = s;
              }
            }
          }
        }
      }

      const newDrawings: Drawing[] = [];

      // Add a rectangle box around the pattern bounds
      newDrawings.push({
        id: `radar-pattern-box`,
        tool: 'rectangle',
        points: [
          { time: alignedStartTime, price: p.low },
          { time: alignedEndTime, price: p.high },
        ],
        color: color,
      });

      if (alternatingSwings.length >= 2) {
        const typeLower = p.name.toLowerCase();
        const isTriangle = typeLower.includes('triangle') || typeLower.includes('pennant');
        const isWedge = typeLower.includes('wedge');
        const isChannel = typeLower.includes('rectangle') || typeLower.includes('flag') || typeLower.includes('channel');

        const peaks = alternatingSwings.filter(s => s.isPeak);
        const troughs = alternatingSwings.filter(s => !s.isPeak);

        if ((isTriangle || isWedge || isChannel) && peaks.length >= 2 && troughs.length >= 2) {
          const firstPeak = peaks[0];
          const lastPeak = peaks[peaks.length - 1];
          const firstTrough = troughs[0];
          const lastTrough = troughs[troughs.length - 1];

          // Resistance trendline
          newDrawings.push({
            id: `radar-pattern-resistance`,
            tool: 'trendline',
            points: [
              { time: firstPeak.time, price: firstPeak.price },
              { time: lastPeak.time, price: lastPeak.price },
            ],
            color,
          });

          // Support trendline
          newDrawings.push({
            id: `radar-pattern-support`,
            tool: 'trendline',
            points: [
              { time: firstTrough.time, price: firstTrough.price },
              { time: lastTrough.time, price: lastTrough.price },
            ],
            color,
          });
        } else {
          // Draw alternating swings as trendlines
          for (let i = 0; i < alternatingSwings.length - 1; i++) {
            newDrawings.push({
              id: `radar-pattern-wave-${i}`,
              tool: 'trendline',
              points: [
                { time: alternatingSwings[i].time, price: alternatingSwings[i].price },
                { time: alternatingSwings[i + 1].time, price: alternatingSwings[i + 1].price },
              ],
              color,
            });
          }
        }
      }

      // Update the store
      const drawings = useChartUIStore.getState().drawings;
      const cleanDrawings = drawings.filter(d => !d.id.startsWith('radar-'));
      useChartUIStore.setState({ drawings: [...cleanDrawings, ...newDrawings] });
    };

    const drawStrategy = (target: RadarVizTarget) => {
      const s = target.strategy!;
      const color = BIAS_COLORS[s.bias] ?? BIAS_COLORS.NEUTRAL;
      const bullish = s.bias === 'BULLISH';

      const intervalMs = TIMEFRAME_MS[target.timeframe] ?? 600_000;
      const intervalSec = intervalMs / 1000;
      const alignedTime = Math.floor(s.time / intervalSec) * intervalSec;

      const marker: SeriesMarker<Time> = {
        time: alignedTime as Time,
        position: bullish ? 'belowBar' : 'aboveBar',
        color,
        shape: bullish ? 'arrowUp' : 'arrowDown',
        text: s.name,
        id: `radar-strategy-${alignedTime}`,
      };
      markers.setMarkers([marker]);

      // Horizontal reference line at the strategy's key level
      const level = s.level ?? s.price;
      if (Number.isFinite(level)) {
        priceLine = series.createPriceLine({
          price: level,
          color,
          lineWidth: 2,
          lineStyle: 2, // dashed
          axisLabelVisible: true,
          title: s.name,
        });
      }
    };

    const handleTimeRangeChange = () => {
      render();
    };

    render();

    chart.timeScale().subscribeVisibleTimeRangeChange(handleTimeRangeChange);
    const unsubRadar = useRadarStore.subscribe(() => render());

    let lastSym = useTradeStore.getState().selectedSymbol;
    let lastTf = useTradeStore.getState().activeTimeframe;
    const unsubTrade = useTradeStore.subscribe((s) => {
      if (s.selectedSymbol !== lastSym || s.activeTimeframe !== lastTf) {
        lastSym = s.selectedSymbol;
        lastTf = s.activeTimeframe;
        render();
      }
    });

    return () => {
      if (retryTimeout) clearTimeout(retryTimeout);
      chart.timeScale().unsubscribeVisibleTimeRangeChange(handleTimeRangeChange);
      unsubRadar();
      unsubTrade();
      clearLevel();
      clearRadarDrawings();
      try { markers.setMarkers([]); } catch { /* detached */ }
      try { markers.detach(); } catch { /* already detached */ }
    };
  }, [chartRef, candleSeriesRef, fibOverlayRef, chartData]);
}
