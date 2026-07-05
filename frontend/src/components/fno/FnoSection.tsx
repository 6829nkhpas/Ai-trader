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
      {/* Section toolbar */}
      <div className="flex items-center justify-between gap-4 border-b border-border-default bg-surface px-3 py-1.5">
        <div className="flex items-center gap-3">
          <span className="text-[11px] font-bold uppercase tracking-widest text-text-muted">
            F&amp;O
          </span>

          <label className="flex items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wider text-text-secondary">
              Underlying
            </span>
            <select
              aria-label="Underlying"
              value={fnoUnderlying}
              onChange={(e) => setFnoUnderlying(e.target.value)}
              className="rounded-none border border-border-default bg-elevated px-2 py-1 text-[11px] font-semibold text-text-primary focus:border-emerald-500/40 focus:outline-none"
            >
              {underlyings.map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wider text-text-secondary">
              Expiry
            </span>
            <select
              aria-label="Expiry"
              value={fnoExpiry}
              onChange={(e) => setFnoExpiry(e.target.value)}
              className="rounded-none border border-border-default bg-elevated px-2 py-1 text-[11px] font-semibold text-text-primary focus:border-emerald-500/40 focus:outline-none"
            >
              {/* '' resolves to the bridge's nearest available expiry. */}
              <option value="">Nearest</option>
              {expiries.map((e) => (
                <option key={e} value={e}>
                  {e}
                </option>
              ))}
            </select>
          </label>
        </div>

        {statusLabel && (
          <div className="flex items-center gap-2 text-[10px] font-mono text-text-secondary">
            <span
              className={`inline-flex items-center gap-1 rounded-none border px-1.5 py-0.5 uppercase tracking-wider ${
                statusLabel.closed
                  ? 'border-amber-500/30 bg-amber-500/10 text-amber-400'
                  : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
              }`}
            >
              {statusLabel.closed ? 'Closed' : 'Live'}
            </span>
            {statusLabel.ts && (
              <span className="text-text-muted">Snapshot {statusLabel.ts}</span>
            )}
          </div>
        )}
      </div>

      {/* Body */}
      <div className="relative min-h-0 flex-1">
        {loading && viewState === null && !lastGoodViewState.current ? (
          <div className="flex h-full w-full items-center justify-center gap-2 text-text-secondary">
            <Loader2 size={16} className="animate-spin" />
            <span className="text-xs font-semibold uppercase tracking-wider">
              Loading F&amp;O analytics…
            </span>
          </div>
        ) : viewState?.kind === 'service-error' && !lastGoodViewState.current ? (
          // Transport Err with no cached data — show service error
          <FnoServiceState detail={viewState.detail} />
        ) : effectiveView && (effectiveView.kind === 'ready' || effectiveView.kind === 'partial') ? (
          <div className="flex h-full w-full flex-col">
            {isFallback && <HistoricalDataBanner snapshotTs={effectiveView.snapshotTs} />}
            <div className="min-h-0 flex-1">
              <Group orientation="horizontal" className="h-full w-full">
                <Panel defaultSize={68} minSize={40}>
                  <Group orientation="vertical" className="h-full w-full">
                    <Panel defaultSize={55} minSize={20}>
                      <OiProfileChart model={effectiveView.oi} />
                    </Panel>
                    <Separator className="h-px cursor-row-resize bg-border-default transition-colors hover:bg-emerald-500/40 data-[separator]:h-1" />
                    <Panel defaultSize={45} minSize={20}>
                      <IvSkewChart model={effectiveView.iv} />
                    </Panel>
                  </Group>
                </Panel>
                <Separator className="w-px cursor-col-resize bg-border-default transition-colors hover:bg-emerald-500/40 data-[separator]:w-1" />
                <Panel defaultSize={32} minSize={22}>
                  <OptionsHud hud={effectiveView.hud} />
                </Panel>
              </Group>
            </div>
          </div>
        ) : viewState === null || viewState.kind === 'unavailable' ? (
          <FnoUnavailableState
            reason={viewState?.reason ?? 'F&O option data is currently unavailable.'}
            lastSnapshotTs={viewState?.kind === 'unavailable' ? viewState.lastSnapshotTs : null}
          />
        ) : viewState?.kind === 'service-error' ? (
          <FnoServiceState detail={viewState.detail} />
        ) : null}
      </div>
    </div>
  );
}
