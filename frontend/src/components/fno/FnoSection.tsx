'use client';

/**
 * F&O Frontend Section (F4) — FnoSection (task 8.1).
 *
 * The F&O workspace shown while `fnoMode` is active (page.tsx mounts this only
 * then). It owns four things and nothing more — it computes no options analytic
 * and renders exactly what F1/F2/F3 produce via the IPC bridge (R9.1, R9.5):
 *
 *  1. The resizable panel layout (reusing the terminal's `react-resizable-panels`
 *     primitive and the dark-theme CSS tokens), rendering the OiProfileChart, the
 *     IvSkewChart, and the OptionsHud together in a single workspace (R2.1, R2.3).
 *  2. The `Underlying_Selector` (bounded to the configured index underlyings the
 *     bridge returns — R2.2, R9.3) and the `Expiry_Selector` (the available
 *     expiries for the selected underlying — R2.2).
 *  3. The fetch/subscribe lifecycle: on mount (i.e. `fnoMode` true) call
 *     `fno_list_chains` → `get_fno_analytics` → `fno_subscribe`; on selector
 *     change re-`get_fno_analytics` and re-`fno_subscribe`; on unmount
 *     (`fnoMode` false) call `fno_unsubscribe` (R6.2, R7.1, R7.3).
 *  4. A single section-level `listen('fno-snapshot', …)` whose payloads run
 *     through `toFnoViewState`; the listener is cleaned up on unmount.
 *
 * It branches on `viewState.kind`: `unavailable` → `FnoUnavailableState`
 * (honest empty/error state — R6.4, R8.1); otherwise it renders the three views
 * together. A backend/transport error from `get_fno_analytics` is caught and
 * surfaced as an `unavailable` state rather than crashing the UI (R6.5).
 */

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

  // The configured chains (underlyings + their expiries) for the selectors.
  const [chains, setChains] = useState<FnoChains | null>(null);
  // The latest tagged view state; `null` until the first payload resolves.
  const [viewState, setViewState] = useState<FnoViewState | null>(null);
  const [loading, setLoading] = useState(true);

  // ── Single `fno-snapshot` listener + unsubscribe teardown (mount once) ────
  // Registered at the section level so it is live before any snapshot arrives;
  // each streamed payload runs through the pure `toFnoViewState` selector. The
  // listener is dropped and the scoped poll loop is stopped on unmount — i.e.
  // when `fnoMode` flips false and page.tsx unmounts the section (R7.1, R7.3).
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
      // Stop the scoped poll loop so no F&O work runs while hidden (R7.3).
      invoke('fno_unsubscribe').catch((err) =>
        console.warn('[FnoSection] fno_unsubscribe failed:', err),
      );
    };
  }, []);

  // ── Populate the selectors from `fno_list_chains` (mount once) ────────────
  // The bridge bounds `underlyings` to the configured index chains established
  // by F1, so the Underlying_Selector can never offer an unconfigured underlying
  // (R2.2, R9.3).
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

  // ── Fetch the first payload + (re)subscribe on selector change ────────────
  // Runs on mount and whenever the underlying/expiry changes: re-fetch the
  // current snapshot via `get_fno_analytics`, then re-`fno_subscribe` with the
  // new key (the Rust slot aborts the prior loop). A transport/HTTP error from
  // the bridge becomes an `unavailable` view state, never a crash (R6.4, R6.5).
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
          setViewState({
            kind: 'unavailable',
            reason:
              typeof err === 'string'
                ? err
                : 'The F&O service returned an error or is unreachable.',
            lastSnapshotTs: null,
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

  // ── Derive the selector option lists (pure helpers — see ./selectors) ─────
  // Both lists are derived by the pure `deriveUnderlyingOptions` /
  // `deriveExpiryOptions` selectors so the bounding guarantee (R2.2, R9.3) is
  // property-tested in isolation (Property 11).
  const underlyings = useMemo(
    () => deriveUnderlyingOptions(chains, fnoUnderlying),
    [chains, fnoUnderlying],
  );

  const expiries = useMemo(
    () => deriveExpiryOptions(chains, fnoUnderlying),
    [chains, fnoUnderlying],
  );

  // ── Header status label (snapshot time / market status) ───────────────────
  const statusLabel = useMemo(() => {
    if (!viewState || viewState.kind === 'unavailable') return null;
    const ts = formatSnapshotTs(viewState.snapshotTs);
    const closed = viewState.marketStatus === 'closed';
    return { ts, closed };
  }, [viewState]);

  return (
    <div className="flex h-full w-full min-h-0 flex-col bg-background font-sans">
      {/* ── Section toolbar: underlying + expiry selectors and status ──────── */}
      <div className="flex items-center justify-between gap-4 border-b border-border-default bg-surface px-3 py-1.5">
        <div className="flex items-center gap-3">
          <span className="text-[11px] font-bold uppercase tracking-widest text-text-muted">
            F&amp;O
          </span>

          {/* Underlying_Selector — configured index underlyings only (R2.2, R9.3) */}
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

          {/* Expiry_Selector — available expiries for the selected underlying (R2.2) */}
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

      {/* ── Body: branch on the view state ─────────────────────────────────── */}
      <div className="relative min-h-0 flex-1">
        {loading && viewState === null ? (
          <div className="flex h-full w-full items-center justify-center gap-2 text-text-secondary">
            <Loader2 size={16} className="animate-spin" />
            <span className="text-xs font-semibold uppercase tracking-wider">
              Loading F&amp;O analytics…
            </span>
          </div>
        ) : viewState === null || viewState.kind === 'unavailable' ? (
          // Honest empty/error state (R6.4, R6.5, R8.1, R8.4).
          <FnoUnavailableState
            reason={viewState?.reason ?? 'F&O option data is currently unavailable.'}
            lastSnapshotTs={viewState?.kind === 'unavailable' ? viewState.lastSnapshotTs : null}
          />
        ) : (
          // ready | partial → render the three views together in the resizable
          // panel layout (R2.1, R2.3).
          <Group orientation="horizontal" className="h-full w-full">
            <Panel defaultSize={68} minSize={40}>
              <Group orientation="vertical" className="h-full w-full">
                <Panel defaultSize={55} minSize={20}>
                  <OiProfileChart model={viewState.oi} />
                </Panel>
                <Separator className="h-px cursor-row-resize bg-border-default transition-colors hover:bg-emerald-500/40 data-[separator]:h-1" />
                <Panel defaultSize={45} minSize={20}>
                  <IvSkewChart model={viewState.iv} />
                </Panel>
              </Group>
            </Panel>
            <Separator className="w-px cursor-col-resize bg-border-default transition-colors hover:bg-emerald-500/40 data-[separator]:w-1" />
            <Panel defaultSize={32} minSize={22}>
              <OptionsHud hud={viewState.hud} />
            </Panel>
          </Group>
        )}
      </div>
    </div>
  );
}
