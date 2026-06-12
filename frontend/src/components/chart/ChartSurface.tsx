'use client';

// Feature: professional-charting-suite
//
// ChartSurface — the shell that hosts the engine-driven price renderer and the
// premium chart UX (Requirement 12).
//
// It mounts:
//   - the persistently-visible, interactive controls (Requirement 12.1):
//       · a chart-type selector (the 11 CHART_TYPES),
//       · an indicator-manager entry point (toggles IndicatorManagerPanel),
//       · the drawing toolbar (ChartToolsBar),
//       · the chart-mode toggle (Standard / Volume Profile / Footprint),
//       · the Timeframe selector,
//       · a strategy entry point (select + configure a strategy);
//   - overlay settings dialogs for chart-type and strategy parameters that keep
//     the underlying chart visible and do not navigate away (Requirements 12.2,
//     12.3);
//   - a fullscreen control that uses the native Fullscreen API and falls back to
//     the in-app maximized overlay state when the request throws or is
//     unsupported, surfacing a "fullscreen unavailable" indication
//     (Requirements 12.4, 12.5).
//
// Local UI state owns which dialog/panel is open and the selected chart type,
// chart-type params, applied strategy, and strategy params; the selections are
// passed straight down to {@link ChartRenderer}. Parameter edits are validated
// by the pure engines so invalid values are rejected and the last valid values
// retained (Requirements 1.6, 8.6).

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  CandlestickChart,
  LineChart as LineChartIcon,
  Settings2,
  Maximize2,
  Minimize2,
  Clock,
  Activity,
  ChevronDown,
  AlertTriangle,
  X,
} from 'lucide-react';

import ChartRenderer from './ChartRenderer';
import ChartToolsBar from './ChartToolsBar';
import ChartModeToggle from './ChartHeader';
import IndicatorManagerPanel from './IndicatorManagerPanel';
import FootprintChart from './FootprintChart';

import { useTradeStore } from '../../store/useTradeStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import {
  CHART_TYPES,
  CHART_TYPE_PARAM_SPEC,
  CHART_TYPE_PARAM_DEFAULTS,
  validateChartTypeParams,
  listStrategies,
  getStrategy,
  validateParams,
  type ChartType,
  type ChartTypeParams,
  type StrategyDef,
  type StrategyParams,
} from '../../charting/engines';
import type { NumericRange } from '../../charting/types';
import {
  planFullscreenToggle,
  fullscreenFailureFallback,
} from './fullscreenFallback';
import { TIMEFRAME_GROUPS, type Timeframe } from '../../utils/chartTypes';
import type { ChartTimeframe } from '../../store/useTradeStore';

// ── Display labels ────────────────────────────────────────────────────────

const CHART_TYPE_LABELS: Record<ChartType, string> = {
  candlestick: 'Candlestick',
  'hollow-candle': 'Hollow Candle',
  'ohlc-bar': 'OHLC Bar',
  line: 'Line',
  area: 'Area',
  baseline: 'Baseline',
  'heikin-ashi': 'Heikin Ashi',
  renko: 'Renko',
  kagi: 'Kagi',
  'point-figure': 'Point & Figure',
  'line-break': 'Line Break',
};

/** Friendly labels for the numeric parameters surfaced in settings dialogs. */
const PARAM_LABELS: Record<string, string> = {
  renkoBoxSize: 'Box Size',
  pfBoxSize: 'Box Size',
  pfReversal: 'Reversal (boxes)',
  kagiReversal: 'Reversal',
  lineBreakCount: 'Line Count',
  fast: 'Fast Period',
  slow: 'Slow Period',
  period: 'Period',
  oversold: 'Oversold',
  overbought: 'Overbought',
  lookback: 'Lookback',
};

const paramLabel = (key: string) => PARAM_LABELS[key] ?? key;

/** Which transient overlay (if any) is currently open. */
type OpenDialog = 'none' | 'chart-type' | 'strategy';

