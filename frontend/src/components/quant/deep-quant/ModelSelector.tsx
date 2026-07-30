'use client';

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Sparkles, ChevronDown, ChevronRight, Check, Lock } from 'lucide-react';
import { MODEL_PROVIDERS, MODEL_SELECTION_LOCKED, type ModelProviderGroup } from '../../../store/useQuantStore';

interface ModelSelectorProps {
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  variant?: 'default' | 'inline';
  // When true, models are shown but NOT selectable (beta/omniroute gateway).
  // Defaults to the build-time MODEL_SELECTION_LOCKED so callers don't have to
  // wire it, but can be overridden.
  locked?: boolean;
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
export default function ModelSelector({ value, onChange, disabled = false, variant = 'default', locked = MODEL_SELECTION_LOCKED }: ModelSelectorProps) {
  const [open, setOpen] = useState(false);
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const [panelPos, setPanelPos] = useState<{ left: number; top?: number; bottomOffset?: number }>({ left: 0 });
  const [openDirection, setOpenDirection] = useState<'up' | 'down'>('up');
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
    return 'Auto';
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
    if (r) {
      const spaceBelow = window.innerHeight - r.bottom;
      const spaceAbove = r.top;
      const panelWidth = 210;
      const panelHeight = Math.min(MODEL_PROVIDERS.length * 32 + 8, window.innerHeight * 0.6);
      const clampedLeft = Math.min(r.left, window.innerWidth - panelWidth - 8);
      if (spaceBelow >= panelHeight || spaceBelow >= spaceAbove) {
        setOpenDirection('down');
        setPanelPos({ left: clampedLeft, top: r.bottom + 6 });
      } else {
        setOpenDirection('up');
        setPanelPos({ left: clampedLeft, bottomOffset: window.innerHeight - r.top + 6 });
      }
    }
    setActiveGroup(null);
    setOpen((o) => !o);
  };

  const onProviderEnter = (e: React.MouseEvent<HTMLButtonElement>, group: ModelProviderGroup) => {
    setActiveGroup(group.provider);
    if (group.models.length > 1) {
      const rect = e.currentTarget.getBoundingClientRect();
      const flyoutWidth = 260;
      const fitsRight = rect.right + flyoutWidth <= window.innerWidth - 8;
      const maxTop = window.innerHeight - 16 - 320;
      setFlyoutPos({
        left: fitsRight ? rect.right - 1 : rect.left - flyoutWidth + 1,
        top: Math.max(8, Math.min(rect.top, maxTop)),
      });
    }
  };

  const pick = (id: string) => {
    if (locked) return; // beta/omniroute: selection is locked to the default model
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
        title={locked ? 'Model selection is locked in beta — upgrade to choose any model' : 'Select the LLM provider / model'}
        className={variant === 'inline'
          ? "flex items-center gap-1 bg-transparent px-1 py-0.5 text-[10px] font-sans font-semibold text-text-muted hover:text-text-primary focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
          : "flex w-full h-7 items-center justify-between rounded bg-elevated/35 border border-border-default/60 px-2 py-1 text-[10px] font-sans font-semibold text-text-primary hover:bg-elevated/65 hover:border-border-default/90 transition-all focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
        }
      >
        {variant === 'inline' ? (
          <>
            <span className="max-w-[150px] truncate">{selectedLabel}</span>
            <ChevronDown size={11} className={`text-text-muted transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
          </>
        ) : (
          <>
            <div className="flex items-center gap-1.5 min-w-0">
              <Sparkles size={11} className="text-accent shrink-0" />
              <span className="truncate">{selectedLabel}</span>
            </div>
            {locked
              ? <Lock size={11} className="text-amber-500 shrink-0" />
              : <ChevronDown size={11} className={`text-text-muted shrink-0 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />}
          </>
        )}
      </button>

      {open && mounted && createPortal(
        <>
          {/* Provider panel (parent rows) — opens upward. overflow-x hidden so a
              long provider label can never introduce a horizontal scrollbar. */}
          <div
            data-model-portal
            style={{
              position: 'fixed',
              left: panelPos.left,
              ...(openDirection === 'down'
                ? { top: panelPos.top }
                : { bottom: panelPos.bottomOffset }),
            }}
            className="z-[9999] w-[230px] max-h-[60vh] overflow-y-auto overflow-x-hidden bg-surface border border-border-default shadow-2xl scrollbar-thin py-1"
          >
            {locked && (
              <div className="flex items-start gap-1.5 px-3 py-2 mb-1 border-b border-border-default/40 text-[9px] leading-snug text-text-muted">
                <Lock size={11} className="text-amber-500 shrink-0 mt-px" />
                <span>Model selection is locked in beta. Upgrade to choose any model.</span>
              </div>
            )}
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
                  disabled={locked}
                  aria-disabled={locked}
                  className={`flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-[11px] transition-colors ${
                    locked
                      ? 'text-text-muted/60 cursor-not-allowed'
                      : m.id === value
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
                  {locked
                    ? <Lock size={11} className="text-text-muted/60 shrink-0" />
                    : m.id === value && <Check size={12} className="text-emerald-500 shrink-0" />}
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
