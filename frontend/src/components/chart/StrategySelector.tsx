'use client';

import React, { useState, useMemo } from 'react';
import { Activity, ChevronDown, Settings2 } from 'lucide-react';
import { useOutsideClose } from '../../hooks/useOutsideClose';
import {
  listStrategies,
  getStrategy,
  type StrategyDef,
} from '../../charting/engines';

interface StrategySelectorProps {
  activeStrategyId: string | null;
  onSelect: (id: string | null) => void;
  onOpenSettings: () => void;
  noText?: boolean;
}

export default function StrategySelector({
  activeStrategyId,
  onSelect,
  onOpenSettings,
  noText = false,
}: StrategySelectorProps) {
  const [open, setOpen] = useState(false);
  const ref = useOutsideClose<HTMLDivElement>(() => setOpen(false));
  const strategies = useMemo<StrategyDef[]>(
    () => listStrategies().map((id) => getStrategy(id)).filter((d): d is StrategyDef => !!d),
    [],
  );
  const active = activeStrategyId ? getStrategy(activeStrategyId) : undefined;

  return (
    <div className="flex h-full items-center justify-center gap-0.5">
      <div className="relative flex items-center justify-center" ref={ref}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-label="Strategy"
          title={noText ? `Strategy: ${active ? active.name : 'None'}` : undefined}
          className={
            noText
              ? `flex h-7 w-7 items-center justify-center rounded-sm transition-colors ${
                  open || active
                    ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                    : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                }`
              : `flex h-full items-center gap-1.5 px-2.5 text-[11px] font-semibold transition-colors border-r border-border-default bg-surface ${
                  active
                    ? 'text-primary'
                    : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                }`
          }
        >
          <Activity size={noText ? 14 : 13} className={active ? 'text-primary' : 'text-text-muted'} />
          {!noText && <span>{active ? active.name : 'Strategy'}</span>}
          {!noText && <ChevronDown size={11} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} />}
        </button>
        {open && (
          <div className={`absolute right-0 z-50 mt-px w-48 rounded-none border border-border-default bg-surface/95 p-1 shadow-2xl backdrop-blur-xl ${
            noText ? 'top-[32px]' : 'top-full'
          }`}>
            <button
              type="button"
              onClick={() => {
                onSelect(null);
                setOpen(false);
              }}
              className={`flex w-full items-center rounded-none px-2.5 py-1.5 text-left text-[11px] transition-colors ${!activeStrategyId
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
                className={`flex w-full items-center justify-between rounded-none px-2.5 py-1.5 text-left text-[11px] transition-colors ${s.id === activeStrategyId
                    ? 'bg-primary/10 font-semibold text-primary'
                    : 'text-text-secondary hover:bg-elevated hover:text-text-primary'
                  }`}
              >
                <span>{s.name}</span>
                {s.id === activeStrategyId && (
                  <span className="h-1.5 w-1.5 rounded-none bg-primary" />
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
          title="Strategy Settings"
          className={
            noText
              ? "flex h-7 w-7 items-center justify-center rounded-sm text-text-secondary hover:bg-elevated hover:text-text-primary transition-colors cursor-pointer"
              : "flex h-full w-9 items-center justify-center border-r border-border-default bg-surface text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
          }
        >
          <Settings2 size={13} />
        </button>
      )}
    </div>
  );
}
