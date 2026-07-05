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

export default function LeftPanel() {
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);
  
  const consensusData = useQuantStore((s) => s.consensusData);
  const loadConsensusForSymbol = useQuantStore((s) => s.loadConsensusForSymbol);
  const isFetchingPatterns = useQuantStore((s) => s.isFetchingPatterns);
  const multiTfPatterns = useQuantStore((s) => s.multiTfPatterns);

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
              <div className="flex flex-col items-center justify-center gap-3 p-4 py-6">
                <div className="relative">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-elevated border border-border-subtle">
                    <Activity size={16} className="text-text-muted animate-pulse" />
                  </div>
                  <div className="absolute -top-1 -right-1 h-2 w-2 rounded-full bg-amber-500/30 border border-amber-500/50 animate-ping" />
                </div>
                <div className="text-center">
                  <p className="text-[9px] font-semibold text-text-muted">No Technical Data for {selectedSymbol || 'symbol'}</p>
                  <p className="text-[8px] text-text-muted/50 mt-0.5">Run Deep Quant Analysis to<br />compute technical consensus</p>
                </div>
              </div>
            );
          }

          return <LiveAssetHUD data={consensusData} />;
        })()}

        {/* Dynamic Pattern Scanner */}
        {(isFetchingPatterns || multiTfPatterns) && (
          <MultiTfPatternsView />
        )}
      </div>
    </div>
  );
}
