'use client';

/**
 * The single-chart workspace shared by the INTRADAY, SWING and INVESTOR modes.
 *
 * WHY ONE COMPONENT INSTEAD OF THREE
 *
 * This replaces `IntradayLayout`, `SwingLayout` and `InvestorLayout`, which were
 * three separate components rendering byte-for-byte the same tree: a
 * `#<profile>-hud` wrapper around one `MainTerminalChart`. They differed only in
 * that wrapper's `id` (referenced nowhere) and in a default `timeframe` prop that
 * was dead, because `page.tsx` always passed `activeTimeframe` explicitly — and
 * `MainTerminalChart` ignores the prop regardless.
 *
 * Being three distinct component TYPES was not cosmetic. `page.tsx` selected
 * between them with `switch (activeProfile)`, so every mode switch changed the
 * element type at that position, and React's reconciler must unmount and remount
 * on a type change. That tore down the whole chart subtree — `widget.remove()`,
 * a fresh `new TradingView.widget(...)`, a new datafeed, a full `getBars` round
 * trip, and a ghost-line redraw — every time the user moved between Intraday,
 * Swing and Investor. Hence the reported "loads the same instrument's candles
 * again and again".
 *
 * None of that work was necessary: nothing about the chart differs across those
 * three modes. The chart reads `selectedSymbol` and `activeTimeframe` straight
 * from the store, neither of which `setActiveProfile` touches, and the tree is
 * otherwise profile-agnostic. With one component type the switch becomes a
 * cheap attribute patch on the wrapper and the chart simply stays mounted.
 *
 * F&O and split view are NOT folded in here: those are genuinely different trees
 * (`FnoSection` / `SplitChartContainer`) and remounting for them is correct.
 */

import React from 'react';
import MainTerminalChart from '../MainTerminalChart';
import { TradeProfile } from '../../store/useTradeStore';

interface TerminalChartPaneProps {
  /**
   * The active workspace mode. Used ONLY to derive the wrapper's `id`, kept for
   * continuity with the previous per-profile ids. It deliberately does not reach
   * the chart: passing it down would give the subtree a reason to care about the
   * mode, which is the coupling this component exists to remove.
   */
  activeProfile: TradeProfile;
}

export default function TerminalChartPane({ activeProfile }: TerminalChartPaneProps) {
  return (
    <div
      id={`${activeProfile.toLowerCase()}-hud`}
      className="flex h-full flex-col min-h-0 rounded-none border-none bg-surface overflow-hidden"
    >
      <MainTerminalChart />
    </div>
  );
}
