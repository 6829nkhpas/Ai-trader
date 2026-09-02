// components/quant/session/sessionLabel.ts
//
// How a session is named in the UI. Pure functions, kept out of the components so the naming
// rules can be tested without rendering anything.

import type { SessionSummary } from '../../../lib/fq/api';

/**
 * Times are formatted in the exchange's timezone, not the browser's.
 *
 * A session's label is a trading timestamp — "9:15 AM" means the open. A user travelling, or on a
 * machine with a wrong timezone, would otherwise see labels that disagree with every chart and
 * order in the app. Asia/Kolkata is the market this product trades.
 */
const MARKET_TIME_ZONE = 'Asia/Kolkata';

// Built once. `Intl.DateTimeFormat` construction is not cheap and a tab bar formats a label per
// tab per render.
const timeFormat = new Intl.DateTimeFormat('en-IN', {
  hour: 'numeric',
  minute: '2-digit',
  hour12: true,
  timeZone: MARKET_TIME_ZONE,
});

const dayFormat = new Intl.DateTimeFormat('en-IN', {
  day: 'numeric',
  month: 'short',
  timeZone: MARKET_TIME_ZONE,
});

/** `h:mm a` for an epoch-seconds timestamp, in market time. */
export function formatSessionTime(epochSeconds: number): string {
  if (!Number.isFinite(epochSeconds) || epochSeconds <= 0) return '';
  // The API sends epoch SECONDS (REAL, matching `hashchain.now()`); `Date` takes milliseconds.
  // Getting this wrong produces 1970, which is why it is converted in exactly one place.
  return timeFormat.format(new Date(epochSeconds * 1000)).toUpperCase();
}

/** `12 Mar` for an epoch-seconds timestamp, in market time. */
export function formatSessionDay(epochSeconds: number): string {
  if (!Number.isFinite(epochSeconds) || epochSeconds <= 0) return '';
  return dayFormat.format(new Date(epochSeconds * 1000));
}

/**
 * The compact label a tab shows.
 *
 * A user rename WINS. `title` is nullable precisely so the client can tell "never named" from
 * "named by the user": a non-null title is a deliberate act, and showing a derived label over it
 * would make the rename look like it failed.
 *
 * With no title the label is derived — `RELIANCE · 10m · 9:15 AM`. The time disambiguates the case
 * the old `${symbol}::${profile}` key could not represent at all: two sessions on the same symbol
 * and timeframe, which simply overwrote each other before.
 */
export function sessionTabLabel(session: SessionSummary): string {
  if (session.title && session.title.trim()) return session.title.trim();
  const time = formatSessionTime(session.created_at);
  return [session.symbol, session.timeframe, time].filter(Boolean).join(' · ');
}

/**
 * The full description for a tooltip and for screen readers.
 *
 * The tab itself is deliberately terse, so everything trimmed out of it lives here: the profile,
 * the day, and the derived label when a user title has replaced it.
 */
export function sessionTabTooltip(session: SessionSummary): string {
  const parts = [session.symbol, session.timeframe, session.profile];
  const day = formatSessionDay(session.created_at);
  const time = formatSessionTime(session.created_at);
  if (day || time) parts.push([day, time].filter(Boolean).join(' '));
  const derived = parts.filter(Boolean).join(' · ');
  const title = session.title?.trim();
  return title ? `${title} — ${derived}` : derived;
}

/**
 * What a tab announces to assistive technology.
 *
 * The visible label is an abbreviation dense enough to be unreadable aloud ("RELIANCE · 10m ·
 * 9:15 AM"), and a streaming tab's state is conveyed visually by a pulsing dot that a screen
 * reader cannot see. Both are spelled out here.
 */
export function sessionTabAriaLabel(session: SessionSummary, isStreaming: boolean): string {
  const base = sessionTabTooltip(session);
  return isStreaming ? `${base}, analysis running` : base;
}
