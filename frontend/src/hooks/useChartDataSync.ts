import { useEffect, useRef } from 'react';
import type { Time } from 'lightweight-charts';
import type { PredictiveSignal } from '../store/useTradeStore';
import type { ChartCandle, VolumeBar, EmaPoint, ChartRefs, Timeframe } from '../utils/chartTypes';
import { TIMEFRAME_MS } from '../utils/chartTypes';

export function useChartDataSync(
  refs: ChartRefs,
  chartData: ChartCandle[],
  volumeData: VolumeBar[],
  ema9Data: EmaPoint[],
  ema21Data: EmaPoint[],
  effectiveTimeframe: Timeframe,
  activeSymbol: string,
  predictiveSignals: PredictiveSignal[],
  isExpanded: boolean = false
) {
  const { chartRef, candleSeriesRef, volumeSeriesRef, ema9SeriesRef, ema21SeriesRef, ghostLineRef, chartContainerRef } = refs;

  const lastPaintedCandleCountRef = useRef<number>(0);
  const lastPaintedTimeframeRef = useRef<string>('');
  const lastPaintedSymbolRef = useRef<string>('');

  // ── Clear chart immediately when symbol changes (before new data arrives) ─
  // Without this, the old symbol's candles stay visible during the async
  // historical fetch, making it look like the chart didn't respond to the click.
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current) return;
    const prevSymbol = lastPaintedSymbolRef.current;
    if (prevSymbol !== '' && prevSymbol !== activeSymbol) {
      candleSeriesRef.current.setData([]);
      volumeSeriesRef.current.setData([]);
      if (ema9SeriesRef.current) ema9SeriesRef.current.setData([]);
      if (ema21SeriesRef.current) ema21SeriesRef.current.setData([]);
      if (ghostLineRef.current) ghostLineRef.current.setData([]);
      // Reset the painted-count so the next data arrival triggers a full setData
      lastPaintedCandleCountRef.current = 0;
      lastPaintedSymbolRef.current = activeSymbol;
    }
  }, [activeSymbol, candleSeriesRef, volumeSeriesRef, ema9SeriesRef, ema21SeriesRef, ghostLineRef]);

  // ── Smart data sync: setData on full reset, update() for last candle ─
  useEffect(() => {
    if (!candleSeriesRef.current || !volumeSeriesRef.current) return;

    const prevTimeframe = lastPaintedTimeframeRef.current;
    const prevSymbol = lastPaintedSymbolRef.current;
    const prevCount = lastPaintedCandleCountRef.current;

    const timeframeChanged = prevTimeframe !== effectiveTimeframe;
    const symbolChanged = prevSymbol !== activeSymbol;

    // ── Empty data: chart is loading or had a failed fetch ──────────────
    // Always clear the canvas and update refs so the next data arrival
    // triggers a full setData() correctly (fixes stuck lastPaintedTimeframeRef).
    if (chartData.length === 0) {
      if (timeframeChanged || symbolChanged) {
        candleSeriesRef.current.setData([]);
        volumeSeriesRef.current.setData([]);
        if (ema9SeriesRef.current) ema9SeriesRef.current.setData([]);
        if (ema21SeriesRef.current) ema21SeriesRef.current.setData([]);
        if (ghostLineRef.current) ghostLineRef.current.setData([]);
        lastPaintedTimeframeRef.current = effectiveTimeframe;
        lastPaintedSymbolRef.current = activeSymbol;
        lastPaintedCandleCountRef.current = 0;
      }
      return;
    }

    const newCandleArrived = chartData.length !== prevCount;

    if (timeframeChanged || symbolChanged || newCandleArrived) {
      // ── DIAGNOSTIC TRACER — Final Mile (chartData → setData boundary) ──
      // This is the very last gate before lightweight-charts. If Rust and
      // React Parse logs both look healthy but THIS shows an integrity
      // failure or zero items, the breakage is in the aggregation /
      // merge layer (mergedCandles → aggregateCandles → chartData).
      console.log(
        `🎨 [CHART RENDER] Calling setData with ${chartData.length} items ` +
        `(symbol=${activeSymbol}, tf=${effectiveTimeframe}).`
      );
      if (chartData.length > 0) {
        const isValid = chartData.every(
          (c) =>
            c.time !== undefined &&
            c.time !== null &&
            !Number.isNaN(c.open) &&
            !Number.isNaN(c.high) &&
            !Number.isNaN(c.low) &&
            !Number.isNaN(c.close)
        );
        console.log(`🎨 [CHART RENDER] Data Integrity Check Passed? : ${isValid}`);
        console.log("🎨 [CHART RENDER] Sample First:", JSON.stringify(chartData[0]));
        console.log(
          "🎨 [CHART RENDER] Sample Last :",
          JSON.stringify(chartData[chartData.length - 1])
        );
        if (!isValid) {
          const bad = chartData.find(
            (c) =>
              c.time === undefined ||
              c.time === null ||
              Number.isNaN(c.open) ||
              Number.isNaN(c.high) ||
              Number.isNaN(c.low) ||
              Number.isNaN(c.close)
          );
          console.error("🎨 [CHART RENDER ERROR] Malformed candle detected!", bad);
        }
      }

      candleSeriesRef.current.setData(
        chartData as Array<{ time: Time; open: number; high: number; low: number; close: number }>
      );
      volumeSeriesRef.current.setData(
        volumeData as Array<{ time: Time; value: number; color: string }>
      );
      if (ema9SeriesRef.current) {
        ema9SeriesRef.current.setData(ema9Data as Array<{ time: Time; value: number }>);
      }
      if (ema21SeriesRef.current) {
        ema21SeriesRef.current.setData(ema21Data as Array<{ time: Time; value: number }>);
      }

      lastPaintedTimeframeRef.current = effectiveTimeframe;
      lastPaintedSymbolRef.current = activeSymbol;
      lastPaintedCandleCountRef.current = chartData.length;

      if (timeframeChanged || symbolChanged || prevCount === 0) {
        // Traditional chart viewport: show the latest ~100 candles with the
        // newest candle on the right edge + rightOffset breathing room.
        // This matches TradingView / Zerodha Kite / any professional chart.
        //
        // scrollToRealTime() alone doesn't work on first data load (no prior
        // visible range). Setting an explicit visible logical range ensures
        // the chart always opens at the latest data.
        const ts = chartRef.current?.timeScale();
        if (ts && chartData.length > 0) {
          const visibleBars = Math.min(chartData.length, 100);
          const fromIndex = chartData.length - visibleBars;
          ts.setVisibleLogicalRange({
            from: fromIndex,
            to: chartData.length + 10, // +10 = rightOffset breathing room
          });
        }
      }
    } else {
      // ── SMOOTH UPDATE PATH ─────────────────────────────────────────
      // BUG-6: Wrapped in try-catch. series.update() throws when the new
      // candle's timestamp is earlier than the last painted one — this happens
      // when a live WS tick is superseded by a historical candle at a slightly
      // different ms boundary. Full setData() is always safe as a fallback.
      const lastCandle = chartData[chartData.length - 1];
      const lastVolume = volumeData[volumeData.length - 1];
      const lastEma9 = ema9Data[ema9Data.length - 1];
      const lastEma21 = ema21Data[ema21Data.length - 1];

      try {
        candleSeriesRef.current.update(lastCandle as { time: Time; open: number; high: number; low: number; close: number });
        volumeSeriesRef.current.update(lastVolume as { time: Time; value: number; color: string });
        if (ema9SeriesRef.current && lastEma9) ema9SeriesRef.current.update(lastEma9 as { time: Time; value: number });
        if (ema21SeriesRef.current && lastEma21) ema21SeriesRef.current.update(lastEma21 as { time: Time; value: number });
      } catch (_err) {
        // Fallback: full repaint is safe and produces no visual artifacts.
        candleSeriesRef.current.setData(chartData as Array<{ time: Time; open: number; high: number; low: number; close: number }>);
        volumeSeriesRef.current.setData(volumeData as Array<{ time: Time; value: number; color: string }>);
        if (ema9SeriesRef.current) ema9SeriesRef.current.setData(ema9Data as Array<{ time: Time; value: number }>);
        if (ema21SeriesRef.current) ema21SeriesRef.current.setData(ema21Data as Array<{ time: Time; value: number }>);
        lastPaintedCandleCountRef.current = chartData.length;
      }
    }
  }, [chartData, volumeData, ema9Data, ema21Data, effectiveTimeframe, activeSymbol, candleSeriesRef, volumeSeriesRef, ema9SeriesRef, ema21SeriesRef, chartRef]);

  // ── Ghost Line (predictive forward projection) ──────────────────────
  //
  // Two paths:
  //   1. If the backend Predictive Agent has published a signal for this
  //      symbol, project a straight line from the current close to the
  //      predicted close.
  //   2. Fallback: compute an OLS linear regression directly over the
  //      displayed merged candles (`chartData`), using the **array index**
  //      as the X-axis. Project the slope forward `GHOST_CANDLES` bars.
  //
  // CRITICAL: all X-axis values use zero-based indices (0, 1, 2, …), NOT
  // raw Unix timestamps.  Using timestamps overflows the OLS accumulators
  // (n * sumXY and n * sumXX hit ~1e30+) producing NaN/Infinity slopes →
  // ghost line dives off-screen.
  //
  // Reactivity:
  //   The effect's dep array includes `chartData`, so every new live tick
  //   that mutates `mergedCandles → aggregateCandles → chartData` triggers
  //   a fresh regression and `ghostLineSeries.setData(projectedData)`.
  const GHOST_CANDLES = 5;
  // Lookback window for the OLS regression. Capped to keep the slope
  // responsive to recent action while remaining numerically stable.
  const REGRESSION_WINDOW = 60;

  useEffect(() => {
    if (!ghostLineRef.current) return;

    // Need enough bars for a meaningful slope.
    if (chartData.length < 8) {
      ghostLineRef.current.setData([]);
      return;
    }

    const lastCandle = chartData[chartData.length - 1];
    const intervalSec = Math.floor((TIMEFRAME_MS[effectiveTimeframe] ?? TIMEFRAME_MS['10m']) / 1000);
    const currentPrice = lastCandle.close;

    // Guard: current price must be a valid positive number
    if (!Number.isFinite(currentPrice) || currentPrice <= 0) {
      ghostLineRef.current.setData([]);
      return;
    }

    // ── Path 1: Backend Predictive Signal ────────────────────────────
    if (predictiveSignals.length > 0) {
      const symbolSignals = activeSymbol
        ? predictiveSignals.filter((s) => s.symbol.toUpperCase() === activeSymbol.toUpperCase())
        : predictiveSignals;

      const latest = symbolSignals.length > 0 ? symbolSignals[symbolSignals.length - 1] : null;

      if (latest) {
        const targetTimeSec = Math.floor(latest.target_timestamp_ms / 1000);
        const predictedPrice = latest.predicted_close_price;
        const minValidTime = lastCandle.time - intervalSec * 10;

        // Sanity checks:
        //   1. Target timestamp must be reasonably close to the current candle
        //   2. Predicted price must be finite and positive
        //   3. Predicted price must not deviate more than 20% from current
        //      (a >20% move in one projection window is almost certainly bad data)
        const priceDeviation = Math.abs(predictedPrice - currentPrice) / currentPrice;
        const priceIsValid = Number.isFinite(predictedPrice) && predictedPrice > 0 && priceDeviation < 0.20;

        if (targetTimeSec > minValidTime && priceIsValid) {
          const endTime = Math.max(targetTimeSec, lastCandle.time + intervalSec * GHOST_CANDLES);
          const slope = (predictedPrice - currentPrice) / GHOST_CANDLES;

          const points = Array.from({ length: GHOST_CANDLES + 1 }, (_, i) => ({
            time: (lastCandle.time + i * intervalSec) as Time,
            value: +(currentPrice + slope * i).toFixed(2),
          }));
          points[points.length - 1] = { time: endTime as Time, value: +(predictedPrice).toFixed(2) };

          ghostLineRef.current.setData(points);
          return;
        }
      }
    }

    // ── Path 2: OLS Linear Regression on chartData (zero-based index) ─
    //
    // Regress over the trailing window of merged candles. X = array index,
    // Y = candle.close. This matches the directive's normalised-axis form
    // exactly and prevents the float overflow that occurs when X = unix ts.
    const window = chartData.slice(-REGRESSION_WINDOW);
    const n = window.length;

    let sumX = 0, sumY = 0, sumXY = 0, sumXX = 0;
    for (let i = 0; i < n; i++) {
      const x = i;            // 0, 1, 2 … NOT candle.time
      const y = window[i].close;
      sumX  += x;
      sumY  += y;
      sumXY += x * y;
      sumXX += x * x;
    }

    const denom = n * sumXX - sumX * sumX;
    if (denom === 0) {
      ghostLineRef.current.setData([]);
      return;
    }
    const slope = (n * sumXY - sumX * sumY) / denom;
    const intercept = (sumY - slope * sumX) / n;

    // 👻 TASK 1 — Trace OLS Regression Output
    console.log(`👻 [GHOST MATH] n=${n}, slope=${slope}, intercept=${intercept}, denom=${denom}, sumX=${sumX}, sumY=${sumY}, sumXY=${sumXY}, sumXX=${sumXX}`);

    // Guard: slope/intercept must be finite numbers.
    if (!Number.isFinite(slope) || !Number.isFinite(intercept)) {
      console.warn(`👻 [GHOST MATH] ABORT — non-finite slope or intercept`);
      ghostLineRef.current.setData([]);
      return;
    }

    // ── Project forward ─────────────────────────────────────────────────
    // ANCHORED PROJECTION: Discard the OLS intercept for forward points.
    // The regression slope tells us the per-bar trend, but the intercept
    // is valid only in the regression's index coordinate system. At the
    // last bar (index n-1) the best-fit y can diverge significantly from
    // the actual close price, causing a visible vertical drop/detachment.
    //
    // Instead: Point 0 = currentPrice @ lastCandle.time (perfect anchor),
    // subsequent points = currentPrice + slope * k.
    //
    // Map the index axis back to UNIX seconds based on the active
    // timeframe interval so lightweight-charts can render it.

    // 👻 TASK 2 — Trace Future Time Mapping (pre-loop context)
    console.log(`👻 [GHOST PROJECTION] intervalSec=${intervalSec}, lastCandle.time=${lastCandle.time}, effectiveTimeframe=${effectiveTimeframe}, TIMEFRAME_MS=${TIMEFRAME_MS[effectiveTimeframe]}, currentPrice=${currentPrice}`);

    // Clamp slope so the terminal point stays within ±5% of current price.
    // This keeps the ghost line visible even in a runaway trend.
    const maxMove = currentPrice * 0.05;
    const rawTerminal = currentPrice + slope * GHOST_CANDLES;
    const clampedTerminal = Math.max(
      currentPrice - maxMove,
      Math.min(currentPrice + maxMove, rawTerminal)
    );
    const clampedSlope = (clampedTerminal - currentPrice) / GHOST_CANDLES;

    console.log(`👻 [GHOST MATH] rawTerminal=${rawTerminal}, clampedTerminal=${clampedTerminal}, clampedSlope=${clampedSlope}, maxMove=${maxMove}`);

    const projectedData: { time: Time; value: number }[] = [];

    for (let k = 0; k <= GHOST_CANDLES; k++) {
      const futureTime = (lastCandle.time + k * intervalSec) as Time;
      const projectedValue = +(currentPrice + clampedSlope * k).toFixed(2);

      // 👻 TASK 2 — Trace each projected step
      console.log(`👻 [GHOST PROJECTION] Step ${k}: baseTime=${lastCandle.time}, k*intervalSec=${k * intervalSec}, calculatedTime=${futureTime}, projectedValue=${projectedValue}`);

      projectedData.push({ time: futureTime, value: projectedValue });
    }

    // Suppress noise: don't draw if the total move is < 0.005% of price.
    const totalMove = Math.abs(projectedData[GHOST_CANDLES].value - currentPrice);
    if (totalMove / currentPrice < 0.00005) {
      ghostLineRef.current.setData([]);
      return;
    }

    // 👻 TASK 3 — Trace Final Payload to Lightweight Charts
    console.log("👻 [GHOST RENDER] Payload length:", projectedData.length);
    if (projectedData.length > 0) {
      console.log("👻 [GHOST RENDER] Point 1 (Current):", JSON.stringify(projectedData[0]));
      console.log("👻 [GHOST RENDER] Point N (Future):", JSON.stringify(projectedData[projectedData.length - 1]));
    }
    console.log("👻 [GHOST RENDER] Full payload:", JSON.stringify(projectedData));

    ghostLineRef.current.setData(projectedData);
  }, [predictiveSignals, activeSymbol, chartData, effectiveTimeframe, ghostLineRef]);

  // ── Update time scale on timeframe change ───────────────────────────
  useEffect(() => {
    const tf = effectiveTimeframe;
    const barSpacing =
      tf === '1M' ? 20
      : tf === '1W' ? 16
      : tf === '1D' ? 14
      : tf === '4h' || tf === '3h' || tf === '2h' ? 12
      : tf === '1h' || tf === '1H' ? 10
      : tf === '125m' || tf === '75m' || tf === '30m' ? 9
      : 8;

    chartRef.current?.timeScale().applyOptions({
      secondsVisible: false,
      barSpacing,
    });
  }, [effectiveTimeframe, chartRef]);

  // ── Resize on expand/collapse ────────────────────────────────────────
  useEffect(() => {
    if (chartRef.current && chartContainerRef.current) {
      const { width, height } = chartContainerRef.current.getBoundingClientRect();
      chartRef.current.resize(Math.floor(width), Math.floor(height));
    }
  }, [isExpanded, chartRef, chartContainerRef]);
}
