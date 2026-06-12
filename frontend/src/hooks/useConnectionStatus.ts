// Feature: professional-charting-suite
//
// useConnectionStatus — surfaces the Realtime_Feed connection state for the
// charting suite (Requirements 9.7, 9.8).
//
// The store already tracks the live backend link via `connectionStatus`
// (driven by the aggregator/decision WebSocket lifecycle) and the lower-level
// `wsStatus`. This hook reuses that state and reduces it via the pure
// `deriveConnectionStatus` mapper (see `charting/connectionStatus`) to a
// single, stable view the renderer can use to show/remove the
// disconnected-feed indicator:
//
//   - `isDisconnected` flips true the moment the feed link drops
//     (WS `onclose`/`onerror` set `connectionStatus = 'DISCONNECTED'`),
//     which is well within the 2-second budget of Requirement 9.7;
//   - `isDisconnected` flips back to false on reconnect
//     (WS `onopen` sets `connectionStatus = 'CONNECTED'`), satisfying the
//     2-second removal budget of Requirement 9.8.
//
// The transient `CONNECTING` state is intentionally NOT treated as
// disconnected so the indicator does not flicker during normal reconnect
// backoff cycles; it only appears once the feed is actually down.

import { useTradeStore } from '../store/useTradeStore';
import {
  deriveConnectionStatus,
  type ConnectionStatus,
  type FeedConnectionStatus,
} from '../charting/connectionStatus';

export type { ConnectionStatus, FeedConnectionStatus };

/**
 * Derive the realtime-feed connection status from the trade store.
 *
 * Reuses the existing `connectionStatus` / `wsStatus` store state rather than
 * opening a separate socket, so the indicator stays in lock-step with the
 * actual feed lifecycle.
 */
export function useConnectionStatus(): ConnectionStatus {
  const connectionStatus = useTradeStore((s) => s.connectionStatus);
  const wsStatus = useTradeStore((s) => s.wsStatus);

  return deriveConnectionStatus(connectionStatus, wsStatus);
}
