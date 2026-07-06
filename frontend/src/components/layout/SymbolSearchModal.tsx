'use client';
// SymbolSearchModal — search overlay for NSE stocks, indices, and F&O contracts.
import React, { useEffect, useRef } from 'react';
import { Search, X } from 'lucide-react';
import { resultSymbol } from '../../types/searchResult';
import FnoResultRow, { highlightText } from './FnoResultRow';
import { useSymbolSearch, type SearchTab } from './useSymbolSearch';

interface SymbolSearchModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialQuery?: string;
}

export default function SymbolSearchModal({ isOpen, onClose, initialQuery }: SymbolSearchModalProps) {
  const {
    query,
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
  } = useSymbolSearch({ onClose });

  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const exchangeMenuRef = useRef<HTMLDivElement>(null);

  // Focus input on mount/open
  useEffect(() => {
    if (isOpen) {
      const timer = setTimeout(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
        const q = initialQuery || '';
        handleInputChange(q);
        if (q) handleSearch(q);
        setSelectedIndex(-1);
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [isOpen, initialQuery, handleSearch, handleInputChange, setSelectedIndex]);

  // Close exchange menu on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (exchangeMenuRef.current && !exchangeMenuRef.current.contains(e.target as Node)) {
        setShowExchangeMenu(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [setShowExchangeMenu]);

  // Handle keyboard events
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex((prev: number) => (prev < filteredResults.length - 1 ? prev + 1 : prev));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex((prev: number) => (prev > 0 ? prev - 1 : prev));
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

  if (!isOpen) return null;

  const TABS: SearchTab[] = ['Stock', 'Index', 'F&O'];

  const placeholder =
    activeTab === 'F&O'
      ? 'Search options & futures (e.g. NIFTY 24000 CE)...'
      : activeTab === 'Index'
        ? 'Search indices...'
        : 'Search NSE stocks...';

  const emptyHint =
    activeTab === 'F&O'
      ? 'Type to search for options & futures'
      : 'Type to search for NSE stocks or indices';

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
          <button onClick={onClose} className="rounded p-1 text-text-muted hover:bg-elevated hover:text-text-primary transition-colors flex items-center justify-center" title="Close">
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
              placeholder={placeholder}
              className="h-9 w-full rounded border border-border-default bg-surface pl-10 pr-10 text-[13px] text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-0 uppercase"
            />
            {query && (
              <button onClick={() => handleInputChange('')} className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary transition-colors" title="Clear">
                <X size={15} />
              </button>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="px-4 pb-2 flex gap-1.5 border-b border-border-default/30">
          {TABS.map((tab) => (
            <button
              key={tab}
              onClick={() => { setActiveTab(tab); setSelectedIndex(0); }}
              className={`rounded px-3 py-1 text-xs font-semibold uppercase tracking-wider transition-colors ${
                activeTab === tab
                  ? tab === 'F&O'
                    ? 'bg-amber-500 text-black border border-amber-500'
                    : 'bg-white text-black border border-white'
                  : 'bg-elevated/45 text-text-muted hover:text-text-primary border border-border-default/30'
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        {/* Column headers */}
        <ColumnHeaders
          activeTab={activeTab}
          selectedExchange={selectedExchange}
          showExchangeMenu={showExchangeMenu}
          setShowExchangeMenu={setShowExchangeMenu}
          setSelectedExchange={setSelectedExchange}
          setSelectedIndex={setSelectedIndex}
          exchangeMenuRef={exchangeMenuRef}
        />

        {/* Results List */}
        <div ref={listRef} className="flex-1 overflow-y-auto max-h-[350px] divide-y divide-border-default/10 [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
          {isSearching && (
            <div className="flex items-center justify-center py-8 text-xs text-text-muted gap-2">
              <span className="animate-spin text-text-primary">⚡</span>
              Searching database...
            </div>
          )}

          {!isSearching && searchError && (
            <div className="text-center py-8 text-xs text-red-400 font-semibold">{searchError}</div>
          )}

          {!isSearching && !searchError && filteredResults.length === 0 && (
            <div className="text-center py-8 text-xs text-text-muted">
              {query.trim().length >= 2 ? 'No matches found' : emptyHint}
            </div>
          )}

          {!isSearching && !searchError && filteredResults.map((r, index) => {
            const sym = resultSymbol(r);
            const isSelected = index === selectedIndex;

            if (r.kind === 'FNO') {
              return (
                <FnoResultRow
                  key={sym + index}
                  result={r}
                  isSelected={isSelected}
                  query={query}
                  onClick={() => handleSelectResult(r)}
                  onMouseEnter={() => setSelectedIndex(index)}
                />
              );
            }

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
                    {highlightText(r.name, query)}
                  </span>
                </div>
                <div className="flex items-center gap-2 text-[9px] uppercase font-bold tracking-wider shrink-0 text-text-muted">
                  <span className="text-text-primary/70">{r.exchange.toUpperCase()}</span>
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

interface ColumnHeadersProps {
  activeTab: SearchTab;
  selectedExchange: 'NSE' | 'BSE' | 'ALL';
  showExchangeMenu: boolean;
  setShowExchangeMenu: (v: boolean) => void;
  setSelectedExchange: (v: 'NSE' | 'BSE' | 'ALL') => void;
  setSelectedIndex: (v: number) => void;
  exchangeMenuRef: React.RefObject<HTMLDivElement | null>;
}

function ColumnHeaders({ activeTab, selectedExchange, showExchangeMenu, setShowExchangeMenu, setSelectedExchange, setSelectedIndex, exchangeMenuRef }: ColumnHeadersProps) {
  return (
    <div className="flex justify-between items-center px-4 py-1.5 border-b border-border-default/30 text-[10px] font-bold text-text-muted tracking-wider bg-elevated/5">
      <div className="flex gap-4">
        {activeTab === 'F&O' ? (
          <>
            <span className="w-8">TYPE</span>
            <span>CONTRACT</span>
          </>
        ) : (
          <>
            <span className="w-20">SYMBOL</span>
            <span>DESCRIPTION</span>
          </>
        )}
      </div>
      {activeTab !== 'F&O' ? (
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
                  onClick={() => { setSelectedExchange(ex); setShowExchangeMenu(false); setSelectedIndex(0); }}
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
      ) : (
        <span className="text-amber-400/80">NFO</span>
      )}
    </div>
  );
}

function ChevronDownIcon() {
  return (
    <svg className="h-3 w-3 text-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
    </svg>
  );
}
