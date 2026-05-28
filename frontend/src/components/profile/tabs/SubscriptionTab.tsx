import React from 'react';
import { Calendar, Shield, Loader2, ArrowRight } from 'lucide-react';

interface SubscriptionTabProps {
  user: any;
  formatDate: (date: any) => string;
  updatingTier: boolean;
  handleTierUpdate: (newTier: 'FREE' | 'PRO') => Promise<void>;
}

export default function SubscriptionTab({ user, formatDate, updatingTier, handleTierUpdate }: SubscriptionTabProps) {
  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-200">
      <div>
        <h2 className="text-xl font-extrabold text-white tracking-tight">Subscription Plan</h2>
        <p className="text-xs text-text-secondary mt-1">Manage billing schedules, renewal dates, and payment methods</p>
      </div>

      {/* Status banner */}
      <div className={`flex items-center gap-3 rounded-xl border p-4 ${
        user?.subscription?.status === 'ACTIVE'
          ? 'border-emerald-500/20 bg-emerald-500/5 text-emerald-400'
          : 'border-border-default/40 bg-[#0c0f1d]/50 text-text-secondary'
      }`}>
        <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${
          user?.subscription?.status === 'ACTIVE' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-elevated/30 text-text-secondary'
        }`}>
          <Shield size={20} />
        </div>
        <div>
          <h4 className="text-sm font-bold text-white">
            {user?.subscription?.status === 'ACTIVE' ? 'Premium Subscription Active' : 'Starter Account'}
          </h4>
          <p className="text-[11px] mt-0.5">
            {user?.subscription?.status === 'ACTIVE' 
              ? 'All premium charting tools, VWEPR solvers, and DeepSeek predictive insight models are enabled.'
              : 'Basic standard indicators enabled. Upgrade to unleash custom ML agents and real-time execution capabilities.'}
          </p>
        </div>
      </div>

      {/* Grid details */}
      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-xl border border-border-default/40 bg-[#0c0f1d]/50 p-4">
          <span className="text-[10px] uppercase tracking-wider text-text-secondary">Current Billing Tier</span>
          <div className="flex items-center gap-2 mt-1">
            <span className={`rounded-md px-2.5 py-0.5 text-xs font-black ${
              user?.tier === 'PRO' || user?.tier === 'PREMIUM'
                ? 'bg-emerald-500/10 border border-emerald-500/20 text-emerald-400'
                : 'bg-elevated border border-border-default/60 text-text-secondary'
            }`}>
              {user?.tier || 'FREE'}
            </span>
          </div>
        </div>

        <div className="rounded-xl border border-border-default/40 bg-[#0c0f1d]/50 p-4">
          <span className="text-[10px] uppercase tracking-wider text-text-secondary">Subscription Status</span>
          <div className="flex items-center gap-2 mt-1.5">
            <span className={`h-2.5 w-2.5 rounded-full ${
              user?.subscription?.status === 'ACTIVE' 
                ? 'bg-emerald-400 shadow-[0_0_8px_#34d399]' 
                : 'bg-amber-500 shadow-[0_0_8px_#f59e0b]'
            }`} />
            <span className="text-sm font-bold text-white uppercase tracking-wide">
              {user?.subscription?.status || 'INACTIVE'}
            </span>
          </div>
        </div>

        <div className="rounded-xl border border-border-default/40 bg-[#0c0f1d]/50 p-4">
          <span className="text-[10px] uppercase tracking-wider text-text-secondary">Billing Cycle Period End</span>
          <div className="flex items-center gap-2 text-sm font-bold text-white mt-1">
            <Calendar size={14} className="text-emerald-400" />
            <span>
              {user?.subscription?.currentPeriodEnd 
                ? formatDate(user.subscription.currentPeriodEnd) 
                : 'Never Expires (Free Tier)'}
            </span>
          </div>
        </div>

        <div className="rounded-xl border border-border-default/40 bg-[#0c0f1d]/50 p-4">
          <span className="text-[10px] uppercase tracking-wider text-text-secondary">Payment Method Mapped</span>
          <div className="flex items-center gap-2 text-sm font-bold text-white mt-1.5">
            {user?.subscription?.stripeCustomerId === 'phonepe_merchant_cust' ? (
              <div className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                <span className="text-xs uppercase font-extrabold text-emerald-400 tracking-wider">PhonePe UPI Gateway</span>
              </div>
            ) : user?.subscription?.stripeCustomerId ? (
              <div className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-blue-400" />
                <span className="text-xs uppercase font-extrabold text-blue-400 tracking-wider">Stripe Secure Card</span>
              </div>
            ) : user?.subscription?.razorpayCustomerId ? (
              <div className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-indigo-400" />
                <span className="text-xs uppercase font-extrabold text-indigo-400 tracking-wider">Razorpay Gateway</span>
              </div>
            ) : (
              <span className="text-xs text-text-secondary font-medium">No recorded payment method</span>
            )}
          </div>
        </div>
      </div>

      {/* Detailed Plan ID Reference */}
      {user?.subscription && (
        <div className="rounded-xl border border-border-default/40 bg-[#0c0f1d]/50 p-4">
          <span className="text-[10px] uppercase tracking-wider text-text-secondary block mb-1">Subscription Reference ID</span>
          <span className="text-[11px] font-mono text-text-secondary break-all">
            {user.subscription.id}
          </span>
        </div>
      )}

      {/* Upgrade or Downgrade Action Promo Panels */}
      {(!user?.tier || user?.tier === 'FREE') ? (
        <div className="rounded-2xl border border-emerald-500/25 bg-emerald-500/5 p-6 relative overflow-hidden">
          <div className="absolute -right-8 -top-8 h-20 w-20 rounded-full bg-emerald-500/10 blur-xl"></div>
          <h3 className="text-sm font-bold text-white uppercase tracking-wider">Unleash PRO Execution Power</h3>
          <p className="text-xs text-text-secondary mt-1 leading-relaxed max-w-lg">
            Upgrade to PRO to unlock continuous Ohlc ML curvature analysis, advanced ReAct LLM pipelines, live broker callbacks, and algorithmic execution.
          </p>

          <div className="mt-4 flex items-center justify-start">
            {!updatingTier ? (
              <button
                onClick={() => handleTierUpdate('PRO')}
                className="flex items-center gap-2 rounded-xl bg-emerald-500 hover:bg-emerald-600 px-5 py-3 text-xs font-bold text-white transition-all active:scale-[0.98] shadow-md shadow-emerald-500/15"
              >
                <span>UPGRADE TO PRO PLAN</span>
                <ArrowRight size={14} />
              </button>
            ) : (
              <div className="flex items-center gap-2">
                <Loader2 size={16} className="animate-spin text-emerald-400" />
                <span className="text-xs text-text-secondary">Activating PRO tier parameters...</span>
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="rounded-2xl border border-border-default/40 bg-elevated/10 p-6">
          <h3 className="text-sm font-bold text-white uppercase tracking-wider">Downgrade Plan</h3>
          <p className="text-xs text-text-secondary mt-1 leading-relaxed max-w-lg">
            Downgrading to FREE will suspend automated agent pipelines and live broker callbacks. Standard indicators will remain fully active.
          </p>

          <div className="mt-4 flex items-center justify-start">
            {!updatingTier ? (
              <button
                onClick={() => handleTierUpdate('FREE')}
                className="rounded-xl border border-border-default/50 bg-elevated/20 hover:bg-red-500/10 hover:border-red-500/20 hover:text-red-400 px-5 py-3 text-xs font-bold text-text-secondary transition-all active:scale-[0.98]"
              >
                <span>DOWNGRADE TO STARTER FREE</span>
              </button>
            ) : (
              <div className="flex items-center gap-2">
                <Loader2 size={16} className="animate-spin text-text-secondary" />
                <span className="text-xs text-text-secondary">Downgrading subscription...</span>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
