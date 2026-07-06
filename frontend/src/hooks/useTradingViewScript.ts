'use client';

import { useState, useEffect } from 'react';

/**
 * useTradingViewScript — shared hook to dynamically load the TradingView Charting Library script
 * and return its loaded status.
 */
export function useTradingViewScript() {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    if (window.TradingView) {
      setReady(true);
      return;
    }

    const scriptSrc = '/static/charting_library/charting_library/charting_library.standalone.js';
    const existingScript = document.querySelector(`script[src="${scriptSrc}"]`);

    if (existingScript) {
      const handleLoad = () => setReady(true);
      existingScript.addEventListener('load', handleLoad);
      return () => {
        existingScript.removeEventListener('load', handleLoad);
      };
    }

    const script = document.createElement('script');
    script.src = scriptSrc;
    script.async = true;
    script.onload = () => setReady(true);
    document.head.appendChild(script);
  }, []);

  return ready;
}
