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

      // Highlight box coordinates
      const xEnd = chart.timeScale().timeToCoordinate(p.time as Time);
      const xStart = p.start_time ? chart.timeScale().timeToCoordinate(p.start_time as Time) : null;
      
      const yHigh = series.priceToCoordinate(p.high);
      const yLow = series.priceToCoordinate(p.low);
      if (xEnd === null || yHigh === null || yLow === null) return;

      const top = Math.min(yHigh, yLow) - 6;
      const height = Math.abs(yLow - yHigh) + 12;
      
      let left = xEnd - 9;
      let width = 18;
      
      if (xStart !== null && xStart < xEnd) {
        left = xStart - 9;
        width = (xEnd - xStart) + 18;
      }

      // Create an SVG element to draw structural elements
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('style', 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;overflow:visible;');
      boxLayer.appendChild(svg);

      // 1. Draw outer bounding rect
      const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
      rect.setAttribute('x', String(left));
      rect.setAttribute('y', String(top));
      rect.setAttribute('width', String(width));
      rect.setAttribute('height', String(height));
      rect.setAttribute('fill', `${color}06`);
      rect.setAttribute('stroke', color);
      rect.setAttribute('stroke-width', '1');
      rect.setAttribute('stroke-dasharray', '4,4');
      rect.setAttribute('rx', '4');
      rect.setAttribute('style', `filter: drop-shadow(0 0 4px ${color}20);`);
      svg.appendChild(rect);

      // Filter candles in this pattern range
      if (p.start_time) {
        const startT = p.start_time;
        const endT = p.time;
        const rangeCandles = chartData.filter(c => c.time >= startT && c.time <= endT);
        
        if (rangeCandles.length >= 3) {
          // Detect local swings (peaks and troughs)
          const swings: { x: number; y: number; isPeak: boolean }[] = [];
          const n = rangeCandles.length;
          
          // Use adaptive window based on range length
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
            
            const x = chart.timeScale().timeToCoordinate(rangeCandles[i].time as Time);
            if (x === null) continue;
            
            if (isPeak) {
              const y = series.priceToCoordinate(rangeCandles[i].high);
              if (y !== null) {
                swings.push({ x, y, isPeak: true });
              }
            } else if (isTrough) {
              const y = series.priceToCoordinate(rangeCandles[i].low);
              if (y !== null) {
                swings.push({ x, y, isPeak: false });
              }
            }
          }

          // Alternating swings (Peak -> Trough -> Peak -> Trough)
          const alternatingSwings: { x: number; y: number; isPeak: boolean }[] = [];
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
                  if (s.y < last.y) { // Y is inverted on screen
                    alternatingSwings[alternatingSwings.length - 1] = s;
                  }
                } else {
                  if (s.y > last.y) {
                    alternatingSwings[alternatingSwings.length - 1] = s;
                  }
                }
              }
            }
          }

          if (alternatingSwings.length >= 2) {
            const typeLower = p.name.toLowerCase();
            const isTriangle = typeLower.includes('triangle') || typeLower.includes('pennant');
            const isWedge = typeLower.includes('wedge');
            const isChannel = typeLower.includes('rectangle') || typeLower.includes('flag') || typeLower.includes('channel');

            const peaks = alternatingSwings.filter(s => s.isPeak);
            const troughs = alternatingSwings.filter(s => !s.isPeak);

            // 2. Draw converging / parallel trendlines + shaded area for structural chart patterns
            if ((isTriangle || isWedge || isChannel) && peaks.length >= 2 && troughs.length >= 2) {
              const firstPeak = peaks[0];
              const lastPeak = peaks[peaks.length - 1];
              const firstTrough = troughs[0];
              const lastTrough = troughs[troughs.length - 1];

              // Shaded interior polygon
              const polyPoints = `${firstPeak.x},${firstPeak.y} ${lastPeak.x},${lastPeak.y} ${lastTrough.x},${lastTrough.y} ${firstTrough.x},${firstTrough.y}`;
              const polygon = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
              polygon.setAttribute('points', polyPoints);
              polygon.setAttribute('fill', color);
              polygon.setAttribute('opacity', '0.08');
              svg.appendChild(polygon);

              // Resistance trendline
              const resLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
              resLine.setAttribute('x1', String(firstPeak.x));
              resLine.setAttribute('y1', String(firstPeak.y));
              resLine.setAttribute('x2', String(lastPeak.x));
              resLine.setAttribute('y2', String(lastPeak.y));
              resLine.setAttribute('stroke', color);
              resLine.setAttribute('stroke-width', '2.5');
              resLine.setAttribute('style', `filter: drop-shadow(0 0 3px ${color}60);`);
              svg.appendChild(resLine);

              // Support trendline
              const supLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
              supLine.setAttribute('x1', String(firstTrough.x));
              supLine.setAttribute('y1', String(firstTrough.y));
              supLine.setAttribute('x2', String(lastTrough.x));
              supLine.setAttribute('y2', String(lastTrough.y));
              supLine.setAttribute('stroke', color);
              supLine.setAttribute('stroke-width', '2.5');
              supLine.setAttribute('style', `filter: drop-shadow(0 0 3px ${color}60);`);
              svg.appendChild(supLine);
            } else {
              // 3. Draw standard zig-zag structural wave path connecting all alternating swings
              let pathD = '';
              alternatingSwings.forEach((s, idx) => {
                if (idx === 0) {
                  pathD += `M ${s.x} ${s.y}`;
                } else {
                  pathD += ` L ${s.x} ${s.y}`;
                }
              });

              const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
              path.setAttribute('d', pathD);
              path.setAttribute('fill', 'none');
              path.setAttribute('stroke', color);
              path.setAttribute('stroke-width', '2');
              path.setAttribute('stroke-dasharray', '3,3');
              path.setAttribute('opacity', '0.8');
              svg.appendChild(path);

              // Render vertex nodes
              alternatingSwings.forEach((s) => {
                const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
                circle.setAttribute('cx', String(s.x));
                circle.setAttribute('cy', String(s.y));
                circle.setAttribute('r', '3.5');
                circle.setAttribute('fill', color);
                circle.setAttribute('stroke', '#131922');
                circle.setAttribute('stroke-width', '1.5');
                svg.appendChild(circle);
              });
            }
          }
        }
      }
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
