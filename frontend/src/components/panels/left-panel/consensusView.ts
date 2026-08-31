/**
 * Presentation rules shared by the technical-consensus surfaces.
 *
 * The summary strip and the full HUD now describe the same reading in two places.
 * If each kept its own thresholds they would eventually disagree — the strip
 * calling a score "BULLISH" while the HUD called it "STRONG BULL", or one
 * flagging a reading as stale while the other still showed it as current. The
 * wording and the staleness rule therefore live here, once.
 */

/**
 * How old a consensus reading may be before it is flagged as no longer current.
 *
 * The consensus is only recomputed on an explicit FIND/VERIFY press, so a
 * retained reading is legitimate — but a reading from a previous session must not
 * be indistinguishable from a live one.
 */
export const CONSENSUS_STALE_AFTER_MS = 5 * 60 * 1000;

/** Human-readable age, e.g. `42s ago`, `3m ago`, `2h ago`, `1d ago`. */
export function formatAge(ms: number): string {
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

/** The verdict word for a -100..+100 trend score. */
export function trendVerdict(score: number): string {
  if (score > 50) return 'STRONG BULL';
  if (score > 0) return 'BULLISH';
  if (score < -50) return 'STRONG BEAR';
  if (score < 0) return 'BEARISH';
  return 'NEUTRAL';
}

/** Text colour for a trend score. Zero is amber, not green: neutral is not mildly bullish. */
export function trendColor(score: number): string {
  if (score > 50) return 'text-emerald-400';
  if (score > 0) return 'text-emerald-400/70';
  if (score < -50) return 'text-rose-400';
  if (score < 0) return 'text-rose-400/70';
  return 'text-amber-400';
}

/** Fill colour for a trend gauge, matching `trendColor`'s bands. */
export function trendBg(score: number): string {
  if (score > 50) return 'bg-emerald-500';
  if (score > 0) return 'bg-emerald-500/60';
  if (score < -50) return 'bg-rose-500';
  if (score < 0) return 'bg-rose-500/60';
  return 'bg-amber-500/60';
}

/** Where a -100..+100 trend score sits on a 0..100 gauge, with 50 as neutral. */
export function trendGaugePercent(score: number): number {
  const clamped = Math.max(-100, Math.min(100, score));
  return Math.round(((clamped + 100) / 200) * 100);
}

/**
 * Whether a consensus report describes the symbol the user is looking at.
 *
 * The store retains the last computed report regardless of what is charted now,
 * so this check is what stops one symbol's numbers being presented under
 * another's name.
 */
export function consensusMatchesSymbol(
  reportSymbol: string | undefined,
  selectedSymbol: string | null | undefined,
): boolean {
  if (!reportSymbol || !selectedSymbol) return false;
  return reportSymbol.trim().toUpperCase() === selectedSymbol.trim().toUpperCase();
}
