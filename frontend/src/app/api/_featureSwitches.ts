// app/api/_featureSwitches.ts — SERVER-ONLY resolution of the feature kill
// switches, and the enforcement helper the capability routes apply.
//
// Why this is not in `lib/featureFlags.ts`
// ---------------------------------------
// The switches used to be read from `NEXT_PUBLIC_ENABLE_*`. Next.js implements a
// `NEXT_PUBLIC_` var as a build-time textual substitution into the JS bundle, so
// that design had two defects:
//
//   1. The value shipped to the browser, where it is readable and editable. A
//      user could flip a kill switch in devtools and render premium UI.
//   2. Changing a switch required a full rebuild — and this app's production
//      build peaks north of 1 GB of RSS on a memory-tight droplet.
//
// The names here carry NO `NEXT_PUBLIC_` prefix, which is precisely what keeps
// them server-side: Next only inlines the prefixed form, so `process.env.ENABLE_X`
// resolves against the container's real environment at request time and never
// appears in a client chunk. Flipping a switch is now a container restart.
//
// ⚠ Moving the READ server-side does not by itself make gating tamper-proof.
//   Whatever the client is told, it can lie to itself about. `assertFeatureEnabled`
//   below is the part that actually binds: it runs in the request path, so a
//   client that unlocks its own UI still gets a 403 from the capability.

import {
  ALL_SWITCHES_OFF,
  FEATURE_IDS,
  type FeatureId,
  type FeatureKillSwitches,
  type FeatureRuntimeConfig,
} from '../../lib/featureFlags';

/**
 * The server-side env var backing each feature's deployment kill switch.
 *
 * Written as literal `process.env.<NAME>` member expressions for the same reason
 * `lib/env.ts` is: a computed `process.env[key]` lookup is not statically
 * analysable. That mattered acutely for the old client-side implementation (it
 * silently read `undefined` in every browser — see
 * `lib/__tests__/featureFlags.staticEnv.test.ts`), and while a Node server has a
 * real `process.env` that makes computed access safe, keeping the literal form
 * means the two sides cannot drift into different habits.
 */
function readSwitchEnv(): Record<FeatureId, string | undefined> {
  return {
    deepseekGlm: process.env.ENABLE_DEEPSEEK_GLM,
    multiModel: process.env.ENABLE_MULTI_MODEL,
    ghostline: process.env.ENABLE_GHOSTLINE,
    footprint: process.env.ENABLE_FOOTPRINT,
    topup: process.env.ENABLE_TOPUP,
    instantNews: process.env.ENABLE_INSTANT_NEWS,
    advanceChart: process.env.ENABLE_ADVANCE_CHART,
  };
}

/**
 * Whether an env value means "on".
 *
 * Accepts `true`/`1`/`yes`/`on` case-insensitively. The old implementation
 * accepted only the exact string `'true'`, which made `ENABLE_FOOTPRINT=1` look
 * like a disabled feature instead of a typo — a silent misconfiguration on a
 * value an operator sets by hand.
 */
export function envSwitchOn(raw: string | undefined): boolean {
  const v = (raw ?? '').trim().toLowerCase();
  return v === 'true' || v === '1' || v === 'yes' || v === 'on';
}

/**
 * Whether this deployment enforces gating.
 *
 * `FEATURE_ENFORCEMENT` is the server-side counterpart of the old
 * `NEXT_PUBLIC_PROD`. When it is unset the deployment is treated as local
 * development and every feature is unlocked, matching the long-standing dev
 * behaviour.
 *
 * The name is deliberately not a bare `PROD`: that collides with the reserved
 * `import.meta.env.PROD` in the Vite-based test tooling, and a generic name is a
 * poor choice for a container variable whose value decides whether paid features
 * are enforced — too easy for an unrelated tool to set.
 */
export function enforcementEnabled(): boolean {
  return envSwitchOn(process.env.FEATURE_ENFORCEMENT);
}

/** Resolve the deployment's kill switches from the server environment. */
export function resolveKillSwitches(): FeatureKillSwitches {
  const raw = readSwitchEnv();
  const out = { ...ALL_SWITCHES_OFF };
  for (const id of FEATURE_IDS) out[id] = envSwitchOn(raw[id]);
  return out;
}

/** The full config handed to the client by `app/api/features/route.web.ts`. */
export function resolveFeatureConfig(): FeatureRuntimeConfig {
  return { enforced: enforcementEnabled(), switches: resolveKillSwitches() };
}

/**
 * Whether `id` is usable in this deployment, ignoring the per-user entitlement.
 *
 * In a non-enforcing deployment everything is on, mirroring
 * `computeFeatureAccess`, so local development is unaffected by enforcement.
 */
export function featureEnabled(id: FeatureId): boolean {
  if (!enforcementEnabled()) return true;
  return resolveKillSwitches()[id];
}

/**
 * Server-side gate for a route that implements a gated capability.
 *
 * Returns `null` when the request may proceed, or a 403 `Response` to return
 * immediately. This is the enforcement the client cannot edit around: the kill
 * switch is consulted in the request path, on the server, every time.
 *
 * SCOPE — read this before assuming a feature is protected. This enforces the
 * deployment-wide kill switch ONLY. It does NOT enforce the per-user plan
 * entitlement, because these proxy routes do not resolve the caller's identity:
 * the JWT is minted by the separate `api-web.stratai.live` deployment, and the
 * `/api/v1/internal/entitlement/{user_id}` endpoint needed to verify a plan
 * server-side does not exist yet. Until it does, a *subscriber-level* bypass
 * remains possible on the web path and per-user gating stays an affordance.
 * The one authoritative per-user gate today is
 * `agents/deep-quant-loop/entitlements.py`.
 */
export function assertFeatureEnabled(id: FeatureId, label: string): Response | null {
  if (featureEnabled(id)) return null;
  return Response.json(
    {
      error:
        `${label} is disabled in this deployment. If you believe you have access to it, ` +
        `contact support — the operator controls this switch, not your plan.`,
    },
    { status: 403 },
  );
}
