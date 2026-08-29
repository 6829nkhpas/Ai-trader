'use client';
// FnoSection — F&O workspace with OI profile, IV skew, Options HUD.
// Falls back to cached historical data when live data is unavailable.
import React, { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import FnoSkeleton from './FnoSkeleton';

import { useTradeStore } from '../../store/useTradeStore';
import {
  toFnoViewState,
  type FnoChains,
  type FnoPayload,
  type FnoUnavailableMarker,
  type FnoViewState,
} from './viewModel';
import { deriveExpiryOptions, deriveUnderlyingOptions } from './selectors';
import FnoUnavailableState from './FnoUnavailableState';
import FnoServiceState from './FnoServiceState';
import HistoricalDataBanner from './HistoricalDataBanner';
import { useFnoSnapshotCache } from './useFnoSnapshotCache';
import FnoChartPanel from './FnoChartPanel';
import { bridgeInvoke, bridgeListen, type UnlistenFn } from '../../lib/bridge';

/** Bridge payload delivered by both `get_fno_analytics` and `fno-snapshot`. */
type FnoSnapshot = FnoPayload | FnoUnavailableMarker;

/** Format the snapshot epoch-ms for the header status label, or `null`. */
function formatSnapshotTs(ts: number): string | null {
  if (!Number.isFinite(ts)) return null;
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export default function FnoSection() {
  const fnoUnderlying = useTradeStore((s) => s.fnoUnderlying);
  const fnoExpiry = useTradeStore((s) => s.fnoExpiry);
  const setFnoUnderlying = useTradeStore((s) => s.setFnoUnderlying);
  const setFnoExpiry = useTradeStore((s) => s.setFnoExpiry);

  const [chains, setChains] = useState<FnoChains | null>(null);
  const [viewState, setViewState] = useState<FnoViewState | null>(null);
  const [loading, setLoading] = useState(true);

  // Persistent cache: saves last good snapshot to localStorage keyed by
  // underlying, so historical data survives page reloads.
  const lastGoodViewState = useFnoSnapshotCache(viewState, fnoUnderlying);

  // fno-snapshot listener + unsubscribe teardown (mount once).
  useEffect(() => {
    let cancelled = false;
    let unlisten: UnlistenFn | undefined;

    (async () => {
      try {
        const unlistenFn = await bridgeListen<FnoSnapshot>('fno-snapshot', (event) => {
          if (!cancelled) {
            setViewState(toFnoViewState(event.payload));
          }
        });
        if (cancelled) {
          unlistenFn();
        } else {
          unlisten = unlistenFn;
        }
      } catch (err) {
        console.error('[FnoSection] failed to register fno-snapshot listener:', err);
      }
    })();

    return () => {
      cancelled = true;
      unlisten?.();
      bridgeInvoke('fno_unsubscribe').catch((err) =>
        console.warn('[FnoSection] fno_unsubscribe failed:', err),
      );
    };
  }, []);

  // Populate the selectors from fno_list_chains (mount once).
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const result = await bridgeInvoke<FnoChains>('fno_list_chains');
        if (!cancelled) {
          setChains(result);
        }
      } catch (err) {
        if (!cancelled) {
          console.warn('[FnoSection] fno_list_chains failed:', err);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // Fetch the first payload + (re)subscribe on selector change.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      setLoading(true);
      try {
        const payload = await bridgeInvoke<FnoSnapshot>('get_fno_analytics', {
          underlying: fnoUnderlying,
          expiry: fnoExpiry,
        });
        if (!cancelled) {
          setViewState(toFnoViewState(payload));
        }
      } catch (err) {
        if (!cancelled) {
          // Transport failure — surface actionable service/config state.
          setViewState({
            kind: 'service-error',
            detail:
              typeof err === 'string'
                ? err
                : 'The F&O service returned an error or is unreachable.',
          });
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }

      // (Re)start the scoped poll loop for the active key (R6.2, R7.1).
      try {
        await bridgeInvoke('fno_subscribe', {
          underlying: fnoUnderlying,
          expiry: fnoExpiry,
        });
      } catch (err) {
        console.warn('[FnoSection] fno_subscribe failed:', err);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [fnoUnderlying, fnoExpiry]);

  // Derive the selector option lists (pure helpers from ./selectors).
  const underlyings = useMemo(
    () => deriveUnderlyingOptions(chains, fnoUnderlying),
    [chains, fnoUnderlying],
  );

  const expiries = useMemo(
    () => deriveExpiryOptions(chains, fnoUnderlying),
    [chains, fnoUnderlying],
  );

  // Header status label — from active viewState or cached fallback.
  const renderState = viewState;
  // `service-error` is included here now.
  //
  // It used to be excluded, so a transport failure blanked the panel down to the
  // red "unreachable" card even when a perfectly good snapshot was sitting in
  // localStorage. The cached snapshot is REAL data that was really measured — it
  // is just not current — so showing it behind an explicit "Service Unreachable"
  // banner is strictly more useful than showing nothing. With no cached snapshot
  // the service-error card still renders (see `renderBody`).
  const hasCachedSnapshot = lastGoodViewState.current !== null;
  const isFallback =
    hasCachedSnapshot &&
    (renderState === null ||
      renderState.kind === 'unavailable' ||
      renderState.kind === 'service-error');
  const fallbackReason =
    renderState?.kind === 'service-error' ? 'service-unreachable' : 'market-closed';
  const effectiveView = isFallback ? lastGoodViewState.current! : renderState;

  const statusLabel = useMemo(() => {
    if (!effectiveView || (effectiveView.kind !== 'ready' && effectiveView.kind !== 'partial')) return null;
    const ts = formatSnapshotTs(effectiveView.snapshotTs);
    const closed = effectiveView.marketStatus === 'closed';
    return { ts, closed };
  }, [effectiveView]);

  // ── Body: branch on the settled view-state kind ──────────────────────────
  // Components branch on `kind` so a fabricated value can never reach a chart or
  // HUD field: a transport rejection renders the actionable service/config
  // state, a resolved no-data marker renders the honest empty state, and only a
  // ready/partial snapshot renders the analytics + contract chart.
  const renderBody = () => {
    if (loading && !effectiveView) {
      return <FnoSkeleton rows={12} />;
    }

    if (!effectiveView) {
      return <FnoUnavailableState reason="F&O analytics unavailable." lastSnapshotTs={null} />;
    }

    if (effectiveView.kind === 'service-error') {
      return <FnoServiceState detail={effectiveView.detail} />;
    }

    if (effectiveView.kind === 'unavailable') {
      return (
        <FnoUnavailableState
          reason={effectiveView.reason}
          lastSnapshotTs={effectiveView.lastSnapshotTs}
        />
      );
    }

    // ready | partial — render the contract price chart.
    return (
      <div className="flex h-full w-full min-h-0 flex-col">
        <FnoChartPanel />
      </div>
    );
  };

  return (
    <div className="flex h-full w-full min-h-0 flex-col bg-background font-sans">
      {/* Historical/cached-data banner when serving a fallback snapshot. */}
      {isFallback &&
        effectiveView &&
        (effectiveView.kind === 'ready' || effectiveView.kind === 'partial') && (
          <HistoricalDataBanner
            snapshotTs={effectiveView.snapshotTs}
            reason={fallbackReason}
          />
        )}

      {/* Body */}
      <div className="relative min-h-0 flex-1">{renderBody()}</div>
    </div>
  );
}
