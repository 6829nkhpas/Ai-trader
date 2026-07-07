'use client';
// FnoSection — F&O workspace with OI profile, IV skew, Options HUD.
// Falls back to cached historical data when live data is unavailable.
import React, { useEffect, useMemo, useState } from 'react';
import { Group, Panel, Separator } from 'react-resizable-panels';
import { Loader2 } from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';

import { useTradeStore } from '../../store/useTradeStore';
import {
  toFnoViewState,
  type FnoChains,
  type FnoPayload,
  type FnoUnavailableMarker,
  type FnoViewState,
} from './viewModel';
import { deriveExpiryOptions, deriveUnderlyingOptions } from './selectors';
import OiProfileChart from './OiProfileChart';
import IvSkewChart from './IvSkewChart';
import OptionsHud from './OptionsHud';
import FnoUnavailableState from './FnoUnavailableState';
import FnoServiceState from './FnoServiceState';
import HistoricalDataBanner from './HistoricalDataBanner';
import { useFnoSnapshotCache } from './useFnoSnapshotCache';
import FnoChartPanel from './FnoChartPanel';

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
        const unlistenFn = await listen<FnoSnapshot>('fno-snapshot', (event) => {
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
      invoke('fno_unsubscribe').catch((err) =>
        console.warn('[FnoSection] fno_unsubscribe failed:', err),
      );
    };
  }, []);

  // Populate the selectors from fno_list_chains (mount once).
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const result = await invoke<FnoChains>('fno_list_chains');
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
        const payload = await invoke<FnoSnapshot>('get_fno_analytics', {
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
        await invoke('fno_subscribe', {
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
  const isFallback =
    (renderState === null || renderState.kind === 'unavailable') &&
    lastGoodViewState.current !== null;
  const effectiveView = isFallback ? lastGoodViewState.current! : renderState;

  const statusLabel = useMemo(() => {
    if (!effectiveView || (effectiveView.kind !== 'ready' && effectiveView.kind !== 'partial')) return null;
    const ts = formatSnapshotTs(effectiveView.snapshotTs);
    const closed = effectiveView.marketStatus === 'closed';
    return { ts, closed };
  }, [effectiveView]);

  return (
    <div className="flex h-full w-full min-h-0 flex-col bg-background font-sans">
      {/* Body — chart takes full screen */}
      <div className="relative min-h-0 flex-1">
        <FnoChartPanel />
      </div>
    </div>
  );
}
