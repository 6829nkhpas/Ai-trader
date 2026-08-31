// Styling decisions for the pattern scanner, kept pure and out of the JSX.
//
// The panel was entirely monochrome: every bar was `bg-text-primary` or
// `bg-text-muted`, so a bearish 95%-complete Double Top looked exactly like a
// bullish 20% one. Direction is the first thing a trader reads off a pattern, and
// it was the one thing the design did not show.
//
// Colour comes from the THEME TOKENS (`bull` / `bear` / `neutral`), never from
// hardcoded hexes, because those tokens are redefined per theme in `globals.css`
// — the light theme darkens them to clear WCAG AA at the 8-10px sizes this panel
// uses. Hardcoding `emerald-400` here would look right in dark and be illegible
// in light. Note also that a `dark:` variant would NOT work: the app never adds a
// `.dark` class, so those utilities are inert.
//
// Colour is always paired with a text label or icon in the markup — it is never
// the only carrier of meaning.

export type PatternSentiment = 'bullish' | 'bearish' | 'neutral';

/**
 * Normalise the API's free-text sentiment.
 *
 * The field arrives as a display string ("Bullish", "bearish", occasionally
 * "Bullish Reversal"), so this matches on the prefix rather than requiring an
 * exact token, and anything unrecognised is neutral rather than silently bullish.
 */
export function patternSentiment(raw: string | null | undefined): PatternSentiment {
  const s = raw?.trim().toLowerCase() ?? '';
  if (s.startsWith('bull')) return 'bullish';
  if (s.startsWith('bear')) return 'bearish';
  return 'neutral';
}

export interface SentimentTheme {
  /** Text colour for the sentiment word itself. */
  text: string;
  /** Badge surface, text and border as one set. */
  badge: string;
  /** Gradient stops for a filled progress/confidence bar. */
  bar: string;
  /** The vertical accent down the card's leading edge. */
  edge: string;
  /** Barely-there wash revealed on hover, to tint the whole row. */
  wash: string;
  /** A solid dot or small marker. */
  dot: string;
  /** Soft outer glow for the leading edge on a live pattern. */
  glow: string;
}

const THEMES: Record<PatternSentiment, SentimentTheme> = {
  bullish: {
    text: 'text-bull',
    badge: 'bg-bull/10 text-bull border-bull/40',
    bar: 'from-bull/45 via-bull to-bull',
    edge: 'bg-bull',
    wash: 'group-hover:bg-bull/6',
    dot: 'bg-bull',
    glow: 'shadow-[0_0_6px_var(--color-bull)]',
  },
  bearish: {
    text: 'text-bear',
    badge: 'bg-bear/10 text-bear border-bear/40',
    bar: 'from-bear/45 via-bear to-bear',
    edge: 'bg-bear',
    wash: 'group-hover:bg-bear/6',
    dot: 'bg-bear',
    glow: 'shadow-[0_0_6px_var(--color-bear)]',
  },
  neutral: {
    text: 'text-neutral',
    badge: 'bg-neutral/10 text-neutral border-neutral/40',
    bar: 'from-neutral/45 via-neutral to-neutral',
    edge: 'bg-neutral',
    wash: 'group-hover:bg-neutral/6',
    dot: 'bg-neutral',
    glow: 'shadow-[0_0_6px_var(--color-neutral)]',
  },
};

export function sentimentTheme(sentiment: PatternSentiment): SentimentTheme {
  return THEMES[sentiment];
}

/** Badge classes for a neutral/among-the-furniture tag. */
export const MUTED_TAG = 'bg-elevated text-text-secondary border-border-default';

/**
 * Volume validation reads as a three-state confirmation, so it gets a colour and
 * a glyph rather than only a glyph: confirmed is a positive signal regardless of
 * the pattern's direction, so it is green even on a bearish pattern.
 */
export function volumeTag(validation: string): { badge: string; glyph: string } {
  const v = validation.toLowerCase();
  if (v.includes('confirm')) return { badge: 'bg-bull/10 text-bull border-bull/40', glyph: '✓' };
  if (v.includes('form')) return { badge: 'bg-neutral/10 text-neutral border-neutral/40', glyph: '◎' };
  return { badge: MUTED_TAG, glyph: '○' };
}

/**
 * A breakout that is actually happening takes the pattern's own direction — that
 * is the moment the pattern is paying off, so it should read as loudly as the
 * sentiment badge. Anything still pending stays muted so it cannot be mistaken
 * for a live break.
 */
export function breakoutTag(status: string, sentiment: PatternSentiment): string {
  const s = status.toLowerCase();
  const active = s.includes('break') || s.includes('broken') || s.includes('confirm');
  return active ? sentimentTheme(sentiment).badge : MUTED_TAG;
}

/**
 * Confidence is a strength reading, not a direction, so it is banded rather than
 * tinted by sentiment: a 30%-confidence bearish pattern should not look as
 * emphatic as a 90% one just because both are bearish.
 */
export function confidenceBand(confidence: number): 'low' | 'medium' | 'high' {
  if (!Number.isFinite(confidence)) return 'low';
  if (confidence >= 0.75) return 'high';
  if (confidence >= 0.5) return 'medium';
  return 'low';
}

/** Opacity applied to the confidence bar so a weak reading looks weak. */
export const CONFIDENCE_BAND_OPACITY: Record<'low' | 'medium' | 'high', string> = {
  low: 'opacity-45',
  medium: 'opacity-75',
  high: 'opacity-100',
};
