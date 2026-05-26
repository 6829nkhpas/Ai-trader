'use client';

import React from 'react';

interface VerificationFormProps {
  side: 'BUY' | 'SELL';
  setSide: (side: 'BUY' | 'SELL') => void;
  entry: string;
  setEntry: (entry: string) => void;
  setHasManuallySetEntry: (val: boolean) => void;
  stopLoss: string;
  setStopLoss: (sl: string) => void;
  setHasManuallySetSL: (val: boolean) => void;
  takeProfit: string;
  setTakeProfit: (tp: string) => void;
  setHasManuallySetTP: (val: boolean) => void;
  userAnalysis: string;
  setUserAnalysis: (val: string) => void;
  slPercent: string | null;
  tpPercent: string | null;
  riskToReward: string | null;
}

export default function VerificationForm({
  side,
  setSide,
  entry,
  setEntry,
  setHasManuallySetEntry,
  stopLoss,
  setStopLoss,
  setHasManuallySetSL,
  takeProfit,
  setTakeProfit,
  setHasManuallySetTP,
  userAnalysis,
  setUserAnalysis,
  slPercent,
  tpPercent,
  riskToReward,
}: VerificationFormProps) {
  return (
    <div className="mx-3 mt-3 p-3 rounded-xl border border-slate-800 bg-slate-900/30 backdrop-blur-md flex flex-col gap-3">
      <div className="flex items-center justify-between border-b border-slate-800 pb-1.5">
        <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Configure Setup</span>
        <span className="text-[9px] text-slate-500">Auto-filled via NSE LTP</span>
      </div>

      {/* Side selector */}
      <div className="flex rounded-lg bg-slate-950 p-0.5 border border-slate-800/50">
        <button
          type="button"
          onClick={() => setSide('BUY')}
          className={`flex-grow py-1 rounded-md text-[10px] font-bold transition-all ${
            side === 'BUY'
              ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/20'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          BUY / LONG
        </button>
        <button
          type="button"
          onClick={() => setSide('SELL')}
          className={`flex-grow py-1 rounded-md text-[10px] font-bold transition-all ${
            side === 'SELL'
              ? 'bg-rose-500/15 text-rose-400 border border-rose-500/20'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          SELL / SHORT
        </button>
      </div>

      {/* Input fields */}
      <div className="grid grid-cols-3 gap-2">
        <div className="flex flex-col gap-1">
          <label className="text-[8px] font-semibold text-slate-400 uppercase">Entry Price</label>
          <input
            type="number"
            step="any"
            value={entry}
            onChange={(e) => {
              setEntry(e.target.value);
              setHasManuallySetEntry(true);
            }}
            className="w-full bg-slate-950/80 border border-slate-800 rounded px-2 py-1 text-xs text-white font-mono focus:border-emerald-500 focus:outline-none"
          />
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-[8px] font-semibold text-slate-400 uppercase">Stop Loss</label>
          <input
            type="number"
            step="any"
            value={stopLoss}
            onChange={(e) => {
              setStopLoss(e.target.value);
              setHasManuallySetSL(true);
            }}
            className={`w-full bg-slate-950/80 border rounded px-2 py-1 text-xs text-white font-mono focus:outline-none ${
              side === 'BUY'
                ? 'border-rose-950/50 focus:border-rose-500'
                : 'border-emerald-950/50 focus:border-emerald-500'
            }`}
          />
          {slPercent && (
            <span
              className={`text-[8px] self-end font-mono ${
                parseFloat(slPercent) < 0 ? 'text-rose-400' : 'text-emerald-400'
              }`}
            >
              {slPercent}%
            </span>
          )}
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-[8px] font-semibold text-slate-400 uppercase">Take Profit</label>
          <input
            type="number"
            step="any"
            value={takeProfit}
            onChange={(e) => {
              setTakeProfit(e.target.value);
              setHasManuallySetTP(true);
            }}
            className={`w-full bg-slate-950/80 border rounded px-2 py-1 text-xs text-white font-mono focus:outline-none ${
              side === 'BUY'
                ? 'border-emerald-950/50 focus:border-emerald-500'
                : 'border-rose-950/50 focus:border-rose-500'
            }`}
          />
          {tpPercent && (
            <span
              className={`text-[8px] self-end font-mono ${
                parseFloat(tpPercent) >= 0 ? 'text-emerald-400' : 'text-rose-400'
              }`}
            >
              {parseFloat(tpPercent) >= 0 ? '+' : ''}
              {tpPercent}%
            </span>
          )}
        </div>
      </div>

      {/* Risk-to-Reward Badge */}
      {riskToReward && (
        <div className="flex justify-between items-center rounded-lg bg-slate-950 p-2 border border-slate-800/40 text-[10px]">
          <span className="text-slate-400 font-semibold">Risk:Reward Ratio</span>
          <span
            className={`font-black font-mono px-2 py-0.5 rounded ${
              parseFloat(riskToReward) >= 2.0
                ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                : parseFloat(riskToReward) >= 1.5
                ? 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
            }`}
          >
            1 : {riskToReward}
          </span>
        </div>
      )}

      {/* User analysis note */}
      <div className="flex flex-col gap-1.5">
        <label className="text-[8px] font-semibold text-slate-400 uppercase">My Trade Logic / Notes</label>
        <textarea
          value={userAnalysis}
          onChange={(e) => setUserAnalysis(e.target.value)}
          placeholder="Describe your reasoning (e.g. buying the bounce on ema-21, MACD divergence)"
          className="w-full bg-slate-950/80 border border-slate-800 rounded px-2 py-1.5 text-xs text-slate-200 placeholder-slate-600 focus:border-emerald-500 focus:outline-none min-h-[60px] max-h-[120px] resize-y leading-relaxed"
        />
      </div>
    </div>
  );
}
