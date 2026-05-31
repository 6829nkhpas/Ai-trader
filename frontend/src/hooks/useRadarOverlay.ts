// hooks/useRadarOverlay.ts — On-chart visualization for Quant Radar (FEAT-037).
//
// Renders the user-selected radar detection on the master chart:
//   • Pattern  → a candle marker (arrow/flag) at the bar where it formed,
//                plus a translucent highlight box around that candle drawn
//                on the fib overlay div (ref-based, no React re-renders).
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
import type { ChartRefs, ChartCandle } from '../utils/chartTypes';
import { useRadarStore, type RadarVizTarget } from '../store/useRadarStore';
import { useTradeStore } from '../store/useTradeStore';
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

    // A dedicated child div inside the fib overlay container for the
    // pattern highlight box, so we don't fight the fib zone painter.
    const boxLayer = document.createElement('div');
    boxLayer.style.cssText = 'position:absolute;inset:0;pointer-events:none;';
    fibOverlayRef.current?.appendChild(boxLayer);

    const clearLevel = () => {
      if (priceLine) {
        try { series.removePriceLine(priceLine); } catch { /* detached */ }
        priceLine = null;
      }
    };

    const render = () => {
      const { vizTarget, vizEnabled } = useRadarStore.getState();
      boxLayer.innerHTML = '';
      markers.setMarkers([]);
      clearLevel();

      if (!vizEnabled || !vizTarget) return;

      // Only draw when the chart's active symbol + timeframe match the
      // detection. Clicking a radar item routes both via the trade store,
      // but a manual timeframe/symbol change must not leave stale markers
      // on bars they don't belong to.
      const { activeTimeframe, selectedSymbol } = useTradeStore.getState();
      if (vizTarget.timeframe !== activeTimeframe) return;
      if (vizTarget.symbol.toUpperCase() !== selectedSymbol.toUpperCase()) return;

      if (vizTarget.kind === 'pattern' && vizTarget.pattern) {
        drawPattern(vizTarget);
      } else if (vizTarget.kind === 'strategy' && vizTarget.strategy) {
        drawStrategy(vizTarget);
      }
    };

    const drawPattern = (target: RadarVizTarget) => {
      const p = target.pattern!;
      const color = BIAS_COLORS[p.bias] ?? BIAS_COLORS.NEUTRAL;
      const bullish = p.bias === 'BULLISH';

      const marker: SeriesMarker<Time> = {
        time: p.time as Time,
        position: bullish ? 'belowBar' : 'aboveBar',
        color,
        shape: bullish ? 'arrowUp' : 'arrowDown',
        text: p.name,
        id: `radar-pattern-${p.time}`,
      };
      markers.setMarkers([marker]);

      // Highlight box around the candle's high/low at its timestamp.
      const x = chart.timeScale().timeToCoordinate(p.time as Time);
      const yHigh = series.priceToCoordinate(p.high);
      const yLow = series.priceToCoordinate(p.low);
      if (x === null || yHigh === null || yLow === null) return;

      const top = Math.min(yHigh, yLow) - 6;
      const height = Math.abs(yLow - yHigh) + 12;
      const half = 9; // half-width of the highlight box in px

      const box = document.createElement('div');
      box.style.cssText =
        `position:absolute;top:${top}px;left:${x - half}px;width:${half * 2}px;height:${height}px;` +
        `border:1.5px solid ${color};border-radius:3px;background:${color}1a;` +
        `box-shadow:0 0 10px ${color}55;pointer-events:none;`;
      boxLayer.appendChild(box);
    };

    const drawStrategy = (target: RadarVizTarget) => {
      const s = target.strategy!;
      const color = BIAS_COLORS[s.bias] ?? BIAS_COLORS.NEUTRAL;
      const bullish = s.bias === 'BULLISH';

      const marker: SeriesMarker<Time> = {
        time: s.time as Time,
        position: bullish ? 'belowBar' : 'aboveBar',
        color,
        shape: bullish ? 'arrowUp' : 'arrowDown',
        text: s.name,
        id: `radar-strategy-${s.time}`,
      };
      markers.setMarkers([marker]);

      // Horizontal reference line at the strategy's key level.
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

    render();

    // Redraw on pan/zoom (coordinates change) and on radar store changes.
    chart.timeScale().subscribeVisibleTimeRangeChange(render);
    const unsubRadar = useRadarStore.subscribe(render);

    // The trade store updates on every tick — only redraw when the active
    // symbol or timeframe actually changes (not on every price update).
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
      chart.timeScale().unsubscribeVisibleTimeRangeChange(render);
      unsubRadar();
      unsubTrade();
      clearLevel();
      try { markers.setMarkers([]); } catch { /* detached */ }
      try { markers.detach(); } catch { /* already detached */ }
      boxLayer.remove();
    };
  }, [chartRef, candleSeriesRef, fibOverlayRef, chartData]);
}
