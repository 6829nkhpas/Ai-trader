/**
 * Required public configuration, validated at import.
 *
 * Each value is read as a literal `process.env.NEXT_PUBLIC_*` expression because
 * Next inlines those at build time — indexing `process.env` dynamically would
 * leave `undefined` in the bundle. See `lib/__tests__/featureFlags.staticEnv.test.ts`,
 * which enforces that rule.
 */

/**
 * Narrow a required env var, failing loudly when it is missing.
 *
 * The throw is the feature: every one of these is load-bearing at first paint, so
 * a blank value would surface much later as a broken request or a dead button.
 * Returning `string` (not `string | undefined`) also means callers do not each
 * have to re-assert it.
 */
function required(name: string, value: string | undefined): string {
  if (!value) {
    throw new Error(`Missing ${name}. Set it in frontend/.env.local (see .env.example).`);
  }
  return value;
}

/** The Strat AI API (auth, credit, billing, profile). */
export const API_BASE_URL = required(
  'NEXT_PUBLIC_API_BASE_URL',
  process.env.NEXT_PUBLIC_API_BASE_URL,
);

/** The account/billing surface linked from upsell and manage-account CTAs. */
export const DASHBOARD_URL = required(
  'NEXT_PUBLIC_DASHBOARD_URL',
  process.env.NEXT_PUBLIC_DASHBOARD_URL,
);

/**
 * The sign-in surface (auth.stratai.live).
 *
 * The terminal has no login form of its own — it redirects here and relies on the
 * `.stratai.live` session cookie the auth surface sets. Unset means an
 * unauthenticated visitor has nowhere to go, so it is required like the rest.
 */
export const AUTH_URL = required('NEXT_PUBLIC_AUTH_URL', process.env.NEXT_PUBLIC_AUTH_URL);

export const API_V1_PREFIX = '/api/v1';

// PROD controls whether premium features are gated by the user's plan.
// In dev (false) every feature is unlocked so developers can test freely.
export const IS_PROD = process.env.NEXT_PUBLIC_PROD === 'true';
