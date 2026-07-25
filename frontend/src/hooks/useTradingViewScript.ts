'use client';

import { useState, useEffect, useCallback } from 'react';

/**
 * useTradingViewScript — shared hook to dynamically load the TradingView
 * Charting Library script and return its loaded/error status.
 *
 * Returns { ready, error } so the consumer can show a meaningful fallback
 * when the script fails to load (e.g. CSP block, 404, network error).
 */
export function useTradingViewScript(): { ready: boolean; error: string | null } {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const checkReady = useCallback(() => {
    if (typeof window !== 'undefined' && window.TradingView) {
      setReady(true);
      return true;
    }
    return false;
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    // Already loaded (e.g. hot-reload, second mount)
    if (checkReady()) return;

    const scriptSrc = '/static/charting_library/charting_library/charting_library.standalone.js';
    const existingScript = document.querySelector<HTMLScriptElement>(
      `script[src="${scriptSrc}"]`,
    );

    if (existingScript) {
      // Script element exists — it may have already loaded or still be loading.
      if (checkReady()) return;

      const handleLoad = () => {
        // The script fires `load` but TradingView might not be on window yet in
        // edge cases; give it a tick.
        setTimeout(() => {
          if (!checkReady()) {
            setError('TradingView script loaded but window.TradingView is undefined');
          }
        }, 100);
      };
      const handleError = () =>
        setError('TradingView script failed to load (existing element)');

      existingScript.addEventListener('load', handleLoad);
      existingScript.addEventListener('error', handleError);
      return () => {
        existingScript.removeEventListener('load', handleLoad);
        existingScript.removeEventListener('error', handleError);
      };
    }

    // Create the script element.
    const script = document.createElement('script');
    script.src = scriptSrc;
    script.async = true;

    script.onload = () => {
      console.log('[TV Script] charting_library.standalone.js loaded');
      setTimeout(() => {
        if (!checkReady()) {
          setError('TradingView script loaded but window.TradingView is undefined');
        }
      }, 100);
    };

    script.onerror = (e) => {
      console.error('[TV Script] Failed to load charting_library.standalone.js:', e);
      setError(
        `Failed to load charting library script. ` +
        `Check that /static/charting_library/ exists and CSP allows script loading.`,
      );
    };

    document.head.appendChild(script);
  }, [checkReady]);

  return { ready, error };
}
