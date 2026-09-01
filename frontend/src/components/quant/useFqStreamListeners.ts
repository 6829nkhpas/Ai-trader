'use client';

// components/quant/useFqStreamListeners.ts
//
// Registers the two bridge listeners that carry every glass-box frame.
//
// Extracted from `DeepQuantPanel` because the standalone session route is a DIFFERENT React tree.
// Left inline, a run started from the panel would stream fine and the same run opened at
// `/find-trade/session/{id}` would receive nothing at all — the frames are emitted either way, with
// nobody subscribed. One hook, two callers.
//
// Mounted at the container level rather than inside the transcript component. `AgentTerminal` only
// mounts once a run is in flight, which raced the backend SSE stream and intermittently dropped the
// opening REASONING/TOOL frames, leaving the glass box blank.

import { useEffect } from 'react';

import { FQ_MULTI_SESSION } from '../../lib/env';
import { bridgeListen } from '../../lib/bridge';
import { useQuantStore, type StreamEventPayload } from '../../store/useQuantStore';
import { useSessionStore } from '../../store/useSessionStore';

/**
 * Subscribe to a bridge channel for the lifetime of the caller.
 *
 * The `cancelled` flag is not defensive noise: `bridgeListen` is async, so a component that unmounts
 * before it resolves would otherwise leak a listener that keeps writing into the store forever.
 */
function useBridgeChannel(
  channel: string,
  handler: (payload: StreamEventPayload) => void,
  enabled = true,
) {
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let dispose: (() => void) | undefined;
    (async () => {
      try {
        const off = await bridgeListen<StreamEventPayload>(channel, (event) => {
          if (!cancelled) handler(event.payload);
        });
        if (cancelled) off();
        else dispose = off;
      } catch (err) {
        console.error(`Failed to register ${channel} listener:`, err);
      }
    })();
    return () => {
      cancelled = true;
      dispose?.();
    };
    // `handler` is intentionally not a dependency: every caller passes a module-level dispatch, and
    // including it would tear down and re-register the listener on each render — losing frames in the
    // gap.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channel, enabled]);
}

export function useFqStreamListeners() {
  // Analysis frames. Routed through `handleStreamEvent`, which forwards to the session store when the
  // flag is on and writes the flat fields when it is off.
  useBridgeChannel('deep-quant-stream', (payload) => {
    useQuantStore.getState().handleStreamEvent(payload);
  });

  // Q&A frames, multi-session path only.
  //
  // On the legacy path `useQuantStore.askQuestion` registers its own listener for the duration of one
  // question. The session-aware ask cannot: it has to survive a session switch mid-answer, and a
  // per-question listener dies with the closure that created it — which is how the rest of an answer
  // went missing when the user changed tabs. Gated so the legacy path does not get a second listener
  // handling the same frames twice.
  useBridgeChannel(
    'deep-quant-qa-stream',
    (payload) => {
      useSessionStore.getState().applyFrame(payload);
    },
    FQ_MULTI_SESSION,
  );
}
