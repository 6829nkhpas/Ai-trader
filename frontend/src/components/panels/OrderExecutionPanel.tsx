'use client';

import React, { useState } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { DollarSign, Briefcase } from 'lucide-react';

export default function OrderExecutionPanel() {
  const { activeDecision, portfolioBalance, positions, executeTrade, rejectTrade } = useTradeStore();
  const [quantity, setQuantity] = useState<number>(100);

  if (!activeDecision) {
    return (
      <div className="flex flex-col gap-2 px-3 py-2">
        <div className="flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-text-secondary">
            <Briefcase size={14} /> Portfolio State
          </h2>
          <span className="text-xs text-text-muted">No active signal</span>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm text-text-secondary">
            <span>Available Balance:</span>
            <span className="flex items-center text-lg font-bold text-text-primary">
              <DollarSign size={18} className="mr-1 text-bull" />
              {portfolioBalance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </div>

          {Object.keys(positions).length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-xs font-semibold uppercase text-text-secondary">Positions</span>
              {Object.entries(positions).map(([sym, qty]) => (
                <div key={sym} className="rounded-full border border-border-default bg-surface px-2 py-1 text-xs text-text-secondary">
                  <span className="font-bold text-text-primary">{sym}</span>: {qty}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  const isBuy = activeDecision.action_type === 'BUY';
  const isHold = activeDecision.action_type === 'HOLD';
  const actionColor = isBuy ? 'text-bull' : isHold ? 'text-neutral' : 'text-bear';
  const buttonColor = isBuy
    ? 'bg-bull text-text-primary hover:brightness-95'
    : isHold
      ? 'bg-primary text-text-primary hover:bg-primary-hover'
      : 'bg-bear text-text-primary hover:brightness-95';
  const entryValue = activeDecision.price ? `$${activeDecision.price.toFixed(2)}` : '--';
  const targetValue = '--';
  const stopValue = '--';

  return (
    <div className="flex flex-col gap-2 px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-45">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-text-secondary">Trade Strip</h2>
          <div className="mt-1 text-sm font-semibold text-text-primary">
            {activeDecision.symbol}{' '}
            <span className={`font-bold ${actionColor}`}>{activeDecision.action_type}</span>
          </div>
          <div className="text-xs text-text-secondary">Conviction {activeDecision.final_conviction_score}%</div>
        </div>

        <div className="flex items-center gap-4 text-xs">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary">Entry</div>
            <div className="text-sm font-semibold text-text-primary">{entryValue}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary">Target</div>
            <div className="text-sm font-semibold text-text-primary">{targetValue}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-secondary">Stop</div>
            <div className="text-sm font-semibold text-text-primary">{stopValue}</div>
          </div>
        </div>

        <div className="flex min-w-48 flex-1 items-start gap-2 text-xs text-text-secondary">
          <span className="font-semibold text-text-secondary">Reasoning:</span>
          <span>{activeDecision.reasoning || 'Live backend decision received without a reasoning string.'}</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex flex-1 items-center gap-3">
          <div className="min-w-35 flex-1">
            <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-text-secondary">Quantity</label>
            <input
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(Number(e.target.value))}
              className="w-full rounded-lg border border-border-default bg-surface px-2 py-1.5 font-mono text-sm text-text-primary transition-all focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary"
              min="1"
              disabled={isHold}
            />
          </div>
          <div className="min-w-40 flex-1">
            <label className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-text-secondary">
              Est. Value (Price: ${activeDecision.price?.toFixed(2) || '---'})
            </label>
            <div className="flex h-8 w-full items-center rounded-lg border border-border-default bg-surface px-2 font-mono text-sm text-text-secondary">
              {activeDecision.price
                ? `$${(activeDecision.price * quantity).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                : 'N/A'}
            </div>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-3">
          <button
            onClick={() => rejectTrade(activeDecision)}
            className="rounded-xl border border-border-default bg-card px-4 py-2 text-xs font-bold text-text-secondary transition-colors hover:bg-elevated"
          >
            REJECT
          </button>
          <button
            onClick={() => executeTrade(activeDecision, quantity)}
            className={`rounded-lg px-4 py-2 text-xs font-bold uppercase transition-colors text-white ${isBuy ? 'bg-[#16A34A] hover:bg-[#047857]' : isHold ? 'bg-primary hover:bg-primary-hover' : 'bg-[#DC2626] hover:bg-red-800'}`}
          >
            {isHold ? 'ACKNOWLEDGE HOLD' : `${activeDecision.action_type}`}
          </button>
        </div>
      </div>
    </div>
  );
}