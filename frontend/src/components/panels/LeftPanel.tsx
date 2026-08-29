'use client';

import React, { useEffect } from 'react';
import { Activity } from 'lucide-react';
import { useTradeStore } from '../../store/useTradeStore';
import { useQuantStore } from '../../store/useQuantStore';

// ── Subcomponents ──────────────────────────────────────────────────────
import LiveAssetHUD from './left-panel/LiveAssetHUD';
import SentimentBlock from './left-panel/SentimentBlock';
import WatchlistBlock from './left-panel/WatchlistBlock';
import SymbolSearchBlock from './left-panel/SymbolSearchBlock';
import MultiTfPatternsView from '../quant/deep-quant/MultiTfPatternsView';
import WaitIcon from './WaitIcon';

export default function LeftPanel() {
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  
  const consensusData = useQuantStore((s) => s.consensusData);
  const consensusComputedAt = useQuantStore((s) => s.consensusComputedAt);
  const loadConsensusForSymbol = useQuantStore((s) => s.loadConsensusForSymbol);
  const isFetchingPatterns = useQuantStore((s) => s.isFetchingPatterns);
  const multiTfPatterns = useQuantStore((s) => s.multiTfPatterns);
  const patternsError = useQuantStore((s) => s.patternsError);
  const fetchMultiTfPatterns = useQuantStore((s) => s.fetchMultiTfPatterns);

  const activeSentiment = useQuantStore((s) => s.activeSentiment);
  const isFetchingSentiment = useQuantStore((s) => s.isFetchingSentiment);
  const sentimentError = useQuantStore((s) => s.sentimentError);
  const loadSentimentForSymbol = useQuantStore((s) => s.loadSentimentForSymbol);

  // Trigger sentiment fetch on symbol change — fully independent of market hours
  useEffect(() => {
    if (selectedSymbol) {
      loadSentimentForSymbol(selectedSymbol);
    }
  }, [selectedSymbol, loadSentimentForSymbol]);

  // Load cached consensus for the selected symbol (or clear if no cache exists)
  useEffect(() => {
    if (selectedSymbol) {
      loadConsensusForSymbol(selectedSymbol);
    }
  }, [selectedSymbol, loadConsensusForSymbol]);

  // Load multi-timeframe chart-pattern detection, but only once the chart
  // datafeed has populated historicalCache for this symbol. Without this guard
  // the Tauri command fires before get_historical_view has triggered the Kite
  // backfill into QuestDB, so the Rust engine sees 0–1 candles and returns
  // "Insufficient data / 1 candle available".
  const historicalCache = useTradeStore((s) => s.historicalCache);
  const symUpper = selectedSymbol?.toUpperCase() ?? '';
  const hasCacheForSymbol = symUpper
    ? Object.keys(historicalCache).some((k) => k.startsWith(`${symUpper}::`) && historicalCache[k].length >= 30)
    : false;

  useEffect(() => {
    if (selectedSymbol && hasCacheForSymbol) {
      fetchMultiTfPatterns(selectedSymbol);
    }
  }, [selectedSymbol, hasCacheForSymbol, fetchMultiTfPatterns]);

  return (
    <div className="flex h-full flex-col select-none">
      {/* Search Input block (remains hidden in LeftPanel as it is globally handled by layouts/modals) */}
      <SymbolSearchBlock />

      {/* Dynamic Drag-and-Drop Watchlist Block */}
      <WatchlistBlock />

      {/* ── Bottom Section HUD (Consensus + Sentiment) ── */}
      <div className="flex-1 min-h-0 overflow-y-auto scrollbar-thin">
        {/* Sentiment Block */}
        <SentimentBlock 
          sentiment={activeSentiment} 
          isLoading={isFetchingSentiment} 
          error={sentimentError} 
        />

        {/* Consensus HUD */}
        {(() => {
          const symbolMatch = consensusData && selectedSymbol
            ? consensusData.symbol?.toUpperCase() === selectedSymbol.toUpperCase()
            : !!consensusData;

          if (!consensusData || !symbolMatch) {
            return (
              <div className="flex flex-col items-center justify-center gap-3 p-6 text-center animate-in fade-in duration-200">
                <div className="w-44 h-24 flex items-center justify-center shrink-0">
                  <WaitIcon className="w-full h-full object-contain" />
                </div>
                <div className="space-y-1">
                  <p className="text-xs font-bold text-text-primary tracking-tight">
                    No Technical Data for <span className="text-emerald-500 font-extrabold">{selectedSymbol || 'symbol'}</span>
                  </p>
                  <p className="text-[10px] text-text-secondary leading-relaxed max-w-[220px] mx-auto">
                    Run Deep Quant Analysis to compute technical consensus
                  </p>
                </div>
              </div>
            );
          }

          return <LiveAssetHUD data={consensusData} computedAt={consensusComputedAt} />;
        })()}

        {/* Dynamic Pattern Scanner.
            `patternsError` is part of the condition: a failed scan leaves
            `multiTfPatterns` null, so without it the panel unmounted entirely and
            the failure had nowhere to be reported. */}
        {(isFetchingPatterns || multiTfPatterns || patternsError) && (
          <MultiTfPatternsView />
        )}
      </div>
    </div>
  );
}
