import { IS_PROD } from './env';
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

// Maps a feature id to the env-var-backed kill switch + the corresponding
// server accessFlag key. The kill switch is a global on/off for the whole
// deployment; the accessFlag is the per-user plan entitlement. In prod a
// feature is usable only when BOTH are true; in dev both are ignored.
interface FeatureGateConfig {
  envEnabled: boolean;
  accessFlag: keyof AccessFlags;
}

function envFlag(key: string): boolean {
  return process.env[key] === 'true';
}

const FEATURE_CONFIG: Record<FeatureId, FeatureGateConfig> = {
  deepseekGlm:   { envEnabled: envFlag('NEXT_PUBLIC_ENABLE_DEEPSEEK_GLM'),  accessFlag: 'canAccessDeepseekGLM' },
  multiModel:    { envEnabled: envFlag('NEXT_PUBLIC_ENABLE_MULTI_MODEL'),   accessFlag: 'canAccessMultiModel' },
  ghostline:    { envEnabled: envFlag('NEXT_PUBLIC_ENABLE_GHOSTLINE'),       accessFlag: 'canAccessGhostline' },
  footprint:    { envEnabled: envFlag('NEXT_PUBLIC_ENABLE_FOOTPRINT'),       accessFlag: 'canAccessFootprint' },
  topup:        { envEnabled: envFlag('NEXT_PUBLIC_ENABLE_TOPUP'),           accessFlag: 'canAccessTopup' },
  instantNews:  { envEnabled: envFlag('NEXT_PUBLIC_ENABLE_INSTANT_NEWS'),    accessFlag: 'canSeeInstantNewsSantiments' },
  advanceChart: { envEnabled: envFlag('NEXT_PUBLIC_ENABLE_ADVANCE_CHART'),   accessFlag: 'canGetAdvanceChartAccess' },
};

export const FEATURE_IDS: readonly FeatureId[] = Object.keys(FEATURE_CONFIG) as FeatureId[];

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

// Compute the full access map from the user's accessFlags.
// In dev (IS_PROD=false) every feature is unlocked regardless of the user's
// plan — the kill switches and accessFlags are only enforced in prod.
export function computeFeatureAccess(accessFlags: AccessFlags | null | undefined): FeatureAccessMap {
  if (!IS_PROD) {
    return {
      deepseekGlm: true,
      multiModel: true,
      ghostline: true,
      footprint: true,
      topup: true,
      instantNews: true,
      advanceChart: true,
    };
  }

  const result = {} as FeatureAccessMap;
  for (const id of FEATURE_IDS) {
    const cfg = FEATURE_CONFIG[id];
    const granted = accessFlags ? Boolean(accessFlags[cfg.accessFlag]) : false;
    result[id] = cfg.envEnabled && granted;
  }
  return result;
}

export function hasFeature(map: FeatureAccessMap, id: FeatureId): boolean {
  return Boolean(map[id]);
}
