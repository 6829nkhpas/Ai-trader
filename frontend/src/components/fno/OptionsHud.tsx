'use client';

/**
 * F&O Frontend Section (F4) — OptionsHud (task 7.4).
 *
 * A presentational panel that renders the headline options analytics HUD from
 * a single `HudModel` (produced by the pure `buildHudModel` selector). It owns
 * NO analytics and NO state: it renders exactly what the view-model carries.
 *
 * It renders (Requirement 5.1–5.4, 8.2, 8.3):
 * - The chain context — underlying, expiry, and whether the rendered chain is
 *   the symbol's own chain or a broad-market benchmark (R5.4).
 * - The agent `Options_Bias` state (`bullish` / `bearish` / `neutral`) with its
 *   key driving signals (`biasSignals`) (R5.2).
 * - The headline analytics: PCR by OI and by volume, max-pain level, aggregate
 *   OI bias (call/put buildup), nearest OI walls (support/resistance), the
 *   IV-skew summary (put-minus-call / slope / ATM IV), and the futures basis
 *   (R5.1).
 *
 * Every `null` leaf renders as an explicit "N/A" badge — never `0`, `''`, or a
 * fabricated value (R5.3, R8.2). Partial analytics therefore surface the
 * present fields and flag the absent ones honestly (R8.3).
 *
 * Scope: pure presentation, matching the terminal's dark institutional theme
 * (the `--text-*`, `--border-default`, `--surface`, bull/bear tokens used by
 * the sibling F&O components).
 */

import React from 'react';
import { Activity, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import type { HudModel, NaOr, OptionsBiasState, ChainContext } from './viewModel';

export interface OptionsHudProps {
  /** The HUD view-model (output of `buildHudModel`). Every null leaf → N/A. */
  hud: HudModel;
}

/**
 * Explicit "N/A" badge for any field whose source value is `null`/omitted.
 * Never substitutes a zero or fabricated value (R5.3, R8.2).
 */
function NaBadge() {
  return (
    <span className="inline-flex items-center rounded-none border border-border-default bg-elevated px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-text-muted">
      N/A
    </span>
  );
}

/**
 * Format a finite number for display with a sensible precision; returns the
 * N/A badge when the value is `null`. Pure and total — only called with values
 * the selector already constrained to `finite | null`.
 */
function formatNumber(value: NaOr<number>, fractionDigits = 2): React.ReactNode {
  if (value === null) {
    return <NaBadge />;
  }
  return (
    <span className="font-mono text-text-primary">
      {value.toLocaleString(undefined, {
        minimumFractionDigits: 0,
        maximumFractionDigits: fractionDigits,
      })}
    </span>
  );
}

/** Render a finite string value, or the N/A badge when `null`. */
function formatString(value: NaOr<string>): React.ReactNode {
  if (value === null || value.trim().length === 0) {
    return <NaBadge />;
  }
  return <span className="font-mono text-text-primary">{value}</span>;
}

/** A single labelled metric row: label on the left, value-or-N/A on the right. */
function Metric({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 px-3 py-2">
      <span className="text-[10px] font-medium uppercase tracking-wider text-text-secondary">
        {label}
      </span>
      <span className="text-xs">{children}</span>
    </div>
  );
}

/** A titled grouping of metrics with the shared dark-theme card chrome. */
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col rounded-none border border-border-default bg-surface">
      <div className="border-b border-border-default px-3 py-1.5 text-[10px] font-bold uppercase tracking-widest text-text-muted">
        {title}
      </div>
      <div className="flex flex-col divide-y divide-border-default/60">{children}</div>
    </div>
  );
}

/**
 * Map the agent bias state to its theme treatment. Neutral and the N/A case
 * stay muted so a missing bias never reads as a directional call (R8.2).
 */
function biasTreatment(state: NaOr<OptionsBiasState>): {
  label: string;
  className: string;
  icon: React.ReactNode;
} {
  switch (state) {
    case 'bullish':
      return {
        label: 'Bullish',
        className: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30',
        icon: <TrendingUp size={12} />,
      };
    case 'bearish':
      return {
        label: 'Bearish',
        className: 'bg-rose-500/10 text-rose-400 border-rose-500/30',
        icon: <TrendingDown size={12} />,
      };
    case 'neutral':
      return {
        label: 'Neutral',
        className: 'bg-amber-500/10 text-amber-400 border-amber-500/30',
        icon: <Minus size={12} />,
      };
    default:
      return {
        label: 'N/A',
        className: 'bg-elevated text-text-muted border-border-default',
        icon: <Minus size={12} />,
      };
  }
}

/** Human label for the chain context, or the N/A badge when null (R5.4). */
function chainContextLabel(context: NaOr<ChainContext>): React.ReactNode {
  if (context === 'own-chain') {
    return <span className="font-mono text-text-primary">Own chain</span>;
  }
  if (context === 'broad-market') {
    return <span className="font-mono text-text-primary">Broad-market benchmark</span>;
  }
  return <NaBadge />;
}

