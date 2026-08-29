// ── Types & constants shared by the Home page and its extracted sub-components ──

import type { TradeProfile } from '../store/useTradeStore';

/** Sidebar tab discriminator used by the right sidebar. */
export type SidebarTab = 'profile' | 'deepquant';

/** Per-profile sidebar header configuration. */
export const SIDEBAR_CONFIG: Record<TradeProfile, { label: string; badge: string; badgeColor: string }> = {
  INTRADAY: { label: 'Order Book', badge: 'INTRADAY', badgeColor: 'bg-emerald-500/10 text-emerald-400' },
  SWING: { label: 'Confluence', badge: 'SWING', badgeColor: 'bg-amber-500/10 text-amber-400' },
  INVESTOR: { label: 'Macro Intelligence', badge: 'INVESTOR', badgeColor: 'bg-cyan-500/10 text-cyan-400' },
  FNO: { label: 'Options Flow', badge: 'F&O', badgeColor: 'bg-emerald-500/10 text-emerald-400' },
};

/** Per-profile badge shown in the chart toolbar area. */
export const PROFILE_BADGE_CONFIG: Record<TradeProfile, { label: string; color: string }> = {
  INTRADAY: { label: 'INTRADAY MODE', color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' },
  SWING: { label: 'SWING MODE', color: 'bg-amber-500/10 text-amber-400 border-amber-500/30' },
  INVESTOR: { label: 'INVESTOR MODE', color: 'bg-cyan-500/10 text-cyan-400 border-cyan-500/30' },
  FNO: { label: 'F&O MODE', color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' },
};

/** Real-time Kite quote shape. */
export interface SymbolQuote {
  symbol: string;
  last_price: number;
  open: number;
  high: number;
  low: number;
  close: number; // prev close
  change: number; // % change
  net_change: number;
  volume: number;
}

