// lib/authRedirect.ts — where an unauthenticated visitor is sent to sign in.
//
// The terminal has no login form. Authentication happens on the dedicated auth
// surface (auth.stratai.live), which sets the session as an httpOnly cookie
// scoped to `.stratai.live` — so by the time the user is sent back here, this
// origin is already carrying the session and there is nothing to hand over in
// the URL. No token in a query string, no exchange step, nothing for a referrer
// header or a browser history entry to leak.
//
// This replaced an in-app overlay that drove a desktop-shaped handshake: POST a
// session, open a browser, then race a `strat://` deep-link event against a
// polling loop and exchange the winner for tokens kept in `localStorage`. All of
// that existed to move a session into a Tauri shell that no longer ships.

import { AUTH_URL } from './env';

/**
 * Build the sign-in URL for the auth surface, asking it to return the user here.
 *
 * `?redirect=` is validated on the far side against its own allowlist (see
 * `utils/postAuthRedirect.ts` in the auth app) — an unvalidated redirect param on
 * a login page is a credential-phishing primitive, so neither end trusts it.
 * This function is the other half of that contract: it only ever names THIS
 * origin, so there is no path by which a value from elsewhere reaches the param.
 *
 * `returnTo` defaults to the current location so a user deep-linked into the
 * terminal comes back to the same place rather than the root.
 */
export function signInUrl(returnTo?: string): string {
  const base = AUTH_URL.replace(/\/+$/, '');
  if (typeof window === 'undefined') return base;

  // Origin + path only. A query string of our own would be echoed back through
  // the auth surface, and the terminal's URL params are not worth round-tripping.
  const destination = returnTo ?? `${window.location.origin}${window.location.pathname}`;
  return `${base}/?redirect=${encodeURIComponent(destination)}`;
}

/**
 * Send the browser to the auth surface, replacing the current history entry.
 *
 * `replace`, not `assign`: the terminal is not a page worth going Back to when
 * you are not signed in — with `assign`, Back returns here, the session check
 * fails again, and the user is bounced forward in a loop they cannot escape.
 */
export function redirectToSignIn(returnTo?: string): void {
  if (typeof window === 'undefined') return;
  window.location.replace(signInUrl(returnTo));
}
