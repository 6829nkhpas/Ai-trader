import { create } from 'zustand';
import {
  ALL_SWITCHES_OFF,
  computeFeatureAccess,
  parseFeatureConfig,
  UNRESOLVED_FEATURE_CONFIG,
  type FeatureAccessMap,
  type FeatureId,
  type FeatureRuntimeConfig,
} from '../lib/featureFlags';
import { bridgeInvoke } from '../lib/bridge';
import {
  resolveSku,
  isModeAllowed,
  isCapabilityAllowed,
  skuEnforcementEnabled,
  type Sku,
  type AgentMode,
  type ResearchCapability,
} from '../lib/sku';
import type { AccessFlags } from '../lib/api/types';

interface FeatureState {
  access: FeatureAccessMap;
  /**
   * The deployment's kill switches, as answered by the backend.
   *
   * Starts fail-closed (`UNRESOLVED_FEATURE_CONFIG`) and is replaced once
   * `hydrateConfig` completes. It is deliberately NOT read from the bundle —
   * see `lib/featureFlags.ts`.
   */
  config: FeatureRuntimeConfig;
  /** Whether the backend has answered `get_feature_switches` yet. */
  configLoaded: boolean;
  /** Last plan flags seen, retained so `access` can be recomputed on config change. */
  accessFlags: AccessFlags | null;
  /**
   * TERMINAL / RESEARCH product SKU resolved from the user's plan.
   * Defaults to TERMINAL so the regulated surface is locked until entitlement
   * data has actually arrived (fail closed). See `lib/sku.ts`.
   */
  sku: Sku;
  hydrated: boolean;
  setAccessFlags: (flags: AccessFlags | null) => void;
  hydrateConfig: () => Promise<void>;
  reset: () => void;
}

const EMPTY_ACCESS: FeatureAccessMap = ALL_SWITCHES_OFF;

export const useFeatureStore = create<FeatureState>((set, get) => ({
  access: EMPTY_ACCESS,
  config: UNRESOLVED_FEATURE_CONFIG,
  configLoaded: false,
  accessFlags: null,
  sku: 'TERMINAL',
  hydrated: false,

  setAccessFlags: (flags) =>
    set({
      accessFlags: flags,
      access: computeFeatureAccess(flags, get().config),
      // Resolved from the same flags, but deliberately NOT routed through
      // `computeFeatureAccess` — that helper unlocks everything in dev, which is
      // acceptable for premium polish and unacceptable for a compliance gate.
      sku: resolveSku(flags),
      hydrated: true,
    }),

  /**
   * Ask the backend which features this deployment enables.
   *
   * Web → `GET /api/features`; desktop → the `get_feature_switches` Tauri
   * command. Both read the values from the server/binary environment, so nothing
   * here depends on a constant compiled into the JS bundle.
   *
   * On failure the fail-closed default is kept and the reason is logged rather
   * than swallowed — a locked panel with a silent console is exactly the kind of
   * "works on my machine" report that is impossible to diagnose later.
   */
  hydrateConfig: async () => {
    try {
      const config = parseFeatureConfig(await bridgeInvoke('get_feature_switches'));
      set({
        config,
        configLoaded: true,
        access: computeFeatureAccess(get().accessFlags, config),
      });
    } catch (err) {
      console.error(
        '[useFeatureStore] Could not load the deployment feature configuration; ' +
          'premium features stay locked until it succeeds.',
        err,
      );
      set({ configLoaded: false });
    }
  },

  reset: () =>
    set({
      access: EMPTY_ACCESS,
      accessFlags: null,
      sku: 'TERMINAL',
      hydrated: false,
      // `config` and `configLoaded` deliberately survive a reset: they describe
      // the DEPLOYMENT, not the user, so logging out must not force a refetch.
    }),
}));

export function useFeature(id: FeatureId): boolean {
  return useFeatureStore((s) => s.access[id]);
}

/** The active product SKU. */
export function useSku(): Sku {
  return useFeatureStore((s) => s.sku);
}

/**
 * Whether the RESEARCH-only `capability` may be shown.
 *
 * Returns true when enforcement is off (local dev without
 * `NEXT_PUBLIC_SKU_ENFORCE`) so day-to-day development is unaffected.
 */
export function useResearchCapability(capability: ResearchCapability): boolean {
  const sku = useSku();
  if (!skuEnforcementEnabled()) return true;
  return isCapabilityAllowed(sku, capability);
}

/**
 * Non-hook SKU mode check for use inside Zustand actions and other imperative
 * code. Reads the store directly via `getState()`.
 *
 * This is a UX affordance and defence in depth only — the authoritative gate is
 * server-side in `agents/deep-quant-loop/entitlements.py`.
 */
export function canRunAgentMode(mode: AgentMode): boolean {
  if (!skuEnforcementEnabled()) return true;
  return isModeAllowed(useFeatureStore.getState().sku, mode);
}
