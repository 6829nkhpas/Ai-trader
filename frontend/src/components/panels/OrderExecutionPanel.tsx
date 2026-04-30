'use client';

import React, { useState } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { DollarSign, Briefcase } from 'lucide-react';

export default function OrderExecutionPanel() {
  const { activeDecision, portfolioBalance, positions, executeTrade, rejectTrade } = useTradeStore();
  const [quantity, setQuantity] = useState<number>(100);

  if (!activeDecision) {
    return (
      <div className="flex flex-col gap-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wider text-slate-500">
          <Briefcase size={16} /> Portfolio State
        </h2>

        <div className="flex items-center justify-between">
          <span className="text-slate-600">Available Balance:</span>
          <span className="flex items-center text-xl font-bold text-slate-900">
            <DollarSign size={20} className="mr-1 text-emerald-500" />
            {portfolioBalance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
        </div>

        {Object.keys(positions).length > 0 && (
          <div className="mt-2">
            <span className="mb-2 block text-xs font-semibold uppercase text-slate-500">Active Positions</span>
            <div className="flex flex-wrap gap-2">
              {Object.entries(positions).map(([sym, qty]) => (
                <div key={sym} className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-sm text-slate-700">
                  <span className="font-bold text-slate-900">{sym}</span>: {qty}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    );
  }

  const isBuy = activeDecision.action_type === 'BUY';
  const isHold = activeDecision.action_type === 'HOLD';
  const actionColor = isBuy ? 'text-emerald-600' : isHold ? 'text-blue-600' : 'text-red-600';
  const buttonColor = isBuy
    ? 'bg-emerald-600 hover:bg-emerald-500 text-white'
    : isHold
      ? 'bg-blue-600 hover:bg-blue-500 text-white'
      : 'bg-red-600 hover:bg-red-500 text-white';

  return (
    <div className="flex flex-col gap-5 rounded-2xl border border-blue-100 bg-white p-5 shadow-sm animate-in fade-in zoom-in-95 duration-200">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-500">Execute Automaton</h2>
          <div className="mt-1 text-lg font-medium text-slate-900">
            Recommended Action: <span className={`font-bold ${actionColor}`}>{activeDecision.action_type}</span> {activeDecision.symbol}
          </div>
        </div>
        <div className="text-right">
          <div className="text-sm text-slate-500">Conviction</div>
          <div className="text-xl font-bold text-slate-900">{activeDecision.final_conviction_score}%</div>
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
        <span className="mb-1 block font-semibold text-slate-500">Reasoning:</span>
        {activeDecision.reasoning || 'Live backend decision received without a reasoning string.'}
      </div>

      <div className="flex items-center gap-4">
        <div className="flex-1">
          <label className="mb-2 block text-xs font-semibold uppercase text-slate-500">Quantity (Shares)</label>
          <input
            type="number"
            value={quantity}
            onChange={(e) => setQuantity(Number(e.target.value))}
            className="w-full rounded-lg border border-slate-200 bg-white p-2 font-mono text-slate-900 transition-all focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            min="1"
            disabled={isHold}
          />
        </div>
        <div className="flex-1">
          <label className="mb-2 block text-xs font-semibold uppercase text-slate-500">Est. Value (Price: ${activeDecision.price?.toFixed(2) || '---'})</label>
          <div className="flex h-10 w-full items-center rounded-lg border border-slate-200 bg-slate-50 p-2 font-mono text-slate-700">
            {activeDecision.price ? `$${(activeDecision.price * quantity).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : 'N/A'}
          </div>
        </div>
      </div>

      <div className="flex gap-3 mt-2">
        <button
          onClick={() => rejectTrade(activeDecision)}
          className="flex-1 rounded-xl border border-slate-200 bg-slate-100 px-4 py-3 text-sm font-bold text-slate-700 transition-colors hover:bg-slate-200"
        >
          REJECT / IGNORE
        </button>
        <button
          onClick={() => executeTrade(activeDecision, quantity)}
          className={`flex-2 rounded-xl px-4 py-3 text-sm font-bold uppercase transition-colors ${buttonColor}`}
        >
          {isHold ? 'ACKNOWLEDGE HOLD' : `ACCEPT & ${activeDecision.action_type}`}
        </button>
      </div>
    </div>
  );
}