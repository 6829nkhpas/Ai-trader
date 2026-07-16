import React from 'react';

/**
 * Parses a string and wraps any numeric values (including currency symbols,
 * multipliers, and percentages) in a custom styled badge.
 */
export function highlightNumbers(
  text: string | undefined | null,
  colorClass: string = 'text-text-primary bg-elevated/40 border-border-default/60'
) {
  if (!text) return '';

  // Regex matches:
  // - ₹ followed by numbers (e.g. ₹2608.66)
  // - standard integers or decimals optionally followed by % or x (e.g. 1.5x, 90%, 2587.6)
  const parts = text.split(/(\b\d+(?:\.\d+)?%?x?\b|₹\d+(?:\.\d+)?)/g);

  return parts.map((part, i) => {
    if (i % 2 === 1) {
      return (
        <span
          key={i}
          className={`font-sans font-semibold px-1 py-0.5 border rounded-sm text-[10px] select-text inline-block leading-none align-middle ${colorClass}`}
        >
          {part}
        </span>
      );
    }
    return part;
  });
}
