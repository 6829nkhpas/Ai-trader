'use client';

// QuantRadar.tsx — User-Driven Symbol Radar (FEAT-037)
//
// Lets the user add their own symbols to a radar that continuously tracks
// candlestick patterns and institutional strategies on a chosen timeframe.
// Each located detection shows WHICH timeframe and WHICH candle it formed
// on, and can be clicked to visualize it on the master chart (marker +
// highlight box for patterns, marker + price line for strategies).
//
// It also listens for live `radar-alert` Tauri IPC events from the Rust
// background worker (opt-in via RADAR_ENABLED) and surfaces them inline.

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { useRadarStore, type RadarVizTarget, type RadarSymbolState } from '../../store/useRadarStore';
import type { Timeframe } from '../../utils/chartTypes';
import { TIMEFRAME_GROUPS } from '../../utils/chartTypes';
import {
  BIAS_COLORS,
  type LocatedPattern,
  type LocatedStrategy,
} from '../../utils/radarData';
import { bridgeListen } from '../../lib/bridge';
import {
  Radar,
  X,
  Plus,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  Eye,
  EyeOff,
  CandlestickChart,
  Crosshair,
  Loader2,
  Clock,
  ChevronDown,
} from 'lucide-react';


// ── Live alert payload (mirrors the enriched Rust RadarAlert) ─────────────
interface RadarAlert {
  symbol: string;
  timeframe: string;
  trigger_reason: string;
  trend_score: number;
  momentum: string;
  volatility: string;
  volume_flow: string;
  patterns: LocatedPattern[];
  strategies: LocatedStrategy[];
  timestamp_ms: number;
  severity: string;
}

