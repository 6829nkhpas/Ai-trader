'use client';

// QuantRadar.tsx — Live Market Scanner Overlay
//
// Listens for `radar-alert` Tauri IPC events emitted by the Rust background
// worker and renders them as a sleek bottom-right notification panel.
// Clicking an alert instantly routes the main chart to that symbol.

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import {
  Radar,
  X,
  ChevronDown,
  ChevronUp,
  TrendingUp,
  TrendingDown,
  Zap,
  AlertTriangle,
  Volume2,
  VolumeX,
} from 'lucide-react';

// ── Types ───────────────────────────────────────────────────────────────

interface RadarAlert {
  symbol: string;
  trigger_reason: string;
  trend_score: number;
  momentum: string;
  volatility: string;
  active_strategies: string[];
  active_patterns: string[];
  timestamp_ms: number;
  severity: string; // "HIGH" | "MEDIUM" | "LOW"
}

// ── Helpers ─────────────────────────────────────────────────────────────

const isTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

function timeAgo(timestampMs: number): string {
  const diff = Date.now() - timestampMs;
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  return `${Math.floor(diff / 3_600_000)}h ago`;
}

function severityConfig(severity: string) {
  switch (severity) {
    case 'HIGH':
      return {
        border: 'border-red-500/40',
        bg: 'bg-red-500/5',
        glow: 'shadow-[0_0_12px_rgba(239,68,68,0.15)]',
        badge: 'bg-red-500/15 text-red-400 border-red-500/30',
        icon: '🚨',
        pulse: true,
      };
    case 'MEDIUM':
      return {
        border: 'border-amber-500/40',
        bg: 'bg-amber-500/5',
        glow: 'shadow-[0_0_8px_rgba(245,158,11,0.1)]',
        badge: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
        icon: '⚡',
        pulse: false,
      };
    default:
      return {
        border: 'border-emerald-500/30',
        bg: 'bg-emerald-500/5',
        glow: '',
        badge: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
        icon: '📊',
        pulse: false,
      };
  }
}

// ── Component ───────────────────────────────────────────────────────────

const MAX_ALERTS = 50;

