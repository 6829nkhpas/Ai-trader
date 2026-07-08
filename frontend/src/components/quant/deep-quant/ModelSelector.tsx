'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Cpu, ChevronDown, ChevronRight, Check } from 'lucide-react';
import { MODEL_PROVIDERS, type ModelProviderGroup } from '../../../store/useQuantStore';

interface ModelSelectorProps {
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}

/**
 * Tree-format model picker. The trigger shows the selected model; opening it
 * reveals the provider companies (parent rows), and hovering a provider opens a
 * child flyout listing that provider's models.
 *
 * Both the provider panel AND the child flyout are rendered in a portal as
 * SIBLINGS with fixed positioning — the flyout is NOT nested inside the
 * scrollable provider panel, so the panel's overflow can never clip it (the bug
 * where the submenu was cut off and forced a horizontal scrollbar). The panel
 * opens upward because the composer sits at the bottom of the screen.
 */
export default function ModelSelector({ value, onChange, disabled = false }: ModelSelectorProps) {
  const [open, setOpen] = useState(false);
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const [panelPos, setPanelPos] = useState<{ left: number; bottomOffset: number }>({ left: 0, bottomOffset: 0 });
  const [flyoutPos, setFlyoutPos] = useState<{ left: number; top: number }>({ left: 0, top: 0 });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  const selectedLabel = useMemo(() => {
    for (const g of MODEL_PROVIDERS) {
      for (const m of g.models) {
        if (m.id === value) return m.label;
      }
    }
    return 'Deployment Default';
  }, [value]);

  const activeModels = useMemo(() => {
    const g = MODEL_PROVIDERS.find((x) => x.provider === activeGroup);
    return g && g.models.length > 1 ? g.models : null;
  }, [activeGroup]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement;
      if (triggerRef.current?.contains(t)) return;
      if (t.closest('[data-model-portal]')) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const toggle = () => {
    if (disabled) return;
    const r = triggerRef.current?.getBoundingClientRect();
    if (r) setPanelPos({ left: r.left, bottomOffset: window.innerHeight - r.top + 6 });
    setActiveGroup(null);
    setOpen((o) => !o);
  };

  const onProviderEnter = (e: React.MouseEvent<HTMLButtonElement>, group: ModelProviderGroup) => {
    setActiveGroup(group.provider);
    if (group.models.length > 1) {
      const rect = e.currentTarget.getBoundingClientRect();
      // Abut the flyout to the row's right edge; clamp vertically into view.
      const maxTop = window.innerHeight - 16 - 320;
      setFlyoutPos({ left: rect.right - 1, top: Math.max(8, Math.min(rect.top, maxTop)) });
    }
  };

  const pick = (id: string) => {
    onChange(id);
    setOpen(false);
    setActiveGroup(null);
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={toggle}
        disabled={disabled}
        title="Select the LLM provider / model"
        className="flex items-center gap-1.5 rounded-none bg-surface border border-border-default px-2 py-1 text-[10px] font-sans font-semibold text-text-primary hover:border-text-primary/40 focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
      >
        <Cpu size={11} className="text-text-secondary" />
        <span className="max-w-[150px] truncate">{selectedLabel}</span>
        <ChevronDown size={11} className="text-text-muted" />
      </button>

      {open && mounted && createPortal(
        <>
          {/* Provider panel (parent rows) — opens upward. overflow-x hidden so a
              long provider label can never introduce a horizontal scrollbar. */}
          <div
            data-model-portal
            style={{ position: 'fixed', left: panelPos.left, bottom: panelPos.bottomOffset }}
            className="z-[9999] w-[210px] max-h-[60vh] overflow-y-auto overflow-x-hidden bg-surface border border-border-default shadow-2xl scrollbar-thin py-1"
          >
            {MODEL_PROVIDERS.map((group) => {
              const single = group.models.length === 1;
              const isActive = activeGroup === group.provider;
              return (
                <button
                  key={group.provider}
                  type="button"
                  onMouseEnter={(e) => onProviderEnter(e, group)}
                  onClick={(e) => (single ? pick(group.models[0].id) : onProviderEnter(e, group))}
                  className={`flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left text-[11px] font-bold tracking-wide transition-colors ${
                    isActive ? 'bg-elevated text-text-primary' : 'text-text-primary hover:bg-elevated/60'
                  }`}
                >
                  <span className="truncate">{group.provider}</span>
                  {!single && <ChevronRight size={12} className="text-text-muted shrink-0" />}
                </button>
              );
            })}
          </div>

          {/* Child flyout (models) — a SEPARATE fixed sibling, so the panel's
              overflow can never clip it. */}
          {activeModels && (
            <div
              data-model-portal
              onMouseEnter={() => { /* keep open while hovering the flyout */ }}
              style={{ position: 'fixed', left: flyoutPos.left, top: flyoutPos.top }}
              className="z-[10000] w-[260px] max-h-[70vh] overflow-y-auto overflow-x-hidden bg-surface border border-border-default shadow-2xl scrollbar-thin py-1"
            >
              {activeModels.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => pick(m.id)}
                  className={`flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-[11px] transition-colors ${
                    m.id === value
                      ? 'bg-elevated text-text-primary font-semibold'
                      : 'text-text-secondary hover:bg-elevated/60 hover:text-text-primary'
                  }`}
                >
                  <span className="flex items-center gap-1.5 min-w-0">
                    <span className="truncate">{m.label}</span>
                    {m.recommended && (
                      <span className="shrink-0 rounded-sm bg-emerald-500/15 text-emerald-500 border border-emerald-500/30 px-1 py-px text-[8px] font-bold uppercase tracking-wide">
                        Recommended
                      </span>
                    )}
                  </span>
                  {m.id === value && <Check size={12} className="text-emerald-500 shrink-0" />}
                </button>
              ))}
            </div>
          )}
        </>,
        document.body,
      )}
    </>
  );
}
