import { useState, useEffect } from 'react';
import { useTradeStore } from '../store/useTradeStore';

/**
 * Monitors WebSocket + browser connectivity and returns whether the
 * "Connection Lost" overlay should be shown.
 *
 * Extracted from `Home` to keep the page component lean.
 */
export function useConnectionMonitor(mounted: boolean): boolean {
  const wsStatus = useTradeStore((s) => s.wsStatus);
  const [showConnectionLost, setShowConnectionLost] = useState(false);

  useEffect(() => {
    if (!mounted) return;
    const isTestMode =
      typeof window !== 'undefined' &&
      ((window as any).__ALPHA_TEST_MODE__ ||
        process.env.ALPHA_TEST_MODE === 'true' ||
        process.env.ALPHA_TEST_MODE === '1');
    if (isTestMode) return;

    let isOnline = navigator.onLine;

    const checkConnection = () => {
      isOnline = navigator.onLine;
      if (!isOnline) { setShowConnectionLost(true); return; }
      if (wsStatus === 'error') { setShowConnectionLost(true); return; }
      if (wsStatus === 'connected') setShowConnectionLost(false);
    };

    checkConnection();

    const handleOnlineOffline = () => checkConnection();
    window.addEventListener('online', handleOnlineOffline);
    window.addEventListener('offline', handleOnlineOffline);

    // Initial 5-second grace period for first connect
    let timeoutId: NodeJS.Timeout;
    if (!isOnline || wsStatus === 'error') {
      setShowConnectionLost(true);
    } else if (wsStatus === 'disconnected') {
      timeoutId = setTimeout(() => {
        if (navigator.onLine && useTradeStore.getState().wsStatus === 'disconnected') {
          setShowConnectionLost(true);
        }
      }, 5000);
    } else if (wsStatus === 'connected') {
      setShowConnectionLost(false);
    }

    return () => {
      window.removeEventListener('online', handleOnlineOffline);
      window.removeEventListener('offline', handleOnlineOffline);
      if (timeoutId) clearTimeout(timeoutId);
    };
  }, [mounted, wsStatus]);

  return showConnectionLost;
}
