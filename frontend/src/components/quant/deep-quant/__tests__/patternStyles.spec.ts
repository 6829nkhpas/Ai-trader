// The pattern scanner was monochrome: every bar was `bg-text-primary` or
// `bg-text-muted`, so a bearish 95%-complete Double Top rendered identically to a
// bullish 20% one. These pin the mapping that fixes that, and — more importantly —
// pin that it goes through the THEME TOKENS.
//
// That last part is the load-bearing bit. `bull` / `bear` / `neutral` are redefined
// under `.light` in globals.css, where they darken to clear WCAG AA at the 8-10px
// sizes this panel uses (measured: 5.48:1, 6.47:1, 5.02:1 against white, versus
// 2.54:1, 3.76:1 and 2.15:1 for the dark-theme values). A hardcoded
// `text-emerald-400` would look correct in dark and be illegible in light, and a
// `dark:` variant would not help either — the app never adds a `.dark` class, so
// those utilities never match.
import { describe, expect, it } from 'vitest';

import {
  CONFIDENCE_BAND_OPACITY,
  MUTED_TAG,
  breakoutTag,
  confidenceBand,
  patternSentiment,
  sentimentTheme,
  volumeTag,
} from '../patternStyles';

describe('patternSentiment', () => {
  it('reads the API’s display casing and phrasing', () => {
    for (const raw of ['Bullish', 'bullish', 'BULLISH', 'Bullish Reversal']) {
      expect(patternSentiment(raw)).toBe('bullish');
    }
    for (const raw of ['Bearish', 'bearish', 'Bearish Continuation']) {
      expect(patternSentiment(raw)).toBe('bearish');
    }
  });

  it('falls back to neutral rather than guessing a direction', () => {
    // Claiming a direction we were not given is the one failure that would put a
    // green badge on a pattern the scanner never called bullish.
    for (const raw of ['', '   ', 'sideways', 'unknown', null, undefined]) {
      expect(patternSentiment(raw)).toBe('neutral');
    }
  });
});

describe('sentimentTheme', () => {
  it('uses only theme tokens, never a hardcoded palette colour', () => {
    // A literal hex or a Tailwind palette shade here would break one of the two
    // themes, because only the tokens are redefined per theme.
    for (const s of ['bullish', 'bearish', 'neutral'] as const) {
      const values = Object.values(sentimentTheme(s));
      expect(values.length).toBeGreaterThan(0);
      for (const cls of values) {
        expect(cls).toMatch(/\b(bull|bear|neutral)\b/);
        expect(cls).not.toMatch(/#[0-9a-fA-F]{3,6}/);
        expect(cls).not.toMatch(/(emerald|red|amber|green|rose)-\d{3}/);
      }
    }
  });

  it('gives each direction its own colour so the three are distinguishable', () => {
    const bull = sentimentTheme('bullish');
    const bear = sentimentTheme('bearish');
    const neutral = sentimentTheme('neutral');
    expect(new Set([bull.edge, bear.edge, neutral.edge]).size).toBe(3);
    expect(new Set([bull.bar, bear.bar, neutral.bar]).size).toBe(3);
  });

  it('builds bars as gradients so the fill reads as a bar, not a block', () => {
    for (const s of ['bullish', 'bearish', 'neutral'] as const) {
      // Consumed as `bg-gradient-to-r ${bar}`, so it must carry gradient stops.
      expect(sentimentTheme(s).bar).toMatch(/from-.*via-.*to-/);
    }
  });
});

describe('volumeTag', () => {
  it('reads confirmed volume as positive regardless of the pattern’s direction', () => {
    // Volume confirming a bearish pattern is still a confirmation, so it stays
    // green rather than borrowing the pattern's red.
    const { badge, glyph } = volumeTag('Confirmed');
    expect(badge).toContain('bull');
    expect(glyph).toBe('✓');
  });

  it('marks still-forming volume as a caution and anything else as neutral', () => {
    expect(volumeTag('Forming').badge).toContain('neutral');
    expect(volumeTag('Forming').glyph).toBe('◎');
    expect(volumeTag('Weak').badge).toBe(MUTED_TAG);
    expect(volumeTag('Weak').glyph).toBe('○');
  });

  it('is case insensitive, since the field is free text', () => {
    expect(volumeTag('volume confirmed').badge).toContain('bull');
  });
});

describe('breakoutTag', () => {
  it('takes the pattern’s direction once the break is actually happening', () => {
    expect(breakoutTag('Breaking Down', 'bearish')).toBe(sentimentTheme('bearish').badge);
    expect(breakoutTag('Broken Out', 'bullish')).toBe(sentimentTheme('bullish').badge);
  });

  it('stays muted while pending, so it cannot be mistaken for a live break', () => {
    expect(breakoutTag('Pending', 'bearish')).toBe(MUTED_TAG);
    expect(breakoutTag('Watching', 'bullish')).toBe(MUTED_TAG);
  });
});

describe('confidenceBand', () => {
  it('bands the reading so a weak one does not look emphatic', () => {
    expect(confidenceBand(0.9)).toBe('high');
    expect(confidenceBand(0.75)).toBe('high');
    expect(confidenceBand(0.6)).toBe('medium');
    expect(confidenceBand(0.5)).toBe('medium');
    expect(confidenceBand(0.49)).toBe('low');
    expect(confidenceBand(0)).toBe('low');
  });

  it('treats a non-finite confidence as the weakest band, not the strongest', () => {
    expect(confidenceBand(Number.NaN)).toBe('low');
    expect(confidenceBand(Number.POSITIVE_INFINITY)).toBe('low');
  });

  it('has an opacity for every band', () => {
    for (const band of ['low', 'medium', 'high'] as const) {
      expect(CONFIDENCE_BAND_OPACITY[band]).toMatch(/^opacity-\d+$/);
    }
    // And they must actually differ, or the banding is invisible.
    expect(new Set(Object.values(CONFIDENCE_BAND_OPACITY)).size).toBe(3);
  });
});
