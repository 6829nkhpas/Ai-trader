'use client';

import React from 'react';
import { flushSync } from 'react-dom';
import { Zap, TrendingUp, Landmark, Layers, Search, HelpCircle, Sun, Moon, type LucideIcon } from 'lucide-react';
import { useTradeStore, type TradeProfile } from '../../store/useTradeStore';
import { useAuthStore } from '../../store/useAuthStore';
import { useChartUIStore } from '../../store/useChartUIStore';
import { PROFILES, getInitials } from '../../utils/layoutHelpers';
import NotificationBell from './NotificationBell';
import QuantRadar from '../quant/QuantRadar';

// Icon per workspace mode. Kept here (not in layoutHelpers) so the shared
// PROFILES list stays framework-agnostic and this rail owns its own visuals.
const PROFILE_ICONS: Record<TradeProfile, LucideIcon> = {
  INTRADAY: Zap,
  SWING: TrendingUp,
  INVESTOR: Landmark,
  FNO: Layers,
};

// Collapsed width (icon column only) and expanded width (icons + labels). The
// icon column is COLLAPSED_W wide in both states so icons never shift when the
// rail grows on hover — only the labels to their right fade in.
const COLLAPSED_W = 56; // px — matches w-14 / the icon slot width
const EXPANDED_W = 208; // px — w-52

interface NavRailProps {
  /** Opens the NSE symbol search modal (state lives in TerminalLayout). */
  onOpenSearch: () => void;
  /** Opens the Quick Start guide overlay. */
  onOpenGuide: () => void;
  /** Opens the account profile & settings modal. */
  onOpenProfile: () => void;
}

/**
 * A label that self-clips to zero width while collapsed and expands + fades in
 * when the rail is hovered. It clips ITSELF (max-width + overflow-hidden) so the
 * rail, its buttons and the Radar/Notification dropdowns never need
 * `overflow-hidden` — which would otherwise clip those pop-out panels.
 */
function RailLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="max-w-0 overflow-hidden whitespace-nowrap pr-4 text-sm font-semibold opacity-0 transition-all duration-200 ease-out group-hover:max-w-[150px] group-hover:opacity-100">
      {children}
    </span>
  );
}

/**
 * NavRail — the terminal's primary vertical navigation side panel.
 *
 * Collapsed it shows only icons (no boxes, no labels); the active workspace is
 * indicated purely by colourising its icon. On hover the whole rail widens and
 * OVERLAYS the content (it is absolutely positioned over a fixed-width spacer,
 * so the layout underneath never shifts) and reveals text labels.
 *
 * The mode buttons keep their `profile-btn-${key}` ids, label text and
 * `aria-pressed` state so screen readers announce the active workspace and the
 * existing selector contract is preserved.
 */
