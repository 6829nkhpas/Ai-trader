import React from 'react';
import { Skeleton } from '../../common/Skeleton';

/** Layout-accurate skeleton matching SentimentBlock: header + score card + headline text. */
export default function SentimentSkeleton() {
  return (
    <div className="border-b border-border-default py-2.5 px-0">
      {/* Header bar */}
      <div className="flex items-center gap-1.5 mb-1.5 px-3">
        <Skeleton width="10px" height="10px" className="rounded-sm shrink-0" />
        <Skeleton width="90px" height="8px" />
        <div className="ml-auto">
          <Skeleton width="50px" height="8px" />
        </div>
      </div>

      {/* Score card */}
      <div className="rounded-none px-3 py-2 border-y border-x-0 border-border-default bg-elevated/40">
        <div className="flex items-center justify-between mb-1.5">
          <div className="flex items-center gap-1.5">
            <Skeleton width="36px" height="20px" />
            <Skeleton width="48px" height="14px" className="rounded-sm" />
          </div>
          <Skeleton width="8px" height="8px" className="rounded-sm" />
        </div>
        <Skeleton width="100%" height="8px" />
        <div className="mt-1">
          <Skeleton width="75%" height="8px" />
        </div>
      </div>
    </div>
  );
}
