'use client';

import React from 'react';
import { Calendar, Shield, AtSign, BadgeCheck } from 'lucide-react';
import type { AuthUser } from '../../../../store/useAuthStore';

interface IdentityHeaderProps {
  user: AuthUser | null;
  planName: string | null;
  formatDate: (date: string | number) => string;
}

export default function IdentityHeader({ user, planName, formatDate }: IdentityHeaderProps) {
  return (
    <>
      {/* ── ACCOUNT IDENTITY HEADER ── */}
      <div className="flex items-center gap-4 border-b border-border-default/20 pb-4 shrink-0">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-2xl font-black tracking-wider">
          {(() => {
            const name = user?.name || '';
            if (!name) return 'SA';
            const parts = name.trim().split(/\s+/);
            if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
            return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
          })()}
        </div>
        <div className="min-w-0">
          <h2 className="text-2xl font-black text-text-primary tracking-tight leading-none truncate">
            {user?.name || 'Strat AI Client'}
          </h2>
          <div className="flex items-center gap-2 mt-2 flex-wrap">
            <p className="text-xs text-text-secondary font-medium">{user?.email || 'No email registered'}</p>
          </div>
        </div>
      </div>

      {/* ── METADATA ACCOUNT DETAILS CARD GRID ── */}
      <div className="flex flex-col border-t border-border-default shrink-0">
        {[
          {
            label: 'CURRENT PLAN',
            value: (
              <span className="flex items-center gap-1.5 font-bold">
                <Shield size={14} className="text-emerald-400" />
                <span>{planName && planName !== 'none' ? planName : 'No active plan'}</span>
              </span>
            ),
          },
          {
            label: 'ACCOUNT ROLE',
            value: (
              <span className="flex items-center gap-1.5 font-bold">
                <BadgeCheck size={14} className={user?.role === 'admin' ? 'text-amber-400' : 'text-text-muted'} />
                <span className="uppercase">{user?.role || 'user'}</span>
              </span>
            ),
          },
        ].map((row, i) => (
          <div key={i} className="flex items-center justify-between py-3 border-b border-border-default px-1">
            <span className="text-[9px] uppercase font-black tracking-widest text-text-secondary">{row.label}</span>
            <div className="text-xs text-text-primary font-semibold">{row.value}</div>
          </div>
        ))}
      </div>
    </>
  );
}
