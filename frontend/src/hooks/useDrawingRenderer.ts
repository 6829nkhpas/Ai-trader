import { useEffect } from 'react';
import type { Time } from 'lightweight-charts';
import { LineSeries } from 'lightweight-charts';
import type { ChartRefs, ChartCandle } from '../utils/chartTypes';
import { useChartUIStore } from '../store/useChartUIStore';

export function useDrawingRenderer(
  refs: ChartRefs,
  chartData: ChartCandle[]
) {
  const { chartRef, candleSeriesRef, drawingSeriesRef } = refs;
  const drawings = useChartUIStore((s) => s.drawings);
  const drawingsVisible = useChartUIStore((s) => s.drawingsVisible);

  useEffect(() => {
    const chart = chartRef.current;
    const mainSeries = candleSeriesRef.current;
    if (!chart) return;

    // Remove previous drawing series from chart
    for (const series of drawingSeriesRef.current) {
      try {
        chart.removeSeries(series);
      } catch {
        // series may already be removed if chart was re-created
      }
    }
    drawingSeriesRef.current = [];

    if (!drawingsVisible) return;

    const TOOL_COLORS: Record<string, string> = {
      'trendline': '#2962FF',
      'ray': '#2962FF',
      'info-line': '#00BCD4',
      'extended-line': '#2962FF',
      'trend-angle': '#FF9800',
      'horizontal-line': '#FF6D00',
      'horizontal-ray': '#FF6D00',
      'vertical-line': '#AB47BC',
      'cross-line': '#AB47BC',
      'parallel-channel': '#26A69A',
      'regression-trend': '#EC407A',
      'flat-top-bottom': '#26A69A',
      'disjoint-channel': '#78909C',
      'fib-retracement': '#FFD600',
      'trend-fib': '#FFD600',
      'long-position': '#22c55e',
      'short-position': '#ef4444',
      'price-range': '#00BCD4',
    };

    const TOOL_LINE_STYLES: Record<string, number> = {
      'trendline': 0,
      'ray': 0,
      'info-line': 0,
      'extended-line': 0,
      'trend-angle': 0,
      'horizontal-line': 2,
      'horizontal-ray': 2,
      'vertical-line': 2,
      'cross-line': 2,
      'parallel-channel': 0,
      'regression-trend': 2,
      'flat-top-bottom': 2,
      'disjoint-channel': 0,
    };

    const intervalSec = chartData.length >= 2
      ? chartData[1].time - chartData[0].time
      : 600;

    const createLine = (
      data: { time: Time; value: number }[],
      color: string,
      lineWidth: 1 | 2 | 3 | 4 = 2,
      lineStyle: number = 0,
      title?: string,
    ) => {
      const line = chart.addSeries(LineSeries, {
        color,
        lineWidth,
        lineStyle,
        crosshairMarkerVisible: true,
        crosshairMarkerRadius: 6,
        crosshairMarkerBackgroundColor: '#FFFFFF',
        crosshairMarkerBorderColor: color,
        priceLineVisible: false,
        lastValueVisible: false,
        ...(title ? { title } : {}),
      });
      line.setData(data);
      drawingSeriesRef.current.push(line);
      return line;
    };

    for (const drawing of drawings) {
      if (drawing.points.length < 2) continue;
      const color = TOOL_COLORS[drawing.tool] || '#2962FF';
      const lineStyle = TOOL_LINE_STYLES[drawing.tool] ?? 0;
      const p1 = drawing.points[0];
      const p2 = drawing.points[1];
      const sorted = [p1, p2].sort((a, b) => a.time - b.time);

      switch (drawing.tool) {
        case 'trendline':
        default: {
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 2, lineStyle,
          );
          break;
        }

        case 'ray': {
          const slope = (p2.price - p1.price) / ((p2.time - p1.time) || 1);
          const farTime = sorted[1].time + intervalSec * 200;
          const farPrice = p2.price + slope * (farTime - p2.time);
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
              { time: farTime as Time, value: +farPrice.toFixed(2) },
            ],
            color, 2, 0,
          );
          break;
        }

        case 'info-line': {
          const priceDiff = p2.price - p1.price;
          const pctChange = ((priceDiff / p1.price) * 100).toFixed(2);
          const timeDiffSec = Math.abs(p2.time - p1.time);
          const bars = Math.round(timeDiffSec / intervalSec);
          const hours = Math.floor(timeDiffSec / 3600);
          const mins = Math.floor((timeDiffSec % 3600) / 60);
          const duration = hours > 24
            ? `${Math.floor(hours / 24)}d ${hours % 24}h ${mins}m`
            : `${hours}h ${mins}m`;
          const angle = Math.atan2(priceDiff, bars || 1) * (180 / Math.PI);
          const title = `${priceDiff >= 0 ? '+' : ''}${priceDiff.toFixed(2)} (${pctChange}%) · ${bars} bars (${duration}) · ${angle.toFixed(1)}°`;

          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 2, 0, title,
          );
          break;
        }

        case 'extended-line': {
          const exSlope = (p2.price - p1.price) / ((p2.time - p1.time) || 1);
          const leftTime = sorted[0].time - intervalSec * 200;
          const rightTime = sorted[1].time + intervalSec * 200;
          const leftPrice = sorted[0].price + exSlope * (leftTime - sorted[0].time);
          const rightPrice = sorted[1].price + exSlope * (rightTime - sorted[1].time);
          createLine(
            [
              { time: leftTime as Time, value: +leftPrice.toFixed(2) },
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
              { time: rightTime as Time, value: +rightPrice.toFixed(2) },
            ],
            color, 2, 0,
          );
          break;
        }

        case 'trend-angle': {
          const taBars = Math.round(Math.abs(p2.time - p1.time) / intervalSec);
          const taDiff = p2.price - p1.price;
          const taAngle = Math.atan2(taDiff, taBars || 1) * (180 / Math.PI);
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 2, 0, `∠ ${taAngle.toFixed(1)}°`,
          );
          break;
        }

        case 'horizontal-line': {
          if (mainSeries) {
            mainSeries.createPriceLine({
              price: p1.price,
              color,
              lineWidth: 1,
              lineStyle: 2,
              axisLabelVisible: true,
            });
          }
          break;
        }

        case 'horizontal-ray': {
          const hrFarTime = sorted[0].time + intervalSec * 500;
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: hrFarTime as Time, value: sorted[0].price },
            ],
            color, 1, 2,
          );
          break;
        }

        case 'vertical-line': {
          const vHigh = p1.price * 1.15;
          const vLow = p1.price * 0.85;
          createLine(
            [
              { time: sorted[0].time as Time, value: +vLow.toFixed(2) },
              { time: sorted[0].time as Time, value: +vHigh.toFixed(2) },
            ],
            color, 1, 2,
          );
          break;
        }

        case 'cross-line': {
          const clLeftTime = sorted[0].time - intervalSec * 100;
          const clRightTime = sorted[0].time + intervalSec * 100;
          createLine(
            [
              { time: clLeftTime as Time, value: p1.price },
              { time: clRightTime as Time, value: p1.price },
            ],
            color, 1, 2,
          );
          const clHigh = p1.price * 1.10;
          const clLow = p1.price * 0.90;
          createLine(
            [
              { time: sorted[0].time as Time, value: +clLow.toFixed(2) },
              { time: sorted[0].time as Time, value: +clHigh.toFixed(2) },
            ],
            color, 1, 2,
          );
          break;
        }

        case 'parallel-channel':
        case 'flat-top-bottom': {
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 2, 0,
          );
          const offset = Math.abs(sorted[1].price - sorted[0].price) * 0.5;
          const direction = sorted[1].price > sorted[0].price ? -1 : 1;
          createLine(
            [
              { time: sorted[0].time as Time, value: +(sorted[0].price + offset * direction).toFixed(2) },
              { time: sorted[1].time as Time, value: +(sorted[1].price + offset * direction).toFixed(2) },
            ],
            color, 1, 2,
          );
          break;
        }

        case 'regression-trend': {
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 2, 0,
          );
          const rtRange = Math.abs(sorted[1].price - sorted[0].price) * 0.3;
          createLine(
            [
              { time: sorted[0].time as Time, value: +(sorted[0].price + rtRange).toFixed(2) },
              { time: sorted[1].time as Time, value: +(sorted[1].price + rtRange).toFixed(2) },
            ],
            color, 1, 2,
          );
          createLine(
            [
              { time: sorted[0].time as Time, value: +(sorted[0].price - rtRange).toFixed(2) },
              { time: sorted[1].time as Time, value: +(sorted[1].price - rtRange).toFixed(2) },
            ],
            color, 1, 2,
          );
          break;
        }

        case 'disjoint-channel': {
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 2, 0,
          );
          const dcOffset = Math.abs(sorted[1].price - sorted[0].price) * 0.4;
          createLine(
            [
              { time: sorted[0].time as Time, value: +(sorted[0].price - dcOffset * 0.5).toFixed(2) },
              { time: sorted[1].time as Time, value: +(sorted[1].price - dcOffset * 1.5).toFixed(2) },
            ],
            color, 1, 2,
          );
          break;
        }

        case 'fib-retracement':
        case 'trend-fib': {
          const fibLevels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
          const fibRange = sorted[1].price - sorted[0].price;
          const fibAlpha = ['FF', 'CC', 'AA', '99', 'AA', 'CC', 'FF'];
          for (let i = 0; i < fibLevels.length; i++) {
            const level = fibLevels[i];
            const price = sorted[0].price + fibRange * level;
            createLine(
              [
                { time: sorted[0].time as Time, value: +price.toFixed(2) },
                { time: sorted[1].time as Time, value: +price.toFixed(2) },
              ],
              color, 1, 2,
              `${(level * 100).toFixed(1)}% — ${price.toFixed(2)}`,
            );
          }
          break;
        }

        case 'fib-extension': {
          const extLevels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.272, 1.618, 2, 2.618];
          const extRange = sorted[1].price - sorted[0].price;
          for (const level of extLevels) {
            const price = sorted[0].price + extRange * level;
            createLine(
              [
                { time: sorted[0].time as Time, value: +price.toFixed(2) },
                { time: (sorted[1].time + intervalSec * 50) as Time, value: +price.toFixed(2) },
              ],
              color, level > 1 ? 1 : 1, level > 1 ? 0 : 2,
              `${(level * 100).toFixed(1)}%`,
            );
          }
          break;
        }

        case 'fib-channel': {
          const chFibLevels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
          const chRange = Math.abs(sorted[1].price - sorted[0].price) * 0.5;
          for (const level of chFibLevels) {
            const offset = chRange * level;
            createLine(
              [
                { time: sorted[0].time as Time, value: +(sorted[0].price + offset).toFixed(2) },
                { time: sorted[1].time as Time, value: +(sorted[1].price + offset).toFixed(2) },
              ],
              color, level === 0 || level === 1 ? 2 : 1, level === 0 || level === 1 ? 0 : 2,
              level === 0 ? '' : `${(level * 100).toFixed(1)}%`,
            );
          }
          break;
        }

        case 'fib-time-zone':
        case 'fib-time-trend': {
          const fibSequence = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55];
          let cumBars = 0;
          const vHigh = Math.max(sorted[0].price, sorted[1].price) * 1.05;
          const vLow = Math.min(sorted[0].price, sorted[1].price) * 0.95;
          for (const n of fibSequence) {
            cumBars += n;
            const t = sorted[0].time + intervalSec * cumBars;
            if (t > sorted[1].time + intervalSec * 300) break;
            createLine(
              [
                { time: t as Time, value: +vLow.toFixed(2) },
                { time: t as Time, value: +vHigh.toFixed(2) },
              ],
              color, 1, 2,
              `${cumBars}`,
            );
          }
          break;
        }

        case 'fib-speed-fan': {
          const fanLevels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
          const fanRange = sorted[1].price - sorted[0].price;
          for (const level of fanLevels) {
            const targetPrice = sorted[0].price + fanRange * level;
            const farTime = sorted[1].time + intervalSec * 100;
            const farSlope = (targetPrice - sorted[0].price) / ((sorted[1].time - sorted[0].time) || 1);
            const farPrice = targetPrice + farSlope * (farTime - sorted[1].time);
            createLine(
              [
                { time: sorted[0].time as Time, value: sorted[0].price },
                { time: sorted[1].time as Time, value: +targetPrice.toFixed(2) },
                { time: farTime as Time, value: +farPrice.toFixed(2) },
              ],
              color, level === 0.5 ? 2 : 1, level === 0.5 ? 0 : 2,
              `${(level * 100).toFixed(1)}%`,
            );
          }
          break;
        }

        case 'fib-circles': {
          const circLevels = [0.236, 0.382, 0.5, 0.618, 0.786, 1];
          const circRange = Math.abs(sorted[1].price - sorted[0].price);
          const midPrice = (sorted[0].price + sorted[1].price) / 2;
          const midTime = Math.round((sorted[0].time + sorted[1].time) / 2);
          for (const level of circLevels) {
            const radius = circRange * level;
            const tSpread = Math.round((sorted[1].time - sorted[0].time) * level / 2);
            createLine(
              [
                { time: (midTime - tSpread) as Time, value: +midPrice.toFixed(2) },
                { time: midTime as Time, value: +(midPrice + radius / 2).toFixed(2) },
                { time: (midTime + tSpread) as Time, value: +midPrice.toFixed(2) },
              ],
              color, 1, 2,
              `${(level * 100).toFixed(1)}%`,
            );
            createLine(
              [
                { time: (midTime - tSpread) as Time, value: +midPrice.toFixed(2) },
                { time: midTime as Time, value: +(midPrice - radius / 2).toFixed(2) },
                { time: (midTime + tSpread) as Time, value: +midPrice.toFixed(2) },
              ],
              color, 1, 2,
            );
          }
          break;
        }

        case 'fib-spiral': {
          const spiralLevels = [1, 1.618, 2.618, 4.236, 6.854];
          const spRange = Math.abs(sorted[1].price - sorted[0].price);
          const spDir = sorted[1].price > sorted[0].price ? 1 : -1;
          for (const mult of spiralLevels) {
            const targetPrice = sorted[0].price + spRange * mult * spDir;
            const targetTime = sorted[0].time + (sorted[1].time - sorted[0].time) * mult;
            createLine(
              [
                { time: sorted[0].time as Time, value: sorted[0].price },
                { time: targetTime as Time, value: +targetPrice.toFixed(2) },
              ],
              color, 1, 2,
              `${mult.toFixed(3)}`,
            );
          }
          break;
        }

        case 'fib-arcs': {
          const arcLevels = [0.236, 0.382, 0.5, 0.618, 0.786];
          const arcRange = Math.abs(sorted[1].price - sorted[0].price);
          const arcTimeDiff = sorted[1].time - sorted[0].time;
          for (const level of arcLevels) {
            const radius = arcRange * level;
            const tR = Math.round(arcTimeDiff * level);
            const pts = [];
            for (let i = 0; i <= 8; i++) {
              const frac = i / 8;
              const t = sorted[1].time - tR + Math.round(tR * 2 * frac);
              const pOffset = radius * Math.sqrt(1 - Math.pow(frac * 2 - 1, 2));
              pts.push({ time: t as Time, value: +(sorted[1].price + pOffset).toFixed(2) });
            }
            createLine(pts, color, 1, 2, `${(level * 100).toFixed(1)}%`);
          }
          break;
        }

        case 'fib-wedge': {
          const wLevels = [0.236, 0.382, 0.5, 0.618, 0.786];
          const wRange = sorted[1].price - sorted[0].price;
          const convergenceTime = sorted[1].time + (sorted[1].time - sorted[0].time);
          const convergencePrice = (sorted[0].price + sorted[1].price) / 2;
          for (const level of wLevels) {
            const startPrice = sorted[0].price + wRange * level;
            createLine(
              [
                { time: sorted[0].time as Time, value: +startPrice.toFixed(2) },
                { time: convergenceTime as Time, value: +convergencePrice.toFixed(2) },
              ],
              color, 1, 2,
              `${(level * 100).toFixed(1)}%`,
            );
          }
          break;
        }

        case 'pitchfan': {
          const pfLevels = [0.25, 0.382, 0.5, 0.618, 0.75, 1];
          const pfRange = sorted[1].price - sorted[0].price;
          const pfTimeDiff = sorted[1].time - sorted[0].time;
          for (const level of pfLevels) {
            const targetPrice = sorted[0].price + pfRange * level;
            const farTime = sorted[1].time + pfTimeDiff;
            const slope = (targetPrice - sorted[0].price) / (pfTimeDiff || 1);
            const farPrice = targetPrice + slope * pfTimeDiff;
            createLine(
              [
                { time: sorted[0].time as Time, value: sorted[0].price },
                { time: sorted[1].time as Time, value: +targetPrice.toFixed(2) },
                { time: farTime as Time, value: +farPrice.toFixed(2) },
              ],
              color, level === 0.5 ? 2 : 1, level === 0.5 ? 0 : 2,
            );
          }
          break;
        }

        case 'gann-box':
        case 'gann-square-fixed':
        case 'gann-square': {
          const gLevels = [0, 0.25, 0.5, 0.75, 1];
          const gPriceRange = sorted[1].price - sorted[0].price;
          const gTimeDiff = sorted[1].time - sorted[0].time;
          for (const level of gLevels) {
            const price = sorted[0].price + gPriceRange * level;
            createLine(
              [
                { time: sorted[0].time as Time, value: +price.toFixed(2) },
                { time: sorted[1].time as Time, value: +price.toFixed(2) },
              ],
              color, level === 0 || level === 1 ? 2 : 1,
              level === 0 || level === 1 ? 0 : 2,
              `${(level * 100).toFixed(0)}%`,
            );
          }
          const vPriceHigh = Math.max(sorted[0].price, sorted[1].price);
          const vPriceLow = Math.min(sorted[0].price, sorted[1].price);
          for (const level of gLevels) {
            if (level === 0 || level === 1) continue;
            const t = sorted[0].time + Math.round(gTimeDiff * level);
            createLine(
              [
                { time: t as Time, value: +vPriceLow.toFixed(2) },
                { time: t as Time, value: +vPriceHigh.toFixed(2) },
              ],
              color, 1, 2,
            );
          }
          createLine(
            [
              { time: sorted[0].time as Time, value: sorted[0].price },
              { time: sorted[1].time as Time, value: sorted[1].price },
            ],
            color, 1, 2,
          );
          break;
        }

        case 'gann-fan': {
          const gannMultipliers = [0.125, 0.25, 0.333, 0.5, 1, 2, 3, 4, 8];
          const gannLabels = ['1×8', '1×4', '1×3', '1×2', '1×1', '2×1', '3×1', '4×1', '8×1'];
          const gfTimeDiff = sorted[1].time - sorted[0].time;
          const gfPricePerBar = (sorted[1].price - sorted[0].price) / (gfTimeDiff / intervalSec || 1);
          for (let i = 0; i < gannMultipliers.length; i++) {
            const mult = gannMultipliers[i];
            const farTime = sorted[0].time + gfTimeDiff * 2;
            const barsToFar = (farTime - sorted[0].time) / intervalSec;
            const farPrice = sorted[0].price + gfPricePerBar * mult * barsToFar;
            createLine(
              [
                { time: sorted[0].time as Time, value: sorted[0].price },
                { time: farTime as Time, value: +farPrice.toFixed(2) },
              ],
              color, mult === 1 ? 2 : 1, mult === 1 ? 0 : 2,
              gannLabels[i],
            );
          }
          break;
        }
      }
    }
  }, [drawings, drawingsVisible, chartData, chartRef, candleSeriesRef, drawingSeriesRef]);
}
