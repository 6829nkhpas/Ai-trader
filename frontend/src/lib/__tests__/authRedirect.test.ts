// @vitest-environment jsdom
//
// The sign-in redirect.
//
// `NEXT_PUBLIC_AUTH_URL` is stubbed globally by `vitest.config.ts` to
// `https://auth.test.invalid`.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { redirectToSignIn, signInUrl } from '../authRedirect';

const AUTH = 'https://auth.test.invalid';

/** Point `window.location` at a given app URL for the duration of a test. */
function atLocation(href: string) {
  const url = new URL(href);
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      origin: url.origin,
      pathname: url.pathname,
      search: url.search,
      href: url.href,
      replace: vi.fn(),
      assign: vi.fn(),
    },
  });
  return window.location as unknown as { replace: ReturnType<typeof vi.fn> };
}

const realLocation = window.location;

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, value: realLocation });
});

describe('signInUrl', () => {
  it('asks the auth surface to return the user to this origin and path', () => {
    atLocation('https://app.stratai.live/');
    expect(signInUrl()).toBe(
      `${AUTH}/?redirect=${encodeURIComponent('https://app.stratai.live/')}`,
    );
  });

  it('preserves a deep link so the user comes back where they were', () => {
    atLocation('https://app.stratai.live/dashboard');
    expect(signInUrl()).toBe(
      `${AUTH}/?redirect=${encodeURIComponent('https://app.stratai.live/dashboard')}`,
    );
  });

  it('drops the current query string from the returned-to URL', () => {
    // The terminal's own params are not worth round-tripping through another
    // origin, and echoing arbitrary query state back through a redirect param is
    // how surprises get in.
    atLocation('https://app.stratai.live/?symbol=TCS&secret=abc');
    expect(signInUrl()).toBe(
      `${AUTH}/?redirect=${encodeURIComponent('https://app.stratai.live/')}`,
    );
  });

  it('encodes the destination so it cannot break out of the parameter', () => {
    atLocation('https://app.stratai.live/');
    const url = signInUrl('https://app.stratai.live/?a=1&b=2');
    // The `&` must be percent-encoded, or everything after it reads as a
    // sibling param of the auth page rather than part of the destination.
    expect(url).not.toContain('&b=2');
    expect(new URL(url).searchParams.get('redirect')).toBe(
      'https://app.stratai.live/?a=1&b=2',
    );
  });

  it('does not double the slash when the configured auth URL has a trailing one', () => {
    atLocation('https://app.stratai.live/');
    expect(signInUrl()).not.toContain('//?redirect');
  });
});

describe('redirectToSignIn', () => {
  it('replaces the history entry instead of pushing one', () => {
    const location = atLocation('https://app.stratai.live/');
    redirectToSignIn();

    // `replace`, not `assign`: with a pushed entry, pressing Back returns to the
    // terminal, the session check fails again, and the user is bounced forward —
    // a loop with no way out.
    expect(location.replace).toHaveBeenCalledTimes(1);
    expect(location.replace).toHaveBeenCalledWith(
      `${AUTH}/?redirect=${encodeURIComponent('https://app.stratai.live/')}`,
    );
  });
});
