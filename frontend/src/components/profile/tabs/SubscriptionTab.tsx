'use client';

import React from 'react';
import { Loader2, ArrowRight, Shield, Sparkles, Calendar, Coins, Gauge } from 'lucide-react';
import type { CreditData, AccessFlags } from '../../../lib/api/types';
import { dashboardUrl, openExternalUrl } from '../../../lib/redirect';

interface SubscriptionTabProps {
  credit: CreditData | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

const ACCESS_FLAG_LABELS: { key: keyof AccessFlags; label: string }[] = [
  { key: 'canAccessDeepseekGLM', label: 'DeepSeek GLM' },
  { key: 'canAccessMultiModel', label: 'Multi-Model' },
  { key: 'canAccessGhostline', label: 'Ghostline' },
  { key: 'canAccessFootprint', label: 'Footprint' },
  { key: 'canAccessTopup', label: 'Credit Top-up' },
  { key: 'canSeeInstantNewsSantiments', label: 'Instant News' },
  { key: 'canGetAdvanceChartAccess', label: 'Advanced Charts' },
];

const formatDate = (iso: string | null): string => {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return iso;
  }
};

const formatCreditLogDate = (iso: string): string => {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
};

export default function SubscriptionTab({ credit, loading, error, refetch }: SubscriptionTabProps) {
  const handleManage = () => openExternalUrl(dashboardUrl());

  if (loading && !credit) {
    return (
      <div className="flex flex-1 items-center justify-center p-8">
        <Loader2 size={28} className="animate-spin text-text-muted" />
      </div>
    );
  }

  if (error && !credit) {
    return (
      <div className="flex flex-col items-center justify-center p-8 text-center">
        <p className="text-sm font-semibold text-rose-400">Failed to load subscription</p>
        <p className="text-xs text-text-secondary mt-1">{error}</p>
        <button
          onClick={refetch}
          className="mt-3 rounded-none border border-border-default bg-elevated px-3 py-1.5 text-xs font-semibold text-text-primary hover:bg-elevated"
        >
          Retry
        </button>
      </div>
    );
  }

  const active = credit?.hasActiveSubscription ?? false;
  const planName = credit?.planName && credit.planName !== 'none' ? credit.planName : 'No active plan';
  const multiplier = credit?.creditMultiplier;
  const normalizedMultiplier = multiplier && multiplier >= 100 ? multiplier / 100 : multiplier;
  const logs = credit?.creditLogs ?? [];

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-200">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-extrabold text-text-primary tracking-tight">Subscription & Credits</h2>
          <p className="text-xs text-text-secondary mt-1">Credit balance, access flags, and recent credit activity</p>
        </div>
        <button
          onClick={handleManage}
          className="flex items-center gap-2 rounded-none border border-border-default bg-elevated hover:bg-elevated hover:text-text-primary px-4 py-2 text-xs font-bold text-text-secondary transition-all active:scale-[0.98]"
        >
          <span>MANAGE ON DASHBOARD</span>
          <ArrowRight size={14} />
        </button>
      </div>

      <div className={`flex items-center gap-3 rounded-none border p-4 ${active ? 'border-emerald-500/20 bg-emerald-500/5 text-emerald-400/90' : 'border-border-default bg-elevated text-text-secondary'}`}>
        <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-none border ${active ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 'bg-elevated text-text-secondary border-border-default'}`}>
          <Shield size={20} />
        </div>
        <div>
          <h4 className="text-sm font-bold text-text-primary">{active ? `${planName} Subscription Active` : 'Starter Account'}</h4>
          <p className="text-[11px] mt-0.5">
            {active
              ? 'All premium features included with this plan are enabled.'
              : 'No active subscription. Upgrade from the dashboard to unlock premium features.'}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-none border border-border-default bg-elevated p-4">
          <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-text-secondary">
            <Coins size={12} />
            <span>Credits</span>
          </div>
          <p className="mt-2 text-xl font-black text-text-primary font-mono">
            {(credit?.credits ?? 0).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}
          </p>
        </div>
        <div className="rounded-none border border-border-default bg-elevated p-4">
          <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-text-secondary">
            <Calendar size={12} />
            <span>Expires</span>
          </div>
          <p className="mt-2 text-xs font-bold text-text-primary">{formatDate(credit?.expiresAt ?? null)}</p>
        </div>
        <div className="rounded-none border border-border-default bg-elevated p-4">
          <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-text-secondary">
            <Gauge size={12} />
            <span>Multiplier</span>
          </div>
          <p className="mt-2 text-xs font-bold text-text-primary">
            {normalizedMultiplier ? `${normalizedMultiplier}×` : '—'}
          </p>
        </div>
      </div>

      <div>
        <h3 className="text-xs font-bold uppercase tracking-wider text-text-secondary mb-2">Access Flags</h3>
        <div className="grid grid-cols-2 gap-2">
          {ACCESS_FLAG_LABELS.map(({ key, label }) => {
            const enabled = credit?.accessFlags?.[key] ?? false;
            return (
              <div
                key={key}
                className={`flex items-center justify-between rounded-none border px-3 py-2 ${enabled ? 'border-emerald-500/20 bg-emerald-500/5' : 'border-border-default bg-elevated/60'}`}
              >
                <span className="text-xs font-semibold text-text-primary">{label}</span>
                <span className={`text-[10px] font-bold uppercase tracking-wider ${enabled ? 'text-emerald-400' : 'text-text-muted'}`}>
                  {enabled ? 'Enabled' : 'Disabled'}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-xs font-bold uppercase tracking-wider text-text-secondary">Recent Credit Activity</h3>
          <span className="text-[10px] text-text-muted">Last 50</span>
        </div>
        {logs.length === 0 ? (
          <div className="flex flex-col items-center justify-center p-6 text-center rounded-none border border-border-default bg-elevated/60">
            <Sparkles size={28} className="text-text-muted mb-2" />
            <p className="text-xs font-semibold text-text-secondary">No credit activity yet</p>
            <p className="text-[11px] text-text-muted mt-0.5">Credit transactions will appear here.</p>
          </div>
        ) : (
          <div className="rounded-none border border-border-default overflow-hidden max-h-80 overflow-y-auto scrollbar-none">
            <table className="w-full text-left border-collapse">
              <thead className="sticky top-0 z-10 bg-elevated border-b border-border-default">
                <tr>
                  <th className="px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-text-secondary">Amount</th>
                  <th className="px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-text-secondary">Type</th>
                  <th className="px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-text-secondary">Description</th>
                  <th className="px-4 py-2 text-[10px] font-bold uppercase tracking-wider text-text-secondary text-right">Date</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-default">
                {logs.map((log) => {
                  const positive = log.amount > 0;
                  return (
                    <tr key={log.id} className="hover:bg-elevated transition-colors">
                      <td className={`px-4 py-2 text-xs font-mono font-bold ${positive ? 'text-emerald-400' : log.amount < 0 ? 'text-rose-400' : 'text-text-secondary'}`}>
                        {positive ? '+' : ''}{log.amount.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 })}
                      </td>
                      <td className="px-4 py-2 text-xs text-text-secondary">{log.type}</td>
                      <td className="px-4 py-2 text-xs text-text-primary max-w-[240px] truncate">{log.description}</td>
                      <td className="px-4 py-2 text-xs text-text-secondary text-right font-mono">{formatCreditLogDate(log.createdAt)}</td>
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
