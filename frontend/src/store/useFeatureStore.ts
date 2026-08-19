import { create } from 'zustand';
import { computeFeatureAccess, type FeatureAccessMap, type FeatureId } from '../lib/featureFlags';
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
   * TERMINAL / RESEARCH product SKU resolved from the user's plan.
   * Defaults to TERMINAL so the regulated surface is locked until entitlement
   * data has actually arrived (fail closed). See `lib/sku.ts`.
   */
  sku: Sku;
  hydrated: boolean;
  setAccessFlags: (flags: AccessFlags | null) => void;
  reset: () => void;
}

const EMPTY_ACCESS: FeatureAccessMap = {
  deepseekGlm: false,
  multiModel: false,
  ghostline: false,
  footprint: false,
  topup: false,
  instantNews: false,
  advanceChart: false,
};

export const useFeatureStore = create<FeatureState>((set) => ({
  access: EMPTY_ACCESS,
  sku: 'TERMINAL',
  hydrated: false,
  setAccessFlags: (flags) =>
    set({
      access: computeFeatureAccess(flags),
      // Resolved from the same flags, but deliberately NOT routed through
      // `computeFeatureAccess` — that helper unlocks everything in dev, which is
      // acceptable for premium polish and unacceptable for a compliance gate.
      sku: resolveSku(flags),
      hydrated: true,
    }),
  reset: () => set({ access: EMPTY_ACCESS, sku: 'TERMINAL', hydrated: false }),
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
