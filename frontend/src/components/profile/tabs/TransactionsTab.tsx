import React from 'react';
import { Loader2, FileText, TrendingUp, TrendingDown } from 'lucide-react';

interface SqlTrade {
  id: string;
  symbol: string;
  entry_price: number;
  exit_price: number;
  pnl: number;
  type: string;
  size: number;
  timestamp: number;
}

interface TransactionsTabProps {
  loadingTrades: boolean;
  sqlTrades: SqlTrade[];
  formatSqlDate: (timestamp: number) => string;
}

export default function TransactionsTab({
  loadingTrades,
  sqlTrades,
  formatSqlDate,
}: TransactionsTabProps) {
  return (
    <div className="flex flex-col h-full space-y-4">
      <div>
        <h2 className="text-xl font-extrabold text-white tracking-tight">Transaction Journal</h2>
        <p className="text-xs text-text-secondary mt-1">Completed trades stored permanently in the local SQLite db</p>
      </div>

      <div className="flex-1 min-h-0 rounded-xl border border-border-default/40 bg-muted/30 overflow-hidden flex flex-col">
        {loadingTrades ? (
          <div className="flex flex-1 flex-col items-center justify-center p-8">
            <Loader2 size={32} className="animate-spin text-emerald-400 mb-2" />
            <span className="text-xs text-text-secondary">Loading local trade journal...</span>
          </div>
        ) : sqlTrades.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center p-8 text-center">
            <FileText size={40} className="text-text-secondary mb-3 opacity-40" />
            <h4 className="text-sm font-bold text-white">No Completed Trades</h4>
            <p className="text-xs text-text-secondary mt-1 leading-normal max-w-xs mx-auto">
              Transactions will appear here automatically when paper trading positions are closed or exit criteria are triggered.
            </p>
          </div>
        ) : (
          <div className="flex-1 overflow-auto">
            <table className="w-full text-left border-collapse">
              <thead className="sticky top-0 z-10 bg-elevated border-b border-border-default/40">
                <tr>
                  <th className="px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-text-secondary">Symbol</th>
                  <th className="px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-text-secondary">Action</th>
                  <th className="px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-text-secondary">Size</th>
                  <th className="px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-text-secondary">Entry</th>
                  <th className="px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-text-secondary">Exit</th>
                  <th className="px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-text-secondary">PnL (₹)</th>
                  <th className="px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-text-secondary text-right">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-default/20">
                {sqlTrades.map((t) => {
                  const isProfit = t.pnl > 0;
                  return (
                    <tr key={t.id} className="hover:bg-elevated/15 transition-colors">
                      <td className="px-4 py-3 text-xs font-bold text-white">{t.symbol}</td>
                      <td className="px-4 py-3 text-xs">
                        <span className={`rounded-md px-2 py-0.5 text-[9px] font-bold ${
                          t.type === 'BUY' 
                            ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400' 
                            : 'bg-red-500/10 border border-red-500/20 text-red-400'
                        }`}>
                          {t.type}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-text-primary font-medium">{t.size}</td>
                      <td className="px-4 py-3 text-xs text-text-primary">₹{t.entry_price.toFixed(2)}</td>
                      <td className="px-4 py-3 text-xs text-text-primary">₹{t.exit_price.toFixed(2)}</td>
                      <td className={`px-4 py-3 text-xs font-bold ${isProfit ? 'text-emerald-400' : t.pnl < 0 ? 'text-red-400' : 'text-text-secondary'}`}>
                        <div className="flex items-center gap-1">
                          {isProfit ? <TrendingUp size={12} /> : t.pnl < 0 ? <TrendingDown size={12} /> : null}
                          <span>{isProfit ? '+' : ''}{t.pnl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-xs text-text-secondary text-right font-mono">{formatSqlDate(t.timestamp)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
