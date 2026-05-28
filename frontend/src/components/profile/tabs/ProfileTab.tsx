import { Calendar, Shield, Link2 } from 'lucide-react';

interface ProfileTabProps {
  user: any;
  paperPortfolio: any;
  formatDate: (date: any) => string;
  realWalletBalance?: number;
}

export default function ProfileTab({ user, paperPortfolio, formatDate, realWalletBalance }: ProfileTabProps) {
  const broker = user?.brokerConnection;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        {broker?.avatarUrl ? (
          <img 
            src={broker.avatarUrl} 
            alt={user?.name || 'Profile Avatar'} 
            className="h-14 w-14 rounded-2xl object-cover border border-border-default/60 shadow-lg"
          />
        ) : (
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xl font-bold tracking-wider">
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
          <h2 className="text-xl font-extrabold text-white tracking-tight">Account Profile</h2>
          <div className="flex items-center gap-2 mt-1">
            <p className="text-xs text-text-secondary">Manage subscription tiers and basic account metadata</p>
            {broker && (
              <span className="flex items-center gap-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 text-[9px] font-bold text-emerald-400 uppercase tracking-wide">
                <Link2 size={10} />
                Kite Linked
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Profile details grid */}
      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-xl border border-border-default/40 bg-[#0c0f1d]/50 p-4">
          <span className="text-[10px] uppercase tracking-wider text-text-secondary">Full Name</span>
          <p className="text-sm font-bold text-white mt-1">{user?.name || 'Strat AI Client'}</p>
        </div>
        <div className="rounded-xl border border-border-default/40 bg-[#0c0f1d]/50 p-4">
          <span className="text-[10px] uppercase tracking-wider text-text-secondary">Email Address</span>
          <p className="text-sm font-bold text-white mt-1">{user?.email || 'N/A'}</p>
        </div>
        <div className="rounded-xl border border-border-default/40 bg-[#0c0f1d]/50 p-4">
          <span className="text-[10px] uppercase tracking-wider text-text-secondary">Member Since</span>
          <div className="flex items-center gap-2 text-sm font-bold text-white mt-1">
            <Calendar size={14} className="text-emerald-400" />
            <span>{formatDate(user?.createdAt)}</span>
          </div>
        </div>
        <div className="rounded-xl border border-border-default/40 bg-[#0c0f1d]/50 p-4">
          <span className="text-[10px] uppercase tracking-wider text-text-secondary">Wallet Cash Balance</span>
          <p className="text-sm font-bold text-emerald-400 mt-1 font-mono">
            ₹{realWalletBalance !== undefined && realWalletBalance !== null 
              ? realWalletBalance.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) 
              : user?.walletBalance !== undefined && user?.walletBalance !== null 
                ? user.walletBalance.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) 
                : '0.00'}
          </p>
        </div>
      </div>

      {/* VIP Membership Visual Card representation */}
      <div className="relative overflow-hidden rounded-2xl border border-emerald-500/20 bg-gradient-to-br from-emerald-500/10 via-[#0c0f1d]/80 to-slate-900 p-6 shadow-lg">
        {/* Glow ring */}
        <div className="absolute -right-16 -top-16 h-36 w-36 rounded-full bg-emerald-500/10 blur-2xl"></div>

        <div className="flex justify-between items-start">
          <div>
            <span className="text-[9px] uppercase tracking-widest text-emerald-400 font-bold">Strat AI Membership Card</span>
            <h3 className="text-lg font-black text-white mt-1">
              {user?.tier === 'PRO' ? 'PRO TRADER EDITION' : 'STARTER FREE EDITION'}
            </h3>
          </div>
          <Shield className="text-emerald-400 shrink-0" size={24} />
        </div>

        <div className="mt-8 flex justify-between items-end">
          <div>
            <span className="text-[8px] uppercase tracking-wider text-text-secondary block">Linked Broker</span>
            <span className="text-[10px] font-bold text-[#4ade80] tracking-wider uppercase">
              {broker ? `${broker.broker} • ${broker.brokerUserId}` : 'NO BROKER CONNECTED'}
            </span>
          </div>
          <div className="text-right">
            <span className="text-[8px] uppercase tracking-wider text-text-secondary block">Simulated Balance</span>
            <span className="text-sm font-black text-white">
              ₹{paperPortfolio?.balance?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) || '1,000,000.00'}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
