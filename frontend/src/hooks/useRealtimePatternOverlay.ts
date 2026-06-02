// hooks/useRealtimePatternOverlay.ts — On-chart visualization for real-time patterns detected by the Quant-RAG Agent.
//
// Renders the detected patterns from the Quant-RAG Agent on the chart:
//   • Pattern Marker → a candle marker (arrow/flag) at the bar where the pattern completed/formed.
//   • Trendlines     → connecting lines between the pattern pivot points (e.g. X-A-B-C-D).
//   • Bounding Box   → a dashed rectangle outlining the bounds of the pattern.
//
// Subscribes to the useTradeStore's latestInsight field. If a pattern is present, it constructs
// drawing primitives and inserts them into useChartUIStore so they render natively.

import { useEffect } from 'react';
import {
  createSeriesMarkers,
  type Time,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
} from 'lightweight-charts';
import { TIMEFRAME_MS, type ChartRefs, type ChartCandle } from '../utils/chartTypes';
import { useTradeStore } from '../store/useTradeStore';
import { useChartUIStore, type Drawing } from '../store/useChartUIStore';

const BIAS_COLORS: Record<string, string> = {
  BULLISH: '#10b981', // Emerald
  BEARISH: '#f43f5e', // Rose
  NEUTRAL: '#06b6d4', // Cyan
};

export function useRealtimePatternOverlay(refs: ChartRefs, chartData: ChartCandle[]) {
  const { chartRef, candleSeriesRef } = refs;

  useEffect(() => {
    const chart = chartRef.current;
    const series = candleSeriesRef.current;
    if (!chart || !series) return;

    // Dedicated markers plugin instance for real-time patterns
    const markers: ISeriesMarkersPluginApi<Time> = createSeriesMarkers(series, []);

    const clearPatternDrawings = () => {
      const drawings = useChartUIStore.getState().drawings;
      const cleanDrawings = drawings.filter(d => !d.id.startsWith('realtime-pattern-'));
      if (drawings.length !== cleanDrawings.length) {
        useChartUIStore.setState({ drawings: cleanDrawings });
      }
    };

    const render = () => {
      markers.setMarkers([]);
      
      const { selectedSymbol, activeTimeframe, latestInsight } = useTradeStore.getState();

      // Only draw when the active timeframe is 10m (Quant-RAG patterns are 10m exclusive)
      // and active symbol matches the insight symbol.
      if (
        !latestInsight ||
        !latestInsight.pattern ||
        activeTimeframe !== '10m' ||
        latestInsight.symbol.toUpperCase() !== selectedSymbol.toUpperCase()
      ) {
        clearPatternDrawings();
        return;
      }

      const pattern = latestInsight.pattern;
      const bias = (pattern.implied_bias || 'neutral').toUpperCase();
      const color = bias.includes('BULLISH')
        ? BIAS_COLORS.BULLISH
        : bias.includes('BEARISH')
        ? BIAS_COLORS.BEARISH
        : BIAS_COLORS.NEUTRAL;

      const isBullish = bias.includes('BULLISH');
      const intervalMs = TIMEFRAME_MS['10m'] ?? 600_000;
      const intervalSec = intervalMs / 1000;
      const alignTime = (t: number) => Math.floor(t / intervalSec) * intervalSec;

      // Aligned end time
      const alignedEndTime = alignTime(pattern.end_time || pattern.points[pattern.points.length - 1]?.time || Date.now() / 1000);

      // Create a marker on the series at the pattern end bar
      const marker: SeriesMarker<Time> = {
        time: alignedEndTime as Time,
        position: isBullish ? 'belowBar' : 'aboveBar',
        color,
        shape: isBullish ? 'arrowUp' : 'arrowDown',
        text: `${pattern.detected_pattern} (${pattern.status})`,
        id: `realtime-pattern-marker-${alignedEndTime}`,
      };
      markers.setMarkers([marker]);

      const newDrawings: Drawing[] = [];

      // ── Bounding Box ──
      if (pattern.start_time && (pattern.end_time || pattern.points[pattern.points.length - 1]?.time)) {
        const start = alignTime(pattern.start_time);
        const end = alignTime(pattern.end_time || pattern.points[pattern.points.length - 1].time);
        newDrawings.push({
          id: `realtime-pattern-box`,
          tool: 'rectangle',
          points: [
            { time: start, price: pattern.low },
            { time: end, price: pattern.high },
          ],
          color,
        });
      }

      // ── Trendlines connecting key swing points ──
      if (Array.isArray(pattern.points) && pattern.points.length >= 2) {
        for (let i = 0; i < pattern.points.length - 1; i++) {
          const pt1 = pattern.points[i];
          const pt2 = pattern.points[i + 1];
          if (pt1 && pt2) {
            newDrawings.push({
              id: `realtime-pattern-line-${i}`,
              tool: 'trendline',
              points: [
                { time: alignTime(pt1.time), price: pt1.price },
                { time: alignTime(pt2.time), price: pt2.price },
              ],
              color,
            });
          }
        }
      }

      // Update the drawings in ChartUIStore
      const drawings = useChartUIStore.getState().drawings;
      const cleanDrawings = drawings.filter(d => !d.id.startsWith('realtime-pattern-'));
      useChartUIStore.setState({ drawings: [...cleanDrawings, ...newDrawings] });
    };

    render();

    // Subscribe to time scale change, symbol change, timeframe change, and insights
    chart.timeScale().subscribeVisibleTimeRangeChange(render);

    let lastInsight = useTradeStore.getState().latestInsight;
    let lastSym = useTradeStore.getState().selectedSymbol;
    let lastTf = useTradeStore.getState().activeTimeframe;

    const unsubTrade = useTradeStore.subscribe((s) => {
      if (
        s.selectedSymbol !== lastSym ||
        s.activeTimeframe !== lastTf ||
        s.latestInsight !== lastInsight
      ) {
        lastSym = s.selectedSymbol;
        lastTf = s.activeTimeframe;
        lastInsight = s.latestInsight;
        render();
      }
    });

    return () => {
      chart.timeScale().unsubscribeVisibleTimeRangeChange(render);
      unsubTrade();
      clearPatternDrawings();
      try { markers.setMarkers([]); } catch { /* detached */ }
      try { markers.detach(); } catch { /* already detached */ }
    };
  }, [chartRef, candleSeriesRef, chartData]);
}
