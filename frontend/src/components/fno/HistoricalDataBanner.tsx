'use client';

/**
 * HistoricalDataBanner — Amber notification banner for F&O section.
 *
 * Displayed at the top of the F&O workspace when showing cached/historical
 * data instead of live market data. Auto-dismissed when live data resumes
 * (the parent FnoSection stops rendering this when viewState is ready/partial).
 */

import React from 'react';
import { AlertTriangle } from 'lucide-react';

interface HistoricalDataBannerProps {
  /** Epoch-ms timestamp of the cached snapshot. */
  snapshotTs: number;
  /**
   * Why cached data is being shown.
   *
   * The trailing label was hardcoded to "Market Closed", which is only one of
   * the reasons a snapshot gets served from cache. When the F&O service is
   * unreachable, telling the user the market is closed is simply wrong — so the
   * caller now states the actual reason.
   */
  reason?: 'market-closed' | 'service-unreachable';
}

/** Amber banner displayed when showing historical/cached F&O data. */
export default function HistoricalDataBanner({
  snapshotTs,
  reason = 'market-closed',
}: HistoricalDataBannerProps) {
  const formatted = (() => {
    if (!Number.isFinite(snapshotTs)) return null;
    const d = new Date(snapshotTs);
    if (Number.isNaN(d.getTime())) return null;
    return d.toLocaleString(undefined, {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  })();

  return (
    <div className="flex items-center gap-2 border-b border-amber-500/25 bg-amber-500/8 px-3 py-1.5">
      <AlertTriangle size={12} className="shrink-0 text-amber-400" />
      <span className="text-[10px] font-semibold uppercase tracking-wider text-amber-400">
        Showing Historical Data
      </span>
      {formatted && (
        <span className="text-[10px] font-mono text-amber-400/70">
          — Last snapshot: {formatted}
        </span>
      )}
      <span className="ml-auto text-[9px] text-amber-400/50 uppercase tracking-wider">
        {reason === 'service-unreachable' ? 'Service Unreachable' : 'Market Closed'}
      </span>
    </div>
  );
}
