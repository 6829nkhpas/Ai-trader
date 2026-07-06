/**
 * useSymbolSearch — search logic hook for SymbolSearchModal.
 *
 * Encapsulates debounced instrument search, result selection routing
 * (equity → chart, F&O → FNO profile), and filter state. Extracted to
 * keep the modal component under 300 lines.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { useTradeStore } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import { SearchResult, resultSymbol } from '../../types/searchResult';

const DEFAULT_FNO_UNDERLYINGS = ['NIFTY 50', 'BANKNIFTY'];

const INDEX_NFO_ALIASES: Record<string, string> = {
  'NIFTY 50': 'NIFTY',
  'NIFTY BANK': 'BANKNIFTY',
  'BANKNIFTY': 'BANKNIFTY',
  'NIFTY FIN SERVICE': 'FINNIFTY',
  'FINNIFTY': 'FINNIFTY',
  'NIFTY MIDCAP SELECT': 'MIDCPNIFTY',
  'MIDCPNIFTY': 'MIDCPNIFTY',
  'NIFTY NEXT 50': 'NIFTYNXT50',
  'NIFTYNXT50': 'NIFTYNXT50',
};

/** Whether a search result represents a market index. */
export function isIndex(r: SearchResult): boolean {
  const name = r.kind === 'EQ' ? r.symbol : r.underlying;
  const upperName = name?.toUpperCase() || '';
  return [
    'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX', 'MIDCPNIFTY',
    'NIFTY_50', 'NIFTY 50', 'NIFTY BANK', 'NIFTY FINANCIAL SERVICES'
  ].includes(upperName);
}

export type SearchTab = 'Stock' | 'Index' | 'F&O';

interface UseSymbolSearchOptions {
  onClose: () => void;
}

export function useSymbolSearch({ onClose }: UseSymbolSearchOptions) {
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [activeTab, setActiveTab] = useState<SearchTab>('Stock');
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const [selectedExchange, setSelectedExchange] = useState<'NSE' | 'BSE' | 'ALL'>('NSE');
  const [showExchangeMenu, setShowExchangeMenu] = useState(false);

  const searchTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const addToWatchlist = useTradeStore((s) => s.addToWatchlist);
  const setSelectedSymbol = useTradeStore((s) => s.setSelectedSymbol);
  const setPaneSymbol = useChartUIStore((s) => s.setPaneSymbol);
  const activePaneId = useChartUIStore((s) => s.activePaneId);
  const splitView = useChartUIStore((s) => s.splitView);
  const setActiveProfile = useTradeStore((s) => s.setActiveProfile);
  const setFnoUnderlying = useTradeStore((s) => s.setFnoUnderlying);

  const handleSearch = useCallback(async (searchQuery: string) => {
    const normalized = searchQuery.trim();
    if (normalized.length < 2) {
      setSearchResults([]);
      setIsSearching(false);
      setSearchError(null);
      return;
    }
    setIsSearching(true);
    setSearchError(null);
    try {
      const results = await invoke<SearchResult[]>('search_instruments', { query: normalized });
      setSearchResults(results || []);
      setSelectedIndex(results && results.length > 0 ? 0 : -1);
    } catch (err) {
      console.error('[SymbolSearchModal] search failed:', err);
      setSearchResults([]);
      setSearchError('Search failed — please try again');
    } finally {
      setIsSearching(false);
    }
  }, []);

  const handleInputChange = useCallback((value: string) => {
    setQuery(value);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    if (!value.trim() || value.trim().length < 2) {
      setSearchResults([]);
      setSearchError(null);
      setSelectedIndex(-1);
      return;
    }
    searchTimeoutRef.current = setTimeout(() => handleSearch(value), 300);
  }, [handleSearch]);

  const routeSymbolToChart = useCallback((symbol: string) => {
    if (splitView) {
      setPaneSymbol(activePaneId, symbol);
    } else {
      setSelectedSymbol(symbol);
    }
  }, [splitView, setPaneSymbol, activePaneId, setSelectedSymbol]);

  const handleSelectResult = useCallback(async (r: SearchResult) => {
    const symbol = resultSymbol(r);
    const sector = r.kind === 'EQ' ? 'EQ' : r.optionType;

    let displayName = symbol;
    if (r.kind === 'EQ') {
      displayName = (r.name || r.symbol).replace(/"/g, '');
    } else {
      let expiryFormatted = r.expiry;
      if (r.expiry) {
        try {
          const date = new Date(r.expiry);
          if (!isNaN(date.getTime())) {
            const day = date.getDate();
            const month = date.toLocaleString('en-US', { month: 'short' });
            expiryFormatted = `${day} ${month}`;
          }
        } catch (e) {}
      }
      if (r.optionType === 'FUT') {
        displayName = `${r.underlying} FUT (${expiryFormatted})`;
      } else {
        displayName = `${r.underlying} ${r.strike} ${r.optionType} (${expiryFormatted})`;
      }
    }

    addToWatchlist({
      symbol,
      token: 0,
      name: displayName,
      sector,
      lastPrice: 0,
      change: 0,
    });

    // ── F&O contract selection ──────────────────────────────────────────
    // Switch to FNO profile, set the underlying, and route the specific
    // tradingsymbol to the chart pane so the contract's price chart loads.
    if (r.kind === 'FNO' && typeof r.underlying === 'string') {
      setActiveProfile('FNO');

      // Resolve configured name (e.g. 'NIFTY 50') or use raw underlying.
      const matchedConfig = DEFAULT_FNO_UNDERLYINGS.find((u) => {
        const ru = r.underlying.toUpperCase();
        return u.toUpperCase() === ru || INDEX_NFO_ALIASES[u.toUpperCase()] === ru;
      });
      setFnoUnderlying(matchedConfig ?? r.underlying);

      // Route the contract tradingsymbol to the chart for price data.
      routeSymbolToChart(symbol);
      onClose();

      // Register the underlying with the option-chain subscriber (best-effort).
      if (!matchedConfig) {
        invoke<boolean>('fno_request_underlying', { underlying: r.underlying }).catch(
          (err) => console.warn('[SymbolSearch] fno_request_underlying failed:', err),
        );
      }
      return;
    }

    // ── Equity selection ────────────────────────────────────────────────
    routeSymbolToChart(symbol);
    onClose();
  }, [addToWatchlist, routeSymbolToChart, setActiveProfile, setFnoUnderlying, onClose]);

  // Filter results by active tab and selected exchange
  const filteredResults = searchResults.filter((r) => {
    // F&O tab: show only FNO results (no exchange filter — all NFO)
    if (activeTab === 'F&O') {
      return r.kind === 'FNO';
    }
    // Stock/Index tabs: only show equities
    if (r.kind === 'FNO') return false;
    const isIdx = isIndex(r);
    const tabMatch = activeTab === 'Index' ? isIdx : !isIdx;
    if (!tabMatch) return false;

    if (selectedExchange === 'ALL') return true;
    return r.exchange.toUpperCase() === selectedExchange;
  });

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    };
  }, []);

  return {
    query,
    setQuery,
    activeTab,
    setActiveTab,
    isSearching,
    searchError,
    selectedIndex,
    setSelectedIndex,
    selectedExchange,
    setSelectedExchange,
    showExchangeMenu,
    setShowExchangeMenu,
    filteredResults,
    handleSearch,
    handleInputChange,
    handleSelectResult,
  };
}
