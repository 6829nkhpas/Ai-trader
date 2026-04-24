'use client';

import React, { useEffect, useRef, useState } from 'react';
import { createChart, IChartApi, ISeriesApi, SeriesMarker } from 'lightweight-charts';
import { useTradeStore, AggregatedDecision } from '../store/useTradeStore';

export default function TradingChart() {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  
  const liveDecisions = useTradeStore((state) => state.liveDecisions);
  const [hoveredDecision, setHoveredDecision] = useState<AggregatedDecision | null>(null);

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { color: '#111111' },
        textColor: '#ddd',
      },
      grid: {
        vertLines: { color: '#333' },
        horzLines: { color: '#333' },
      },
      width: chartContainerRef.current.clientWidth,
      height: 500,
    });

    const candlestickSeries = chart.addCandlestickSeries({
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
      wickUpColor: '#26a69a',
      wickDownColor: '#ef5350',
    });

    chartRef.current = chart;
    seriesRef.current = candlestickSeries;

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, []);

  // Update chart data & markers when new decisions arrive
  useEffect(() => {
    if (!seriesRef.current || liveDecisions.length === 0) return;

    const decision = liveDecisions[liveDecisions.length - 1];
    
    // Use timestamp_ms to generate a live, advancing candlestick
    const time = (decision.timestamp_ms / 1000) as any;
    
    // Simulated price movement using timestamp for demo purposes
    const simulatedPrice = decision.price || (100 + (decision.timestamp_ms % 10));
    
    const candle = {
      time,
      open: simulatedPrice - 1,
      high: simulatedPrice + 2,
      low: simulatedPrice - 2,
      close: simulatedPrice,
    };

    seriesRef.current.update(candle);

    // Update markers
    const markers: SeriesMarker<any>[] = liveDecisions
      .filter((d) => d.action_type === 'BUY' && d.final_conviction_score > 70)
      .map((d) => ({
        time: (d.timestamp_ms / 1000) as any,
        position: 'belowBar',
        color: '#26a69a',
        shape: 'arrowUp',
        text: 'BUY',
        id: d.timestamp_ms.toString(),
      }));

    if (markers.length > 0) {
      seriesRef.current.setMarkers(markers);
    }
  }, [liveDecisions]);

  // Handle marker hover
  useEffect(() => {
    if (!chartRef.current || !seriesRef.current) return;

    const handleCrosshairMove = (param: any) => {
      if (!param.time || !param.seriesData || param.point === undefined) {
        setHoveredDecision(null);
        return;
      }

      // Check if we are hovering over a marker by finding matching decision time
      const hoveredTime = param.time as number;
      const matchedDecision = liveDecisions.find((d) => 
        Math.abs((d.timestamp_ms / 1000) - hoveredTime) < 1 &&
        d.action_type === 'BUY' && 
        d.final_conviction_score > 70
      );

      setHoveredDecision(matchedDecision || null);
    };

    chartRef.current.subscribeCrosshairMove(handleCrosshairMove);

    return () => {
      chartRef.current?.unsubscribeCrosshairMove(handleCrosshairMove);
    };
  }, [liveDecisions]);

  return (
    <div className="relative w-full max-w-5xl mx-auto mt-8">
      <div 
        ref={chartContainerRef} 
        className="w-full rounded-lg overflow-hidden border border-gray-800 shadow-xl"
      />
      
      {/* Glass-Box Overlay */}
      {hoveredDecision && (
        <div className="absolute top-4 left-4 z-10 bg-black/80 backdrop-blur-md border border-gray-700 p-4 rounded-xl shadow-2xl text-white transition-opacity duration-200 pointer-events-none">
          <h3 className="font-bold text-lg mb-2 text-green-400">AI Decision: {hoveredDecision.action_type}</h3>
          <div className="space-y-1 text-sm">
            <p>
              <span className="text-gray-400">Conviction:</span>{' '}
              <span className="font-semibold">{hoveredDecision.final_conviction_score}%</span>
            </p>
            <p>
              <span className="text-gray-400">Technical Weight:</span>{' '}
              <span className="font-semibold">{hoveredDecision.technical_weight_used}%</span>
            </p>
            <p>
              <span className="text-gray-400">Sentiment Weight:</span>{' '}
              <span className="font-semibold">{hoveredDecision.sentiment_weight_used}%</span>
            </p>
            <p className="mt-2 text-xs text-gray-300 max-w-xs italic border-t border-gray-700 pt-2">
              "{hoveredDecision.reasoning}"
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
