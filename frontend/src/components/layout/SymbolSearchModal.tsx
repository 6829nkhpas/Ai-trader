'use client';
// SymbolSearchModal — search overlay for NSE stocks, indices, and F&O contracts.
import React, { useEffect, useRef } from 'react';
import { Search, X, Loader2 } from 'lucide-react';
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

  const TABS: SearchTab[] = ['ALL', 'Stock', 'Index', 'F&O'];

  const placeholder =
    activeTab === 'ALL' 
      ? 'Search globally across stocks, indices, F&O'
      : activeTab === 'F&O'
        ? 'Filter options & futures'
        : activeTab === 'Index'
          ? 'Filter indices...'
          : 'Filter stocks...';

  const emptyHint = 'Type any symbol, stock, index or option contract to search globally';

  return (
    <div className="fixed inset-0 z-9999 flex items-center justify-center bg-black/40 dark:bg-black/60 backdrop-blur-md select-none p-4">
      {/* Backdrop click close */}
      <div className="absolute inset-0" onClick={onClose} />

      <div
        className="relative w-full max-w-160 rounded-xl border border-border-default/80 bg-surface/85 dark:bg-[#12141a]/90 backdrop-blur-xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh] z-10"
        onKeyDown={handleKeyDown}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border-default/40 bg-surface/50 dark:bg-black/20">
          <span className="text-sm font-semibold text-text-primary tracking-wide">Symbol Search</span>
          <button onClick={onClose} className="rounded-lg p-1 text-text-muted hover:bg-elevated/80 hover:text-text-primary transition-colors flex items-center justify-center" title="Close">
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
              className="h-9.5 w-full rounded-lg border border-border-default/60 bg-card/60 dark:bg-black/30 pl-10 pr-10 text-[13px] text-text-primary placeholder:text-text-muted focus:outline-none focus:border-emerald-500/60 focus:ring-1 focus:ring-emerald-500/30 transition-all uppercase font-medium"
            />
            {query && (
              <button onClick={() => handleInputChange('')} className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary transition-colors" title="Clear">
                <X size={15} />
              </button>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="px-4 pb-2.5 flex gap-1.5 border-b border-border-default/30">
          {TABS.map((tab) => (
            <button
              key={tab}
              onClick={() => { setActiveTab(tab); setSelectedIndex(0); }}
              className={`rounded-lg px-3.5 py-1 text-xs font-bold uppercase tracking-wider transition-all ${
                activeTab === tab
                  ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/40 shadow-xs'
                  : 'bg-elevated/40 text-text-muted hover:text-text-primary hover:bg-elevated/70 border border-border-default/30'
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
        <div ref={listRef} className="flex-1 overflow-y-auto max-h-87.5 divide-y divide-border-default/10 [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
          {isSearching && (
            <div className="flex items-center justify-center py-8 text-xs text-text-muted gap-2">
              <Loader2 size={12} className="animate-spin text-text-primary" />
              Searching database...
            </div>
          )}

          {!isSearching && searchError && (
            <div className="text-center py-8 text-xs text-rose-600 dark:text-rose-400 font-semibold">{searchError}</div>
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
                  isSelected ? 'bg-emerald-500/10 dark:bg-emerald-500/15 text-text-primary font-medium' : 'hover:bg-elevated/40 text-text-secondary'
                }`}
              >
                <div className="flex items-baseline gap-4 min-w-0">
                  <span className="w-20 font-bold text-xs truncate text-text-primary">
                    {highlightText(sym, query)}
                  </span>
                  <span className="text-[11px] truncate max-w-70">
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
        <div className="text-[10px] text-text-muted text-center py-2.5 bg-surface/40 dark:bg-black/20 border-t border-border-default/30">
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
    <div className="flex justify-between items-center px-4 py-1.5 border-b border-border-default/30 text-[10px] font-bold text-text-muted tracking-wider bg-surface/30 dark:bg-black/20">
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
            <div className="absolute right-0 top-full mt-1 z-50 w-24 rounded-lg border border-border-default/80 bg-surface/95 dark:bg-[#181a20]/95 backdrop-blur-xl shadow-xl py-1 text-center">
              {(['NSE', 'BSE', 'ALL'] as const).map((ex) => (
                <button
                  key={ex}
                  onClick={() => { setSelectedExchange(ex); setShowExchangeMenu(false); setSelectedIndex(0); }}
                  className={`w-full text-center px-2 py-1.5 text-[9px] font-bold uppercase tracking-wider hover:bg-elevated/40 transition-colors ${
                    selectedExchange === ex ? 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10' : 'text-text-muted'
                  }`}
                >
                  {ex}
                </button>
              ))}
            </div>
          )}
        </div>
      ) : (
        <span className="text-text-primary/70 font-bold uppercase text-[9px] tracking-wider">NFO</span>
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
