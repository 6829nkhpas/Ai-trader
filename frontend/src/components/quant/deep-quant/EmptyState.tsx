'use client';

import React from 'react';
import { Zap } from 'lucide-react';

interface EmptyStateProps {
  symbol: string;
}

export default function EmptyState({ symbol }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 p-4 py-10">
      <div className="relative">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-elevated border border-border-default">
          <Zap size={24} className="text-text-muted/60" />
        </div>
      </div>
      <div className="text-center">
        <p className="text-[11px] font-semibold text-text-muted">Deep Quant Engine Ready</p>
        <p className="text-[9px] text-text-muted/50 mt-1 leading-relaxed max-w-[180px]">
          Press the button above to run<br />
          the full AI analysis pipeline<br />
          for <span className="text-text-secondary font-semibold">{symbol}</span>
        </p>
      </div>
    </div>
  );
}
