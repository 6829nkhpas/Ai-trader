import React from 'react';
import { Skeleton } from '../../common/Skeleton';

interface WatchlistSkeletonProps {
  rows?: number;
}

/** Layout-accurate skeleton matching the watchlist row grid (symbol + sector pill | price + change). */
export default function WatchlistSkeleton({ rows = 6 }: WatchlistSkeletonProps) {
  return (
    <div className="flex flex-col">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="flex items-center justify-between gap-1 px-3 py-2 border-l-2 border-transparent"
        >
          {/* Left: symbol + name */}
          <div className="flex flex-col gap-1.5 flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <Skeleton width="64px" height="10px" />
              <Skeleton width="28px" height="10px" className="rounded-sm" />
            </div>
            <Skeleton width="88px" height="8px" />
          </div>
          {/* Right: price + change */}
          <div className="flex flex-col items-end gap-1.5 shrink-0">
            <Skeleton width="56px" height="10px" />
            <Skeleton width="40px" height="8px" />
          </div>
        </div>
      ))}
    </div>
  );
}