function timeAgo(ms: number): string {
  const diff = Date.now() - ms;
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m`;
  return `${Math.floor(diff / 3_600_000)}h`;
}

function biasChip(bias: string) {
  const color = BIAS_COLORS[bias] ?? BIAS_COLORS.NEUTRAL;
  return { color, bg: `${color}1a`, border: `${color}40` };
}

export default function QuantRadar() {
  const [isOpen, setIsOpen] = useState(false);
  const [input, setInput] = useState('');
  const [liveAlerts, setLiveAlerts] = useState<RadarAlert[]>([]);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [radarTfDropdownOpen, setRadarTfDropdownOpen] = useState(false);
  const radarTfDropdownRef = useRef<HTMLDivElement>(null);

  const symbols = useRadarStore((s) => s.symbols);
  const scans = useRadarStore((s) => s.scans);
  const timeframe = useRadarStore((s) => s.timeframe);
  const vizTarget = useRadarStore((s) => s.vizTarget);
  const vizEnabled = useRadarStore((s) => s.vizEnabled);

  const addSymbol = useRadarStore((s) => s.addSymbol);
  const removeSymbol = useRadarStore((s) => s.removeSymbol);
  const setTimeframe = useRadarStore((s) => s.setTimeframe);
  const scanOne = useRadarStore((s) => s.scanOne);
  const scanAll = useRadarStore((s) => s.scanAll);
  const setVizTarget = useRadarStore((s) => s.setVizTarget);
  const toggleViz = useRadarStore((s) => s.toggleViz);

  // ── Hydrate + auto-scan on mount ─────────────────────────────────
  useEffect(() => {
    const store = useRadarStore.getState();
    void store.hydrate();
    store.startAutoScan();
    return () => store.stopAutoScan();
  }, []);

  // ── Close on outside click ───────────────────────────────────────
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
      if (radarTfDropdownRef.current && !radarTfDropdownRef.current.contains(e.target as Node)) {
        setRadarTfDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  // ── Live radar-alert subscription (background worker) ────────────
  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    (async () => {
      try {
        if (cancelled) return;
        const u = await bridgeListen<RadarAlert>('radar-alert', (event) => {
          if (cancelled) return;
          setLiveAlerts((prev) => [event.payload, ...prev].slice(0, 30));
        });
        if (cancelled) u();
        else unlisten = u;
      } catch (err) {
        console.warn('[Radar] alert listener unavailable:', err);
      }
    })();
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []);

  // ── Add symbol handler ───────────────────────────────────────────
  const handleAdd = useCallback(() => {
    const sym = input.trim().toUpperCase();
    if (!sym) return;
    addSymbol(sym);
    setInput('');
  }, [input, addSymbol]);

  // ── Visualize a detection on the chart ───────────────────────────
  const visualizePattern = useCallback(
    (symbol: string, p: LocatedPattern) => {
      useTradeStore.getState().setSelectedSymbol(symbol);
      useTradeStore.getState().setActiveTimeframe(timeframe);
      const target: RadarVizTarget = { symbol, timeframe, kind: 'pattern', pattern: p };
      setVizTarget(target);
    },
    [timeframe, setVizTarget]
  );

  const visualizeStrategy = useCallback(
    (symbol: string, s: LocatedStrategy) => {
      useTradeStore.getState().setSelectedSymbol(symbol);
      useTradeStore.getState().setActiveTimeframe(timeframe);
      const target: RadarVizTarget = { symbol, timeframe, kind: 'strategy', strategy: s };
      setVizTarget(target);
    },
    [timeframe, setVizTarget]
  );

  // ── Aggregate counts for the trigger badge ───────────────────────
  const totalDetections = useMemo(() => {
    return Object.values(scans).reduce((acc, s) => {
      if (!s.scan) return acc;
      return acc + s.scan.patterns.length + s.scan.strategies.length;
    }, 0);
  }, [scans]);

  const anyLoading = useMemo(
    () => Object.values(scans).some((s) => s.loading),
    [scans]
  );

  return (
    <div className="relative" ref={dropdownRef}>
      {/* ── Trigger Button ── */}
      <button
        type="button"
        id="quant-radar-navbar-btn"
        onClick={() => setIsOpen((p) => !p)}
        className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-semibold shadow-sm transition-all duration-200 select-none ${
          isOpen
            ? 'bg-emerald-500/15 border-emerald-500/30 text-emerald-600 dark:text-emerald-400'
            : 'bg-card border-border-default text-text-secondary hover:bg-elevated hover:text-text-primary'
        }`}
        title="Open Quant Radar"
      >
        <div className="relative flex items-center">
          <Radar
            size={13}
            className={`${anyLoading ? 'animate-spin text-emerald-600 dark:text-emerald-400' : symbols.length > 0 ? 'text-emerald-600 dark:text-emerald-400 animate-pulse' : 'text-text-secondary'}`}
          />
        </div>
        <span>Radar</span>
        {totalDetections > 0 && (
          <span className="rounded-full bg-emerald-500/10 dark:bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-bold text-emerald-600 dark:text-emerald-400 tabular-nums">
            {totalDetections}
          </span>
        )}
      </button>

      {/* ── Dropdown Panel ── */}
      {isOpen && (
        <div className="absolute right-0 top-full mt-2 z-[999] flex flex-col w-[400px] max-h-[560px] rounded-none border border-border-default bg-surface/95 backdrop-blur-xl shadow-2xl">
          {/* ── Header ── */}
          <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-border-default bg-surface/80 rounded-none">
            <div className="flex items-center gap-2">
              <Radar size={15} className="text-emerald-400" />
              <span className="text-xs font-bold tracking-wide text-text-primary uppercase">
                Quant Radar
              </span>
              <span className="rounded-none bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-bold text-emerald-400 tabular-nums">
                {symbols.length} symbol{symbols.length !== 1 ? 's' : ''}
              </span>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={toggleViz}
                className={`rounded p-1 transition-colors ${vizEnabled ? 'text-emerald-400 hover:bg-emerald-500/10' : 'text-text-muted hover:bg-elevated'}`}
                title={vizEnabled ? 'On-chart visualization ON' : 'On-chart visualization OFF'}
              >
                {vizEnabled ? <Eye size={12} /> : <EyeOff size={12} />}
              </button>
              <button
                type="button"
                onClick={() => void scanAll()}
                className="rounded p-1 text-text-muted transition-colors hover:bg-elevated hover:text-text-primary"
                title="Rescan all symbols"
              >
                <RefreshCw size={12} className={anyLoading ? 'animate-spin' : ''} />
              </button>
              <button
                type="button"
                onClick={() => setIsOpen(false)}
                className="rounded p-1 text-text-muted transition-colors hover:bg-elevated hover:text-text-primary"
                title="Close"
              >
                <X size={12} />
              </button>
            </div>
          </div>

          {/* ── Add Symbol + Timeframe Picker ── */}
          <div className="flex items-center gap-2 px-3 py-2 border-b border-border-default bg-surface/60">
            <div className="flex flex-1 items-center gap-1 rounded-md border border-border-default bg-card px-2 py-1">
              <Plus size={12} className="text-text-muted" />
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleAdd(); }}
                placeholder="Add symbol (e.g. RELIANCE)"
                className="w-full bg-transparent text-xs text-text-primary placeholder:text-text-muted/60 outline-none uppercase"
              />
              <button
                type="button"
                onClick={handleAdd}
                className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-bold text-emerald-400 hover:bg-emerald-500/25"
              >
                ADD
              </button>
            </div>
            {/* Custom Radar Timeframe Dropdown */}
            <div className="relative" ref={radarTfDropdownRef}>
              <button
                type="button"
                onClick={() => setRadarTfDropdownOpen(!radarTfDropdownOpen)}
                className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[10px] font-semibold transition-all border ${
                  radarTfDropdownOpen
                    ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40 shadow-[0_0_10px_rgba(16,185,129,0.12)]'
                    : 'bg-card text-text-secondary hover:bg-elevated border-border-default hover:text-text-primary'
                }`}
                title="Radar timeframe"
              >
                <Clock size={11} className={radarTfDropdownOpen ? 'text-emerald-400 animate-pulse' : 'text-text-muted'} />
                <span>{timeframe}</span>
                <ChevronDown size={11} className={`transition-transform duration-200 ${radarTfDropdownOpen ? 'rotate-180' : ''}`} />
              </button>

              {radarTfDropdownOpen && (
                <div className="absolute right-0 top-full z-50 mt-1 w-64 rounded-xl border border-border-default bg-surface/90 backdrop-blur-xl shadow-2xl p-3 scrollbar-none animate-in fade-in slide-in-from-top-2 duration-200">
                  {TIMEFRAME_GROUPS.map((group) => {
                    const isDays = group.label === 'Days';
                    return (
                      <div key={group.label} className="mb-3 last:mb-0">
                        <div className="px-1 pb-1 text-[10px] font-bold uppercase tracking-wider text-text-muted/80 mb-1.5 border-b border-border-default/20">
                          {group.label}
                        </div>
                        <div className={`grid ${isDays ? 'grid-cols-3' : 'grid-cols-2'} gap-1`}>
                          {group.items.map((item) => {
                            const isActive = timeframe === item.tf;
                            return (
                              <button
                                key={item.tf}
                                type="button"
                                onClick={() => {
                                  setTimeframe(item.tf as Timeframe);
                                  setRadarTfDropdownOpen(false);
                                }}
                                className={`flex items-center justify-between rounded-md px-2 py-1.5 text-[11px] transition-all duration-150 border ${
                                  isActive
                                    ? 'bg-emerald-500/10 text-emerald-400 font-bold border-emerald-500/30 shadow-[0_0_8px_rgba(16,185,129,0.08)]'
                                    : 'bg-card/40 text-text-secondary hover:bg-elevated hover:text-text-primary border-transparent hover:border-border-default'
                                }`}
                              >
                                <span>{item.display}</span>
                                {isActive && <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse shadow-[0_0_6px_rgba(52,211,153,0.8)]" />}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* ── Symbol Cards (located detections) ── */}
          <div className="flex-1 overflow-y-auto scrollbar-none">
            {symbols.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-2 py-10 text-text-muted">
                <Radar size={28} className="opacity-30" />
                <p className="text-xs">No symbols on your radar yet</p>
                <p className="text-[10px] opacity-60">Add a symbol above to track patterns & strategies</p>
              </div>
            ) : (
              symbols.map((sym) => (
                <SymbolCard
                  key={sym}
                  symbol={sym}
                  state={scans[sym]}
                  timeframe={timeframe}
                  vizTarget={vizTarget}
                  onRemove={() => removeSymbol(sym)}
                  onRescan={() => void scanOne(sym)}
                  onSelect={() => useTradeStore.getState().setSelectedSymbol(sym)}
                  onVizPattern={(p) => visualizePattern(sym, p)}
                  onVizStrategy={(s) => visualizeStrategy(sym, s)}
                />
              ))
            )}
          </div>

          {/* ── Live alerts footer (background worker) ── */}
          {liveAlerts.length > 0 && (
            <div className="border-t border-border-default bg-surface/70 px-3 py-1.5">
              <div className="flex items-center gap-1.5 text-[9px] font-bold uppercase tracking-wide text-amber-400">
                <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
                {liveAlerts.length} live alert{liveAlerts.length !== 1 ? 's' : ''}
                <span className="font-normal text-text-muted normal-case">
                  · latest {liveAlerts[0].symbol} {timeAgo(liveAlerts[0].timestamp_ms)} ago
                </span>
              </div>
            </div>
          )}

          {/* ── Status bar ── */}
          <div className="flex items-center justify-between px-3 py-1.5 border-t border-border-default bg-surface/60 text-[9px] text-text-muted rounded-b-xl">
            <span className="flex items-center gap-1">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
              {/* The scan runs in quant-core via tool-server, so this is always
                  the real engine — it used to read "Open in desktop app" off
                  isTauri(), which is no longer a distinction that exists. */}
              Scan engine live
            </span>
            <span className="tabular-nums">{timeframe} timeframe</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Per-symbol card ─────────────────────────────────────────────────────

interface SymbolCardProps {
  symbol: string;
  state: RadarSymbolState | undefined;
  timeframe: Timeframe;
  vizTarget: RadarVizTarget | null;
  onRemove: () => void;
  onRescan: () => void;
  onSelect: () => void;
  onVizPattern: (p: LocatedPattern) => void;
  onVizStrategy: (s: LocatedStrategy) => void;
}

function SymbolCard({
  symbol,
  state,
  timeframe,
  vizTarget,
  onRemove,
  onRescan,
  onSelect,
  onVizPattern,
  onVizStrategy,
}: SymbolCardProps) {
  const scan = state?.scan ?? null;
  const loading = state?.loading ?? false;
  const error = state?.error ?? null;
  const trend = scan?.trend_score ?? 0;
  const bullish = trend >= 0;

  const isVizPattern = (p: LocatedPattern) =>
    vizTarget?.kind === 'pattern' &&
    vizTarget.symbol === symbol &&
    vizTarget.pattern?.time === p.time &&
    vizTarget.pattern?.name === p.name;

  const isVizStrategy = (s: LocatedStrategy) =>
    vizTarget?.kind === 'strategy' &&
    vizTarget.symbol === symbol &&
    vizTarget.strategy?.time === s.time &&
    vizTarget.strategy?.name === s.name;

  return (
    <div className="border-b border-border-default/50 px-3 py-2.5">
      {/* Row: symbol + trend + actions */}
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          onClick={onSelect}
          className="flex items-center gap-2 text-left group"
          title="Load on chart"
        >
          <span className="text-xs font-bold text-text-primary group-hover:text-emerald-400 transition-colors">
            {symbol}
          </span>
          {scan && (
            <span className={`flex items-center gap-0.5 text-[9px] font-semibold ${bullish ? 'text-emerald-400' : 'text-red-400'}`}>
              {bullish ? <TrendingUp size={9} /> : <TrendingDown size={9} />}
              {trend > 0 ? '+' : ''}{trend}
            </span>
          )}
          {scan && (
            <span className="text-[9px] text-text-muted">{scan.momentum_state} · {scan.volatility_state}</span>
          )}
        </button>
        <div className="flex items-center gap-1">
          {loading && <Loader2 size={11} className="animate-spin text-emerald-400" />}
          <button
            type="button"
            onClick={onRescan}
            className="rounded p-0.5 text-text-muted hover:text-text-primary hover:bg-elevated transition-colors"
            title="Rescan"
          >
            <RefreshCw size={10} />
          </button>
          <button
            type="button"
            onClick={onRemove}
            className="rounded p-0.5 text-text-muted hover:text-red-400 hover:bg-elevated transition-colors"
            title="Remove from radar"
          >
            <X size={11} />
          </button>
        </div>
      </div>

      {/* Detections */}
      {error ? (
        <p className="mt-1 text-[10px] text-amber-400/70">{error}</p>
      ) : scan && (scan.patterns.length > 0 || scan.strategies.length > 0) ? (
        <div className="mt-1.5 flex flex-col gap-1.5">
          {/* Strategies */}
          {scan.strategies.length > 0 && (
            <div className="flex flex-wrap items-center gap-1">
              <Crosshair size={9} className="text-text-muted" />
              {scan.strategies.map((s) => {
                const c = biasChip(s.bias);
                const active = isVizStrategy(s);
                return (
                  <button
                    key={`s-${s.name}-${s.time}`}
                    type="button"
                    onClick={() => onVizStrategy(s)}
                    className="rounded border px-1.5 py-0.5 text-[9px] font-semibold transition-all"
                    style={{
                      color: c.color,
                      background: active ? c.color + '33' : c.bg,
                      borderColor: active ? c.color : c.border,
                      boxShadow: active ? `0 0 8px ${c.color}55` : 'none',
                    }}
                    title={`Visualize ${s.name} @ ${timeframe} (candle #${s.candle_index})`}
                  >
                    {s.name}
                  </button>
                );
              })}
            </div>
          )}
          {/* Patterns */}
          {scan.patterns.length > 0 && (
            <div className="flex flex-wrap items-center gap-1">
              <CandlestickChart size={9} className="text-text-muted" />
              {scan.patterns.map((p) => {
                const c = biasChip(p.bias);
                const active = isVizPattern(p);
                return (
                  <button
                    key={`p-${p.name}-${p.time}`}
                    type="button"
                    onClick={() => onVizPattern(p)}
                    className="rounded border px-1.5 py-0.5 text-[9px] font-medium transition-all"
                    style={{
                      color: c.color,
                      background: active ? c.color + '33' : c.bg,
                      borderColor: active ? c.color : c.border,
                      boxShadow: active ? `0 0 8px ${c.color}55` : 'none',
                    }}
                    title={`Visualize ${p.name} @ ${timeframe} (candle #${p.candle_index})`}
                  >
                    {p.name}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      ) : scan && !loading ? (
        <p className="mt-1 text-[10px] text-text-muted/60">No patterns or strategies on {timeframe}</p>
      ) : null}
    </div>
  );
}