export default function QuantRadar() {
  const [alerts, setAlerts] = useState<RadarAlert[]>([]);
  const [isExpanded, setIsExpanded] = useState(true);
  const [isMinimized, setIsMinimized] = useState(false);
  const [soundEnabled, setSoundEnabled] = useState(true);
  const [unreadCount, setUnreadCount] = useState(0);
  // Filter: true = only show current symbol's alerts; false = show all
  const [filterToSymbol, setFilterToSymbol] = useState(true);
  const alertListRef = useRef<HTMLDivElement>(null);

  const activeSymbol = useTradeStore((s) => s.selectedSymbol);

  // ── Tauri Event Subscription ─────────────────────────────────────
  useEffect(() => {
    if (!isTauri()) return;

    let cancelled = false;
    let unlisten: (() => void) | undefined;

    (async () => {
      try {
        const { listen } = await import('@tauri-apps/api/event');
        if (cancelled) return;

        const u = await listen<RadarAlert>('radar-alert', (event) => {
          if (cancelled) return;
          const alert = event.payload;

          setAlerts((prev) => {
            // Deduplicate: don't add if same symbol + reason within last 5 minutes
            const isDuplicate = prev.some(
              (a) =>
                a.symbol === alert.symbol &&
                a.trigger_reason === alert.trigger_reason &&
                alert.timestamp_ms - a.timestamp_ms < 5 * 60_000
            );
            if (isDuplicate) return prev;

            const updated = [alert, ...prev].slice(0, MAX_ALERTS);
            return updated;
          });

          setUnreadCount((c) => c + 1);

          // Play notification sound for HIGH severity
          if (soundEnabled && alert.severity === 'HIGH') {
            try {
              const ctx = new AudioContext();
              const osc = ctx.createOscillator();
              const gain = ctx.createGain();
              osc.connect(gain);
              gain.connect(ctx.destination);
              osc.frequency.value = 880;
              osc.type = 'sine';
              gain.gain.value = 0.08;
              gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
              osc.start(ctx.currentTime);
              osc.stop(ctx.currentTime + 0.3);
            } catch {
              // AudioContext not available — ignore
            }
          }
        });

        if (cancelled) {
          u();
        } else {
          unlisten = u;
        }
      } catch {
        // Not in Tauri context
      }
    })();

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [soundEnabled]);

  // ── Alert Click Handler ──────────────────────────────────────────
  const handleAlertClick = useCallback((alert: RadarAlert) => {
    useTradeStore.getState().setSelectedSymbol(alert.symbol);
    setUnreadCount(0);
  }, []);

  // ── Derived: filtered alerts ─────────────────────────────────────
  const displayedAlerts = filterToSymbol && activeSymbol
    ? alerts.filter((a) => a.symbol.toUpperCase() === activeSymbol.toUpperCase())
    : alerts;

  const filteredUnread = filterToSymbol && activeSymbol
    ? alerts.filter((a) =>
        a.symbol.toUpperCase() === activeSymbol.toUpperCase() &&
        Date.now() - a.timestamp_ms < 60_000
      ).length
    : unreadCount;

  // ── Dismiss Alert ────────────────────────────────────────────────
  const dismissAlert = useCallback((index: number, e: React.MouseEvent) => {
    e.stopPropagation();
    setAlerts((prev) => prev.filter((_, i) => i !== index));
  }, []);

  // ── Clear All ────────────────────────────────────────────────────
  const clearAll = useCallback(() => {
    setAlerts([]);
    setUnreadCount(0);
  }, []);

  // Reset unread when expanded
  useEffect(() => {
    if (isExpanded && !isMinimized) {
      setUnreadCount(0);
    }
  }, [isExpanded, isMinimized]);

  // ── Minimized State (just the floating icon) ─────────────────────
  if (isMinimized) {
    return (
      <button
        type="button"
        onClick={() => setIsMinimized(false)}
        className="fixed bottom-4 right-4 z-50 flex items-center gap-2 rounded-full border border-border-default bg-surface px-4 py-2.5 text-xs font-semibold text-text-primary shadow-lg transition-all duration-300 hover:bg-elevated hover:scale-105 active:scale-95 group"
        title="Open Quant Radar"
      >
        <div className="relative">
          <Radar size={16} className="text-emerald-400 group-hover:text-emerald-300 transition-colors" />
          {unreadCount > 0 && (
            <span className="absolute -top-1.5 -right-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-500 px-1 text-[9px] font-bold text-white animate-pulse">
              {unreadCount > 9 ? '9+' : unreadCount}
            </span>
          )}
        </div>
        <span className="hidden sm:inline">Radar</span>
        {alerts.length > 0 && (
          <span className="rounded-full bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-bold text-emerald-400">
            {alerts.length}
          </span>
        )}
      </button>
    );
  }

  // ── Full Panel ───────────────────────────────────────────────────
  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col w-[360px] max-h-[480px] rounded-xl border border-border-default bg-surface/95 backdrop-blur-xl shadow-2xl overflow-hidden transition-all duration-300">
      {/* ── Header ─────────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-border-default bg-surface/80">
        <div className="flex items-center gap-2">
          <div className="relative">
            <Radar size={15} className="text-emerald-400" />
            {alerts.length > 0 && (
              <span className="absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-emerald-400 animate-ping" />
            )}
          </div>
          <span className="text-xs font-bold tracking-wide text-text-primary uppercase">Quant Radar</span>
          {/* Symbol filter badge */}
          <button
            type="button"
            onClick={() => setFilterToSymbol((v) => !v)}
            className={`rounded px-1.5 py-0.5 text-[9px] font-bold border transition-colors ${
              filterToSymbol
                ? 'bg-blue-500/15 text-blue-400 border-blue-500/30 hover:bg-blue-500/25'
                : 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20 hover:bg-emerald-500/20'
            }`}
            title={filterToSymbol ? 'Showing current symbol only — click to show all' : 'Showing all symbols — click to filter to current symbol'}
          >
            {filterToSymbol ? activeSymbol || 'SYMBOL' : 'ALL'}
          </button>
          {displayedAlerts.length > 0 && (
            <span className="rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-bold text-emerald-400 tabular-nums">
              {displayedAlerts.length} alert{displayedAlerts.length !== 1 ? 's' : ''}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1">
          {/* Sound toggle */}
          <button
            type="button"
            onClick={() => setSoundEnabled(!soundEnabled)}
            className={`rounded p-1 transition-colors ${soundEnabled ? 'text-emerald-400 hover:bg-emerald-500/10' : 'text-text-muted hover:bg-elevated'}`}
            title={soundEnabled ? 'Mute alerts' : 'Unmute alerts'}
          >
            {soundEnabled ? <Volume2 size={12} /> : <VolumeX size={12} />}
          </button>

          {/* Clear all */}
          {alerts.length > 0 && (
            <button
              type="button"
              onClick={clearAll}
              className="rounded p-1 text-text-muted transition-colors hover:bg-elevated hover:text-red-400"
              title="Clear all alerts"
            >
              <AlertTriangle size={12} />
            </button>
          )}

          {/* Collapse/Expand */}
          <button
            type="button"
            onClick={() => setIsExpanded(!isExpanded)}
            className="rounded p-1 text-text-muted transition-colors hover:bg-elevated hover:text-text-primary"
            title={isExpanded ? 'Collapse' : 'Expand'}
          >
            {isExpanded ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
          </button>

          {/* Minimize */}
          <button
            type="button"
            onClick={() => setIsMinimized(true)}
            className="rounded p-1 text-text-muted transition-colors hover:bg-elevated hover:text-text-primary"
            title="Minimize"
          >
            <X size={12} />
          </button>
        </div>
      </div>

      {/* ── Alert List ─────────────────────────────────────────── */}
      {isExpanded && (
        <div
          ref={alertListRef}
          className="flex-1 overflow-y-auto scrollbar-thin"
          style={{ maxHeight: '400px' }}
        >
          {displayedAlerts.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-8 text-text-muted">
              <Radar size={28} className="opacity-30" />
              <p className="text-xs">
                {filterToSymbol
                  ? `No alerts yet for ${activeSymbol || 'selected symbol'}`
                  : 'Scanning all F&O instruments…'}
              </p>
              <p className="text-[10px] opacity-50">Alerts will appear when setups are detected</p>
            </div>
          ) : (
            <div className="flex flex-col">
              {displayedAlerts.map((alert, idx) => {
                const config = severityConfig(alert.severity);
                const isBullish = alert.trend_score > 0;

                return (
                  <div
                    key={`${alert.symbol}-${alert.timestamp_ms}-${idx}`}
                    role="button"
                    tabIndex={0}
                    onClick={() => handleAlertClick(alert)}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleAlertClick(alert); }}
                    className={`group flex flex-col gap-1 px-3 py-2.5 text-left transition-all duration-200 border-b border-border-default/50 hover:bg-elevated/50 cursor-pointer ${config.bg} ${config.glow}`}
                  >
                    {/* Row 1: Symbol + severity + time */}
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className="text-sm">{config.icon}</span>
                        <span className="text-xs font-bold text-text-primary group-hover:text-emerald-400 transition-colors">
                          {alert.symbol}
                        </span>
                        <span className={`rounded-md border px-1.5 py-0.5 text-[9px] font-bold uppercase ${config.badge}`}>
                          {alert.severity}
                        </span>
                        {config.pulse && (
                          <span className="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse" />
                        )}
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[9px] text-text-muted tabular-nums">{timeAgo(alert.timestamp_ms)}</span>
                        <button
                          type="button"
                          onClick={(e) => dismissAlert(idx, e)}
                          className="opacity-0 group-hover:opacity-100 rounded p-0.5 text-text-muted hover:text-red-400 transition-all"
                          title="Dismiss"
                        >
                          <X size={10} />
                        </button>
                      </div>
                    </div>

                    {/* Row 2: Trigger reason */}
                    <p className="text-[11px] font-medium text-text-secondary leading-snug">
                      {alert.trigger_reason}
                    </p>

                    {/* Row 3: Micro stats */}
                    <div className="flex items-center gap-3 text-[9px] text-text-muted">
                      <span className={`flex items-center gap-0.5 font-semibold ${isBullish ? 'text-emerald-400' : 'text-red-400'}`}>
                        {isBullish ? <TrendingUp size={9} /> : <TrendingDown size={9} />}
                        {alert.trend_score > 0 ? '+' : ''}{alert.trend_score}
                      </span>
                      <span>{alert.momentum}</span>
                      <span>{alert.volatility}</span>
                      {alert.active_patterns.length > 0 && (
                        <span className="flex items-center gap-0.5">
                          <Zap size={8} />
                          {alert.active_patterns.length} pattern{alert.active_patterns.length !== 1 ? 's' : ''}
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ── Footer Status Bar ──────────────────────────────────── */}
      <div className="flex items-center justify-between px-3 py-1.5 border-t border-border-default bg-surface/60 text-[9px] text-text-muted">
        <span className="flex items-center gap-1">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
          Live scanning
        </span>
        <span className="tabular-nums">{DEFAULT_SYMBOL_COUNT} instruments</span>
      </div>
    </div>
  );
}

// Reference the same count as the Rust backend
const DEFAULT_SYMBOL_COUNT = 50;
