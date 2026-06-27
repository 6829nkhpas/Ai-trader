import React from 'react';
import { CheckCircle, AlertTriangle, Layers, Loader2, ArrowRight, Info } from 'lucide-react';

interface BrokerTabProps {
  broker: any;
  connectingBroker: boolean;
  handleBrokerConnect: () => Promise<void>;
  setConnectingBroker: (connecting: boolean) => void;
  formatDate: (date: any) => string;
}

const maskValue = (value: string | null | undefined, visibleChars = 5) => {
  if (!value) return 'N/A';
  if (value.length <= visibleChars * 2) return '••••••••';
  return `${value.slice(0, visibleChars)}••••••••${value.slice(-visibleChars)}`;
};

export default function BrokerTab({
  broker,
  connectingBroker,
  handleBrokerConnect,
  setConnectingBroker,
  formatDate,
}: BrokerTabProps) {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-extrabold text-white tracking-tight">Kite Broker Status</h2>
        <p className="text-xs text-text-secondary mt-1">Live market data streaming session & transaction authority details</p>
      </div>

      {broker ? (
        /* Connected State View */
        <div className="space-y-5">
          <div className="flex items-center gap-4 rounded-xl border border-emerald-500/20 bg-emerald-500/5 p-4">
            {broker.avatarUrl ? (
              <img 
                src={broker.avatarUrl} 
                alt={broker.userName || 'Broker Avatar'} 
                className="h-12 w-12 shrink-0 rounded-xl object-cover border border-emerald-500/30 shadow-md"
              />
            ) : (
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-emerald-500/15 text-emerald-400">
                <CheckCircle size={24} />
              </div>
            )}
            <div>
              <h4 className="text-sm font-bold text-white">Zerodha Kite Connected</h4>
              <p className="text-[11px] text-emerald-400/80 mt-0.5">
                Active Session for {broker.userName || 'User'}. Credentials securely stored in Tauri Key Vault.
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="rounded-xl border border-border-default/40 bg-surface/50 p-4">
              <span className="text-[10px] uppercase tracking-wider text-text-secondary block">Client User Name</span>
              <span className="text-sm font-bold text-white block mt-1">{broker.userName || 'N/A'}</span>
            </div>
            <div className="rounded-xl border border-border-default/40 bg-surface/50 p-4">
              <span className="text-[10px] uppercase tracking-wider text-text-secondary block">Broker User ID</span>
              <span className="text-sm font-mono font-bold text-white block mt-1">{broker.brokerUserId || 'N/A'}</span>
            </div>
            <div className="rounded-xl border border-border-default/40 bg-surface/50 p-4">
              <span className="text-[10px] uppercase tracking-wider text-text-secondary block">Account Type</span>
              <span className="text-xs font-bold text-white block mt-1 uppercase tracking-wide">
                {broker.userType?.replace('/', ' / ') || 'N/A'}
              </span>
            </div>
            <div className="rounded-xl border border-border-default/40 bg-surface/50 p-4">
              <span className="text-[10px] uppercase tracking-wider text-text-secondary block">Connected Account Email</span>
              <span className="text-sm font-bold text-white block mt-1 truncate">{broker.email || 'N/A'}</span>
            </div>
            <div className="rounded-xl border border-border-default/40 bg-surface/50 p-4">
              <span className="text-[10px] uppercase tracking-wider text-text-secondary block">Kite API Key</span>
              <span className="text-xs font-mono font-bold text-white block mt-1">{maskValue(broker.apiKey)}</span>
            </div>
            <div className="rounded-xl border border-border-default/40 bg-surface/50 p-4">
              <span className="text-[10px] uppercase tracking-wider text-text-secondary block">Public Token</span>
              <span className="text-xs font-mono font-bold text-white block mt-1">{maskValue(broker.publicToken, 6)}</span>
            </div>
            <div className="col-span-2 rounded-xl border border-border-default/40 bg-surface/50 p-4">
              <span className="text-[10px] uppercase tracking-wider text-text-secondary block">Session Auth Time</span>
              <span className="text-sm font-bold text-white block mt-1">{formatDate(broker.loginTime)}</span>
            </div>
            {/* Daily Reconnection Tip */}
            <div className="col-span-2 flex items-start gap-2.5 rounded-lg bg-elevated/40 border border-border-default/30 p-3.5 text-[10px] text-text-secondary leading-relaxed">
              <Info size={14} className="shrink-0 text-blue-400 mt-0.5" />
              <span>
                <strong>Daily Reconnection Required:</strong> Zerodha Kite API requires you to refresh your access token by reconnecting your broker account every day after <strong>6:00 AM IST</strong>.
              </span>
            </div>
          </div>

          {/* Permissions & Capabilities */}
          <div className="rounded-xl border border-border-default/40 bg-surface/50 p-5">
            <div className="flex items-center gap-2 mb-4">
              <Layers size={14} className="text-emerald-400" />
              <h4 className="text-xs font-bold text-white uppercase tracking-wider">Authorized Market Streams & Limits</h4>
            </div>
            
            <div className="space-y-4">
              <div>
                <span className="text-[9px] uppercase tracking-wider text-text-secondary block mb-1.5">Exchanges Supported</span>
                <div className="flex flex-wrap gap-1.5">
                  {broker.exchanges?.map((exch: string) => (
                    <span key={exch} className="rounded bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[9px] font-bold text-emerald-400 uppercase tracking-wide">
                      {exch}
                    </span>
                  )) || <span className="text-text-muted text-xs">None</span>}
                </div>
              </div>

              <div>
                <span className="text-[9px] uppercase tracking-wider text-text-secondary block mb-1.5">Supported Products</span>
                <div className="flex flex-wrap gap-1.5">
                  {broker.products?.map((prod: string) => (
                    <span key={prod} className="rounded bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[9px] font-bold text-emerald-400 uppercase tracking-wide">
                      {prod}
                    </span>
                  )) || <span className="text-text-muted text-xs">None</span>}
                </div>
              </div>

              <div>
                <span className="text-[9px] uppercase tracking-wider text-text-secondary block mb-1.5">Supported Order Types</span>
                <div className="flex flex-wrap gap-1.5">
                  {broker.orderTypes?.map((ord: string) => (
                    <span key={ord} className="rounded bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[9px] font-bold text-emerald-400 uppercase tracking-wide">
                      {ord}
                    </span>
                  )) || <span className="text-text-muted text-xs">None</span>}
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : (
        /* Disconnected / Connect Action Panel */
        <div className="rounded-2xl border border-border-default bg-surface/30 backdrop-blur-xl p-8 text-center transition-all duration-300">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-amber-500/10 border border-amber-500/20 text-amber-400">
            <AlertTriangle size={28} />
          </div>
          <h3 className="text-lg font-extrabold text-white">No Broker Linked</h3>
          <p className="text-xs text-text-secondary mt-1.5 leading-relaxed max-w-sm mx-auto">
            Authorize your Zerodha Kite broker connection to enable live institutional market feed ingestion and portfolio execution.
          </p>

          <div className="mt-6 flex flex-col items-center gap-4">
            <div className="max-w-sm mx-auto flex items-start gap-2.5 rounded-lg bg-amber-500/10 border border-amber-500/20 p-3.5 text-[10px] text-amber-400 text-left leading-relaxed">
              <Info size={14} className="shrink-0 mt-0.5" />
              <span>
                <strong>Daily Reconnection Required:</strong> Zerodha access tokens expire daily. Please connect your Zerodha account every day after <strong>6:00 AM IST</strong> to start a new active session.
              </span>
            </div>

            {!connectingBroker ? (
              <button
                onClick={handleBrokerConnect}
                className="flex items-center gap-2.5 rounded-xl bg-emerald-500 hover:bg-emerald-600 px-6 py-3.5 text-xs font-bold text-white transition-all active:scale-[0.98] shadow-md shadow-emerald-500/15"
              >
                <span>CONNECT ZERODHA KITE</span>
                <ArrowRight size={14} />
              </button>
            ) : (
              <div className="flex flex-col items-center">
                <Loader2 size={24} className="animate-spin text-emerald-400 mb-2" />
                <span className="text-xs text-text-secondary">Waiting for Zerodha authentication...</span>
                <button
                  onClick={() => setConnectingBroker(false)}
                  className="mt-3 text-[10px] font-bold text-text-secondary hover:text-white uppercase transition-colors"
                >
                  Cancel
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
