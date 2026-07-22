import React, { useState } from 'react';
import { useAuthStore } from '../../store/useAuthStore';
import { Landmark, ArrowRight, Loader2, Info, ArrowLeft } from 'lucide-react';

export default function BrokerConnectCard() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const [loading, setLoading] = useState(false);

  // "Back" here means: drop the current session and return to the auth
  // overlay (login / sign-up). The overlay is rendered automatically by
  // page.tsx whenever `isAuthenticated` is false, so calling logout is
  // all that's needed — no router navigation required.
  const handleBack = () => {
    if (loading) return;
    logout();
  };

  const handleConnect = async () => {
    setLoading(true);
    const userId = user?.id || '';
    const authBase = process.env.NEXT_PUBLIC_AUTH_SERVICE_URL || 'http://localhost:3001';
    const connectUrl = `${authBase}/api/broker/zerodha/connect?userId=${userId}`;

    console.log(`[BrokerConnect] Redirecting to: ${connectUrl}`);

    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('open_browser', { url: connectUrl });
    } catch (err) {
      console.warn('[BrokerConnect] Failed to use Tauri open_browser command, falling back to window.open:', err);
      if (typeof window !== 'undefined') {
        window.open(connectUrl, '_blank');
      }
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-background px-4">
      {/* Background layer */}
      <div className="absolute inset-0 bg-[linear-gradient(to_bottom,#0b0f19_0%,#090d16_100%)]">
        <div className="absolute inset-0 opacity-5 bg-[radial-gradient(#10b981_1px,transparent_1px)] [background-size:32px_32px]"></div>
      </div>

      <div className="relative w-full max-w-md rounded-2xl border border-border-default bg-surface/30 backdrop-blur-xl p-8 shadow-2xl transition-all duration-300">
        <div className="absolute -top-12 -left-12 -z-10 h-40 w-40 rounded-full bg-emerald-500/5 blur-3xl"></div>

        {/* Back to login/signup */}
        <button
          type="button"
          onClick={handleBack}
          disabled={loading}
          className="absolute left-3 top-3 flex items-center gap-1.5 rounded-md px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-text-muted hover:text-text-primary hover:bg-elevated/60 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          title="Back to login"
        >
          <ArrowLeft size={12} />
          Back
        </button>

        <div className="mb-6 text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-500/10 border border-emerald-500/25 text-emerald-400">
            <Landmark size={28} />
          </div>
          <h2 className="text-xl font-black text-white">Connect Broker Account</h2>
          <p className="text-xs text-text-muted mt-1.5 leading-relaxed max-w-xs mx-auto">
            Authorize your Zerodha Kite broker connection to enable live institutional market feed ingestion and portfolio execution.
          </p>
        </div>

        {!loading ? (
          <div className="space-y-4">
            {/* Daily Reconnection Reminder */}
            <div className="flex items-start gap-2.5 rounded-lg bg-amber-500/10 border border-amber-500/20 p-3.5 text-[10px] text-amber-400 leading-relaxed">
              <Info size={14} className="shrink-0 text-amber-400 mt-0.5" />
              <span>
                <strong>Daily Reconnection Required:</strong> Zerodha access tokens expire daily. Please connect your Zerodha account every day after <strong>6:00 AM IST</strong> to start a new active session.
              </span>
            </div>

            <button
              onClick={handleConnect}
              className="flex w-full items-center justify-between gap-3 rounded-xl bg-[#10b981] hover:bg-[#059669] p-4 text-xs font-bold text-white shadow-lg shadow-[#10b981]/10 hover:shadow-[#10b981]/20 active:scale-[0.98] transition-all"
            >
              <span className="tracking-wide">CONNECT ZERODHA KITE</span>
              <ArrowRight size={16} />
            </button>

            <div className="flex items-start gap-2.5 rounded-lg bg-elevated/40 border border-border-default p-3.5 text-[10px] text-text-muted leading-relaxed">
              <Info size={14} className="shrink-0 text-blue-400 mt-0.5" />
              <span>
                <strong>Note:</strong> Zerodha access tokens are encrypted and cached in Tauri Stronghold vault storage, never reaching cloud servers or browser storage.
              </span>
            </div>
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-6 text-center">
            <Loader2 size={36} className="animate-spin text-emerald-400 mb-4" />
            <p className="text-sm font-semibold text-white tracking-wide">Waiting for Zerodha authentication...</p>
            <p className="text-[10px] text-text-muted mt-1 leading-normal max-w-[240px]">
              Complete authorization inside the newly opened browser window to link your portfolio.
            </p>
            
            <button
              onClick={() => setLoading(false)}
              className="mt-6 text-[10px] font-bold text-text-muted hover:text-text-primary uppercase tracking-wider transition-colors border border-border-default rounded-md px-3 py-1 bg-elevated/20 hover:bg-elevated/40"
            >
              Cancel waiting
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
