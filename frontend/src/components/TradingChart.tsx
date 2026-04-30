'use client';
/* eslint-disable @typescript-eslint/no-explicit-any */

import React, { useEffect, useRef, useState } from 'react';
import {
  CandlestickSeries,
  CrosshairMode,
  createChart,
  createSeriesMarkers,
  IChartApi,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  SeriesMarker,
  Time,
} from 'lightweight-charts';
import { useTradeStore, AggregatedDecision } from '../store/useTradeStore';

export default function TradingChart() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  const liveDecisions = useTradeStore((state) => state.liveDecisions);
  const executedTrades = useTradeStore((state) => state.executedTrades);
  const [hoveredDecision, setHoveredDecision] = useState<AggregatedDecision | null>(null);

  const buildChartData = (decisions: AggregatedDecision[]) => {
    let previousClose = 100;
    let previousTime = 0;
    const candles: Array<{ time: any; open: number; high: number; low: number; close: number }> = [];

    [...decisions]
      .sort((left, right) => left.timestamp_ms - right.timestamp_ms)
      .forEach((decision, index) => {
        const baseTime = Math.floor(decision.timestamp_ms / 1000);
        const convictionStrength = Math.max(0.18, Math.abs(decision.final_conviction_score - 50) / 18);
        const direction =
          decision.action_type === 'SELL' ? -1 : decision.action_type === 'BUY' ? 1 : index % 2 === 0 ? 1 : -1;
        const time = Math.max(previousTime + 1, baseTime);
        const stepMagnitude = 0.08 + convictionStrength * 0.05;
        const midClose = Math.max(1, previousClose + direction * stepMagnitude * 0.65);
        const finalClose = Math.max(1, midClose + direction * stepMagnitude * 0.35);
        const bodySize = 0.05 + convictionStrength * 0.03;
        const wickSize = 0.16 + convictionStrength * 0.14;

        const firstOpen = previousClose;
        const firstClose = Math.max(1, previousClose + direction * bodySize * 0.5);
        candles.push({
          time: time as any,
          open: firstOpen,
          high: Math.max(firstOpen, firstClose) + wickSize * 0.7,
          low: Math.min(firstOpen, firstClose) - wickSize * 0.7,
          close: firstClose,
        });

        const secondTime = time + 1;
        const secondOpen = firstClose;
        const secondClose = midClose;
        candles.push({
          time: secondTime as any,
          open: secondOpen,
          high: Math.max(secondOpen, secondClose) + wickSize,
          low: Math.min(secondOpen, secondClose) - wickSize,
          close: secondClose,
        });

        const thirdTime = time + 2;
        const thirdOpen = secondClose;
        const thirdClose = finalClose;
        candles.push({
          time: thirdTime as any,
          open: thirdOpen,
          high: Math.max(thirdOpen, thirdClose) + wickSize * 0.85,
          low: Math.min(thirdOpen, thirdClose) - wickSize * 0.85,
          close: thirdClose,
        });

        previousClose = finalClose;
        previousTime = thirdTime;
      });

    return candles;
  };

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      crosshair: {
        mode: CrosshairMode.Normal,
      },
      rightPriceScale: {
        borderColor: '#e2e8f0',
      },
      timeScale: {
        borderColor: '#e2e8f0',
        timeVisible: true,
        secondsVisible: false,
        fixLeftEdge: true,
        fixRightEdge: true,
        barSpacing: 6,
        rightOffset: 12,
      },
      layout: {
        background: { color: '#ffffff' },
        textColor: '#334155',
      },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.18)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.18)' },
      },
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight || 400,
    });

    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
      priceLineVisible: false,
      lastValueVisible: true,
      priceFormat: {
        type: 'price',
        precision: 2,
        minMove: 0.01,
      },
    });

    const markers = createSeriesMarkers(candlestickSeries);

    chartRef.current = chart;
    seriesRef.current = candlestickSeries;
    markersRef.current = markers;

    const resizeObserver = new ResizeObserver(() => {
      if (!chartContainerRef.current) return;

      const rect = chartContainerRef.current.getBoundingClientRect();
      chart.resize(Math.floor(rect.width), Math.floor(rect.height));
    });

    resizeObserver.observe(chartContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      markersRef.current?.detach();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
      markersRef.current = null;
    };
  }, []);

  // Update chart data & markers when new decisions arrive
  useEffect(() => {
    if (!seriesRef.current) return;

    const chartData = buildChartData(liveDecisions);
    seriesRef.current.setData(chartData as any);
    chartRef.current?.timeScale().scrollToRealTime();

    const decisionMarkers: SeriesMarker<any>[] = liveDecisions
      .filter((item) => item.action_type !== 'HOLD')
      .slice(-40)
      .map((item) => ({
        time: Math.max(0, Math.floor(item.timestamp_ms / 1000)) as any,
        position: item.action_type === 'BUY' ? 'belowBar' : 'aboveBar',
        color: item.action_type === 'BUY' ? '#22c55e' : '#ef4444',
        shape: item.action_type === 'BUY' ? 'arrowUp' : 'arrowDown',
        text: `${item.action_type} ${item.final_conviction_score}`,
        id: `decision-${item.timestamp_ms}`,
      }));

    const executionMarkers: SeriesMarker<any>[] = executedTrades.map((trade) => ({
      time: Math.max(0, Math.floor(trade.decision.timestamp_ms / 1000)) as any,
      position: trade.decision.action_type === 'BUY' ? 'belowBar' : 'aboveBar',
      color: trade.decision.action_type === 'BUY' ? '#00ff00' : '#ff0000',
      shape: trade.decision.action_type === 'BUY' ? 'circle' : 'square',
      text: `EXECUTED: ${trade.quantity} @ ${trade.decision.price ?? close}`,
      id: `exec-${trade.executedAt}`,
    }));

    const markers = [...decisionMarkers, ...executionMarkers].sort((left, right) => (left.time as number) - (right.time as number));
    markersRef.current?.setMarkers(markers);
  }, [liveDecisions, executedTrades]);

  useEffect(() => {
    if (!chartRef.current) return;

    const handleCrosshairMove = (param: any) => {
      if (!param.time) {
        setHoveredDecision(null);
        return;
      }

      const hoveredTime = param.time as number;
      const matchedDecision = liveDecisions.find((decision) => Math.abs((decision.timestamp_ms / 1000) - hoveredTime) < 1);

      setHoveredDecision(matchedDecision || null);
    };

    chartRef.current.subscribeCrosshairMove(handleCrosshairMove);

    return () => {
      chartRef.current?.unsubscribeCrosshairMove(handleCrosshairMove);
    };
  }, [liveDecisions]);

  return (
    <div className="relative flex h-full min-h-0 w-full flex-col rounded-2xl border border-slate-200 bg-white shadow-[0_10px_30px_rgba(15,23,42,0.08)]">
      <div className="flex items-center justify-between gap-4 border-b border-slate-200 px-5 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="truncate text-sm font-semibold text-slate-900">Live Market Tape</div>
          <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-semibold text-emerald-600 ring-1 ring-emerald-200">
            {liveDecisions.length} decisions
          </span>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-3 text-xs text-slate-500">
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-500" /> Up
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-full bg-red-500" /> Down
          </span>
          <span className="flex items-center gap-1">
            <span className="h-2.5 w-2.5 rounded-full bg-slate-400" /> Hold
          </span>
        </div>
      </div>

      <div
        ref={chartContainerRef}
        className="h-full w-full flex-1 overflow-hidden rounded-b-2xl"
        style={{ minHeight: '560px' }}
      />

      {!hoveredDecision && liveDecisions.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white/70 text-sm text-slate-500 backdrop-blur-sm">
          Waiting for backend decisions...
        </div>
      )}

      {hoveredDecision && (
        <div className="pointer-events-none absolute left-5 top-5 z-10 rounded-2xl border border-slate-200 bg-white/95 p-4 text-slate-900 shadow-xl backdrop-blur-md transition-opacity duration-200">
          <h3 className="mb-2 text-base font-semibold text-emerald-600">AI Decision: {hoveredDecision.action_type}</h3>
          <div className="space-y-1 text-sm text-slate-600">
            <p>
              <span className="text-slate-500">Conviction:</span>{' '}
              <span className="font-semibold">{hoveredDecision.final_conviction_score}%</span>
            </p>
            <p>
              <span className="text-slate-500">Technical Weight:</span>{' '}
              <span className="font-semibold">{(hoveredDecision.technical_weight_used * 100).toFixed(0)}%</span>
            </p>
            <p>
              <span className="text-slate-500">Sentiment Weight:</span>{' '}
              <span className="font-semibold">{(hoveredDecision.sentiment_weight_used * 100).toFixed(0)}%</span>
            </p>
            <p className="mt-2 max-w-xs border-t border-slate-200 pt-2 text-xs italic text-slate-500">
              &quot;{hoveredDecision.reasoning || 'Live backend decision'}&quot;
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
