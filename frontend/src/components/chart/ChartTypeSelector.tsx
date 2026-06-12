'use client';

import React, { useState } from 'react';
import { CandlestickChart, ChevronDown } from 'lucide-react';
import { useOutsideClose } from '../../hooks/useOutsideClose';
import { CHART_TYPES, type ChartType } from '../../charting/engines';

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

export { CHART_TYPE_LABELS };

interface ChartTypeSelectorProps {
  value: ChartType;
  onSelect: (t: ChartType) => void;
}

export default function ChartTypeSelector({ value, onSelect }: ChartTypeSelectorProps) {
  const [open, setOpen] = useState(false);
  const ref = useOutsideClose<HTMLDivElement>(() => setOpen(false));

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Chart type"
        className="flex h-7 items-center gap-1 rounded-md border border-border-default bg-surface px-2 text-[11px] font-semibold text-text-secondary transition-colors hover:bg-elevated hover:text-text-primary"
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
              className={`flex w-full items-center justify-between rounded-md px-2.5 py-1.5 text-left text-[11px] transition-colors ${t === value
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
