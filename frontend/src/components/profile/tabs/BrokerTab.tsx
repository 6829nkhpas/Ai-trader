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
        <h2 className="text-xl font-extrabold text-text-primary tracking-tight">Kite Broker Status</h2>
        <p className="text-xs text-text-secondary mt-1">Live market data streaming session & transaction authority details</p>
      </div>

      {broker ? (
        /* Connected State View */
        <div className="space-y-5">
          <div className="flex items-center gap-4 rounded-none border border-emerald-500/20 bg-emerald-500/5 p-4">
            {broker.avatarUrl ? (
              <img 
                src={broker.avatarUrl} 
                alt={broker.userName || 'Broker Avatar'} 
                className="h-12 w-12 shrink-0 rounded-none object-cover border border-border-default shadow-md"
              />
            ) : (
              <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-none bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                <CheckCircle size={24} />
              </div>
            )}
            <div>
              <h4 className="text-sm font-bold text-text-primary">Zerodha Kite Connected</h4>
              <p className="text-[11px] text-emerald-400/80 mt-0.5">
                Active Session for {broker.userName || 'User'}. Credentials securely stored in Tauri Key Vault.
              </p>
            </div>
          </div>

          <div className="flex flex-col border-t border-border-default">
            {[
              { label: 'Client User Name', value: broker.userName },
              { label: 'Broker User ID', value: broker.brokerUserId, mono: true },
              { label: 'Account Type', value: broker.userType?.replace('/', ' / ') },
              { label: 'Connected Account Email', value: broker.email },
              { label: 'Kite API Key', value: maskValue(broker.apiKey), mono: true },
              { label: 'Public Token', value: maskValue(broker.publicToken, 6), mono: true },
              { label: 'Session Auth Time', value: formatDate(broker.loginTime) }
            ].map((row, i) => (
              <div key={i} className="flex items-center justify-between py-3 border-b border-border-default px-1">
                <span className="text-[10px] uppercase tracking-wider text-text-secondary">{row.label}</span>
                <span className={`text-xs font-semibold text-text-primary ${row.mono ? 'font-mono' : ''}`}>{row.value || 'N/A'}</span>
              </div>
            ))}
          </div>

          {/* Daily Reconnection Tip */}
          <div className="flex items-start gap-2.5 rounded-none bg-blue-500/5 border border-blue-500/20 p-3.5 text-[10px] text-blue-400 leading-relaxed">
            <Info size={14} className="shrink-0 text-blue-400 mt-0.5" />
            <span>
              <strong>Daily Reconnection Required:</strong> Zerodha Kite API requires you to refresh your access token by reconnecting your broker account every day after <strong>6:00 AM IST</strong>.
            </span>
          </div>

          {/* Permissions & Capabilities */}
          <div className="rounded-none border border-border-default/40 bg-surface/50 p-5">
            <div className="flex items-center gap-2 mb-4">
              <Layers size={14} className="text-emerald-400" />
              <h4 className="text-xs font-bold text-text-primary uppercase tracking-wider">Authorized Market Streams & Limits</h4>
            </div>
            
            <div className="space-y-4">
              <div>
                <span className="text-[9px] uppercase tracking-wider text-text-secondary block mb-1.5">Exchanges Supported</span>
                <div className="flex flex-wrap gap-1.5">
                  {broker.exchanges?.map((exch: string) => (
                    <span key={exch} className="rounded-none bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[9px] font-bold text-emerald-400 uppercase tracking-wide">
                      {exch}
                    </span>
                  )) || <span className="text-text-muted text-xs">None</span>}
                </div>
              </div>

              <div>
                <span className="text-[9px] uppercase tracking-wider text-text-secondary block mb-1.5">Supported Products</span>
                <div className="flex flex-wrap gap-1.5">
                  {broker.products?.map((prod: string) => (
                    <span key={prod} className="rounded-none bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[9px] font-bold text-emerald-400 uppercase tracking-wide">
                      {prod}
                    </span>
                  )) || <span className="text-text-muted text-xs">None</span>}
                </div>
              </div>

              <div>
                <span className="text-[9px] uppercase tracking-wider text-text-secondary block mb-1.5">Supported Order Types</span>
                <div className="flex flex-wrap gap-1.5">
                  {broker.orderTypes?.map((ord: string) => (
                    <span key={ord} className="rounded-none bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[9px] font-bold text-emerald-400 uppercase tracking-wide">
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
        <div className="rounded-none border border-amber-500/15 bg-amber-500/5 p-8 text-center transition-all duration-300">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-none bg-amber-500/10 border border-amber-500/20 text-amber-400">
            <AlertTriangle size={28} />
          </div>
          <h3 className="text-lg font-extrabold text-text-primary">No Broker Linked</h3>
          <p className="text-xs text-text-secondary mt-1.5 leading-relaxed max-w-sm mx-auto">
            Authorize your Zerodha Kite broker connection to enable live institutional market feed ingestion and portfolio execution.
          </p>

          <div className="mt-6 flex flex-col items-center gap-4">
            <div className="max-w-sm mx-auto flex items-start gap-2.5 rounded-none bg-amber-500/5 border border-amber-500/10 p-3.5 text-[10px] text-amber-400 text-left leading-relaxed">
              <Info size={14} className="shrink-0 mt-0.5 text-amber-400" />
              <span>
                <strong>Daily Reconnection Required:</strong> Zerodha access tokens expire daily. Please connect your Zerodha account every day after <strong>6:00 AM IST</strong> to start a new active session.
              </span>
            </div>

            {!connectingBroker ? (
              <button
                onClick={handleBrokerConnect}
                className="flex items-center gap-2.5 rounded-none bg-text-primary text-surface hover:bg-text-secondary px-5 py-3 text-xs font-bold transition-all active:scale-[0.98] border border-text-primary"
              >
                <span>CONNECT ZERODHA KITE</span>
                <ArrowRight size={14} />
              </button>
            ) : (
              <div className="flex flex-col items-center">
                <Loader2 size={24} className="animate-spin text-text-muted mb-2" />
                <span className="text-xs text-text-secondary">Waiting for Zerodha authentication...</span>
                <button
                  onClick={() => setConnectingBroker(false)}
                  className="mt-3 text-[10px] font-bold text-text-secondary hover:text-text-primary uppercase transition-colors"
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
