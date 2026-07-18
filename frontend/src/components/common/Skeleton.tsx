import React from 'react';

interface SkeletonProps {
  className?: string;
  width?: string;
  height?: string;
}

/** Base skeleton bar — uses the `.skeleton-shimmer` CSS class (theme-adaptive). */
export function Skeleton({ className = '', width, height }: SkeletonProps) {
  return (
    <div
      className={`skeleton-shimmer rounded ${className}`}
      style={{ width, height }}
    />
  );
}

interface SkeletonTextProps {
  lines?: number;
  widths?: string[];
  lineHeight?: string;
  gap?: string;
  className?: string;
}

/** Renders N lines of skeleton text with varying widths. */
export function SkeletonText({
  lines = 3,
  widths,
  lineHeight = '8px',
  gap = '6px',
  className = '',
}: SkeletonTextProps) {
  const defaultWidths = ['100%', '85%', '60%'];
  return (
    <div className={`flex flex-col ${className}`} style={{ gap }}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          width={widths?.[i] ?? defaultWidths[i % defaultWidths.length]}
          height={lineHeight}
        />
      ))}
    </div>
  );
}

interface SkeletonCircleProps {
  size?: number;
  className?: string;
}

/** Circular skeleton for avatars and status dots. */
export function SkeletonCircle({ size = 24, className = '' }: SkeletonCircleProps) {
  return (
    <div
      className={`skeleton-shimmer rounded-full shrink-0 ${className}`}
      style={{ width: size, height: size }}
    />
  );
}
