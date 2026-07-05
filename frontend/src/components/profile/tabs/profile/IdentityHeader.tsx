import React from 'react';
import { Calendar, Shield, Link2 } from 'lucide-react';

interface IdentityHeaderProps {
  user: any;
  broker: any;
  formatDate: (date: any) => string;
  realWalletBalance?: number;
  formatCurrency: (val: number | undefined) => string;
}

export default function IdentityHeader({
  user,
  broker,
  formatDate,
  realWalletBalance,
  formatCurrency,
}: IdentityHeaderProps) {
  return (
    <>
      {/* ── ACCOUNT IDENTITY HEADER ── */}
      <div className="flex items-center gap-4 border-b border-border-default/20 pb-4 shrink-0">
        {broker?.avatarUrl ? (
          <img 
            src={broker.avatarUrl} 
            alt={user?.name || 'Profile Avatar'} 
            className="h-16 w-16 rounded-none object-cover border border-border-default shadow-lg"
          />
        ) : (
          <div className="flex h-16 w-16 items-center justify-center rounded-none bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-2xl font-black tracking-wider">
            {(() => {
              const name = user?.name || '';
              if (!name) return 'SA';
              const parts = name.trim().split(/\s+/);
              if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
              return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
            })()}
          </div>
        )}
        <div>
          <h2 className="text-2xl font-black text-text-primary tracking-tight leading-none">{user?.name || 'Strat AI Client'}</h2>
          <div className="flex items-center gap-2 mt-2">
            <p className="text-xs text-text-secondary font-medium">{user?.email || 'No email registered'}</p>
            {broker && (
              <span className="flex items-center gap-1 rounded-none bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-0.5 text-[9px] font-bold text-emerald-400 uppercase tracking-wide">
                <Link2 size={10} />
                {broker.brokerUserId}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* ── METADATA ACCOUNT DETAILS CARD GRID ── */}
      <div className="flex flex-col border-t border-border-default shrink-0">
        {[
          { 
            label: 'ACCOUNT TIER LEVEL', 
            value: (
              <span className="flex items-center gap-1.5 font-bold">
                <Shield size={14} className="text-emerald-400" />
                <span>{user?.tier || 'FREE'} Tier</span>
              </span>
            ) 
          },
          { 
            label: 'MEMBER REGISTRATION', 
            value: (
              <span className="flex items-center gap-2 font-bold font-mono">
                <Calendar size={14} className="text-text-muted" />
                <span>{formatDate(user?.createdAt)}</span>
              </span>
            ) 
          },
          { 
            label: 'LIVE WALLET BALANCE', 
            value: (
              <span className="text-base font-black text-emerald-400 font-mono">
                {formatCurrency(realWalletBalance)}
              </span>
            ) 
          }
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
