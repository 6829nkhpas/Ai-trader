// Feature: professional-charting-suite
//
// Pure realtime-feed connection-status reducer (Requirements 9.7, 9.8).
//
// The store tracks the live backend link via `connectionStatus` (driven by the
// aggregator/decision WebSocket lifecycle) and the lower-level `wsStatus`. This
// pure reducer maps those raw signals to the single, stable view the renderer
// uses to show/remove the disconnected-feed indicator. Keeping it free of the
// store lets it be unit-tested without a socket or React (mirroring the
// `charting/zoom` helper pattern).

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
 * Map the raw store connection signals to the normalized connection view.
 *
 * Rules:
 *   - the feed is `connected` when either signal reports an open link;
 *   - an explicit handshake (`CONNECTING`/`connecting`) is treated as transient
 *     `connecting` — NOT disconnected — so the indicator does not flicker
 *     during reconnect backoff;
 *   - anything else is `disconnected`, which drives the visible indicator.
 *
 * Side-effect free.
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