export interface ChartSurfaceProps {
  className?: string;
  /** Initial chart type; defaults to candlestick (Requirement 1.4 fallback). */
  initialChartType?: ChartType;
  /** Initial applied strategy id, or null when none is applied. */
  initialStrategyId?: string | null;
}

/**
 * The professional chart surface shell. Composes the price renderer with the
 * persistent control bar, the indicator manager, settings dialogs, and the
 * fullscreen control.
 */
export default function ChartSurface({
  className = '',
  initialChartType = 'candlestick',
  initialStrategyId = null,
}: ChartSurfaceProps) {
  // ── Selection state (passed down to ChartRenderer) ────────────────────
  const [chartType, setChartType] = useState<ChartType>(initialChartType);
  const [chartTypeParams, setChartTypeParams] = useState<ChartTypeParams>({});
  const [activeStrategyId, setActiveStrategyId] = useState<string | null>(
    initialStrategyId,
  );
  const [strategyParams, setStrategyParams] = useState<StrategyParams>({});

  // ── Transient UI state: which overlay/panel is open ───────────────────
  const [openDialog, setOpenDialog] = useState<OpenDialog>('none');
  const [showIndicatorManager, setShowIndicatorManager] = useState(false);

  // ── Chart-mode + timeframe (shared store state) ───────────────────────
  const chartMode = useTradeStore((s) => s.chartMode);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);
  const setActiveTimeframe = useTradeStore((s) => s.setActiveTimeframe);

  // ── Fullscreen state + failure fallback (Requirements 12.4, 12.5) ─────
  const isFullscreen = useChartUIStore((s) => s.isFullscreen);
  const setIsFullscreen = useChartUIStore((s) => s.setIsFullscreen);
  const toggleFullscreen = useChartUIStore((s) => s.toggleFullscreen);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const [fullscreenUnavailable, setFullscreenUnavailable] = useState(false);

  // Keep the in-app `isFullscreen` flag in sync with the native fullscreen
  // element so exiting via the browser (Esc) restores the in-app layout.
  useEffect(() => {
    const onChange = () => {
      if (typeof document === 'undefined') return;
      if (document.fullscreenElement) {
        setIsFullscreen(true);
      } else if (!fullscreenUnavailable) {
        // Only clear when we are not in the in-app fallback maximized state.
        setIsFullscreen(false);
      }
    };
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, [setIsFullscreen, fullscreenUnavailable]);

  const handleToggleFullscreen = useCallback(async () => {
    const el = surfaceRef.current;
    const hasDocument = typeof document !== 'undefined';
    const isNativeFullscreen = hasDocument && !!document.fullscreenElement;
    const action = planFullscreenToggle({
      isNativeFullscreen,
      inAppFallbackActive: fullscreenUnavailable && isFullscreen,
      canRequestFullscreen: !!el && typeof el?.requestFullscreen === 'function',
    });
    try {
      switch (action) {
        case 'exit-native':
          await document.exitFullscreen();
          setFullscreenUnavailable(false);
          return;
        case 'exit-fallback':
          setFullscreenUnavailable(false);
          toggleFullscreen();
          return;
        case 'request-native':
          await el!.requestFullscreen();
          setFullscreenUnavailable(false);
          return;
        default:
          // No Fullscreen API in this environment.
          throw new Error('Fullscreen API unavailable');
      }
    } catch {
      // Requirement 12.5: request failed/unsupported → fall back to the in-app
      // maximized overlay state and indicate that native fullscreen is
      // unavailable. The chart stays interactive, just maximized in-app.
      const fallback = fullscreenFailureFallback(isFullscreen);
      setFullscreenUnavailable(fallback.fullscreenUnavailable);
      if (fallback.shouldMaximize) toggleFullscreen();
    }
  }, [fullscreenUnavailable, isFullscreen, toggleFullscreen]);

  // ── Dialog handlers ────────────────────────────────────────────────────
  const closeDialog = useCallback(() => setOpenDialog('none'), []);

  const handleSelectChartType = useCallback((next: ChartType) => {
    setChartType(next);
    // Reset params when switching to a non-parametric type so stale params do
    // not leak into the renderer.
    if (Object.keys(CHART_TYPE_PARAM_SPEC[next]).length === 0) {
      setChartTypeParams({});
    }
  }, []);

  const handleApplyChartTypeParams = useCallback(
    (next: ChartTypeParams) => {
      setChartTypeParams(next);
      closeDialog();
    },
    [closeDialog],
  );

  const handleSelectStrategy = useCallback((id: string | null) => {
    setActiveStrategyId(id);
    setStrategyParams({});
  }, []);

  const handleApplyStrategyParams = useCallback(
    (next: StrategyParams) => {
      setStrategyParams(next);
      closeDialog();
    },
    [closeDialog],
  );

  const chartTypeHasParams =
    Object.keys(CHART_TYPE_PARAM_SPEC[chartType]).length > 0;

  // ── Derived render flags from chart mode (Requirement 12.1 toggle) ────
  const showVolumeProfile = chartMode === 'VOLUME_PROFILE';
  const isFootprint = chartMode === 'FOOTPRINT';

  const effectiveTimeframe = (activeTimeframe as Timeframe) ?? '1m';

  const containerClass = isFullscreen
    ? 'fixed inset-0 z-[60] bg-background'
    : 'relative h-full w-full';

  return (
    <div
      ref={surfaceRef}
      className={`flex flex-col overflow-hidden bg-background ${containerClass} ${className}`}
    >
      {/* ── Persistent control bar (Requirement 12.1) ──────────────────── */}
      <div className="flex shrink-0 items-center gap-2 border-b border-border-default bg-surface/60 px-2 py-1.5">
        {/* Chart-type selector */}
        <ChartTypeSelector value={chartType} onSelect={handleSelectChartType} />

        {/* Chart-type settings entry (only for parametric types) */}
        {chartTypeHasParams && (
          <button
            type="button"
            onClick={() => setOpenDialog('chart-type')}
            aria-label="Chart type settings"
            className="flex h-7 items-center gap-1 rounded-md border border-border-default bg-surface px-2 text-[11px] text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
          >
            <Settings2 size={13} />
          </button>
        )}

        {/* Indicator-manager entry point */}
        <button
          type="button"
          onClick={() => setShowIndicatorManager((v) => !v)}
          aria-label="Indicators"
          className={`flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-[11px] font-semibold transition-colors ${
            showIndicatorManager
              ? 'border-primary/40 bg-primary/10 text-primary'
              : 'border-border-default bg-surface text-text-secondary hover:bg-elevated hover:text-text-primary'
          }`}
        >
          <LineChartIcon size={13} />
          <span>Indicators</span>
        </button>

        {/* Strategy entry point */}
        <StrategySelector
          activeStrategyId={activeStrategyId}
          onSelect={handleSelectStrategy}
          onOpenSettings={() => setOpenDialog('strategy')}
        />

        <div className="ml-auto flex items-center gap-2">
          {/* Timeframe selector */}
          <TimeframeSelector
            value={effectiveTimeframe}
            onSelect={(tf) => setActiveTimeframe(tf as ChartTimeframe)}
          />

          {/* Chart-mode toggle (Standard / Volume Profile / Footprint) */}
          <ChartModeToggle />

          {/* Fullscreen control */}
          <button
            type="button"
            onClick={handleToggleFullscreen}
            aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
            className="flex h-7 w-7 items-center justify-center rounded-md border border-border-default bg-surface text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
          >
            {isFullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        </div>
      </div>

      {/* ── Fullscreen-unavailable indication (Requirement 12.5) ───────── */}
      {fullscreenUnavailable && (
        <div
          role="status"
          className="flex items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-3 py-1 text-[11px] text-amber-400"
        >
          <AlertTriangle size={12} />
          <span>
            Native fullscreen is unavailable here — showing a maximized in-app
            view instead.
          </span>
        </div>
      )}

      {/* ── Chart body: toolbar + renderer ─────────────────────────────── */}
      <div className="relative flex min-h-0 flex-1">
        {/* Drawing toolbar (reused) */}
        <ChartToolsBar className="border-r border-border-default bg-surface/40" />

        {/* Price renderer / footprint surface */}
        <div className="relative min-w-0 flex-1">
          {isFootprint ? (
            <FootprintChart timeframe={effectiveTimeframe} />
          ) : (
            <ChartRenderer
              timeframe={effectiveTimeframe}
              isExpanded={isFullscreen}
              showVolumeProfile={showVolumeProfile}
              chartType={chartType}
              chartTypeParams={chartTypeParams}
              activeStrategyId={activeStrategyId}
              strategyParams={strategyParams}
            />
          )}

          {/* Indicator manager overlay (keeps chart visible — Req 12.2) */}
          {showIndicatorManager && (
            <div className="absolute right-3 top-3 z-50">
              <IndicatorManagerPanel
                onClose={() => setShowIndicatorManager(false)}
              />
            </div>
          )}
        </div>
      </div>

      {/* ── Overlay settings dialogs (Requirements 12.2, 12.3) ─────────── */}
      {openDialog === 'chart-type' && chartTypeHasParams && (
        <NumericParamDialog
          title={`${CHART_TYPE_LABELS[chartType]} Settings`}
          spec={CHART_TYPE_PARAM_SPEC[chartType]}
          current={chartTypeParams as Record<string, number>}
          defaults={CHART_TYPE_PARAM_DEFAULTS as unknown as Record<string, number>}
          validate={(values) => {
            const result = validateChartTypeParams(chartType, values as ChartTypeParams);
            return result.ok
              ? { ok: true, value: result.value as Record<string, number> }
              : { ok: false, errorParam: result.errorParam, message: result.message };
          }}
          onApply={(values) => handleApplyChartTypeParams(values as ChartTypeParams)}
          onClose={closeDialog}
        />
      )}

      {openDialog === 'strategy' && activeStrategyId && (() => {
        const def = getStrategy(activeStrategyId);
        if (!def) return null;
        return (
          <NumericParamDialog
            title={`${def.name} Settings`}
            spec={def.paramSpec}
            current={strategyParams}
            defaults={def.defaults}
            validate={(values) => {
              const result = validateParams(values, def.paramSpec);
              return result.ok
                ? { ok: true, value: result.value }
                : { ok: false, errorParam: result.errorParam, message: result.message };
            }}
            onApply={(values) => handleApplyStrategyParams(values)}
            onClose={closeDialog}
          />
        );
      })()}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Chart-type selector
// ───────────────────────────────────────────────────────────────────────────

function ChartTypeSelector({
  value,
  onSelect,
}: {
  value: ChartType;
  onSelect: (t: ChartType) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useOutsideClose<HTMLDivElement>(() => setOpen(false));

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Chart type"
        className="flex h-7 items-center gap-1.5 rounded-md border border-border-default bg-surface px-2.5 text-[11px] font-semibold text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
      >
        <CandlestickChart size={13} className="text-text-muted" />
        <span>{CHART_TYPE_LABELS[value]}</span>
        <ChevronDown size={11} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
      </button>
      {open && (
        <div className="absolute left-0 top-full z-50 mt-1 w-44 rounded-lg border border-border-default bg-surface/95 p-1 shadow-2xl backdrop-blur-xl">
          {CHART_TYPES.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => {
                onSelect(t);
                setOpen(false);
              }}
              className={`flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-[11px] transition-colors ${
                t === value
                  ? 'bg-primary/10 font-semibold text-primary'
                  : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
              }`}
            >
              <span>{CHART_TYPE_LABELS[t]}</span>
              {t === value && <span className="h-1.5 w-1.5 rounded-full bg-primary" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Timeframe selector
// ───────────────────────────────────────────────────────────────────────────

function TimeframeSelector({
  value,
  onSelect,
}: {
  value: Timeframe;
  onSelect: (tf: Timeframe) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useOutsideClose<HTMLDivElement>(() => setOpen(false));

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Timeframe"
        className="flex h-7 items-center gap-1.5 rounded-md border border-border-default bg-surface px-2.5 text-[11px] font-semibold text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
      >
        <Clock size={12} className="text-text-muted" />
        <span className="uppercase">{value}</span>
        <ChevronDown size={11} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 max-h-80 w-40 overflow-y-auto rounded-lg border border-border-default bg-surface/95 p-1 shadow-2xl backdrop-blur-xl">
          {TIMEFRAME_GROUPS.map((group) => (
            <div key={group.label}>
              <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-text-muted">
                {group.label}
              </div>
              {group.items.map(({ tf, display }) => (
                <button
                  key={tf}
                  type="button"
                  onClick={() => {
                    onSelect(tf);
                    setOpen(false);
                  }}
                  className={`flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-[11px] transition-colors ${
                    tf === value
                      ? 'bg-primary/10 font-semibold text-primary'
                      : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                  }`}
                >
                  <span>{display}</span>
                  <span className="font-mono text-[10px] uppercase text-text-muted">{tf}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Strategy selector + settings entry
// ───────────────────────────────────────────────────────────────────────────

function StrategySelector({
  activeStrategyId,
  onSelect,
  onOpenSettings,
}: {
  activeStrategyId: string | null;
  onSelect: (id: string | null) => void;
  onOpenSettings: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useOutsideClose<HTMLDivElement>(() => setOpen(false));
  const strategies = useMemo<StrategyDef[]>(
    () => listStrategies().map((id) => getStrategy(id)).filter((d): d is StrategyDef => !!d),
    [],
  );
  const active = activeStrategyId ? getStrategy(activeStrategyId) : undefined;

  return (
    <div className="flex items-center gap-1">
      <div className="relative" ref={ref}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-label="Strategy"
          className={`flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-[11px] font-semibold transition-colors ${
            active
              ? 'border-primary/40 bg-primary/10 text-primary'
              : 'border-border-default bg-surface text-text-secondary hover:bg-elevated hover:text-text-primary'
          }`}
        >
          <Activity size={13} className={active ? 'text-primary' : 'text-text-muted'} />
          <span>{active ? active.name : 'Strategy'}</span>
          <ChevronDown size={11} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />
        </button>
        {open && (
          <div className="absolute left-0 top-full z-50 mt-1 w-48 rounded-lg border border-border-default bg-surface/95 p-1 shadow-2xl backdrop-blur-xl">
            <button
              type="button"
              onClick={() => {
                onSelect(null);
                setOpen(false);
              }}
              className={`flex w-full items-center rounded-md px-2.5 py-1.5 text-left text-[11px] transition-colors ${
                !activeStrategyId
                  ? 'bg-primary/10 font-semibold text-primary'
                  : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
              }`}
            >
              None
            </button>
            {strategies.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => {
                  onSelect(s.id);
                  setOpen(false);
                }}
                className={`flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-[11px] transition-colors ${
                  s.id === activeStrategyId
                    ? 'bg-primary/10 font-semibold text-primary'
                    : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                }`}
              >
                <span>{s.name}</span>
                {s.id === activeStrategyId && (
                  <span className="h-1.5 w-1.5 rounded-full bg-primary" />
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {active && (
        <button
          type="button"
          onClick={onOpenSettings}
          aria-label="Strategy settings"
          className="flex h-7 w-7 items-center justify-center rounded-md border border-border-default bg-surface text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
        >
          <Settings2 size={13} />
        </button>
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Generic numeric-parameter settings dialog (overlay)
// ───────────────────────────────────────────────────────────────────────────

type ValidateResult =
  | { ok: true; value: Record<string, number> }
  | { ok: false; errorParam: string; message: string };

function NumericParamDialog({
  title,
  spec,
  current,
  defaults,
  validate,
  onApply,
  onClose,
}: {
  title: string;
  spec: Record<string, NumericRange>;
  current: Record<string, number>;
  defaults: Record<string, number>;
  validate: (values: Record<string, number>) => ValidateResult;
  onApply: (values: Record<string, number>) => void;
  onClose: () => void;
}) {
  const keys = useMemo(() => Object.keys(spec), [spec]);

  // Raw text inputs keyed by param name, seeded from current → default.
  const [raw, setRaw] = useState<Record<string, string>>(() => {
    const seed: Record<string, string> = {};
    for (const k of keys) {
      const v = current[k] ?? defaults[k];
      seed[k] = v === undefined ? '' : String(v);
    }
    return seed;
  });
  const [error, setError] = useState<{ param: string; message: string } | null>(null);

  const handleApply = () => {
    // Parse raw text → numbers, leaving non-numeric as NaN so the engine
    // validator rejects them and identifies the offending parameter
    // (Requirements 1.6, 8.6).
    const values: Record<string, number> = {};
    for (const k of keys) {
      const text = raw[k]?.trim() ?? '';
      values[k] = text === '' ? NaN : Number(text);
    }
    const result = validate(values);
    if (!result.ok) {
      setError({ param: result.errorParam, message: result.message });
      return;
    }
    setError(null);
    onApply(result.value);
  };

  return (
    <div
      className="absolute inset-0 z-[70] flex items-center justify-center bg-black/40 backdrop-blur-[2px]"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onMouseDown={(e) => {
        // Click on the dim backdrop (not the panel) closes the dialog
        // without navigating away (Requirement 12.3).
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="w-72 rounded-xl border border-border-default bg-surface text-text-primary shadow-2xl">
        <div className="flex items-center justify-between border-b border-border-default px-3 py-2">
          <span className="text-sm font-medium">{title}</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close settings"
            className="flex h-6 w-6 items-center justify-center rounded text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
          >
            <X size={14} />
          </button>
        </div>

        <div className="space-y-3 px-3 py-3">
          {keys.length === 0 && (
            <p className="text-xs text-text-secondary">No configurable parameters.</p>
          )}
          {keys.map((k) => {
            const range = spec[k];
            const hasError = error?.param === k;
            return (
              <label key={k} className="block">
                <span className="mb-1 flex items-center justify-between text-[11px] text-text-secondary">
                  <span>{paramLabel(k)}</span>
                  <span className="font-mono text-[10px] text-text-muted">
                    {range.min}–{range.max}
                  </span>
                </span>
                <input
                  type="number"
                  value={raw[k] ?? ''}
                  onChange={(e) => setRaw((prev) => ({ ...prev, [k]: e.target.value }))}
                  step={range.integer ? 1 : 'any'}
                  className={`w-full rounded-md border bg-elevated px-2 py-1.5 text-sm text-text-primary outline-none transition-colors focus:border-primary ${
                    hasError ? 'border-red-500/60' : 'border-border-default'
                  }`}
                />
              </label>
            );
          })}

          {error && (
            <div
              role="alert"
              className="rounded-md bg-red-500/10 px-2 py-1.5 text-xs text-red-400"
            >
              {error.message}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 border-t border-border-default px-3 py-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-3 py-1.5 text-xs text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleApply}
            className="rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-primary/90"
          >
            Apply
          </button>
        </div>
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────────────
// Small hook: close a popover on outside click
// ───────────────────────────────────────────────────────────────────────────

function useOutsideClose<T extends HTMLElement>(onClose: () => void) {
  const ref = useRef<T>(null);
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onClose();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [onClose]);
  return ref;
}