/**
 * Render the agent's driving signals (`biasSignals`) as key/value rows, or the
 * N/A badge when F3 omitted them. Values are stringified defensively so any
 * shape renders without throwing (consumption-only — no analytic logic).
 */
function BiasSignals({ signals }: { signals: NaOr<Record<string, unknown>> }) {
  if (signals === null) {
    return (
      <div className="px-3 py-2">
        <NaBadge />
      </div>
    );
  }

  const entries = Object.entries(signals);
  if (entries.length === 0) {
    return (
      <div className="px-3 py-2">
        <NaBadge />
      </div>
    );
  }

  return (
    <div className="flex flex-col divide-y divide-border-default/60">
      {entries.map(([key, value]) => (
        <Metric key={key} label={key.replace(/_/g, ' ')}>
          <span className="font-mono text-text-primary">{stringifySignal(value)}</span>
        </Metric>
      ))}
    </div>
  );
}

/** Defensively stringify a single signal value for display. */
function stringifySignal(value: unknown): string {
  if (value === null || value === undefined) {
    return '—';
  }
  if (typeof value === 'number') {
    return Number.isFinite(value)
      ? value.toLocaleString(undefined, { maximumFractionDigits: 4 })
      : '—';
  }
  if (typeof value === 'string' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return '—';
  }
}

/**
 * OptionsHud — the headline options-analytics HUD. Pure presentation over a
 * `HudModel`; every null field renders as an explicit N/A badge (R5.3, R8.2).
 */
export function OptionsHud({ hud }: OptionsHudProps) {
  const bias = biasTreatment(hud.biasState);

  return (
    <div className="flex h-full w-full flex-col overflow-y-auto bg-transparent font-sans">
      {/* Chain context header (underlying / expiry / own-chain vs broad-market) — R5.4 */}
      <div className="flex items-center justify-between gap-4 border-b border-border-default bg-surface px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Activity size={14} className="text-text-secondary" />
          <span className="text-sm font-semibold text-text-primary">Options HUD</span>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-text-secondary">
          <span className="font-mono font-semibold text-text-primary">
            {hud.context.underlying.trim().length > 0 ? hud.context.underlying : <NaBadge />}
          </span>
          <span className="text-text-muted">/</span>
          <span className="font-mono text-text-secondary">
            {hud.context.expiry.trim().length > 0 ? hud.context.expiry : <NaBadge />}
          </span>
        </div>
      </div>

      <div className="flex flex-col gap-3 p-3">
        {/* Agent options bias + chain context — R5.2, R5.4 */}
        <div className="flex flex-col rounded-none border border-border-default bg-surface">
          <div className="flex items-center justify-between border-b border-border-default px-3 py-1.5">
            <span className="text-[10px] font-bold uppercase tracking-widest text-text-muted">
              Agent Options Bias
            </span>
            <span
              className={`inline-flex items-center gap-1 rounded-none border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${bias.className}`}
            >
              {bias.icon}
              {bias.label}
            </span>
          </div>
          <Metric label="Chain context">{chainContextLabel(hud.context.chainContext)}</Metric>
        </div>

        {/* Driving signals — R5.2 */}
        <Section title="Driving Signals">
          <BiasSignals signals={hud.biasSignals} />
        </Section>

        {/* Headline analytics — R5.1 */}
        <Section title="Headline Analytics">
          <Metric label="PCR (OI)">{formatNumber(hud.pcrOi)}</Metric>
          <Metric label="PCR (Volume)">{formatNumber(hud.pcrVolume)}</Metric>
          <Metric label="Max Pain">{formatNumber(hud.maxPain, 0)}</Metric>
          <Metric label="Futures Basis">{formatNumber(hud.futuresBasis)}</Metric>
        </Section>

        {/* Aggregate OI bias (call/put buildup) — R5.1 */}
        <Section title="Aggregate OI Bias">
          <Metric label="Call buildup">{formatString(hud.aggregateOiBias.call)}</Metric>
          <Metric label="Put buildup">{formatString(hud.aggregateOiBias.put)}</Metric>
        </Section>

        {/* Nearest OI walls — R5.1 */}
        <Section title="Nearest OI Walls">
          <Metric label="Support">{formatNumber(hud.walls.support, 0)}</Metric>
          <Metric label="Resistance">{formatNumber(hud.walls.resistance, 0)}</Metric>
        </Section>

        {/* IV-skew summary — R5.1 */}
        <Section title="IV Skew">
          {hud.ivSkew === null ? (
            <div className="px-3 py-2">
              <NaBadge />
            </div>
          ) : (
            <>
              <Metric label="Put − Call">{formatNumber(hud.ivSkew.putMinusCall)}</Metric>
              <Metric label="Slope">{formatNumber(hud.ivSkew.slope, 4)}</Metric>
              <Metric label="ATM IV">{formatNumber(hud.ivSkew.atmIv)}</Metric>
            </>
          )}
        </Section>
      </div>
    </div>
  );
}

export default OptionsHud;
