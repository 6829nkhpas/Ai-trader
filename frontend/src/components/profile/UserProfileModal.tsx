'use client';

import React, { useState, useEffect } from 'react';
import { useAuthStore } from '../../store/useAuthStore';
import {
  User,
  X,
  LogOut,
  CreditCard,
  Wallet,
} from 'lucide-react';

import ProfileTab from './tabs/ProfileTab';
import SubscriptionTab from './tabs/SubscriptionTab';
import BillingTab from './tabs/BillingTab';
import { useBillingHistory, useCredit } from '../../hooks/useApi';

interface UserProfileModalProps {
  isOpen: boolean;
  onClose: () => void;
}

type ModalTab = 'profile' | 'subscription' | 'billing';

export default function UserProfileModal({ isOpen, onClose }: UserProfileModalProps) {
  const { user, logout, fetchProfile } = useAuthStore();
  const [activeTab, setActiveTab] = useState<ModalTab>('profile');

  const { data: creditData, loading: creditLoading, error: creditError, refetch: refetchCredit } = useCredit();
  const { data: billingData, loading: billingLoading, error: billingError, refetch: refetchBilling } = useBillingHistory();

  useEffect(() => {
    if (!isOpen) return;
    fetchProfile();
  }, [isOpen, fetchProfile]);

  if (!isOpen) return null;

  const formatDate = (timestampStrOrNum: string | number) => {
    if (!timestampStrOrNum) return 'N/A';
    try {
      const date = new Date(timestampStrOrNum);
      return date.toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch {
      return String(timestampStrOrNum);
    }
  };

  const planName = creditData?.planName && creditData.planName !== 'none' ? creditData.planName : null;

  const navItems: { key: ModalTab; label: string; icon: React.ReactNode }[] = [
    { key: 'profile', label: 'Profile', icon: <User size={15} /> },
    { key: 'subscription', label: 'Subscription & Credits', icon: <CreditCard size={15} /> },
    { key: 'billing', label: 'Billing History', icon: <Wallet size={15} /> },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-md transition-all duration-300">
      <div
        className="relative flex h-[720px] w-full max-w-5xl overflow-hidden rounded-none border border-border-default bg-surface shadow-2xl animate-in fade-in zoom-in-95 duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 z-10 flex h-8 w-8 items-center justify-center rounded-none border border-border-default bg-elevated text-text-secondary hover:bg-elevated hover:text-text-primary transition-all"
        >
          <X size={16} />
        </button>

        {/* ── LEFT SIDEBAR PANEL ── */}
        <aside className="w-64 shrink-0 flex flex-col justify-between border-r border-border-default bg-card p-0">
          <div>
            <div className="p-5 border-b border-border-default flex items-center gap-3">
              <div className="min-w-0">
                <h3 className="text-sm font-bold text-text-primary truncate">{user?.name}</h3>
                <p className={`text-[10px] font-semibold tracking-wider uppercase mt-0.5 ${planName ? 'text-emerald-400' : 'text-text-secondary'}`}>
                  {planName ? `${planName} Plan` : 'No Plan'}
                </p>
              </div>
            </div>

            <nav className="flex flex-col">
              {navItems.map(({ key, label, icon }) => {
                const isActive = activeTab === key;
                return (
                  <button
                    key={key}
                    onClick={() => setActiveTab(key)}
                    className={`flex w-full items-center gap-3 border-b border-border-default px-5 py-3 text-xs font-semibold transition-all ${isActive ? 'bg-emerald-500/10 text-emerald-400' : 'text-text-secondary hover:bg-elevated hover:text-text-primary'}`}
                  >
                    {icon}
                    <span className="flex-1 text-left">{label}</span>
                  </button>
                );
              })}
            </nav>
          </div>

          <button
            onClick={() => {
              logout();
              onClose();
            }}
            className="flex w-full items-center gap-3 border-t border-border-default bg-elevated hover:bg-red-500/10 hover:text-red-400 px-5 py-4 text-xs font-semibold text-text-secondary transition-all"
          >
            <LogOut size={15} />
            <span>Log Out</span>
          </button>
        </aside>

        {/* ── RIGHT DETAIL VIEW PANEL ── */}
        <main className="flex-1 flex flex-col min-h-0 bg-surface p-8 pr-14 overflow-y-auto scrollbar-none">
          {activeTab === 'profile' && (
            <ProfileTab
              user={user}
              planName={planName}
              formatDate={formatDate}
            />
          )}

          {activeTab === 'subscription' && (
            <SubscriptionTab credit={creditData} loading={creditLoading} error={creditError} refetch={refetchCredit} />
          )}

          {activeTab === 'billing' && (
            <BillingTab history={billingData} loading={billingLoading} error={billingError} refetch={refetchBilling} />
          )}
        </main>
      </div>
    </div>
  );
}
