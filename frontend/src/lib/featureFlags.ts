import type { AccessFlags } from './api/types';

// Logical feature ids — the canonical names callers pass to `hasFeature`.
export type FeatureId =
  | 'deepseekGlm'
  | 'multiModel'
  | 'ghostline'
  | 'footprint'
  | 'topup'
  | 'instantNews'
  | 'advanceChart';

/**
 * The per-feature deployment kill switches, as resolved by the BACKEND.
 *
 * These deliberately do NOT come from `NEXT_PUBLIC_*` env vars any more. A
 * `NEXT_PUBLIC_` value is substituted into the JavaScript bundle at build time,
 * which means it is shipped to the client and editable there — so the switch was
 * both readable and flippable by anyone with devtools. It also could not be
 * changed without a full rebuild.
 *
 * The authoritative values now live only in the server's environment
 * (`ENABLE_*`, no prefix) and are resolved by:
 *   - web:     `app/api/features/route.web.ts` → `app/api/_featureSwitches.ts`
 *   - desktop: `src-tauri/src/commands/features.rs` (baked with `option_env!`)
 *
 * The client fetches the resolved map through `bridgeInvoke('get_feature_switches')`.
 *
 * ⚠ This is still a **UI affordance**. Any value the client receives can be
 *   patched in the client. The load-bearing enforcement is server-side at the
 *   capability itself — see `app/api/_featureSwitches.ts::assertFeatureEnabled`,
 *   applied today to the deep-quant agent lifecycle (`/api/deepquant/{run,qa,
 *   resume,stream,cancel}`). Only features whose UI gate has a matching
 *   server-side capability can be enforced that way; `footprint` and `ghostline`
 *   are computed in the browser and have no such capability to gate.
 */
export type FeatureKillSwitches = Record<FeatureId, boolean>;

/** The backend's answer to "what is enabled in this deployment?". */
export interface FeatureRuntimeConfig {
  /**
   * Whether this deployment enforces gating at all. True in production; false in
   * local development, where every feature is unlocked so developers can work
   * without a subscription. Resolved server-side from `PROD`, so it is not a
   * client-editable constant either.
   */
  enforced: boolean;
  /** Per-feature deployment kill switches. */
  switches: FeatureKillSwitches;
}

// Maps a feature id to the server accessFlag key carrying the per-user plan
// entitlement. In an enforcing deployment a feature is usable only when the
// deployment kill switch AND the user's accessFlag are both true.
const ACCESS_FLAG_BY_FEATURE: Record<FeatureId, keyof AccessFlags> = {
  deepseekGlm: 'canAccessDeepseekGLM',
  multiModel: 'canAccessMultiModel',
  ghostline: 'canAccessGhostline',
  footprint: 'canAccessFootprint',
  topup: 'canAccessTopup',
  instantNews: 'canSeeInstantNewsSantiments',
  advanceChart: 'canGetAdvanceChartAccess',
};

export const FEATURE_IDS: readonly FeatureId[] = Object.keys(
  ACCESS_FLAG_BY_FEATURE,
) as FeatureId[];

/** The accessFlag key that gates `id`. Exported for the server-side enforcement. */
export function accessFlagFor(id: FeatureId): keyof AccessFlags {
  return ACCESS_FLAG_BY_FEATURE[id];
}

export const FEATURE_LABELS: Record<FeatureId, string> = {
  deepseekGlm: 'DeepSeek GLM',
  multiModel: 'Multi-Model',
  ghostline: 'Ghostline',
  footprint: 'Footprint',
  topup: 'Credit Top-up',
  instantNews: 'Instant News',
  advanceChart: 'Advanced Charts',
};

export type FeatureAccessMap = Record<FeatureId, boolean>;

/** Every switch off. */
export const ALL_SWITCHES_OFF: FeatureKillSwitches = {
  deepseekGlm: false,
  multiModel: false,
  ghostline: false,
  footprint: false,
  topup: false,
  instantNews: false,
  advanceChart: false,
};

/**
 * The config assumed before the backend has answered.
 *
 * Deliberately fail-closed — `enforced: true` with every switch off. A
 * hydration failure therefore locks premium UI rather than unlocking it, which
 * is the safe direction: the previous build-time constant defaulted to the same
 * locked state, so this changes nothing about the first paint.
 */
export const UNRESOLVED_FEATURE_CONFIG: FeatureRuntimeConfig = {
  enforced: true,
  switches: ALL_SWITCHES_OFF,
};

/** Coerce an untrusted backend payload into a `FeatureRuntimeConfig`. */
export function parseFeatureConfig(payload: unknown): FeatureRuntimeConfig {
  const obj = (payload ?? {}) as { enforced?: unknown; switches?: unknown };
  const rawSwitches = (obj.switches ?? {}) as Record<string, unknown>;
  const switches = {} as FeatureKillSwitches;
  for (const id of FEATURE_IDS) switches[id] = rawSwitches[id] === true;
  return {
    // Anything other than an explicit `false` keeps enforcement on, so a
    // malformed or truncated payload cannot accidentally unlock the app.
    enforced: obj.enforced !== false,
    switches,
  };
}

/**
 * Compute the full access map from the user's plan flags and the backend config.
 *
 * Pure: reads no environment and no module state, so it is fully testable and
 * cannot silently pick up a build-time constant.
 *
 * In a non-enforcing deployment (local dev) every feature is unlocked regardless
 * of plan — the kill switches and accessFlags only apply in production.
 */
export function computeFeatureAccess(
  accessFlags: AccessFlags | null | undefined,
  config: FeatureRuntimeConfig = UNRESOLVED_FEATURE_CONFIG,
): FeatureAccessMap {
  const result = {} as FeatureAccessMap;

  if (!config.enforced) {
    for (const id of FEATURE_IDS) result[id] = true;
    return result;
  }

  for (const id of FEATURE_IDS) {
    const granted = accessFlags ? Boolean(accessFlags[ACCESS_FLAG_BY_FEATURE[id]]) : false;
    result[id] = Boolean(config.switches[id]) && granted;
  }
  return result;
}

export function hasFeature(map: FeatureAccessMap, id: FeatureId): boolean {
  return Boolean(map[id]);
}
