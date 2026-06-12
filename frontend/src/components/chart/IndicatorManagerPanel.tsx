'use client';

// Feature: professional-charting-suite
//
// IndicatorManagerPanel — the trader-facing UI for browsing every registered
// indicator and managing the active-indicator list for the current symbol.
//
//  - Lists all available overlay and oscillator indicators (Requirement 4.1).
//  - A case-insensitive search field filters the list by name using the
//    engine's `searchIndicators` helper (Requirement 4.2).
//  - Adds an indicator, surfacing duplicate / at-capacity rejection messages
//    from the store's `addIndicator` action (Requirement 4.6 + 4.4/4.5).
//  - Shows the active indicators for the current symbol with remove and
//    visibility-toggle controls.

import React, { useMemo, useState } from 'react';
import {
  Search,
  Plus,
  Trash2,
  Eye,
  EyeOff,
  LineChart,
  Activity,
  X,
} from 'lucide-react';
import {
  listIndicators,
  searchIndicators,
  getIndicator,
  type IndicatorDef,
  type IndicatorId,
} from '../../charting/engines';
import {
  useChartUIStore,
  MAX_INDICATORS_PER_SYMBOL,
  type ActiveIndicator,
} from '../../store/useChartUIStore';
import { useTradeStore } from '../../store/useTradeStore';

interface IndicatorManagerPanelProps {
  className?: string;
  /** Optional close handler; when provided a close button is rendered. */
  onClose?: () => void;
}

/** Transient feedback shown after an add attempt (Requirement 4.6, 4.4, 4.5). */
type Feedback = { kind: 'success' | 'error'; message: string };

