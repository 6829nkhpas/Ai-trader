'use client';

import React from 'react';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { useMacroIndicators, type MacroIndicator } from '../../hooks/useMacroIndicators';

function TickerItem({ indicator }: { indicator: MacroIndicator }) {
  const isUp = indicator.direction === 'up';
  const isDown = indicator.direction === 'down';
  const colorClass = isUp
    ? 'text-emerald-500 dark:text-emerald-400'
    : isDown
      ? 'text-rose-500 dark:text-rose-400'
      : 'text-text-secondary';

  const Icon = isUp ? TrendingUp : isDown ? TrendingDown : Minus;

  return (
    <div className="flex items-center gap-2 px-4 py-1.5 whitespace-nowrap border-r border-border-default/60">
      <span className="text-[10px] font-bold uppercase tracking-wider text-text-secondary">
        {indicator.label}
      </span>
      <span className="text-[11px] font-mono font-bold text-text-primary tabular-nums">
        {indicator.value}
      </span>
      <span className={`flex items-center gap-0.5 text-[10px] font-mono font-semibold ${colorClass}`}>
        <Icon size={11} />
        {indicator.change}
      </span>
    </div>
  );
}

// Render the indicator set enough times that two identical halves of the
// marquee always exceed the viewport width, so the loop never reveals an
// "end". Each half has REPEAT copies of the indicators; the second half must
// duplicate the first half exactly for the -50% translate to be seamless,
// but each rendered item still needs a globally-unique React key.
const REPEAT = 5;
const TOTAL_COPIES = REPEAT * 2;

export default function MarketTickerStrip() {
  const { indicators } = useMacroIndicators();

  if (indicators.length === 0) return null;

  const items = Array.from({ length: TOTAL_COPIES }).flatMap((_, copyIdx) =>
    indicators.map((ind, indIdx) => ({
      ...ind,
      key: `${ind.label}-c${copyIdx}-i${indIdx}`,
    })),
  );

  return (
    <div className="relative z-20 flex h-7 shrink-0 items-stretch overflow-hidden border-b border-border-default bg-surface select-none">
      <div className="flex items-stretch animate-marquee whitespace-nowrap will-change-transform">
        {items.map((ind) => (
          <TickerItem key={ind.key} indicator={ind} />
        ))}
      </div>
    </div>
  );
}
