'use client';

import React, { useState, useEffect } from 'react';
import { useAuthStore } from '../../store/useAuthStore';
import { useTradeStore } from '../../store/useTradeStore';
import {
  User,
  Link as LinkIcon,
  FileText,
  Wallet,
  X,
  LogOut,
  CreditCard,
  Shield,
  Layers,
  ClipboardList
} from 'lucide-react';

import ProfileTab from './tabs/ProfileTab';
import SubscriptionTab from './tabs/SubscriptionTab';
import BrokerTab from './tabs/BrokerTab';
import TransactionsTab from './tabs/TransactionsTab';
import PortfolioTab from './tabs/PortfolioTab';
import RiskTab from './tabs/RiskTab';
import PositionsTab from './tabs/PositionsTab';
import OrdersTab from './tabs/OrdersTab';
import { useMargins, usePositions, useOrderBook } from '../../hooks/useAlphaData';

interface UserProfileModalProps {
  isOpen: boolean;
  onClose: () => void;
}

interface SqlTrade {
  id: string;
  symbol: string;
  entry_price: number;
  exit_price: number;
  pnl: number;
  type: string;
  size: number;
  timestamp: number;
}

export default function UserProfileModal({ isOpen, onClose }: UserProfileModalProps) {
  const { user, logout, fetchProfile } = useAuthStore();
  const { paperPortfolio, fetchPaperPortfolio } = useTradeStore();
  const [activeTab, setActiveTab] = useState<'profile' | 'risk' | 'portfolio' | 'positions' | 'orders' | 'subscription' | 'broker' | 'transactions'>('profile');

  // Load data hooks for dynamic portfolio/risk details
  const { data: marginsData, loading: marginsLoading, error: marginsError, refetch: refetchMargins } = useMargins();
  const { data: positionsData, loading: positionsLoading, error: positionsError, refetch: refetchPositions } = usePositions();
  const { orders: ordersData, loading: ordersLoading, error: ordersError, refetch: refetchOrders } = useOrderBook();
  const [sqlTrades, setSqlTrades] = useState<SqlTrade[]>([]);
  const [loadingTrades, setLoadingTrades] = useState(false);
  const [connectingBroker, setConnectingBroker] = useState(false);
  const [updatingTier, setUpdatingTier] = useState(false);

  const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;

  // Hydrate profile and paper portfolio on open
  useEffect(() => {
    if (isOpen) {
      fetchProfile();
      if (isTauri) {
        fetchPaperPortfolio();
      }
    }
  }, [isOpen]);

  // Auto-hydrate specific tabs on tab switch or modal open
  useEffect(() => {
    if (isOpen) {
      if (activeTab === 'risk' || activeTab === 'profile') {
        refetchMargins();
      } else if (activeTab === 'positions') {
        refetchPositions();
      } else if (activeTab === 'orders') {
        refetchOrders();
      }
    }
  }, [isOpen, activeTab]);

  // Fetch SQLite completed trades on open or tab change
  useEffect(() => {
    if (isOpen && activeTab === 'transactions' && isTauri) {
      loadSqlTrades();
    }
  }, [isOpen, activeTab]);

  const loadSqlTrades = async () => {
    setLoadingTrades(true);
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const tradesJson = await invoke<string>('get_trade_history');
      const parsed: SqlTrade[] = JSON.parse(tradesJson);
      setSqlTrades(parsed || []);
    } catch (err) {
      console.error('[UserProfileModal] Failed to fetch SQLite trades:', err);
    } finally {
      setLoadingTrades(false);
    }
  };

  const handleBrokerConnect = async () => {
    setConnectingBroker(true);
    const userId = user?.id || '';
    const connectUrl = `http://localhost:3001/api/broker/zerodha/connect?userId=${userId}`;

    try {
      if (isTauri) {
        const { invoke } = await import('@tauri-apps/api/core');
        await invoke('open_browser', { url: connectUrl });
      } else {
        window.open(connectUrl, '_blank');
      }
    } catch (err) {
      console.warn('[UserProfileModal] Failed to launch broker connect:', err);
      window.open(connectUrl, '_blank');
    }
  };

  const handleTierUpdate = async (newTier: 'FREE' | 'PRO') => {
    setUpdatingTier(true);
    const token = useAuthStore.getState().token;
    try {
      const response = await fetch('http://localhost:3001/api/auth/subscription/tier', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify({ tier: newTier })
      });

      if (response.ok) {
        await fetchProfile();
      } else {
        const data = await response.json();
        alert(data.error || 'Failed to update subscription tier');
      }
    } catch (err) {
      console.error('[UserProfileModal] Tier update failed:', err);
    } finally {
      setUpdatingTier(false);
    }
  };

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
        minute: '2-digit'
      });
    } catch (e) {
      return String(timestampStrOrNum);
    }
  };

  const formatSqlDate = (timestamp: number) => {
    if (!timestamp) return 'N/A';
    const ts = timestamp > 1000000000000 ? timestamp : timestamp * 1000;
    return new Date(ts).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  const broker = user?.brokerConnection;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-md transition-all duration-300">
      <div 
        className="relative flex h-[720px] w-full max-w-5xl overflow-hidden rounded-none border border-border-default/60 bg-surface/80 shadow-2xl backdrop-blur-xl animate-in fade-in zoom-in-95 duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Close Button */}
        <button 
          onClick={onClose}
          className="absolute top-4 right-4 z-10 flex h-8 w-8 items-center justify-center rounded-none border border-border-default/40 bg-elevated/20 text-text-secondary hover:bg-elevated hover:text-white transition-all"
        >
          <X size={16} />
        </button>

        {/* ── LEFT SIDEBAR PANEL ── */}
        <aside className="w-64 shrink-0 flex flex-col justify-between border-r border-border-default/40 bg-background/60 p-0">
          <div>
            {/* User Info Header */}
            <div className="p-5 border-b border-border-default/40 flex items-center gap-3">
              {/* User Info */}
              <div className="min-w-0">
                <h3 className="text-sm font-bold text-white truncate">{user?.name}</h3>
                <p className={`text-[10px] font-semibold tracking-wider uppercase mt-0.5 ${user?.tier === 'PRO' ? 'text-amber-400' : 'text-text-secondary'}`}>
                  {user?.tier} Tier
                </p>
              </div>
            </div>

            {/* Sidebar Navigation */}
            <nav className="flex flex-col">
              <button
                onClick={() => setActiveTab('profile')}
                className={`flex w-full items-center gap-3 border-b border-border-default/40 px-5 py-3 text-xs font-semibold transition-all ${
                  activeTab === 'profile'
                    ? 'bg-emerald-500/5 text-emerald-400'
                    : 'text-text-secondary hover:bg-elevated/40 hover:text-text-primary'
                }`}
              >
                <User size={15} />
                <span>Profile</span>
              </button>

              <button
                onClick={() => setActiveTab('risk')}
                className={`flex w-full items-center gap-3 border-b border-border-default/40 px-5 py-3 text-xs font-semibold transition-all ${
                  activeTab === 'risk'
                    ? 'bg-emerald-500/5 text-emerald-400'
                    : 'text-text-secondary hover:bg-elevated/40 hover:text-text-primary'
                }`}
              >
                <Shield size={15} />
                <span>Margins & Risk</span>
              </button>

              <button
                onClick={() => setActiveTab('positions')}
                className={`flex w-full items-center gap-3 border-b border-border-default/40 px-5 py-3 text-xs font-semibold transition-all ${
                  activeTab === 'positions'
                    ? 'bg-emerald-500/5 text-emerald-400'
                    : 'text-text-secondary hover:bg-elevated/40 hover:text-text-primary'
                }`}
              >
                <Layers size={15} />
                <span>Positions</span>
              </button>

              <button
                onClick={() => setActiveTab('orders')}
                className={`flex w-full items-center gap-3 border-b border-border-default/40 px-5 py-3 text-xs font-semibold transition-all ${
                  activeTab === 'orders'
                    ? 'bg-emerald-500/5 text-emerald-400'
                    : 'text-text-secondary hover:bg-elevated/40 hover:text-text-primary'
                }`}
              >
                <ClipboardList size={15} />
                <span>Order Book</span>
              </button>

              <button
                onClick={() => setActiveTab('subscription')}
                className={`flex w-full items-center gap-3 border-b border-border-default/40 px-5 py-3 text-xs font-semibold transition-all ${
                  activeTab === 'subscription'
                    ? 'bg-emerald-500/5 text-emerald-400'
                    : 'text-text-secondary hover:bg-elevated/40 hover:text-text-primary'
                }`}
              >
                <CreditCard size={15} />
                <span>My Subscription</span>
              </button>

              <button
                onClick={() => setActiveTab('broker')}
                className={`flex w-full items-center justify-between border-b border-border-default/40 px-5 py-3 text-xs font-semibold transition-all ${
                  activeTab === 'broker'
                    ? 'bg-emerald-500/5 text-emerald-400'
                    : 'text-text-secondary hover:bg-elevated/40 hover:text-text-primary'
                }`}
              >
                <div className="flex items-center gap-3">
                  <LinkIcon size={15} />
                  <span>Broker Connection</span>
                </div>
                {broker ? (
                  <span className="h-1.5 w-1.5 rounded-none bg-emerald-400 shadow-[0_0_8px_#34d399]" />
                ) : (
                  <span className="h-1.5 w-1.5 rounded-none bg-amber-500" />
                )}
              </button>

              <button
                onClick={() => setActiveTab('transactions')}
                className={`flex w-full items-center gap-3 border-b border-border-default/40 px-5 py-3 text-xs font-semibold transition-all ${
                  activeTab === 'transactions'
                    ? 'bg-emerald-500/5 text-emerald-400'
                    : 'text-text-secondary hover:bg-elevated/40 hover:text-text-primary'
                }`}
              >
                <FileText size={15} />
                <span>Transaction Journal</span>
              </button>
            </nav>
          </div>

          {/* Sign Out Button */}
          <button
            onClick={() => {
              logout();
              onClose();
            }}
            className="flex w-full items-center gap-3 border-t border-border-default/40 bg-elevated/10 hover:bg-red-500/10 hover:text-red-400 px-5 py-4 text-xs font-semibold text-text-secondary transition-all"
          >
            <LogOut size={15} />
            <span>Log Out</span>
          </button>
        </aside>

        {/* ── RIGHT DETAIL VIEW PANEL ── */}
        <main className="flex-1 flex flex-col min-h-0 bg-background/35 p-8 overflow-y-auto scrollbar-none">
          {activeTab === 'profile' && (
            <ProfileTab 
              user={user} 
              paperPortfolio={paperPortfolio} 
              formatDate={formatDate} 
              realWalletBalance={
                marginsData?.equity?.net !== undefined && marginsData?.equity?.net !== 0
                  ? marginsData.equity.net
                  : ((marginsData?.equity?.available as any)?.live_balance ?? marginsData?.equity?.available?.cash)
              }
              marginsData={marginsData}
              positionsData={positionsData}
              orders={ordersData}
            />
          )}

          {activeTab === 'subscription' && (
            <SubscriptionTab 
              user={user} 
              formatDate={formatDate} 
              updatingTier={updatingTier} 
              handleTierUpdate={handleTierUpdate} 
            />
          )}

          {activeTab === 'broker' && (
            <BrokerTab 
              broker={broker} 
              connectingBroker={connectingBroker} 
              handleBrokerConnect={handleBrokerConnect} 
              setConnectingBroker={setConnectingBroker} 
              formatDate={formatDate} 
            />
          )}

          {activeTab === 'transactions' && (
            <TransactionsTab 
              loadingTrades={loadingTrades} 
              sqlTrades={sqlTrades} 
              formatSqlDate={formatSqlDate} 
            />
          )}

          {activeTab === 'risk' && (
            <RiskTab 
              marginsData={marginsData}
              loading={marginsLoading}
              error={marginsError}
              refetch={refetchMargins}
            />
          )}

          {activeTab === 'portfolio' && (
            <PortfolioTab 
              paperPortfolio={paperPortfolio} 
            />
          )}

          {activeTab === 'positions' && (
            <PositionsTab 
              positionsData={positionsData}
              loading={positionsLoading}
              error={positionsError}
              refetch={refetchPositions}
            />
          )}

          {activeTab === 'orders' && (
            <OrdersTab 
              orders={ordersData}
              loading={ordersLoading}
              error={ordersError}
              refetch={refetchOrders}
            />
          )}
        </main>
      </div>
    </div>
  );
}
