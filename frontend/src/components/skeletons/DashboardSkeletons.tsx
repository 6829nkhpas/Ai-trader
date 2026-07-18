import React from 'react';
import { Skeleton } from '../common/Skeleton';

// ── Risk & Margins Skeleton ────────────────────────────────────────────

/** Matches the 3-column margins grid: large card + 2×2 metric grid. */
export function RiskMarginsSkeleton() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {/* Large margin card */}
      <div className="md:col-span-1 border border-border-default rounded-xl bg-surface/40 p-4 flex flex-col gap-2">
        <Skeleton width="100px" height="8px" />
        <Skeleton width="140px" height="24px" />
        <div className="flex gap-2 mt-2">
          <Skeleton width="60px" height="14px" className="rounded-sm" />
          <Skeleton width="48px" height="14px" className="rounded-sm" />
        </div>
      </div>
      {/* 2×2 metric grid */}
      <div className="md:col-span-2 grid grid-cols-2 gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="border border-border-default rounded-xl bg-surface/40 p-3 flex flex-col gap-2">
            <Skeleton width="80px" height="8px" />
            <Skeleton width="100px" height="16px" />
            <div className="flex items-center gap-1 mt-1">
              <Skeleton width="12px" height="12px" className="rounded-full" />
              <Skeleton width="50px" height="8px" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Positions Skeleton ─────────────────────────────────────────────────

/** Matches the positions tab: sub-tab bar + position rows. */
export function PositionsSkeleton() {
  return (
    <div className="space-y-3">
      {/* Sub-tab bar */}
      <div className="flex items-center gap-1 border-b border-border-default/40 pb-2">
        <Skeleton width="90px" height="20px" className="rounded" />
        <Skeleton width="90px" height="20px" className="rounded" />
      </div>
      {/* Position rows */}
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="flex items-center justify-between px-3 py-2.5 rounded-lg border border-border-default/30 bg-surface/20">
          <div className="flex items-center gap-3">
            <Skeleton width="14px" height="14px" className="rounded-sm" />
            <div className="flex flex-col gap-1">
              <Skeleton width="72px" height="10px" />
              <Skeleton width="48px" height="8px" />
            </div>
          </div>
          <div className="flex items-center gap-4">
            <Skeleton width="40px" height="10px" />
            <Skeleton width="56px" height="10px" />
            <Skeleton width="60px" height="16px" className="rounded-sm" />
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Orders Skeleton ────────────────────────────────────────────────────

/** Matches the orders table: header row + 5 table rows (6 columns). */
export function OrdersSkeleton() {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs border-collapse">
        <thead>
          <tr className="border-b border-border-default/60">
            {['Time', 'Type', 'Symbol', 'Qty', 'Price', 'Status'].map((col) => (
              <th key={col} className="py-2">
                <Skeleton width={col === 'Symbol' ? '56px' : '40px'} height="8px" />
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border-default/20">
          {Array.from({ length: 5 }).map((_, i) => (
            <tr key={i}>
              <td className="py-2.5"><Skeleton width="52px" height="10px" /></td>
              <td className="py-2.5"><Skeleton width="32px" height="14px" className="rounded-sm" /></td>
              <td className="py-2.5"><Skeleton width="64px" height="10px" /></td>
              <td className="py-2.5 text-right"><Skeleton width="28px" height="10px" className="ml-auto" /></td>
              <td className="py-2.5 text-right"><Skeleton width="48px" height="10px" className="ml-auto" /></td>
              <td className="py-2.5 text-center"><Skeleton width="56px" height="16px" className="rounded-sm mx-auto" /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
