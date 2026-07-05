'use client';

import React from 'react';
import IdentityHeader from './profile/IdentityHeader';
import MarginSection from './profile/MarginSection';
import PositionsSection from './profile/PositionsSection';
import OrdersSection from './profile/OrdersSection';

interface ProfileTabProps {
  user: any;
  paperPortfolio: any;
  formatDate: (date: any) => string;
  realWalletBalance?: number;
  marginsData?: any;
  positionsData?: any;
  orders?: any[];
}

export default function ProfileTab({ 
  user, 
  paperPortfolio, 
  formatDate, 
  realWalletBalance,
  marginsData,
  positionsData,
  orders
}: ProfileTabProps) {
  const broker = user?.brokerConnection;

  const formatCurrency = (val: number | undefined) => {
    if (val === undefined || val === null) return '₹0.00';
    return `₹${val.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  const getPnlClass = (val: number) => {
    if (val > 0) return 'text-bull font-bold';
    if (val < 0) return 'text-[#ef4444] font-bold';
    return 'text-text-secondary';
  };

  return (
    <div className="space-y-6 flex flex-col h-full overflow-y-auto pr-1 scrollbar-none">
      {/* Account Identity Header */}
      <IdentityHeader 
        user={user} 
        broker={broker} 
        formatDate={formatDate} 
        realWalletBalance={realWalletBalance}
        formatCurrency={formatCurrency}
      />

      {/* Margins Breakdown */}
      <MarginSection 
        broker={broker} 
        marginsData={marginsData} 
        formatCurrency={formatCurrency}
        getPnlClass={getPnlClass}
      />

      {/* Positions Ledger */}
      <PositionsSection 
        broker={broker} 
        positionsData={positionsData} 
        formatCurrency={formatCurrency}
        getPnlClass={getPnlClass}
      />

      {/* Orders execution logs */}
      <OrdersSection 
        broker={broker} 
        orders={orders} 
        formatCurrency={formatCurrency}
      />

      {/* Membership Info Footer */}
      <div className="border-t border-border-default pt-4 flex flex-col space-y-2 shrink-0">
        <div className="flex justify-between items-center py-1.5">
          <span className="text-[10px] uppercase tracking-widest text-text-secondary">Strat AI Membership Edition</span>
          <span className="text-xs font-black text-text-primary">{user?.tier === 'PRO' ? 'PRO TRADER EDITION' : 'STARTER FREE EDITION'}</span>
        </div>
        <div className="flex justify-between items-center py-1.5 border-t border-border-default/20">
          <span className="text-[10px] uppercase tracking-widest text-text-secondary">Linked Broker Connection</span>
          <span className="text-xs font-semibold text-text-primary">{broker ? `${broker.broker} • ${broker.brokerUserId}` : 'NO LIVE BROKER CONNECTED'}</span>
        </div>
        <div className="flex justify-between items-center py-1.5 border-t border-border-default/20">
          <span className="text-[10px] uppercase tracking-widest text-text-secondary">Paper Trading simulated Balance</span>
          <span className="text-xs font-semibold text-emerald-400">₹{paperPortfolio?.balance?.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) || '1,000,000.00'}</span>
        </div>
      </div>
    </div>
  );
}
