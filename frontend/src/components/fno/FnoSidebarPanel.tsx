'use client';

/**
 * FnoSidebarPanel — compact F&O analytics panel for the right sidebar.
 *
 * Reuses the same Tauri IPC calls as FnoSection (get_fno_analytics +
 * fno-snapshot event) but presents the data in a vertical scrollable column
 * that fits the 300px sidebar width. Displays all key F&O parameters:
 * PCR, Max Pain, OI Walls, IV Skew, Bias, Futures Basis, and the full
 * OI chain table.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { Loader2, TrendingUp, TrendingDown, Minus, Activity, RefreshCw } from 'lucide-react';
import { invoke } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import { useTradeStore } from '../../store/useTradeStore';
import {
  toFnoViewState,
  type FnoChains,
  type FnoPayload,
  type FnoUnavailableMarker,
  type FnoViewState,
  type NaOr,
  type OptionsBiasState,
} from './viewModel';
import { deriveUnderlyingOptions, deriveExpiryOptions } from './selectors';
import OiChainTable from './OiChainTable';

type FnoSnapshot = FnoPayload | FnoUnavailableMarker;

// ── Tiny UI primitives ────────────────────────────────────────────────────

function NA() {
  return (
    <span className="inline-flex items-center rounded px-1 py-0.5 text-[8px] font-bold uppercase tracking-widest border border-border-default bg-elevated text-text-muted">
      N/A
    </span>
  );
}

function fmt(v: NaOr<number>, dec = 2): React.ReactNode {
  if (v === null) return <NA />;
  return (
    <span className="font-mono text-text-primary">
      {v.toLocaleString('en-IN', { minimumFractionDigits: 0, maximumFractionDigits: dec })}
    </span>
  );
}

function fmtStr(v: NaOr<string>): React.ReactNode {
  if (!v || v.trim().length === 0) return <NA />;
  return <span className="font-mono text-text-primary capitalize">{v}</span>;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2 px-3 py-1.5">
      <span className="text-[9px] font-medium uppercase tracking-wider text-text-secondary whitespace-nowrap">{label}</span>
      <span className="text-[11px]">{children}</span>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col border border-border-default bg-surface">
      <div className="border-b border-border-default px-3 py-1 text-[9px] font-bold uppercase tracking-widest text-text-muted bg-elevated/30">
        {title}
      </div>
      <div className="flex flex-col divide-y divide-border-default/50">{children}</div>
    </div>
  );
}

function BiasBadge({ state }: { state: NaOr<OptionsBiasState> }) {
  const cfg = (state ? {
    bullish: { cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', icon: <TrendingUp size={10} />, label: 'Bullish' },
    bearish: { cls: 'bg-rose-500/15 text-rose-400 border-rose-500/30', icon: <TrendingDown size={10} />, label: 'Bearish' },
    neutral: { cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30', icon: <Minus size={10} />, label: 'Neutral' },
  }[state] : null) ?? { cls: 'bg-elevated text-text-muted border-border-default', icon: <Minus size={10} />, label: 'N/A' };

  return (
    <span className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[9px] font-bold uppercase tracking-wider ${cfg.cls}`}>
      {cfg.icon}{cfg.label}
    </span>
  );
}



// ── Main Component ────────────────────────────────────────────────────────

export default function FnoSidebarPanel() {
  const fnoUnderlying = useTradeStore((s) => s.fnoUnderlying);
  const fnoExpiry = useTradeStore((s) => s.fnoExpiry);
  const setFnoUnderlying = useTradeStore((s) => s.setFnoUnderlying);
  const setFnoExpiry = useTradeStore((s) => s.setFnoExpiry);

  const [chains, setChains] = useState<FnoChains | null>(null);
  const [viewState, setViewState] = useState<FnoViewState | null>(null);
  const [loading, setLoading] = useState(true);

  // Selector option lists
  const underlyings = useMemo(() => deriveUnderlyingOptions(chains, fnoUnderlying), [chains, fnoUnderlying]);
  const expiries = useMemo(() => deriveExpiryOptions(chains, fnoUnderlying), [chains, fnoUnderlying]);

  // Register fno-snapshot listener
  useEffect(() => {
    let cancelled = false;
    let unlisten: UnlistenFn | undefined;

    (async () => {
      try {
        const fn = await listen<FnoSnapshot>('fno-snapshot', (event) => {
          if (!cancelled) setViewState(toFnoViewState(event.payload));
        });
        if (cancelled) fn();
        else unlisten = fn;
      } catch { /* not in Tauri context */ }
    })();

    return () => {
      cancelled = true;
      unlisten?.();
      invoke('fno_unsubscribe').catch(() => {});
    };
  }, []);

  // Populate selectors from fno_list_chains
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await invoke<FnoChains>('fno_list_chains');
        if (!cancelled) setChains(result);
      } catch { /* not in Tauri */ }
    })();
    return () => { cancelled = true; };
  }, []);

  // Fetch analytics + subscribe on selector change
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const payload = await invoke<FnoSnapshot>('get_fno_analytics', {
          underlying: fnoUnderlying,
          expiry: fnoExpiry,
        });
        if (!cancelled) setViewState(toFnoViewState(payload));
      } catch (err) {
        if (!cancelled) {
          setViewState({
            kind: 'service-error',
            detail: typeof err === 'string' ? err : 'F&O service unavailable',
          });
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
      try {
        await invoke('fno_subscribe', { underlying: fnoUnderlying, expiry: fnoExpiry });
      } catch { /* not in Tauri */ }
    })();
    return () => { cancelled = true; };
  }, [fnoUnderlying, fnoExpiry]);

  // ── Loading state
  if (loading && viewState === null) {
    return (
      <div className="flex h-40 items-center justify-center gap-2 text-text-secondary">
        <Loader2 size={14} className="animate-spin" />
        <span className="text-[10px] font-semibold uppercase tracking-wider">Loading F&amp;O…</span>
      </div>
    );
  }

  // ── Service error / unavailable states
  if (!viewState || viewState.kind === 'unavailable' || viewState.kind === 'service-error') {
    const msg = viewState?.kind === 'unavailable'
      ? viewState.reason
      : viewState?.kind === 'service-error'
        ? viewState.detail
        : 'F&O data unavailable. Ensure the F&O service is running.';

    return (
      <div className="flex flex-col gap-3 p-4">
        <div className="flex items-center gap-2 rounded border border-amber-500/20 bg-amber-500/5 px-3 py-2">
          <Activity size={12} className="shrink-0 text-amber-400" />
          <p className="text-[10px] text-amber-300 leading-relaxed">{msg}</p>
        </div>
        <button
          type="button"
          onClick={() => {
            setLoading(true);
            setViewState(null);
            invoke<FnoSnapshot>('get_fno_analytics', { underlying: fnoUnderlying, expiry: fnoExpiry })
              .then(p => setViewState(toFnoViewState(p)))
              .catch(() => setViewState({ kind: 'service-error', detail: 'Retry failed' }))
              .finally(() => setLoading(false));
          }}
          className="flex items-center justify-center gap-1.5 rounded border border-border-default bg-elevated px-3 py-1.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary hover:bg-elevated/80 transition-colors"
        >
          <RefreshCw size={10} /> Retry
        </button>
      </div>
    );
  }

  // ── Ready / Partial — render full F&O data
  const { hud } = viewState;
  const isLive = viewState.marketStatus === 'open';

  return (
    <div className="flex flex-col gap-0 divide-y divide-border-default/40">

      {/* ── Selectors + status bar */}
      <div className="flex flex-col gap-2 px-3 py-2 bg-surface">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1">
              <span className="text-[8px] uppercase tracking-wider text-text-muted">Underlying</span>
              <select
                value={fnoUnderlying}
                onChange={(e) => setFnoUnderlying(e.target.value)}
                className="rounded border border-border-default bg-elevated px-1.5 py-0.5 text-[10px] font-semibold text-text-primary focus:outline-none focus:border-emerald-500/40"
              >
                {underlyings.map(u => <option key={u} value={u}>{u}</option>)}
              </select>
            </label>
            <label className="flex items-center gap-1">
              <span className="text-[8px] uppercase tracking-wider text-text-muted">Expiry</span>
              <select
                value={fnoExpiry}
                onChange={(e) => setFnoExpiry(e.target.value)}
                className="rounded border border-border-default bg-elevated px-1.5 py-0.5 text-[10px] font-semibold text-text-primary focus:outline-none focus:border-emerald-500/40"
              >
                <option value="">Nearest</option>
                {expiries.map(e => <option key={e} value={e}>{e}</option>)}
              </select>
            </label>
          </div>
          <span className={`inline-flex items-center gap-0.5 rounded border px-1.5 py-0.5 text-[8px] font-bold uppercase ${
            isLive
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-400'
              : 'border-amber-500/30 bg-amber-500/10 text-amber-400'
          }`}>
            <span className={`h-1.5 w-1.5 rounded-full ${isLive ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
            {isLive ? 'Live' : 'Closed'}
          </span>
        </div>
      </div>

      {/* ── Agent Bias */}
      <div className="flex items-center justify-between px-3 py-2 bg-surface">
        <div className="flex flex-col gap-0.5">
          <span className="text-[8px] font-bold uppercase tracking-widest text-text-muted">Options Bias</span>
          <span className="text-[9px] text-text-secondary">{hud.context.underlying} · {hud.context.expiry || 'Nearest'}</span>
        </div>
        <BiasBadge state={hud.biasState} />
      </div>

      {/* ── Key Metrics */}
      <div className="flex flex-col gap-2 p-2">
        <Card title="Options Analytics">
          <Row label="PCR (OI)">{fmt(hud.pcrOi)}</Row>
          <Row label="PCR (Volume)">{fmt(hud.pcrVolume)}</Row>
          <Row label="Max Pain">
            {hud.maxPain !== null
              ? <span className="font-mono font-bold text-amber-400">₹{hud.maxPain.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</span>
              : <NA />}
          </Row>
          <Row label="Futures Basis">{fmt(hud.futuresBasis)}</Row>
        </Card>

        <Card title="OI Walls">
          <Row label="Support">
            {hud.walls.support !== null
              ? <span className="font-mono font-bold text-emerald-400">₹{hud.walls.support.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</span>
              : <NA />}
          </Row>
          <Row label="Resistance">
            {hud.walls.resistance !== null
              ? <span className="font-mono font-bold text-rose-400">₹{hud.walls.resistance.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</span>
              : <NA />}
          </Row>
        </Card>

        <Card title="Aggregate OI Bias">
          <Row label="Call Buildup">{fmtStr(hud.aggregateOiBias.call)}</Row>
          <Row label="Put Buildup">{fmtStr(hud.aggregateOiBias.put)}</Row>
        </Card>

        <Card title="IV Skew">
          {hud.ivSkew === null ? (
            <div className="px-3 py-2"><NA /></div>
          ) : (
            <>
              <Row label="Put − Call">{fmt(hud.ivSkew.putMinusCall)}</Row>
              <Row label="Slope">{fmt(hud.ivSkew.slope, 4)}</Row>
              <Row label="ATM IV">{fmt(hud.ivSkew.atmIv)}</Row>
            </>
          )}
        </Card>

        {/* Driving signals */}
        {hud.biasSignals !== null && Object.keys(hud.biasSignals).length > 0 && (
          <Card title="Driving Signals">
            {Object.entries(hud.biasSignals).map(([key, value]) => (
              <Row key={key} label={key.replace(/_/g, ' ')}>
                <span className="font-mono text-text-primary text-right">
                  {typeof value === 'number'
                    ? Number.isFinite(value) ? value.toLocaleString(undefined, { maximumFractionDigits: 4 }) : '—'
                    : String(value ?? '—')}
                </span>
              </Row>
            ))}
          </Card>
        )}
      </div>

      {/* ── OI Chain Table */}
      <div className="flex flex-col">
        <div className="border-b border-border-default px-3 py-1 text-[9px] font-bold uppercase tracking-widest text-text-muted bg-elevated/30">
          OI Chain (Call vs Put)
        </div>
        <OiChainTable viewState={viewState as FnoViewState & { kind: 'ready' | 'partial' }} />
      </div>

    </div>
  );
}