export default function NavRail({ onOpenSearch, onOpenGuide, onOpenProfile }: NavRailProps) {
  const activeProfile = useTradeStore((s) => s.activeProfile);
  const setActiveProfile = useTradeStore((s) => s.setActiveProfile);
  const user = useAuthStore((s) => s.user);
  const theme = useChartUIStore((s) => s.theme);
  const toggleTheme = useChartUIStore((s) => s.toggleTheme);

  // Circular view-transition theme swap, centred on the button that was pressed.
  const handleThemeToggle = (event: React.MouseEvent<HTMLButtonElement>) => {
    const doc = document as Document & { startViewTransition?: (cb: () => void) => { finished: Promise<void> } };

    if (!doc.startViewTransition) {
      toggleTheme();
      return;
    }

    const rect = event.currentTarget.getBoundingClientRect();
    const x = event.clientX || (rect.left + rect.width / 2);
    const y = event.clientY || (rect.top + rect.height / 2);
    const endRadius = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y)
    );

    document.documentElement.style.setProperty('--theme-x', `${x}px`);
    document.documentElement.style.setProperty('--theme-y', `${y}px`);
    document.documentElement.style.setProperty('--theme-r', `${endRadius}px`);
    document.documentElement.setAttribute('data-theme-changing', 'true');

    const transition = doc.startViewTransition(() => {
      flushSync(() => {
        toggleTheme();
      });
    });

    transition.finished.finally(() => {
      document.documentElement.removeAttribute('data-theme-changing');
    });
  };

  return (
    // Fixed-width spacer reserves the collapsed rail's footprint in the flex
    // flow; the real <nav> is absolutely positioned on top of it so expanding
    // overlaps the content instead of pushing it.
    <div className="relative shrink-0" style={{ width: COLLAPSED_W }}>
      <nav
        aria-label="Primary navigation"
        style={{ ['--rail-w' as string]: `${EXPANDED_W}px` }}
        className="group absolute inset-y-0 left-0 z-50 flex w-14 flex-col border-r border-border-default bg-surface py-2.5 transition-[width] duration-200 ease-out hover:w-[var(--rail-w)] hover:shadow-2xl"
      >
        {/* ── Brand mark ─────────────────────────────────────── */}
        <div className="flex h-10 items-center">
          <span className="flex w-14 shrink-0 items-center justify-center">
            <img src="/strat.svg" alt="Strat AI" className="h-5 w-5 object-contain" />
          </span>
          <RailLabel>
            <span className="text-text-primary">Strat AI</span>
          </RailLabel>
        </div>

        {/* ── Workspace Mode_Selector (primary navigation) ────── */}
        <div className="mt-2 flex flex-1 flex-col gap-1">
          {PROFILES.map(({ key, label }) => {
            const Icon = PROFILE_ICONS[key];
            const isActive = activeProfile === key;
            return (
              <button
                key={key}
                id={`profile-btn-${key.toLowerCase()}`}
                type="button"
                aria-pressed={isActive}
                onClick={() => setActiveProfile(key)}
                title={label}
                className={`
                  relative flex h-11 cursor-pointer items-center rounded-none transition-colors duration-150
                  focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-emerald-500/50
                  ${isActive ? 'text-emerald-500 dark:text-emerald-400' : 'text-text-secondary hover:text-emerald-500 dark:hover:text-emerald-400'}
                `}
              >
                {/* Active accent bar (thin, non-boxy) */}
                <span
                  className={`absolute left-0 top-1/2 h-6 w-0.5 -translate-y-1/2 rounded-r-full bg-emerald-500 transition-opacity duration-150 ${
                    isActive ? 'opacity-100' : 'opacity-0'
                  }`}
                />
                <span className="flex w-14 shrink-0 items-center justify-center">
                  <Icon size={22} strokeWidth={isActive ? 2.4 : 2} />
                </span>
                <RailLabel>{label}</RailLabel>
              </button>
            );
          })}
        </div>

        {/* ── Bottom utilities ───────────────────────────────── */}
        <div className="mt-auto flex flex-col gap-1">
          <button
            type="button"
            onClick={onOpenSearch}
            title="Search symbol (Ctrl+K)"
            className="flex h-11 cursor-pointer items-center text-text-secondary transition-colors hover:text-emerald-500 dark:hover:text-emerald-400"
          >
            <span className="flex w-14 shrink-0 items-center justify-center">
              <Search size={20} />
            </span>
            <RailLabel>Search</RailLabel>
          </button>

          {/* Quant Radar — full row toggles the panel, which opens to the right */}
          <QuantRadar align="rail" label="Quant Radar" />

          {/* Notifications — full row toggles the panel, which opens to the right */}
          <NotificationBell align="rail" label="Notifications" />

          <button
            type="button"
            onClick={onOpenGuide}
            title="Quick Start Guide"
            className="flex h-11 cursor-pointer items-center text-text-secondary transition-colors hover:text-emerald-500 dark:hover:text-emerald-400"
          >
            <span className="flex w-14 shrink-0 items-center justify-center">
              <HelpCircle size={20} />
            </span>
            <RailLabel>Help</RailLabel>
          </button>

          <button
            type="button"
            onClick={handleThemeToggle}
            title={theme === 'dark' ? 'Switch to Light Theme' : 'Switch to Dark Theme'}
            className="flex h-11 cursor-pointer items-center text-text-secondary transition-colors hover:text-emerald-500 dark:hover:text-emerald-400"
          >
            <span className="flex w-14 shrink-0 items-center justify-center">
              {theme === 'dark' ? <Sun size={20} /> : <Moon size={20} />}
            </span>
            <RailLabel>{theme === 'dark' ? 'Light Theme' : 'Dark Theme'}</RailLabel>
          </button>

          <button
            type="button"
            onClick={onOpenProfile}
            title="Account Profile & Settings"
            className="flex h-11 cursor-pointer items-center text-text-secondary transition-colors hover:text-emerald-500 dark:hover:text-emerald-400"
          >
            <span className="flex w-14 shrink-0 items-center justify-center">
              <span className="flex h-7 w-7 items-center justify-center overflow-hidden rounded-full bg-emerald-500/10 text-[10px] font-bold tracking-wider text-emerald-400">
                {getInitials(user?.name)}
              </span>
            </span>
            <RailLabel>Account</RailLabel>
          </button>
        </div>
      </nav>
    </div>
  );
}
