'use client';

import React, { useEffect, useState } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { useQuantStore } from '../../store/useQuantStore';

// ── Subcomponents ──────────────────────────────────────────────────────
import WatchlistBlock from './left-panel/WatchlistBlock';
import SymbolSearchBlock from './left-panel/SymbolSearchBlock';
import SummaryRail from './left-panel/summary/SummaryRail';
import AnalysisSheet, { type AnalysisTab } from './left-panel/AnalysisSheet';

export default function LeftPanel() {
  const selectedSymbol = useTradeStore((s) => s.selectedSymbol);

  /**
   * Which analysis the detail sheet is showing, or `null` when it is closed.
   *
   * Held here rather than inside the rail because the sheet is a sibling of the
   * whole panel: every strip opens the same one, on its own tab.
   */
  const [sheetTab, setSheetTab] = useState<AnalysisTab | null>(null);
  
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

      {/* Watchlist — takes the height the analytics used to occupy, and scrolls
          within itself rather than pushing the rail off the bottom. */}
      <WatchlistBlock />

      {/* ── Analysis summary rail, pinned below the watchlist ────────────
          One line per analysis. Each opens the shared detail sheet on its own
          tab, which is where the full readings now live. */}
      <SummaryRail
        symbol={selectedSymbol}
        onOpen={setSheetTab}
        sentiment={activeSentiment}
        isSentimentLoading={isFetchingSentiment}
        sentimentError={sentimentError}
        consensus={consensusData}
        consensusComputedAt={consensusComputedAt}
        multiTfPatterns={multiTfPatterns}
        isPatternsLoading={isFetchingPatterns}
        patternsError={patternsError}
      />

      {/* Detail sheet — portaled out of this subtree, see AnalysisSheet's note */}
      <AnalysisSheet
        tab={sheetTab}
        onTabChange={setSheetTab}
        onClose={() => setSheetTab(null)}
        symbol={selectedSymbol}
        sentiment={activeSentiment}
        isSentimentLoading={isFetchingSentiment}
        sentimentError={sentimentError}
        consensus={consensusData}
        consensusComputedAt={consensusComputedAt}
        multiTfPatterns={multiTfPatterns}
        isPatternsLoading={isFetchingPatterns}
        patternsError={patternsError}
      />
    </div>
  );
}
