'use client';

import React from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';

interface ErrorStateProps {
  error: string;
  dataReady: boolean;
  activeMode: 'FIND' | 'VERIFY';
  onRetryFind: () => void;
  onRetryVerify: () => void;
}

export default function ErrorState({
  error,
  dataReady,
  activeMode,
  onRetryFind,
  onRetryVerify,
}: ErrorStateProps) {
  const handleRetry = () => {
    if (activeMode === 'FIND') {
      onRetryFind();
    } else {
      onRetryVerify();
    }
  };

  return (
    <div className="flex flex-col items-center justify-center gap-3 p-4 py-8">
      <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-rose-500/10 border border-rose-500/30">
        <AlertTriangle size={20} className="text-rose-400" />
      </div>
      <div className="text-center">
        <p className="text-[11px] font-semibold text-rose-400">Analysis Failed</p>
        <p className="text-[9px] text-text-muted/60 mt-1 max-w-[200px] leading-relaxed">
          {error}
        </p>
      </div>
      <button
        type="button"
        onClick={handleRetry}
        disabled={!dataReady}
        className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[10px] font-semibold text-text-secondary bg-elevated border border-border-default hover:bg-surface transition-colors ${!dataReady ? 'opacity-50 cursor-not-allowed' : ''}`}
      >
        <RotateCcw size={10} />
        Retry
      </button>
    </div>
  );
}
