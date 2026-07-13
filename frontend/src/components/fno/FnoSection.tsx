'use client';
// FnoSection — F&O workspace with OI profile, IV skew, Options HUD.
// Falls back to cached historical data when live data is unavailable.
import React, { useEffect, useMemo, useState } from 'react';
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

  // ── Body: branch on the settled view-state kind ──────────────────────────
  // Components branch on `kind` so a fabricated value can never reach a chart or
  // HUD field: a transport rejection renders the actionable service/config
  // state, a resolved no-data marker renders the honest empty state, and only a
  // ready/partial snapshot renders the analytics + contract chart.
  const renderBody = () => {
    if (loading && !effectiveView) {
      return (
        <div className="flex h-full w-full items-center justify-center gap-2 text-text-muted">
          <Loader2 size={14} className="animate-spin" />
          <span className="text-[11px] font-semibold uppercase tracking-widest">
            Loading F&amp;O analytics…
          </span>
        </div>
      );
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

    // ready | partial — render the analytics workspace + contract price chart.
    return (
      <div className="flex h-full w-full min-h-0 flex-col">
        <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
          {/* Analytics column: OI-profile / max-pain + IV-skew + Options HUD. */}
          <div className="flex min-h-0 flex-1 flex-col border-b border-border-default/30 lg:border-b-0 lg:border-r">
            <div className="min-h-0 flex-1">
              <OiProfileChart model={effectiveView.oi} />
            </div>
            <div className="min-h-0 flex-1 border-t border-border-default/30">
              <IvSkewChart model={effectiveView.iv} />
            </div>
            <div className="shrink-0 border-t border-border-default/30">
              <OptionsHud hud={effectiveView.hud} />
            </div>
          </div>
          {/* Contract price chart for the selected F&O instrument. */}
          <div className="flex min-h-0 flex-1 flex-col">
            <FnoChartPanel />
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="flex h-full w-full min-h-0 flex-col bg-background font-sans">
      {/* Header: underlying / expiry selectors + live-vs-most-recent status. */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border-default/30 bg-surface px-3 py-1.5">
        <select
          aria-label="F&O underlying"
          value={fnoUnderlying}
          onChange={(e) => setFnoUnderlying(e.target.value)}
          className="rounded-none border border-border-default/40 bg-elevated px-2 py-1 text-[11px] font-semibold text-text-primary"
        >
          {underlyings.map((u) => (
            <option key={u} value={u}>
              {u}
            </option>
          ))}
        </select>
        <select
          aria-label="F&O expiry"
          value={fnoExpiry}
          onChange={(e) => setFnoExpiry(e.target.value)}
          className="rounded-none border border-border-default/40 bg-elevated px-2 py-1 text-[11px] font-semibold text-text-primary"
        >
          <option value="">Nearest</option>
          {expiries.map((x) => (
            <option key={x} value={x}>
              {x}
            </option>
          ))}
        </select>
        {statusLabel && (
          <span className="ml-auto flex items-center gap-1.5 text-[10px] font-mono text-text-muted">
            <span
              className={`inline-block h-1.5 w-1.5 rounded-full ${
                statusLabel.closed ? 'bg-amber-400' : 'bg-emerald-400'
              }`}
            />
            <span className="uppercase tracking-wider">
              {statusLabel.closed ? 'Most recent' : 'Live'}
            </span>
            {statusLabel.ts && <span>{statusLabel.ts}</span>}
          </span>
        )}
      </div>

      {/* Historical/cached-data banner when serving a fallback snapshot. */}
      {isFallback &&
        effectiveView &&
        (effectiveView.kind === 'ready' || effectiveView.kind === 'partial') && (
          <HistoricalDataBanner snapshotTs={effectiveView.snapshotTs} />
        )}

      {/* Body */}
      <div className="relative min-h-0 flex-1">{renderBody()}</div>
    </div>
  );
}
