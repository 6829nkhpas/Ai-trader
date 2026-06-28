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
        <h2 className="text-xl font-extrabold text-text-primary tracking-tight">Subscription Plan</h2>
        <p className="text-xs text-text-secondary mt-1">Manage billing schedules, renewal dates, and payment methods</p>
      </div>

      {/* Status banner */}
      <div className={`flex items-center gap-3 rounded-none border p-4 ${
        user?.subscription?.status === 'ACTIVE'
          ? 'border-emerald-500/20 bg-emerald-500/5 text-emerald-400/90'
          : 'border-border-default/40 bg-elevated/40 text-text-secondary'
      }`}>
        <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-none border ${
          user?.subscription?.status === 'ACTIVE'
            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
            : 'bg-elevated/30 text-text-secondary border-border-default'
        }`}>
          <Shield size={20} />
        </div>
        <div>
          <h4 className="text-sm font-bold text-text-primary">
            {user?.subscription?.status === 'ACTIVE' ? 'Premium Subscription Active' : 'Starter Account'}
          </h4>
          <p className="text-[11px] mt-0.5">
            {user?.subscription?.status === 'ACTIVE' 
              ? 'All premium charting tools, VWEPR solvers, and DeepSeek predictive insight models are enabled.'
              : 'Basic standard indicators enabled. Upgrade to unleash custom ML agents and real-time execution capabilities.'}
          </p>
        </div>
      </div>

      {/* Grid details stacked as lines */}
      <div className="flex flex-col border-t border-border-default">
        {[
          { 
            label: 'Current Billing Tier', 
            value: (
              <span className={`rounded-none px-2.5 py-0.5 text-xs font-black border ${
                user?.tier === 'PRO'
                  ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
                  : 'bg-elevated border-border-default text-text-secondary'
              }`}>
                {user?.tier || 'FREE'}
              </span>
            ) 
          },
          { 
            label: 'Subscription Status', 
            value: (
              <div className="flex items-center gap-2">
                <span className={`h-1.5 w-1.5 rounded-none ${
                  user?.subscription?.status === 'ACTIVE'
                    ? 'bg-emerald-400 shadow-[0_0_8px_#34d399]'
                    : 'bg-text-muted'
                }`} />
                <span className={`text-xs font-black uppercase tracking-wide ${
                  user?.subscription?.status === 'ACTIVE' ? 'text-emerald-400' : 'text-text-muted'
                }`}>
                  {user?.subscription?.status || 'INACTIVE'}
                </span>
              </div>
            ) 
          },
          { 
            label: 'Billing Cycle Period End', 
            value: (
              <span className="text-sm font-bold text-text-primary">
                {user?.subscription?.currentPeriodEnd 
                  ? formatDate(user.subscription.currentPeriodEnd) 
                  : 'Never Expires (Free Tier)'}
              </span>
            ) 
          },
          { 
            label: 'Payment Method Mapped', 
            value: (
              <span className="text-xs text-text-secondary font-medium">
                {user?.subscription?.stripeCustomerId ? 'Stripe Secure Card' : user?.subscription?.razorpayCustomerId ? 'Razorpay Gateway' : 'No recorded payment method'}
              </span>
            ) 
          }
        ].map((row, i) => (
          <div key={i} className="flex items-center justify-between py-3 border-b border-border-default px-1">
            <span className="text-[10px] uppercase tracking-wider text-text-secondary">{row.label}</span>
            <div className="text-xs font-semibold text-text-primary">{row.value}</div>
          </div>
        ))}
      </div>

      {/* Detailed Plan ID Reference */}
      {user?.subscription && (
        <div className="rounded-none border border-border-default/40 bg-surface/50 p-4">
          <span className="text-[10px] uppercase tracking-wider text-text-secondary block mb-1">Subscription Reference ID</span>
          <span className="text-[11px] font-mono text-text-secondary break-all">
            {user.subscription.id}
          </span>
        </div>
      )}

      {/* Upgrade or Downgrade Action Promo Panels */}
      {(!user?.tier || user?.tier === 'FREE') ? (
        <div className="rounded-none border border-emerald-500/20 bg-emerald-500/5 p-6 relative overflow-hidden">
          <h3 className="text-sm font-bold text-text-primary uppercase tracking-wider">Unleash PRO Execution Power</h3>
          <p className="text-xs text-text-secondary mt-1 leading-relaxed max-w-lg">
            Upgrade to PRO to unlock continuous Ohlc ML curvature analysis, advanced ReAct LLM pipelines, live broker callbacks, and algorithmic execution.
          </p>

          <div className="mt-4 flex items-center justify-start">
            {!updatingTier ? (
              <button
                onClick={() => handleTierUpdate('PRO')}
                className="flex items-center gap-2 rounded-none bg-text-primary text-surface hover:bg-text-secondary px-5 py-3 text-xs font-bold transition-all active:scale-[0.98] border border-text-primary"
              >
                <span>UPGRADE TO PRO PLAN</span>
                <ArrowRight size={14} />
              </button>
            ) : (
              <div className="flex items-center gap-2">
                <Loader2 size={16} className="animate-spin text-text-muted" />
                <span className="text-xs text-text-secondary">Activating PRO tier parameters...</span>
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="rounded-none border border-border-default/40 bg-elevated/10 p-6">
          <h3 className="text-sm font-bold text-text-primary uppercase tracking-wider">Downgrade Plan</h3>
          <p className="text-xs text-text-secondary mt-1 leading-relaxed max-w-lg">
            Downgrading to FREE will suspend automated agent pipelines and live broker callbacks. Standard indicators will remain fully active.
          </p>

          <div className="mt-4 flex items-center justify-start">
            {!updatingTier ? (
              <button
                onClick={() => handleTierUpdate('FREE')}
                className="rounded-none border border-border-default bg-elevated/20 hover:bg-red-500/10 hover:text-red-400 px-5 py-3 text-xs font-bold text-text-secondary transition-all active:scale-[0.98]"
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
