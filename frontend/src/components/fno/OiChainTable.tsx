'use client';

import React from 'react';
import { type FnoViewState } from './viewModel';

interface OiChainTableProps {
  viewState: FnoViewState & { kind: 'ready' | 'partial' };
}

function NA() {
  return (
    <span className="inline-flex items-center rounded px-1 py-0.5 text-[8px] font-bold uppercase tracking-widest border border-border-default bg-elevated text-text-muted">
      N/A
    </span>
  );
}

export default function OiChainTable({ viewState }: OiChainTableProps) {
  const rows = viewState.oi.points.slice(0, 20); // show up to 20 strikes
  if (rows.length === 0) {
    return (
      <div className="px-3 py-2">
        <NA />
      </div>
    );
  }

  const maxOi = Math.max(...rows.map((r) => Math.max(r.callOi ?? 0, r.putOi ?? 0)), 1);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[9px] font-mono border-collapse">
        <thead>
          <tr className="bg-elevated/40 text-text-muted uppercase tracking-wider">
            <th className="px-2 py-1 text-right font-bold">Call OI</th>
            <th className="px-2 py-1 text-center font-bold">Strike</th>
            <th className="px-2 py-1 text-left font-bold">Put OI</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-default/40">
          {rows.map((row) => {
            const cWidth = row.callOi ? Math.round((row.callOi / maxOi) * 40) : 0;
            const pWidth = row.putOi ? Math.round((row.putOi / maxOi) * 40) : 0;
            const isMaxPain = viewState.oi.maxPain === row.strike;
            const isSupport = viewState.oi.support === row.strike;
            const isResist = viewState.oi.resistance === row.strike;

            return (
              <tr
                key={row.strike}
                className={`transition-colors hover:bg-elevated/20 ${
                  isMaxPain ? 'bg-amber-500/5' :
                  isSupport ? 'bg-emerald-500/5' :
                  isResist ? 'bg-rose-500/5' : ''
                }`}
              >
                {/* Call OI bar — right aligned */}
                <td className="px-2 py-0.5 text-right">
                  <div className="flex items-center justify-end gap-1">
                    {row.callOi !== null && (
                      <div className="h-1.5 rounded-full bg-cyan-500/60" style={{ width: `${cWidth}px` }} />
                    )}
                    <span className="text-cyan-400 tabular-nums w-16 text-right">
                      {row.callOi !== null ? (row.callOi / 1000).toFixed(0) + 'K' : '—'}
                    </span>
                  </div>
                </td>
                {/* Strike */}
                <td className={`px-2 py-0.5 text-center font-bold ${
                  isMaxPain ? 'text-amber-400' :
                  isSupport ? 'text-emerald-400' :
                  isResist ? 'text-rose-400' : 'text-text-primary'
                }`}>
                  {row.strike.toLocaleString('en-IN')}
                  {isMaxPain && <span className="ml-0.5 text-[7px] text-amber-400">●</span>}
                  {isSupport && <span className="ml-0.5 text-[7px] text-emerald-400">▲</span>}
                  {isResist && <span className="ml-0.5 text-[7px] text-rose-400">▼</span>}
                </td>
                {/* Put OI bar — left aligned */}
                <td className="px-2 py-0.5">
                  <div className="flex items-center gap-1">
                    <span className="text-rose-400 tabular-nums w-16">
                      {row.putOi !== null ? (row.putOi / 1000).toFixed(0) + 'K' : '—'}
                    </span>
                    {row.putOi !== null && (
                      <div className="h-1.5 rounded-full bg-rose-500/60" style={{ width: `${pWidth}px` }} />
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="flex gap-4 px-3 py-1 text-[8px] text-text-muted">
        <span><span className="text-amber-400">●</span> Max Pain</span>
        <span><span className="text-emerald-400">▲</span> Support</span>
        <span><span className="text-rose-400">▼</span> Resistance</span>
      </div>
    </div>
  );
}