export default function IndicatorManagerPanel({
  className = '',
  onClose,
}: IndicatorManagerPanelProps) {
  const activeSymbol = useTradeStore((s) => s.selectedSymbol || 'RELIANCE');

  const addIndicator = useChartUIStore((s) => s.addIndicator);
  const removeIndicator = useChartUIStore((s) => s.removeIndicator);
  const toggleIndicatorVisible = useChartUIStore((s) => s.toggleIndicatorVisible);
  // Subscribe to the per-symbol slice so the active list re-renders on change.
  const activeIndicators = useChartUIStore(
    (s) => s.activeIndicators[activeSymbol] ?? [],
  );

  const [query, setQuery] = useState('');
  const [feedback, setFeedback] = useState<Feedback | null>(null);

  // Case-insensitive name filtering delegated to the engine so the panel and
  // the engine agree on match semantics (Requirement 4.2). An empty query
  // lists every registered indicator (Requirement 4.1).
  const results = useMemo<IndicatorDef[]>(() => {
    const trimmed = query.trim();
    return trimmed.length === 0 ? listIndicators() : searchIndicators(trimmed);
  }, [query]);

  const overlays = useMemo(
    () => results.filter((d) => d.kind === 'overlay'),
    [results],
  );
  const oscillators = useMemo(
    () => results.filter((d) => d.kind === 'oscillator'),
    [results],
  );

  const handleAdd = (id: IndicatorId) => {
    const result = addIndicator(activeSymbol, id);
    if (result.ok) {
      const def = getIndicator(id);
      setFeedback({ kind: 'success', message: `${def?.name ?? id} added` });
    } else {
      // Surface the duplicate / at-capacity message from the store (Req 4.6).
      setFeedback({ kind: 'error', message: result.message });
    }
  };

  const atCapacity = activeIndicators.length >= MAX_INDICATORS_PER_SYMBOL;

  return (
    <div
      className={`flex w-80 flex-col overflow-hidden rounded-lg border border-border-default bg-surface text-text-primary shadow-xl ${className}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border-default px-3 py-2">
        <div className="flex items-center gap-2">
          <LineChart size={15} className="text-primary" />
          <span className="text-sm font-medium">Indicators</span>
          <span className="text-xs text-text-secondary">· {activeSymbol}</span>
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close indicator manager"
            className="flex h-6 w-6 items-center justify-center rounded text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
          >
            <X size={14} />
          </button>
        )}
      </div>

      {/* Search */}
      <div className="border-b border-border-default p-2">
        <div className="flex items-center gap-2 rounded-md border border-border-default bg-elevated px-2 py-1.5 focus-within:border-primary">
          <Search size={14} className="shrink-0 text-text-secondary" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search indicators..."
            aria-label="Search indicators"
            className="w-full bg-transparent text-sm text-text-primary outline-none placeholder:text-text-secondary"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              aria-label="Clear search"
              className="shrink-0 text-text-secondary transition-colors hover:text-text-primary"
            >
              <X size={13} />
            </button>
          )}
        </div>
      </div>

      {/* Feedback (duplicate / at-capacity / added) */}
      {feedback && (
        <div
          role="status"
          className={`px-3 py-1.5 text-xs ${
            feedback.kind === 'error'
              ? 'bg-red-500/10 text-red-400'
              : 'bg-emerald-500/10 text-emerald-400'
          }`}
        >
          {feedback.message}
        </div>
      )}

      {/* Available indicator list */}
      <div className="max-h-72 flex-1 overflow-y-auto">
        {results.length === 0 ? (
          <div className="px-3 py-6 text-center text-xs text-text-secondary">
            No indicators match “{query}”.
          </div>
        ) : (
          <>
            <IndicatorGroup
              title="Overlays"
              icon={LineChart}
              defs={overlays}
              onAdd={handleAdd}
              disabled={atCapacity}
            />
            <IndicatorGroup
              title="Oscillators"
              icon={Activity}
              defs={oscillators}
              onAdd={handleAdd}
              disabled={atCapacity}
            />
          </>
        )}
      </div>

      {/* Active indicators for the current symbol */}
      <div className="border-t border-border-default">
        <div className="flex items-center justify-between px-3 py-2">
          <span className="text-xs font-medium text-text-secondary">
            Active ({activeIndicators.length}/{MAX_INDICATORS_PER_SYMBOL})
          </span>
        </div>
        {activeIndicators.length === 0 ? (
          <div className="px-3 pb-3 text-xs text-text-secondary">
            No active indicators for {activeSymbol}.
          </div>
        ) : (
          <ul className="max-h-40 overflow-y-auto pb-1">
            {activeIndicators.map((ind) => (
              <ActiveIndicatorRow
                key={ind.instanceId}
                indicator={ind}
                onToggleVisible={() =>
                  toggleIndicatorVisible(activeSymbol, ind.instanceId)
                }
                onRemove={() => removeIndicator(activeSymbol, ind.instanceId)}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

interface IndicatorGroupProps {
  title: string;
  icon: React.ElementType;
  defs: IndicatorDef[];
  onAdd: (id: IndicatorId) => void;
  disabled: boolean;
}

function IndicatorGroup({ title, icon: Icon, defs, onAdd, disabled }: IndicatorGroupProps) {
  if (defs.length === 0) return null;
  return (
    <div>
      <div className="flex items-center gap-1.5 bg-elevated/40 px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-text-secondary">
        <Icon size={12} />
        {title}
      </div>
      <ul>
        {defs.map((def) => (
          <li key={def.id}>
            <button
              type="button"
              onClick={() => onAdd(def.id)}
              disabled={disabled}
              className="group flex w-full items-center justify-between px-3 py-1.5 text-sm transition-colors hover:bg-elevated disabled:cursor-not-allowed disabled:opacity-40"
            >
              <span className="text-text-primary">{def.name}</span>
              <Plus
                size={14}
                className="text-text-secondary transition-colors group-hover:text-primary"
              />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

interface ActiveIndicatorRowProps {
  indicator: ActiveIndicator;
  onToggleVisible: () => void;
  onRemove: () => void;
}

function ActiveIndicatorRow({ indicator, onToggleVisible, onRemove }: ActiveIndicatorRowProps) {
  const def = getIndicator(indicator.indicatorId);
  return (
    <li className="flex items-center justify-between px-3 py-1.5 hover:bg-elevated">
      <div className="flex min-w-0 items-center gap-2">
        <span
          className="h-2.5 w-2.5 shrink-0 rounded-full border border-border-default/50"
          style={{ backgroundColor: indicator.style.color }}
        />
        <span
          className={`truncate text-sm ${
            indicator.visible ? 'text-text-primary' : 'text-text-secondary line-through'
          }`}
        >
          {def?.name ?? indicator.indicatorId}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <button
          type="button"
          onClick={onToggleVisible}
          aria-label={indicator.visible ? 'Hide indicator' : 'Show indicator'}
          className="flex h-6 w-6 items-center justify-center rounded text-text-secondary transition-colors hover:bg-surface hover:text-text-primary"
        >
          {indicator.visible ? <Eye size={14} /> : <EyeOff size={14} />}
        </button>
        <button
          type="button"
          onClick={onRemove}
          aria-label="Remove indicator"
          className="flex h-6 w-6 items-center justify-center rounded text-text-secondary transition-colors hover:bg-surface hover:text-red-400"
        >
          <Trash2 size={14} />
        </button>
      </div>
    </li>
  );
}
