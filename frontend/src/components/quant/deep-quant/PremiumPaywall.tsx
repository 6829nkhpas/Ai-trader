'use client';

import React from 'react';
import { Loader2 } from 'lucide-react';

interface PremiumPaywallProps {
  onUpgradeClick: () => void;
}

export default function PremiumPaywall({ onUpgradeClick }: PremiumPaywallProps) {
  const [upgrading, setUpgrading] = React.useState(false);

  const handleClick = async () => {
    setUpgrading(true);
    try {
      await onUpgradeClick();
    } finally {
      setUpgrading(false);
    }
  };

  return (
    <div className="flex h-full flex-col items-center justify-center p-6 text-center bg-background/30 backdrop-blur-md">
      <div
        className="relative max-w-sm w-full p-8 rounded-3xl border border-blue-500/30 bg-surface/75 shadow-2xl flex flex-col items-center overflow-hidden"
        style={{
          boxShadow: '0 20px 50px rgba(0, 0, 0, 0.4), inset 0 0 20px rgba(59, 130, 246, 0.05)',
          background: 'linear-gradient(135deg, rgba(30, 41, 59, 0.7) 0%, rgba(15, 23, 42, 0.95) 100%)',
        }}
      >
        <div className="absolute -top-16 -left-16 w-32 h-32 bg-blue-500/10 rounded-full filter blur-2xl pointer-events-none" />
        <div className="absolute -bottom-16 -right-16 w-32 h-32 bg-violet-500/10 rounded-full filter blur-2xl pointer-events-none" />

        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500/15 to-violet-500/15 border border-blue-500/20 text-blue-400 mb-6 shadow-lg shadow-blue-500/10">
          <svg className="h-8 w-8 animate-pulse" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
          </svg>
        </div>

        <span className="text-[10px] font-black tracking-widest text-blue-400 uppercase mb-2">STRAT AI PRO SUITE</span>
        <h2 className="text-xl font-extrabold text-white tracking-tight mb-3">Deep Quant Access Required</h2>

        <p className="text-xs text-text-secondary leading-relaxed mb-6">
          Unlock institutional-grade breakout scanning, real-time news sentiment indexing, and automated conviction score backtesting.
        </p>

        <div className="w-full flex flex-col gap-2.5 mb-8 text-left text-xs text-text-secondary font-medium">
          <div className="flex items-center gap-2.5 px-3 py-2 rounded-lg bg-white/5 border border-white/5">
            <span className="text-blue-400">⚡</span>
            <span>DeepSeek v4 Autonomous ReAct Agent Loop</span>
          </div>
          <div className="flex items-center gap-2.5 px-3 py-2 rounded-lg bg-white/5 border border-white/5">
            <span className="text-blue-400">📊</span>
            <span>Mathematical Risk Manager & Trade Evaluator</span>
          </div>
          <div className="flex items-center gap-2.5 px-3 py-2 rounded-lg bg-white/5 border border-white/5">
            <span className="text-blue-400">🛡️</span>
            <span>Virtual Execution & Paper Broker Sync</span>
          </div>
        </div>

        <button
          type="button"
          disabled={upgrading}
          onClick={handleClick}
          className="w-full flex items-center justify-center gap-2 py-3 rounded-xl text-xs font-bold uppercase tracking-wider text-white bg-gradient-to-r from-blue-600 to-violet-600 hover:from-blue-600 hover:to-violet-600 hover:shadow-lg hover:shadow-blue-500/20 active:scale-[0.99] transition-all duration-150 disabled:opacity-50"
        >
          {upgrading ? (
            <>
              <Loader2 size={14} className="animate-spin text-white" />
              <span>Initiating Checkout...</span>
            </>
          ) : (
            <span>Upgrade to PRO</span>
          )}
        </button>

        <span className="text-[9px] text-text-muted/60 mt-3">Secure checkout powered by PhonePe</span>
      </div>
    </div>
  );
}
