import React from 'react';
import { Skeleton } from '../common/Skeleton';

interface FnoSkeletonProps {
  rows?: number;
}

/** Layout-accurate skeleton matching the F&O option chain table. */
export default function FnoSkeleton({ rows = 8 }: FnoSkeletonProps) {
  return (
    <div className="flex flex-col">
      {/* Header row */}
      <div className="grid grid-cols-7 gap-1 px-3 py-2 border-b border-border-default bg-elevated/30">
        {['Call LTP', 'Call OI', 'Call Chg', 'Strike', 'Put Chg', 'Put OI', 'Put LTP'].map((col) => (
          <Skeleton key={col} width="100%" height="8px" />
        ))}
      </div>
      {/* Data rows */}
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="grid grid-cols-7 gap-1 px-3 py-[6px] border-b border-border-default/30"
        >
          <Skeleton height="10px" />
          <Skeleton height="10px" />
          <Skeleton height="10px" />
          <Skeleton height="12px" className="rounded-sm" />
          <Skeleton height="10px" />
          <Skeleton height="10px" />
          <Skeleton height="10px" />
        </div>
      ))}
    </div>
  );
}
