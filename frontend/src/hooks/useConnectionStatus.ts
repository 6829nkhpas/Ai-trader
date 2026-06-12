// Feature: professional-charting-suite
//
// useConnectionStatus — surfaces the Realtime_Feed connection state for the
// charting suite (Requirements 9.7, 9.8).
//
// The store already tracks the live backend link via `connectionStatus`
// (driven by the aggregator/decision WebSocket lifecycle) and the lower-level
// `wsStatus`. This hook reuses that state and reduces it to a single, stable
// view the renderer can use to show/remove the disconnected-feed indicator:
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

export type FeedConnectionStatus = 'connected' | 'connecting' | 'disconnected';

export interface ConnectionStatus {
  /** Normalized feed status. */
  status: FeedConnectionStatus;
  /** True only when the realtime feed link is down (Requirement 9.7). */
  isDisconnected: boolean;
  /** True when the realtime feed link is established (Requirement 9.8). */
  isConnected: boolean;
}

/**
 * Pure reducer: map the raw store connection signals to the normalized
 * realtime-feed connection view used by the renderer (Requirements 9.7, 9.8).
 *
 * Rules:
 *   - the feed is `connected` when either signal reports an open link;
 *   - an explicit handshake (`CONNECTING`/`connecting`) is treated as transient
 *     `connecting` — NOT disconnected — so the indicator does not flicker
 *     during reconnect backoff;
 *   - anything else is `disconnected`, which drives the visible indicator.
 *
 * Side-effect free so it can be unit-tested without a store or socket.
 */
export function deriveConnectionStatus(
  connectionStatus: string | null | undefined,
  wsStatus: string | null | undefined,
): ConnectionStatus {
  const isConnected =
    connectionStatus === 'CONNECTED' || wsStatus === 'connected';

  const isConnecting =
    !isConnected &&
    (connectionStatus === 'CONNECTING' || wsStatus === 'connecting');

  const status: FeedConnectionStatus = isConnected
    ? 'connected'
    : isConnecting
      ? 'connecting'
      : 'disconnected';

  return {
    status,
    isConnected,
    isDisconnected: status === 'disconnected',
  };
}

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
