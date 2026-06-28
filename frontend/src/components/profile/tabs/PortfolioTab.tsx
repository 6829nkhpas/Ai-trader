import React from 'react';
import { Activity } from 'lucide-react';
import { VirtualPosition } from '../../../store/useTradeStore';

interface PortfolioTabProps {
  paperPortfolio: any;
}

export default function PortfolioTab({ paperPortfolio }: PortfolioTabProps) {
  return (
    <div className="flex flex-col h-full space-y-5">
      <div>
        <h2 className="text-xl font-extrabold text-text-primary tracking-tight">Paper Trading State</h2>
        <p className="text-xs text-text-secondary mt-1">Real-time mock balance, active risk layers, and open orders</p>
      </div>

      {/* Statistics Panel */}
      <div className="flex flex-col border-t border-border-default mb-4">
        {[
          { 
            label: 'Simulated Balance', 
            value: (
              <span className="text-emerald-400 font-mono font-bold">
                ₹{paperPortfolio?.balance?.toLocaleString(undefined, { minimumFractionDigits: 2 }) || '1,000,000.00'}
              </span>
            )
          },
          { 
            label: 'Active Open Positions', 
            value: <span className="font-semibold text-text-primary">{paperPortfolio?.active_positions?.length || 0}</span>
          },
          { 
            label: 'History Count', 
            value: <span className="font-semibold text-text-primary">{paperPortfolio?.trade_history?.length || 0}</span>
          }
        ].map((row, i) => (
          <div key={i} className="flex items-center justify-between py-3 border-b border-border-default px-1">
            <span className="text-[10px] uppercase tracking-wider text-text-secondary">{row.label}</span>
            <div className="text-xs">{row.value}</div>
          </div>
        ))}
      </div>

      {/* Active Positions Sub-grid */}
      <div className="flex-1 min-h-0 flex flex-col space-y-2.5">
        <div className="flex items-center justify-between px-1">
          <div className="flex items-center gap-2">
            <Activity size={14} className="text-emerald-400 animate-pulse" />
            <h3 className="text-xs font-bold text-text-primary uppercase tracking-wider">Active Open Positions</h3>
          </div>
          <span className="text-[10px] text-text-secondary font-mono">Tauri Engine Sync</span>
        </div>

        <div className="flex-1 min-h-0 rounded-none border border-border-default/40 bg-muted/30 overflow-auto">
          {!paperPortfolio || paperPortfolio.active_positions.length === 0 ? (
            <div className="flex h-32 flex-col items-center justify-center text-center p-4">
              <p className="text-xs text-text-secondary font-medium">No Active Open Positions</p>
              <p className="text-[10px] text-text-muted mt-1 leading-normal max-w-xs">
                Open the trading charts panel, set up risk criteria parameters, and execute buying/selling transactions to engage simulated tracking.
              </p>
            </div>
          ) : (
            <table className="w-full text-left border-collapse">
              <thead className="bg-elevated/80 border-b border-border-default/40">
                <tr>
                  <th className="px-4 py-2.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary">Symbol</th>
                  <th className="px-4 py-2.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary">Side</th>
                  <th className="px-4 py-2.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary">Qty</th>
                  <th className="px-4 py-2.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary">Entry Price</th>
                  <th className="px-4 py-2.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary">Stop Loss</th>
                  <th className="px-4 py-2.5 text-[9px] font-bold uppercase tracking-wider text-text-secondary text-right">Take Profit</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-default/10">
                {paperPortfolio.active_positions.map((pos: VirtualPosition) => (
                  <tr key={pos.id} className="hover:bg-elevated/10">
                    <td className="px-4 py-2.5 text-xs font-bold text-text-primary">{pos.symbol}</td>
                    <td className="px-4 py-2.5 text-xs">
                      <span className={`rounded-none px-1.5 py-0.5 text-[8px] font-bold border ${
                        pos.side === 'BUY'
                          ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
                          : 'bg-rose-500/10 border-rose-500/20 text-rose-400'
                      }`}>
                        {pos.side}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-text-primary font-mono">{pos.quantity}</td>
                    <td className="px-4 py-2.5 text-xs text-text-primary font-mono">₹{pos.entry_price.toFixed(2)}</td>
                    <td className="px-4 py-2.5 text-xs text-rose-400/90 font-mono">₹{pos.stop_loss.toFixed(2)}</td>
                    <td className="px-4 py-2.5 text-xs text-emerald-400/95 text-right font-mono">₹{pos.take_profit.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
