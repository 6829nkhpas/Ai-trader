import React from 'react';
import { Skeleton } from '../../common/Skeleton';

/** Layout-accurate skeleton matching the billing table: header + 4 invoice rows. */
export default function BillingTabSkeleton() {
  return (
    <div className="rounded-none border border-border-default overflow-hidden flex flex-col">
      {/* Table header */}
      <div className="grid grid-cols-4 gap-3 px-4 py-2.5 bg-elevated/30 border-b border-border-default">
        <Skeleton width="48px" height="8px" />
        <Skeleton width="40px" height="8px" />
        <Skeleton width="52px" height="8px" />
        <Skeleton width="44px" height="8px" />
      </div>
      {/* Invoice rows */}
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="grid grid-cols-4 gap-3 px-4 py-3 border-b border-border-default/30 last:border-0">
          <Skeleton width="80px" height="10px" />
          <Skeleton width="64px" height="10px" />
          <Skeleton width="52px" height="10px" />
          <Skeleton width="48px" height="16px" className="rounded-sm" />
        </div>
      ))}
    </div>
  );
}
