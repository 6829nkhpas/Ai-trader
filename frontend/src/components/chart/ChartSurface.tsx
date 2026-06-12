'use client';

// Feature: professional-charting-suite
//
// ChartSurface — the shell that hosts the single engine-driven price renderer
// (`ChartRenderer`) plus the controls that are UNIQUE to the charting suite.
//
// Consolidation note (single source of truth):
//   The terminal page header (app/page.tsx) already globally owns the chart
//   MODE toggle (Standard / Volume Profile / Footprint), the TIMEFRAME selector,
//   and the FULLSCREEN control for every layout. To avoid a duplicated second
//   control bar, ChartSurface intentionally does NOT re-render those — it reads
//   the resulting chart mode from the store and contributes only the controls
//   that did not exist before:
//     · a chart-type selector (the 11 CHART_TYPES),
//     · an indicator-manager entry (toggles IndicatorManagerPanel),
//     · a strategy entry (+ params dialog),
//   alongside the drawing toolbar and the renderer itself. Chart-type and
//   strategy parameter edits are validated by the pure engines so invalid
//   values are rejected and the last valid values retained (Req 1.6, 8.6).

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  CandlestickChart,
  LineChart as LineChartIcon,
  Settings2,
  Activity,
  ChevronDown,
  X,
} from 'lucide-react';

import ChartRenderer from './ChartRenderer';
import IndicatorManagerPanel from './IndicatorManagerPanel';
import FootprintChart from './FootprintChart';

import { useTradeStore } from '../../store/useTradeStore';
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
import type { Timeframe } from '../../utils/chartTypes';

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
 * The chart surface shell. Composes the single engine-driven renderer with the
 * suite-unique controls (chart type, indicators, strategy), the drawing
 * toolbar, the indicator manager, and the settings dialogs. Chart mode,
 * timeframe and fullscreen are owned by the terminal page header.
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

  // ── Chart-mode + timeframe (owned by the page header; read-only here) ──
  const chartMode = useTradeStore((s) => s.chartMode);
  const activeTimeframe = useTradeStore((s) => s.activeTimeframe);

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

  // ── Derived render flags from chart mode (owned by the page header) ───
  const showVolumeProfile = chartMode === 'VOLUME_PROFILE';
  const isFootprint = chartMode === 'FOOTPRINT';

  const effectiveTimeframe = (activeTimeframe as Timeframe) ?? '1m';

  return (
    <div className={`relative flex h-full w-full flex-col overflow-hidden bg-background ${className}`}>
      {/* ── Suite-unique control row (no duplicated mode/timeframe/fullscreen) ── */}
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
      </div>

      {/* ── Chart body: renderer (drawing toolbar is owned by the terminal
          chrome — TerminalLayout in normal mode, the page in fullscreen) ─── */}
      <div className="relative flex min-h-0 flex-1">
        {/* Price renderer / footprint surface */}
        <div className="relative min-w-0 flex-1">
          {isFootprint ? (
            <FootprintChart timeframe={effectiveTimeframe} />
          ) : (
            <ChartRenderer
              timeframe={effectiveTimeframe}
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
