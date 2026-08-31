import type { MultiTfChartPatterns } from '../../../store/useQuantStore';

/**
 * Timeframes the scanner reports on, in display order.
 *
 * Owned here rather than inside the view because the summary strip counts across
 * the same set: if the two lists diverged, the strip's total would silently stop
 * matching the tabs the user can actually open.
 */
export const PATTERN_TIMEFRAMES = ['1m', '5m', '10m', '15m', '1h', '4h', '1d'] as const;

/** The timeframe the scanner opens on when the user has not chosen one. */
export const DEFAULT_PATTERN_TIMEFRAME = '10m';

/** How many patterns a given timeframe is carrying. */
export function patternCountFor(
  multiTfPatterns: MultiTfChartPatterns[] | null | undefined,
  timeframe: string,
): number {
  return multiTfPatterns?.find((p) => p.timeframe === timeframe)?.patterns.length ?? 0;
}

/** Total patterns across every timeframe. Drives the strip's headline count. */
export function totalPatternCount(
  multiTfPatterns: MultiTfChartPatterns[] | null | undefined,
): number {
  return (multiTfPatterns ?? []).reduce((n, p) => n + p.patterns.length, 0);
}

/**
 * The timeframe carrying the most patterns, used both as the scanner's default
 * tab and as the strip's "where to look" hint.
 *
 * Falls back to `DEFAULT_PATTERN_TIMEFRAME` when nothing was found, so the view
 * never opens on an arbitrary empty tab.
 */
export function bestPatternTimeframe(
  multiTfPatterns: MultiTfChartPatterns[] | null | undefined,
): string {
  if (!multiTfPatterns) return DEFAULT_PATTERN_TIMEFRAME;

  const best = multiTfPatterns.reduce<{ tf: string; count: number }>(
    (acc, p) => (p.patterns.length > acc.count ? { tf: p.timeframe, count: p.patterns.length } : acc),
    { tf: DEFAULT_PATTERN_TIMEFRAME, count: -1 },
  );

  return best.count > 0 ? best.tf : DEFAULT_PATTERN_TIMEFRAME;
}

/** How many of the counted patterns are still forming rather than complete. */
export function formingPatternCount(
  multiTfPatterns: MultiTfChartPatterns[] | null | undefined,
): number {
  return (multiTfPatterns ?? []).reduce(
    (n, tf) => n + tf.patterns.filter((p) => p.is_forming).length,
    0,
  );
}
