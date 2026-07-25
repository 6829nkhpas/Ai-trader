import { useState, useEffect, useCallback } from 'react';
import type { SymbolQuote } from '../types/home';
import { kiteFetch } from '../lib/tauriFetch';

/**
 * Polls the Kite quote endpoint for the active symbol every 30 s.
 * Returns the latest quote (or null before the first fetch).
 *
 * Extracted from `Home` to keep the page component lean.
 */
export function useSymbolQuote(symbol: string): SymbolQuote | null {
  const [symbolQuote, setSymbolQuote] = useState<SymbolQuote | null>(null);

  const fetchSymbolQuote = useCallback(async (signal?: AbortSignal) => {
    if (!symbol || symbol === '---') return;
    try {
      const sym = symbol.toUpperCase();
      const isFno = sym.endsWith('FUT') || ((sym.endsWith('CE') || sym.endsWith('PE')) && /\d/.test(sym));
      const exchange = isFno ? 'NFO' : 'NSE';
      const res = await kiteFetch(`/quote?i=${exchange}:${symbol}`);
      if (signal?.aborted) return;
      if (!res.ok) return;
      const data = await res.json();
      if (data.quotes && data.quotes.length > 0) {
        setSymbolQuote(data.quotes[0]);
      }
    } catch (err) {
      // Silence AbortError — expected on unmount
      if (err instanceof DOMException && err.name === 'AbortError') return;
      console.error('[Header] Quote fetch failed:', err);
    }
  }, [symbol]);

  useEffect(() => {
    const controller = new AbortController();
    fetchSymbolQuote(controller.signal);
    const interval = setInterval(() => fetchSymbolQuote(controller.signal), 30_000);
    return () => {
      controller.abort();
      clearInterval(interval);
    };
  }, [fetchSymbolQuote]);

  return symbolQuote;
}
