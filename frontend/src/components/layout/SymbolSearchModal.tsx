'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Search, X } from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { useTradeStore } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import { SearchResult, resultSymbol } from '../../types/searchResult';

interface SymbolSearchModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialQuery?: string;
}

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

const isIndex = (r: SearchResult): boolean => {
  const name = r.kind === 'EQ' ? r.symbol : r.underlying;
  const upperName = name?.toUpperCase() || '';
  return [
    'NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX', 'MIDCPNIFTY',
    'NIFTY_50', 'NIFTY 50', 'NIFTY BANK', 'NIFTY FINANCIAL SERVICES'
  ].includes(upperName);
};

export default function SymbolSearchModal({ isOpen, onClose, initialQuery }: SymbolSearchModalProps) {
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [activeTab, setActiveTab] = useState<'Stock' | 'Index'>('Stock');
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const [selectedExchange, setSelectedExchange] = useState<'NSE' | 'BSE' | 'ALL'>('NSE');
  const [showExchangeMenu, setShowExchangeMenu] = useState(false);
  const exchangeMenuRef = useRef<HTMLDivElement>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
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

  // Focus input on mount/open
  useEffect(() => {
    if (isOpen) {
      const timer = setTimeout(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
        const q = initialQuery || '';
        setQuery(q);
        if (q) {
          handleSearch(q);
        } else {
          setSearchResults([]);
        }
        setSelectedIndex(-1);
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [isOpen, initialQuery, handleSearch]);

  const handleInputChange = (value: string) => {
    setQuery(value);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    if (!value.trim() || value.trim().length < 2) {
      setSearchResults([]);
      setSearchError(null);
      setSelectedIndex(-1);
      return;
    }
    searchTimeoutRef.current = setTimeout(() => handleSearch(value), 300);
  };

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
    const name = r.kind === 'EQ' ? (r.name || r.symbol) : r.underlying;
    
    addToWatchlist({
      symbol,
      token: 0,
      name: name || symbol,
      sector,
      lastPrice: 0,
      change: 0,
    });

    const matchedConfig =
      r.kind === 'FNO' && typeof r.underlying === 'string'
        ? DEFAULT_FNO_UNDERLYINGS.find((u) => {
            const ru = r.underlying.toUpperCase();
            return u.toUpperCase() === ru || INDEX_NFO_ALIASES[u.toUpperCase()] === ru;
          })
        : undefined;

    if (r.kind === 'FNO' && matchedConfig) {
      setActiveProfile('FNO');
      setFnoUnderlying(matchedConfig);
      onClose();
      return;
    }

    if (r.kind === 'FNO' && typeof r.underlying === 'string') {
      onClose();
      try {
        const ok = await invoke<boolean>('fno_request_underlying', {
          underlying: r.underlying,
        });
        if (ok) {
          setActiveProfile('FNO');
          setFnoUnderlying(r.underlying);
          return;
        }
      } catch (err) {
        console.warn('[SymbolSearchModal] fno_request_underlying failed:', err);
      }
      routeSymbolToChart(symbol);
      return;
    }

    routeSymbolToChart(symbol);
    onClose();
  }, [addToWatchlist, routeSymbolToChart, setActiveProfile, setFnoUnderlying, onClose]);

  // Filter results by active tab and selected exchange
  const filteredResults = searchResults.filter((r) => {
    const isIdx = isIndex(r);
    const tabMatch = activeTab === 'Index' ? isIdx : !isIdx;
    if (!tabMatch) return false;

    if (selectedExchange === 'ALL') return true;
    if (r.kind === 'EQ') {
      return r.exchange.toUpperCase() === selectedExchange;
    }
    return selectedExchange === 'NSE';
  });

  // Handle keyboard events
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((prev) => (prev < filteredResults.length - 1 ? prev + 1 : prev));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((prev) => (prev > 0 ? prev - 1 : prev));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (selectedIndex >= 0 && selectedIndex < filteredResults.length) {
        handleSelectResult(filteredResults[selectedIndex]);
      }
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  };

  useEffect(() => {
    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    };
  }, []);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (exchangeMenuRef.current && !exchangeMenuRef.current.contains(e.target as Node)) {
        setShowExchangeMenu(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => {
      document.removeEventListener('mousedown', handler);
    };
  }, []);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-background/80 backdrop-blur-sm select-none p-4">
      {/* Backdrop click close */}
      <div className="absolute inset-0" onClick={onClose} />

      <div 
        className="relative w-full max-w-[640px] rounded-lg border border-border-default bg-surface shadow-2xl overflow-hidden flex flex-col max-h-[85vh] z-10"
        onKeyDown={handleKeyDown}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border-default/50">
          <span className="text-sm font-semibold text-text-primary tracking-wide">Symbol Search</span>
          <button
            onClick={onClose}
            className="rounded p-1 text-text-muted hover:bg-elevated hover:text-text-primary transition-colors flex items-center justify-center"
            title="Close"
          >
            <X size={16} />
          </button>
        </div>

        {/* Input area */}
        <div className="px-4 pt-3 pb-2">
          <div className="relative">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted pointer-events-none" />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => handleInputChange(e.target.value)}
              placeholder="Search NSE symbol..."
              className="h-9 w-full rounded border border-border-default bg-surface pl-10 pr-10 text-[13px] text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-0 uppercase"
            />
            {query && (
              <button 
                onClick={() => handleInputChange('')} 
                className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary transition-colors"
                title="Clear"
              >
                <X size={15} />
              </button>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="px-4 pb-2 flex gap-1.5 border-b border-border-default/30">
          {(['Stock', 'Index'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => {
                setActiveTab(tab);
                setSelectedIndex(0);
              }}
              className={`rounded px-3 py-1 text-xs font-semibold uppercase tracking-wider transition-colors ${
                activeTab === tab
                  ? 'bg-white text-black border border-white'
                  : 'bg-elevated/45 text-text-muted hover:text-text-primary border border-border-default/30'
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        {/* Table column headers */}
        <div className="flex justify-between items-center px-4 py-1.5 border-b border-border-default/30 text-[10px] font-bold text-text-muted tracking-wider bg-elevated/5">
          <div className="flex gap-4">
            <span className="w-20">SYMBOL</span>
            <span>DESCRIPTION</span>
          </div>
          <div className="relative" ref={exchangeMenuRef}>
            <button
              onClick={() => setShowExchangeMenu(!showExchangeMenu)}
              className="flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-elevated/45 text-text-muted hover:text-text-primary transition-colors cursor-pointer"
            >
              <span>{selectedExchange}</span>
              <ChevronDownIcon />
            </button>
            {showExchangeMenu && (
              <div className="absolute right-0 top-full mt-1 z-50 w-20 rounded border border-border-default bg-surface shadow-xl py-1 text-center">
                {(['NSE', 'BSE', 'ALL'] as const).map((ex) => (
                  <button
                    key={ex}
                    onClick={() => {
                      setSelectedExchange(ex);
                      setShowExchangeMenu(false);
                      setSelectedIndex(0);
                    }}
                    className={`w-full text-center px-2 py-1.5 text-[9px] font-bold uppercase tracking-wider hover:bg-elevated/40 transition-colors ${
                      selectedExchange === ex ? 'text-white bg-elevated/20' : 'text-text-muted'
                    }`}
                  >
                    {ex}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Results List */}
        <div ref={listRef} className="flex-1 overflow-y-auto max-h-[350px] divide-y divide-border-default/10 [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
          {isSearching && (
            <div className="flex items-center justify-center py-8 text-xs text-text-muted gap-2">
              <span className="animate-spin text-text-primary">⚡</span>
              Searching database...
            </div>
          )}

          {!isSearching && searchError && (
            <div className="text-center py-8 text-xs text-red-400 font-semibold">
              {searchError}
            </div>
          )}

          {!isSearching && !searchError && filteredResults.length === 0 && (
            <div className="text-center py-8 text-xs text-text-muted">
              {query.trim().length >= 2 ? 'No matches found' : 'Type to search for NSE stocks or indices'}
            </div>
          )}

          {!isSearching && !searchError && filteredResults.map((r, index) => {
            const sym = resultSymbol(r);
            const desc = r.kind === 'EQ' ? r.name : `${r.underlying} ${r.expiry} ${r.strike ? r.strike : ''} ${r.optionType}`;
            const isSelected = index === selectedIndex;

            return (
              <div
                key={sym + index}
                onClick={() => handleSelectResult(r)}
                onMouseEnter={() => setSelectedIndex(index)}
                className={`flex justify-between items-center px-4 py-2.5 cursor-pointer transition-colors ${
                  isSelected ? 'bg-elevated/40 text-text-primary' : 'hover:bg-elevated/20 text-text-secondary'
                }`}
              >
                <div className="flex items-baseline gap-4 min-w-0">
                  <span className="w-20 font-bold text-xs truncate text-text-primary">
                    {highlightText(sym, query)}
                  </span>
                  <span className="text-[11px] truncate max-w-[280px]">
                    {highlightText(desc, query)}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-[9px] uppercase font-bold tracking-wider shrink-0 text-text-muted">
                  <span className="text-text-primary/70">NSE</span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Footer Help */}
        <div className="text-[10px] text-text-muted/60 text-center py-2.5 bg-elevated/10 border-t border-border-default/30">
          Simply start typing while on the chart to pull up this search box
        </div>
      </div>
    </div>
  );
}

const highlightText = (text: string, highlight: string) => {
  if (!highlight.trim()) {
    return <span>{text}</span>;
  }
  const regex = new RegExp(`(${highlight.trim().replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&')})`, 'gi');
  const parts = text.split(regex);
  return (
    <>
      {parts.map((part, i) =>
        regex.test(part) ? (
          <span key={i} className="text-emerald-400 font-bold">
            {part}
          </span>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  );
};

function ChevronDownIcon() {
  return (
    <svg className="h-3 w-3 text-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
    </svg>
  );
}
