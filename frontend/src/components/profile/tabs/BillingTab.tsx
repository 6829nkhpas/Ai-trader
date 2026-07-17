'use client';

import React from 'react';
import { Loader2, FileText, ArrowRight, Receipt } from 'lucide-react';
import type { Payment } from '../../../lib/api/types';
import { latestPaymentStatus } from '../../../lib/api/types';
import { dashboardUrl, openExternalUrl } from '../../../lib/redirect';

interface BillingTabProps {
  history: Payment[] | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

const STATUS_COLORS: Record<string, string> = {
  pending: 'bg-amber-500/10 border-amber-500/30 text-amber-400',
  paid: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400',
  failed: 'bg-rose-500/10 border-rose-500/30 text-rose-400',
  refunded: 'bg-elevated border-border-default text-text-secondary',
};

const formatDate = (iso: string): string => {
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return iso;
  }
};

export default function BillingTab({ history, loading, error, refetch }: BillingTabProps) {
  const handleViewInvoices = () => openExternalUrl(dashboardUrl());

  const payments = history ?? [];

  return (
    <div className="space-y-6 animate-in fade-in slide-in-from-bottom-2 duration-200">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-extrabold text-text-primary tracking-tight">Billing History</h2>
          <p className="text-xs text-text-secondary mt-1">Invoices and payment status</p>
        </div>
        <button
          onClick={handleViewInvoices}
          className="flex items-center gap-2 rounded-none border border-border-default bg-elevated hover:bg-elevated hover:text-text-primary px-4 py-2 text-xs font-bold text-text-secondary transition-all active:scale-[0.98]"
        >
          <span>VIEW INVOICES</span>
          <ArrowRight size={14} />
        </button>
      </div>

      {error && !loading ? (
        <div className="flex flex-col items-center justify-center p-8 text-center rounded-none border border-border-default bg-elevated/60">
          <p className="text-sm font-semibold text-rose-400">Failed to load billing history</p>
          <p className="text-xs text-text-secondary mt-1">{error}</p>
          <button
            onClick={refetch}
            className="mt-3 rounded-none border border-border-default bg-elevated px-3 py-1.5 text-xs font-semibold text-text-primary hover:bg-elevated"
          >
            Retry
          </button>
        </div>
      ) : loading && payments.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-8">
          <Loader2 size={28} className="animate-spin text-text-muted" />
        </div>
      ) : payments.length === 0 ? (
        <div className="flex flex-col items-center justify-center p-8 text-center rounded-none border border-border-default bg-elevated/60">
          <Receipt size={40} className="text-text-secondary mb-3 opacity-40" />
          <h4 className="text-sm font-bold text-text-primary">No Payments Yet</h4>
          <p className="text-xs text-text-secondary mt-1">Your invoices will appear here after your first subscription or top-up.</p>
        </div>
      ) : (
        <div className="rounded-none border border-border-default overflow-hidden flex flex-col">
          <div className="flex-1 overflow-auto max-h-[480px] scrollbar-none">
            <table className="w-full text-left border-collapse">
              <thead className="sticky top-0 z-10 bg-elevated border-b border-border-default">
                <tr>
                  <th className="px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-text-secondary">Invoice</th>
                  <th className="px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-text-secondary">Type</th>
                  <th className="px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-text-secondary text-right">Amount</th>
                  <th className="px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-text-secondary">Status</th>
                  <th className="px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-text-secondary text-right">Date</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-default">
                {payments.map((p) => {
                  const status = latestPaymentStatus(p) ?? 'pending';
                  const isSub = p.type === 'subscription';
                  return (
                    <tr key={p.id} className="hover:bg-elevated transition-colors">
                      <td className="px-4 py-3 text-xs font-mono text-text-primary">{p.invoiceId || '—'}</td>
                      <td className="px-4 py-3 text-xs">
                        <span className={`rounded-none px-2 py-0.5 text-[9px] font-bold uppercase border ${isSub ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-cyan-500/10 border-cyan-500/20 text-cyan-400'}`}>
                          {p.type}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs font-bold text-text-primary text-right font-mono">
                        ₹{p.amount.toLocaleString('en-IN')}
                      </td>
                      <td className="px-4 py-3 text-xs">
                        <span className={`rounded-none px-2 py-0.5 text-[9px] font-bold uppercase border ${STATUS_COLORS[status] ?? STATUS_COLORS.pending}`}>
                          {status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-text-secondary text-right font-mono">{formatDate(p.createdAt)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 text-[11px] text-text-muted">
        <FileText size={12} />
        <span>For refunds, receipts, or payment disputes, use the dashboard billing portal.</span>
      </div>
    </div>
  );
}
