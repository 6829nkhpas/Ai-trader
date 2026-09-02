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

/**
 * Whether the Find Quant Trade workspace uses the multi-session architecture.
 *
 * OFF: `useQuantStore` keeps its `${SYMBOL}::${PROFILE}` keying and its flat mirror, and
 * the panel behaves exactly as it does today.
 * ON: routing moves to `useSessionStore`, keyed by opaque server session id, and the
 * "route an unknown frame to whatever is on screen" fallback is gone.
 *
 * This is a ROLLOUT switch, not a security or entitlement gate — the binding checks are
 * server-side (`DEEP_QUANT_SESSIONS_ENABLED`, `DEEP_QUANT_REQUIRE_IDENTITY`), and nothing
 * here decides what a user is allowed to do. That is why `NEXT_PUBLIC_` is acceptable
 * despite being inlined at build time and therefore editable in devtools: the worst a user
 * can do by flipping it is give themselves a UI whose backend refuses them.
 *
 * A rebuild to change, unlike the server switches. Accepted: the two sides flip once, in a
 * planned order (see the rollout note in `.env.example`).
 */
export const FQ_MULTI_SESSION = process.env.NEXT_PUBLIC_FQ_MULTI_SESSION === 'true';
