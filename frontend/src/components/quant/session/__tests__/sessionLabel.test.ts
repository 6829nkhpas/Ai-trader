// components/quant/session/__tests__/sessionLabel.test.ts
//
// How a session is named. Pure, so it is tested without rendering.

import { describe, expect, it } from 'vitest';

import type { SessionSummary } from '../../../../lib/fq/api';
import {
  formatSessionDay,
  formatSessionTime,
  sessionTabAriaLabel,
  sessionTabLabel,
  sessionTabTooltip,
} from '../sessionLabel';

// 2026-03-12 09:15 IST = 03:45 UTC. Chosen as the market open, in EPOCH SECONDS — the API sends
// seconds (REAL, matching `hashchain.now()`), and treating them as milliseconds silently yields 1970.
const OPEN_IST = Date.UTC(2026, 2, 12, 3, 45, 0) / 1000;

function summary(over: Partial<SessionSummary> = {}): SessionSummary {
  return {
    session_id: 'sess_1',
    title: null,
    symbol: 'RELIANCE',
    timeframe: '10m',
    profile: 'INTRADAY',
    status: 'active',
    created_at: OPEN_IST,
    updated_at: OPEN_IST,
    archived_at: null,
    active_run_id: null,
    message_count: 0,
    last_run: null,
    ...over,
  };
}

describe('formatSessionTime', () => {
  it('formats in MARKET time, not the machine timezone', () => {
    // A trader in another timezone must still see 9:15 AM for the open: the label is a trading
    // timestamp, and one that disagreed with every chart in the app would be worse than no label.
    expect(formatSessionTime(OPEN_IST)).toBe('9:15 AM');
  });

  it('treats the value as seconds', () => {
    // The 1970 bug: passing the same number as milliseconds lands in January 1970. If this ever
    // reads "5:30 AM" for a 2026 timestamp, the conversion was dropped.
    expect(formatSessionTime(OPEN_IST)).not.toBe(formatSessionTime(OPEN_IST / 1000));
  });

  it.each([0, -1, NaN, Infinity])('returns nothing for a nonsense timestamp (%p)', (value) => {
    // A missing timestamp must not print "1 Jan 1970, 5:30 AM" on a tab.
    expect(formatSessionTime(value)).toBe('');
  });
});

describe('formatSessionDay', () => {
  it('formats the market day', () => {
    expect(formatSessionDay(OPEN_IST)).toBe('12 Mar');
  });

  it('returns nothing for a nonsense timestamp', () => {
    expect(formatSessionDay(0)).toBe('');
  });
});

describe('sessionTabLabel', () => {
  it('derives SYMBOL · TF · TIME when the user has not named the session', () => {
    expect(sessionTabLabel(summary())).toBe('RELIANCE · 10m · 9:15 AM');
  });

  it('prefers a user rename', () => {
    // `title` is nullable so the client can tell "never named" from "named deliberately". Showing
    // the derived label over a rename would make the rename look like it failed.
    expect(sessionTabLabel(summary({ title: 'Gap-up thesis' }))).toBe('Gap-up thesis');
  });

  it('falls back to the derived label for a whitespace-only title', () => {
    // A blank tab is unusable and unclickable-looking; the derived label is always meaningful.
    expect(sessionTabLabel(summary({ title: '   ' }))).toBe('RELIANCE · 10m · 9:15 AM');
  });

  it('distinguishes two sessions on the same symbol AND timeframe', () => {
    // The exact case the old `${symbol}::${profile}` key could not represent: the second session
    // overwrote the first. The time is what tells them apart.
    const first = summary({ session_id: 'a', created_at: OPEN_IST });
    const second = summary({ session_id: 'b', created_at: OPEN_IST + 3600 });
    expect(sessionTabLabel(first)).not.toBe(sessionTabLabel(second));
  });

  it('still produces something when the timestamp is missing', () => {
    expect(sessionTabLabel(summary({ created_at: 0 }))).toBe('RELIANCE · 10m');
  });
});

describe('sessionTabTooltip', () => {
  it('carries the detail the tab is too small to show', () => {
    expect(sessionTabTooltip(summary())).toBe('RELIANCE · 10m · INTRADAY · 12 Mar 9:15 AM');
  });

  it('keeps the derived detail alongside a rename', () => {
    // Renaming a session must not hide which symbol and timeframe it analyses — that is the one
    // thing the user cannot re-derive from a custom title.
    expect(sessionTabTooltip(summary({ title: 'Gap-up thesis' }))).toBe(
      'Gap-up thesis — RELIANCE · 10m · INTRADAY · 12 Mar 9:15 AM',
    );
  });
});

describe('sessionTabAriaLabel', () => {
  it('spells out a streaming state that is only shown visually', () => {
    // The pulsing dot is `aria-hidden`, so without this a screen-reader user has no way to know a
    // background session is still working.
    expect(sessionTabAriaLabel(summary(), true)).toContain('analysis running');
    expect(sessionTabAriaLabel(summary(), false)).not.toContain('analysis running');
  });
});
