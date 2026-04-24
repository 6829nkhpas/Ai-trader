'use client';

import React, { useState } from 'react';
import { useTradeStore } from '../../store/useTradeStore';
import { DollarSign, Briefcase } from 'lucide-react';

export default function OrderExecutionPanel() {
  const { activeDecision, portfolioBalance, positions, executeTrade, rejectTrade } = useTradeStore();
  const [quantity, setQuantity] = useState<number>(100);

  if (!activeDecision) {
    return (
      <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 shadow-sm flex flex-col gap-4">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-2">
          <Briefcase size={16} /> Portfolio State
        </h2>

        <div className="flex items-center justify-between">
          <span className="text-slate-300">Available Balance:</span>
          <span className="text-xl font-bold text-slate-100 flex items-center">
            <DollarSign size={20} className="text-emerald-500 mr-1" />
            {portfolioBalance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
        </div>

        {Object.keys(positions).length > 0 && (
          <div className="mt-2">
            <span className="text-xs text-slate-500 uppercase font-semibold mb-2 block">Active Positions</span>
            <div className="flex flex-wrap gap-2">
              {Object.entries(positions).map(([sym, qty]) => (
                <div key={sym} className="px-3 py-1 bg-slate-800 rounded text-sm text-slate-300 border border-slate-700">
                  <span className="font-bold text-slate-200">{sym}</span>: {qty}
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
  const actionColor = isBuy ? 'text-emerald-500' : isHold ? 'text-blue-500' : 'text-red-500';
  const buttonColor = isBuy
    ? 'bg-emerald-600 hover:bg-emerald-500 text-white'
    : isHold
      ? 'bg-blue-600 hover:bg-blue-500 text-white'
      : 'bg-red-600 hover:bg-red-500 text-white';

  return (
    <div className="bg-slate-900 border border-blue-900/50 rounded-lg p-5 shadow-lg flex flex-col gap-5 animate-in fade-in zoom-in-95 duration-200">
      <div className="flex justify-between items-start">
        <div>
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Execute Automaton</h2>
          <div className="text-lg font-medium text-slate-200 mt-1">
            Recommended Action: <span className={`font-bold ${actionColor}`}>{activeDecision.action_type}</span> {activeDecision.symbol}
          </div>
        </div>
        <div className="text-right">
          <div className="text-sm text-slate-500">Conviction</div>
          <div className="text-xl font-bold text-slate-100">{(activeDecision.final_conviction_score * 100).toFixed(1)}%</div>
        </div>
      </div>

      <div className="bg-slate-950/50 p-4 rounded-md border border-slate-800/80 text-sm text-slate-300">
        <span className="font-semibold text-slate-400 block mb-1">Reasoning:</span>
        {activeDecision.reasoning || 'Aggregated signals favor this execution.'}
      </div>

      <div className="flex items-center gap-4">
        <div className="flex-1">
          <label className="text-xs text-slate-400 font-semibold uppercase block mb-2">Quantity (Shares)</label>
          <input
            type="number"
            value={quantity}
            onChange={(e) => setQuantity(Number(e.target.value))}
            className="w-full bg-slate-950 border border-slate-700 rounded p-2 text-slate-100 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all font-mono"
            min="1"
            disabled={isHold}
          />
        </div>
        <div className="flex-1">
          <label className="text-xs text-slate-400 font-semibold uppercase block mb-2">Est. Value (Price: ${activeDecision.price?.toFixed(2) || '---'})</label>
          <div className="w-full bg-slate-900/50 border border-slate-800 rounded p-2 text-slate-300 font-mono flex items-center h-[42px]">
            {activeDecision.price ? `$${(activeDecision.price * quantity).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : 'N/A'}
          </div>
        </div>
      </div>

      <div className="flex gap-3 mt-2">
        <button
          onClick={() => rejectTrade(activeDecision)}
          className="flex-1 py-3 px-4 rounded font-bold text-sm bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 transition-colors"
        >
          REJECT / IGNORE
        </button>
        <button
          onClick={() => executeTrade(activeDecision, quantity)}
          className={`flex-[2] py-3 px-4 rounded font-bold text-sm transition-colors uppercase ${buttonColor}`}
        >
          {isHold ? 'ACKNOWLEDGE HOLD' : `ACCEPT & ${activeDecision.action_type}`}
        </button>
      </div>
    </div>
  );
}