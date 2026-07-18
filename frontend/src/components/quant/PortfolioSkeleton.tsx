import React from 'react';
import { Skeleton } from '../common/Skeleton';

/** Layout-accurate skeleton matching PortfolioDashboard: equity card + position rows + history section. */
export default function PortfolioSkeleton() {
  return (
    <div className="flex flex-col bg-surface border border-border-default/30 rounded-none w-full">
      {/* Equity header card */}
      <div className="p-4 border-b border-border-default">
        <div className="flex items-center justify-between mb-3">
          <Skeleton width="120px" height="10px" />
          <Skeleton width="60px" height="16px" className="rounded-sm" />
        </div>
        <Skeleton width="160px" height="24px" className="mb-2" />
        <div className="flex gap-3">
          <Skeleton width="80px" height="10px" />
          <Skeleton width="64px" height="10px" />
        </div>
      </div>

      {/* Active positions rows */}
      <div className="p-4 border-b border-border-default">
        <Skeleton width="100px" height="8px" className="mb-3" />
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="flex items-center justify-between py-2 border-b border-border-default/20 last:border-0">
            <div className="flex items-center gap-2">
              <Skeleton width="12px" height="12px" className="rounded-sm" />
              <div className="flex flex-col gap-1">
                <Skeleton width="64px" height="10px" />
                <Skeleton width="40px" height="8px" />
              </div>
            </div>
            <Skeleton width="56px" height="14px" className="rounded-sm" />
          </div>
        ))}
      </div>

      {/* Trade history header */}
      <div className="p-4">
        <Skeleton width="90px" height="8px" className="mb-3" />
        <Skeleton width="100%" height="10px" className="mb-1.5" />
        <Skeleton width="80%" height="10px" />
      </div>
    </div>
  );
}
